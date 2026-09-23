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

    def process_pin(self, pin: PinItem) -> DownloadResult:
        """
        Process a single Pin following the 3-tier cascade:
        1. Try outbound source link
        2. Try reverse image search
        3. Fallback to Pinterest originals
        """
        logger.info(f"Processing Pin [{pin.identifier}] - {pin.title or 'No Title'}")
        
        # 1. Baseline: Download Pinterest originals version
        pinterest_data = self._download_and_inspect_image(pin.pinterest_orig_url)
        if pinterest_data:
            pin_bytes, p_width, p_height, p_ext = pinterest_data
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
            
            # Test top candidates from source link (up to 5)
            for cand in candidates[:5]:
                # If cand is a webpage rather than image, skip direct download
                if not cand.url.startswith("http"):
                    continue
                
                self._sleep_polite()
                cand_data = self._download_and_inspect_image(cand.url, cand.headers)
                if not cand_data:
                    continue

                c_bytes, c_width, c_height, c_ext = cand_data
                c_pixels = c_width * c_height

                # Noticeably higher resolution check (at least 10% more pixels, or larger dimension)
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

            # Test top reverse search candidates (up to 6)
            for cand in reverse_cands[:6]:
                self._sleep_polite()
                # If candidate is a page (like pixiv/artstation link from SauceNAO), resolve via source_finder
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

                    if c_pixels > baseline_pixels * 1.15:
                        logger.info(
                            f"Found superior resolution via reverse search: "
                            f"{c_width}x{c_height} > {pinterest_res_str}"
                        )
                        best_bytes = c_bytes
                        best_width = c_width
                        best_height = c_height
                        best_ext = c_ext
                        best_source_url = t_url
                        best_channel = "reverse_search"
                        status = "found_via_reverse_search"
                        status_label = f"成功通过反向搜图找到超大原图"
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
