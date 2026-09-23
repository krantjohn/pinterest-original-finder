"""
Image downloader and resolution comparison engine.
Executes the strict 3-tier cascade:
  1. Source / outbound link candidates
  2. Reverse image search candidates
  3. Pinterest originals fallback (tagged: '未找到更高清来源')
Compares pixel counts and image quality to guarantee the highest resolution image is saved.
"""
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional, Tuple, Dict, Any, List
import io
import time
import random
import httpx
from PIL import Image

from config import settings
from core.board_parser import PinItem
from core.source_finder import SourceFinder, CandidateImage
from core.reverse_search import ReverseSearcher
from utils.logger import logger

@dataclass
class DownloadResult:
    """Detailed record of processing a single Pin."""
    pin_id: str
    pin_url: str
    title: str
    source_link: Optional[str]
    pinterest_orig_url: str
    final_filepath: Optional[str] = None
    final_filename: Optional[str] = None
    final_source_url: str = ""
    source_channel: str = ""  # 'source_outbound', 'reverse_search', 'pinterest_fallback'
    status: str = "pending"   # 'found_via_source', 'found_via_reverse_search', 'pinterest_fallback', 'failed'
    status_label: str = ""
    pinterest_resolution: str = ""
    final_resolution: str = ""
    final_width: int = 0
    final_height: int = 0
    file_size_bytes: int = 0
    is_higher_res: bool = False
    resolution_increase_pct: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ImageDownloader:
    """Downloader that selects the highest resolution image following priority rules."""

    def __init__(self, output_dir: Optional[Path] = None):
        self.output_dir = output_dir or settings.download_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.source_finder = SourceFinder()
        self.reverse_searcher = ReverseSearcher()
        self.headers = {
            "User-Agent": settings.user_agent,
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        }

    def _sleep_polite(self):
        """Respectful delay between requests to avoid rate limits."""
        delay = random.uniform(settings.rate_limit_delay_min, settings.rate_limit_delay_max)
        time.sleep(delay)

    def _download_and_inspect_image(
        self,
        url: str,
        extra_headers: Optional[Dict[str, str]] = None
    ) -> Optional[Tuple[bytes, int, int, str]]:
        """
        Download image into bytes and inspect dimensions and format.
        Returns: (bytes, width, height, extension) or None.
        """
        req_headers = self.headers.copy()
        if extra_headers:
            req_headers.update(extra_headers)

        try:
            with httpx.Client(
                timeout=settings.download_timeout,
                headers=req_headers,
                follow_redirects=True
            ) as client:
                resp = client.get(url)
                if resp.status_code != 200:
                    return None

                content = resp.content
                if len(content) < 1024:  # Under 1KB is likely an error or tracking pixel
                    return None

                # Inspect image with Pillow
                try:
                    with Image.open(io.BytesIO(content)) as img:
                        width, height = img.size
                        fmt = (img.format or "JPEG").lower()
                        if fmt == "jpeg":
                            ext = "jpg"
                        else:
                            ext = fmt
                        return content, width, height, ext
                except Exception as img_err:
                    logger.debug(f"Pillow could not open image from {url}: {img_err}")
                    return None

        except Exception as e:
            logger.debug(f"Failed to download candidate {url}: {e}")
            return None

    @staticmethod
    def _verify_same_image(
        base_img: Optional[Image.Image],
        cand_img: Image.Image,
        max_dist: int = 7
    ) -> Tuple[bool, str]:
        """
        Verify that candidate image is visually identical to baseline Pinterest image.
        Uses aspect ratio tolerance and perceptual hashing (pHash & dHash).
        """
        if not base_img:
            return True, "No baseline image to compare against"

        # 1. Aspect Ratio check (must be within 15% tolerance)
        w_b, h_b = base_img.size
        w_c, h_c = cand_img.size
        ratio_b = w_b / max(h_b, 1)
        ratio_c = w_c / max(h_c, 1)
        ratio_diff = abs(ratio_b - ratio_c) / ratio_b
        if ratio_diff > 0.15:
            return False, f"Aspect ratio mismatch ({ratio_b:.2f} vs {ratio_c:.2f}, diff={ratio_diff:.1%})"

        # 2. Perceptual hash comparison (pHash)
        try:
            import imagehash
            h_b = imagehash.phash(base_img)
            h_c = imagehash.phash(cand_img)
            dist = h_b - h_c
            if dist > max_dist:
                return False, f"Visual mismatch (pHash distance={dist} > {max_dist})"

            dh_b = imagehash.dhash(base_img)
            dh_c = imagehash.dhash(cand_img)
            d_dist = dh_b - dh_c
            if d_dist > max_dist + 2:
                return False, f"Gradient mismatch (dHash distance={d_dist} > {max_dist + 2})"

            return True, f"Visual verified (pHash={dist}, dHash={d_dist})"
        except Exception as e:
            logger.debug(f"Imagehash calculation error: {e}")
            return False, f"Hash check error: {e}"

    def process_pin(self, pin: PinItem) -> DownloadResult:
        """
        Process a single Pin following the 3-tier cascade:
        1. Try outbound source link
        2. Try reverse image search
        3. Fallback to Pinterest originals
        """
        logger.info(f"Processing Pin [{pin.identifier}] - {pin.title or 'No Title'}")

        # Check if already processed and saved in output_dir (instant cache)
        existing_matches = list(self.output_dir.glob(f"{pin.identifier}_*.*"))
        if existing_matches:
            matched_file = existing_matches[0]
            if matched_file.is_file() and matched_file.stat().st_size > 1024:
                try:
                    with Image.open(matched_file) as im:
                        f_w, f_h = im.size
                    p_w = pin.pinterest_width or f_w
                    p_h = pin.pinterest_height or f_h
                    is_higher = (f_w * f_h > p_w * p_h * 1.1)
                    logger.info(f"Pin [{pin.identifier}] already in local cache ({f_w}x{f_h}), skipping network fetch.")
                    return DownloadResult(
                        pin_id=pin.pin_id,
                        pin_url=pin.pin_url,
                        title=pin.title,
                        source_link=pin.source_link,
                        pinterest_orig_url=pin.pinterest_orig_url,
                        final_filepath=str(matched_file),
                        final_filename=matched_file.name,
                        final_source_url=pin.source_link or pin.pinterest_orig_url,
                        source_channel="local_cache",
                        status="cached",
                        status_label="已从本地高速缓存载入",
                        pinterest_resolution=f"{p_w}x{p_h}",
                        final_resolution=f"{f_w}x{f_h}",
                        final_width=f_w,
                        final_height=f_h,
                        file_size_bytes=matched_file.stat().st_size,
                        is_higher_res=is_higher
                    )
                except Exception:
                    pass

        # 1. Baseline: Download Pinterest originals version
        pinterest_data = self._download_and_inspect_image(pin.pinterest_orig_url)
        baseline_img = None
        if pinterest_data:
            pin_bytes, p_width, p_height, p_ext = pinterest_data
            try:
                baseline_img = Image.open(io.BytesIO(pin_bytes))
            except Exception:
                pass
        else:
            pin_bytes, p_width, p_height, p_ext = b"", pin.pinterest_width, pin.pinterest_height, "jpg"

        baseline_pixels = p_width * p_height
        pinterest_res_str = f"{p_width}x{p_height}" if p_width and p_height else "unknown"

        best_bytes = pin_bytes
        best_width = p_width
        best_height = p_height
        best_ext = p_ext
        best_source_url = pin.pinterest_orig_url
        best_channel = "pinterest_fallback"
        status = "pinterest_fallback"
        status_label = "未找到更高清来源（使用 Pinterest 原图）"
        is_higher_res = False

        # --- Tier 1: Try Pin's source / outbound link ---
        if pin.source_link:
            logger.info(f"Tier 1: Inspecting source link: {pin.source_link}")
            self._sleep_polite()
            candidates = self.source_finder.extract_candidates(pin.source_link)

            for cand in candidates[:5]:
                if not cand.url.startswith("http"):
                    continue

                self._sleep_polite()
                cand_data = self._download_and_inspect_image(cand.url, cand.headers)
                if not cand_data:
                    continue

                c_bytes, c_width, c_height, c_ext = cand_data
                c_pixels = c_width * c_height

                # Verify visual identity first
                try:
                    cand_img = Image.open(io.BytesIO(c_bytes))
                    is_same, verify_msg = self._verify_same_image(baseline_img, cand_img)
                    if not is_same:
                        logger.warning(f"Tier 1 candidate rejected: {verify_msg} ({cand.url})")
                        continue
                except Exception:
                    continue

                # Noticeably higher resolution check
                if c_pixels > baseline_pixels * 1.10:
                    logger.info(
                        f"Found superior resolution via source link: "
                        f"{c_width}x{c_height} > {pinterest_res_str} ({cand.source_type})"
                    )
                    best_bytes = c_bytes
                    best_width = c_width
                    best_height = c_height
                    best_ext = c_ext
                    best_source_url = cand.url
                    best_channel = "source_outbound"
                    status = "found_via_source"
                    status_label = f"成功从作者来源获取原图 ({cand.source_type})"
                    is_higher_res = True
                    break

        # --- Tier 2: Reverse Image Search fallback ---
        if not is_higher_res:
            logger.info("Tier 2: Source link yielded no higher-res image, trying reverse search...")
            self._sleep_polite()
            reverse_cands = self.reverse_searcher.search(pin.pinterest_orig_url)

            for cand in reverse_cands[:8]:
                self._sleep_polite()
                target_urls = [cand.url]
                if not self.source_finder.is_direct_image(cand.url):
                    sub_cands = self.source_finder.extract_candidates(cand.url)
                    if sub_cands:
                        target_urls = [sc.url for sc in sub_cands[:2]]

                for t_url in target_urls:
                    cand_data = self._download_and_inspect_image(t_url, cand.headers)
                    if not cand_data:
                        continue

                    c_bytes, c_width, c_height, c_ext = cand_data
                    c_pixels = c_width * c_height

                    # STRICT visual identity check! Reject different illustrations of same character
                    try:
                        cand_img = Image.open(io.BytesIO(c_bytes))
                        is_same, verify_msg = self._verify_same_image(baseline_img, cand_img)
                        if not is_same:
                            logger.warning(f"Tier 2 candidate rejected: {verify_msg} ({t_url})")
                            continue
                    except Exception:
                        continue

                    if c_pixels > baseline_pixels * 1.15:
                        logger.info(
                            f"Found superior resolution via reverse search: "
                            f"{c_width}x{c_height} > {pinterest_res_str} (Verified identical image!)"
                        )
                        best_bytes = c_bytes
                        best_width = c_width
                        best_height = c_height
                        best_ext = c_ext
                        best_source_url = t_url
                        best_channel = "reverse_search"
                        status = "found_via_reverse_search"
                        status_label = "成功通过反向搜图找到超大原图"
                        is_higher_res = True
                        break

                if is_higher_res:
                    break

        # --- Tier 3: Pinterest fallback check ---
        if not is_higher_res and not best_bytes and pinterest_data:
            best_bytes = pinterest_data[0]

        # Calculate final metrics
        final_pixels = best_width * best_height
        pct_increase = 0.0
        if baseline_pixels > 0:
            pct_increase = round(((final_pixels - baseline_pixels) / baseline_pixels) * 100, 1)

        # Save to local disk
        filename = f"{pin.identifier}_{best_width}x{best_height}.{best_ext}"
        filepath = self.output_dir / filename

        if best_bytes:
            with open(filepath, "wb") as f:
                f.write(best_bytes)
            filesize = len(best_bytes)
            logger.info(f"Saved: {filename} ({filesize / 1024 / 1024:.2f} MB, {status_label})")
        else:
            status = "failed"
            status_label = "下载失败"
            filesize = 0
            filepath = None
            filename = None

        return DownloadResult(
            pin_id=pin.pin_id,
            pin_url=pin.pin_url,
            title=pin.title,
            source_link=pin.source_link,
            pinterest_orig_url=pin.pinterest_orig_url,
            final_filepath=str(filepath) if filepath else None,
            final_filename=filename,
            final_source_url=best_source_url,
            source_channel=best_channel,
            status=status,
            status_label=status_label,
            pinterest_resolution=pinterest_res_str,
            final_resolution=f"{best_width}x{best_height}",
            final_width=best_width,
            final_height=best_height,
            file_size_bytes=filesize,
            is_higher_res=is_higher_res,
            resolution_increase_pct=pct_increase
        )
