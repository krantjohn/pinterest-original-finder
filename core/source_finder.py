"""
Source finder module.
Visits outbound / source links from Pins and attempts to extract
high-resolution or original images from author platforms (Pixiv, Twitter/X, ArtStation, boorus)
or generic web pages (OpenGraph, largest img tags, full-res links).
"""
import re
import urllib.parse
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
import httpx
from bs4 import BeautifulSoup

from config import settings, HIGH_CREDIBILITY_DOMAINS
from utils.logger import logger

@dataclass
class CandidateImage:
    """Represents a potential high-res image candidate."""
    url: str
    source_type: str  # 'source_direct', 'pixiv_orig', 'twitter_orig', 'artstation_orig', 'booru_orig', 'source_og', 'source_page'
    source_url: str
    headers: Dict[str, str] = field(default_factory=dict)
    width: Optional[int] = None
    height: Optional[int] = None
    expected_quality_score: int = 50  # 1-100 heuristic score


class SourceFinder:
    """Extracts original image candidates from outbound source links."""

    def __init__(self):
        self.default_headers = {
            "User-Agent": settings.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
        }

    def is_direct_image(self, url: str) -> bool:
        """Check if URL directly points to an image file."""
        parsed = urllib.parse.urlparse(url)
        path = parsed.path.lower()
        return any(path.endswith(ext) for ext in ('.jpg', '.jpeg', '.png', '.webp', '.bmp', '.tiff'))

    def extract_candidates(self, source_url: Optional[str]) -> List[CandidateImage]:
        """Extract all potential high-resolution images from a given source URL."""
        if not source_url:
            return []

        candidates: List[CandidateImage] = []
        source_url = source_url.strip()

        # 1. Direct image link
        if self.is_direct_image(source_url):
            candidates.append(CandidateImage(
                url=source_url,
                source_type="source_direct",
                source_url=source_url,
                expected_quality_score=90
            ))
            return candidates

        parsed = urllib.parse.urlparse(source_url)
        domain = parsed.netloc.lower()

        try:
            # 2. Platform-specific extractors
            if "pixiv.net" in domain:
                pixiv_cand = self._extract_pixiv(source_url)
                if pixiv_cand:
                    candidates.extend(pixiv_cand)

            elif "artstation.com" in domain:
                artstation_cand = self._extract_artstation(source_url)
                if artstation_cand:
                    candidates.extend(artstation_cand)

            elif any(b in domain for b in ["danbooru.donmai.us", "safebooru.org", "gelbooru.com", "yande.re", "konachan.com"]):
                booru_cand = self._extract_booru(source_url, domain)
                if booru_cand:
                    candidates.extend(booru_cand)

            # 3. Generic web page extractor (also acts as fallback for any webpage)
            page_cands = self._extract_from_webpage(source_url)
            if page_cands:
                candidates.extend(page_cands)

        except Exception as e:
            logger.warning(f"Error extracting candidates from {source_url}: {e}")

        # Deduplicate candidates by URL
        seen = set()
        unique_candidates = []
        for cand in candidates:
            if cand.url not in seen:
                seen.add(cand.url)
                unique_candidates.append(cand)

        # Sort by expected quality score descending
        unique_candidates.sort(key=lambda c: c.expected_quality_score, reverse=True)
        return unique_candidates

    def _extract_pixiv(self, url: str) -> List[CandidateImage]:
        """Extract original artwork from Pixiv link."""
        match = re.search(r'(?:artworks/|illust_id=)(\d+)', url)
        if not match:
            return []
        illust_id = match.group(1)
        api_url = f"https://www.pixiv.net/ajax/illust/{illust_id}"
        pages_url = f"https://www.pixiv.net/ajax/illust/{illust_id}/pages"

        headers = {
            "User-Agent": settings.user_agent,
            "Referer": "https://www.pixiv.net/",
        }

        candidates = []
        try:
            with httpx.Client(timeout=settings.request_timeout, headers=headers) as client:
                # First check multiple pages
                resp_pages = client.get(pages_url)
                if resp_pages.status_code == 200:
                    data = resp_pages.json()
                    for p in data.get("body", []):
                        orig_url = p.get("urls", {}).get("original")
                        if orig_url:
                            candidates.append(CandidateImage(
                                url=orig_url,
                                source_type="pixiv_orig",
                                source_url=url,
                                headers={"Referer": "https://www.pixiv.net/"},
                                width=p.get("width"),
                                height=p.get("height"),
                                expected_quality_score=98
                            ))
                if candidates:
                    return candidates

                # Otherwise check single illust
                resp = client.get(api_url)
                if resp.status_code == 200:
                    data = resp.json()
                    body = data.get("body", {})
                    orig_url = body.get("urls", {}).get("original")
                    if orig_url:
                        candidates.append(CandidateImage(
                            url=orig_url,
                            source_type="pixiv_orig",
                            source_url=url,
                            headers={"Referer": "https://www.pixiv.net/"},
                            width=body.get("width"),
                            height=body.get("height"),
                            expected_quality_score=98
                        ))
        except Exception as e:
            logger.debug(f"Pixiv extraction error for {url}: {e}")

        return candidates

    def _extract_artstation(self, url: str) -> List[CandidateImage]:
        """Extract original images from ArtStation artwork link."""
        candidates = []
        match = re.search(r'artstation\.com/artwork/([a-zA-Z0-9]+)', url)
        if not match:
            return candidates

        hash_id = match.group(1)
        api_url = f"https://www.artstation.com/projects/{hash_id}.json"

        try:
            with httpx.Client(timeout=settings.request_timeout, headers=self.default_headers) as client:
                resp = client.get(api_url)
                if resp.status_code == 200:
                    data = resp.json()
                    for asset in data.get("assets", []):
                        img_url = asset.get("image_url")
                        if img_url:
                            candidates.append(CandidateImage(
                                url=img_url,
                                source_type="artstation_orig",
                                source_url=url,
                                width=asset.get("width"),
                                height=asset.get("height"),
                                expected_quality_score=95
                            ))
        except Exception as e:
            logger.debug(f"ArtStation extraction error for {url}: {e}")

        return candidates

    def _extract_booru(self, url: str, domain: str) -> List[CandidateImage]:
        """Extract uncompressed original image from anime image boards."""
        candidates = []
        match = re.search(r'/post(?:s|/show)/(\d+)', url)
        if not match:
            return candidates

        post_id = match.group(1)
        scheme = "https"
        api_url = f"{scheme}://{domain}/posts/{post_id}.json"

        try:
            with httpx.Client(timeout=settings.request_timeout, headers=self.default_headers) as client:
                resp = client.get(api_url)
                if resp.status_code == 200:
                    data = resp.json()
                    file_url = data.get("file_url") or data.get("large_file_url")
                    if file_url:
                        if not file_url.startswith("http"):
                            file_url = urllib.parse.urljoin(url, file_url)
                        candidates.append(CandidateImage(
                            url=file_url,
                            source_type="booru_orig",
                            source_url=url,
                            width=data.get("image_width"),
                            height=data.get("image_height"),
                            expected_quality_score=95
                        ))
        except Exception as e:
            logger.debug(f"Booru extraction error for {url}: {e}")

        return candidates

    def _extract_from_webpage(self, url: str) -> List[CandidateImage]:
        """Extract high-resolution images from standard web pages (OpenGraph, large images)."""
        candidates: List[CandidateImage] = []

        try:
            with httpx.Client(
                timeout=settings.request_timeout,
                headers=self.default_headers,
                follow_redirects=True
            ) as client:
                resp = client.get(url)
                if resp.status_code >= 400:
                    return candidates

                content_type = resp.headers.get("content-type", "")
                if "image/" in content_type:
                    candidates.append(CandidateImage(
                        url=str(resp.url),
                        source_type="source_direct",
                        source_url=url,
                        expected_quality_score=90
                    ))
                    return candidates

                soup = BeautifulSoup(resp.text, "html.parser")

                # A. OpenGraph image (og:image)
                og_img = soup.find("meta", property=re.compile(r"og:image(:url)?", re.I))
                if og_img and og_img.get("content"):
                    og_url = urllib.parse.urljoin(url, og_img["content"].strip())
                    if not self._is_junk_image(og_url):
                        candidates.append(CandidateImage(
                            url=og_url,
                            source_type="source_og",
                            source_url=url,
                            expected_quality_score=85
                        ))

                # B. Twitter card image (twitter:image)
                tw_img = soup.find("meta", attrs={"name": re.compile(r"twitter:image(:src)?", re.I)})
                if tw_img and tw_img.get("content"):
                    tw_url = urllib.parse.urljoin(url, tw_img["content"].strip())
                    if not self._is_junk_image(tw_url):
                        candidates.append(CandidateImage(
                            url=tw_url,
                            source_type="source_twitter_card",
                            source_url=url,
                            expected_quality_score=80
                        ))

                # C. Check links pointing to full-resolution images
                for a_tag in soup.find_all("a", href=True):
                    href = a_tag["href"].strip()
                    if self.is_direct_image(href):
                        full_img_url = urllib.parse.urljoin(url, href)
                        if not self._is_junk_image(full_img_url):
                            candidates.append(CandidateImage(
                                url=full_img_url,
                                source_type="source_link_direct",
                                source_url=url,
                                expected_quality_score=88
                            ))

                # D. Check <img> tags and extract highest resolution from srcset or data attributes
                for img in soup.find_all("img"):
                    # Check srcset for highest width
                    srcset = img.get("srcset")
                    if srcset:
                        best_srcset_url = self._parse_best_srcset(srcset, url)
                        if best_srcset_url and not self._is_junk_image(best_srcset_url):
                            candidates.append(CandidateImage(
                                url=best_srcset_url,
                                source_type="source_srcset_max",
                                source_url=url,
                                expected_quality_score=87
                            ))

                    # Check data-original / data-src / src
                    for attr in ["data-original", "data-full-url", "data-high-res-src", "data-src", "src"]:
                        src = img.get(attr)
                        if src:
                            clean_src = self._clean_image_url(urllib.parse.urljoin(url, src.strip()))
                            if not self._is_junk_image(clean_src):
                                candidates.append(CandidateImage(
                                    url=clean_src,
                                    source_type="source_img_tag",
                                    source_url=url,
                                    expected_quality_score=75
                                ))
                            break

        except Exception as e:
            logger.debug(f"Webpage parse error for {url}: {e}")

        return candidates

    @staticmethod
    def _parse_best_srcset(srcset: str, base_url: str) -> Optional[str]:
        """Parse srcset attribute and pick the URL with the largest width descriptor (e.g. 2000w)."""
        entries = [e.strip() for e in srcset.split(",") if e.strip()]
        best_url = None
        max_size = 0

        for entry in entries:
            parts = entry.split()
            if not parts:
                continue
            entry_url = parts[0]
            size = 1
            if len(parts) > 1:
                size_str = parts[1].lower()
                if size_str.endswith('w'):
                    try:
                        size = int(size_str[:-1])
                    except ValueError:
                        size = 1
                elif size_str.endswith('x'):
                    try:
                        size = int(float(size_str[:-1]) * 1000)
                    except ValueError:
                        size = 1
            if size > max_size:
                max_size = size
                best_url = entry_url

        if best_url:
            return urllib.parse.urljoin(base_url, best_url)
        return None

    @staticmethod
    def _clean_image_url(url: str) -> str:
        """Strip thumbnail/resizing parameters from image URL (e.g. ?w=1024, ?resize=...)."""
        parsed = urllib.parse.urlparse(url)
        # If it's a known CMS resizing query, remove resize queries
        q = urllib.parse.parse_qsl(parsed.query)
        cleaned_q = [(k, v) for k, v in q if k.lower() not in ("w", "width", "resize", "maxwidth", "crop")]
        new_query = urllib.parse.urlencode(cleaned_q)
        return urllib.parse.urlunparse((
            parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment
        ))

    @staticmethod
    def _is_junk_image(url: str) -> bool:
        """Filter out tracking pixels, icons, banners, avatars, etc."""
        url_lower = url.lower()
        junk_patterns = [
            "avatar", "logo", "icon", "favicon", "banner", "footer", "header",
            "spacer", "blank.gif", "1x1", "pixel", "badge", "button", ".svg",
            "emoji", "smilies", "adsystem", "analytics"
        ]
        return any(pattern in url_lower for pattern in junk_patterns)
