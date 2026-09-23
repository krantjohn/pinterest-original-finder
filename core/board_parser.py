"""
Board parser module using gallery-dl's mature extractor engine.
Parses Pinterest boards (including sections, shortlinks, single pins)
and extracts originals URLs, dimensions, and outbound source links.
"""
from dataclasses import dataclass, asdict
from typing import List, Optional, Generator
import urllib.parse
from pathlib import Path

from gallery_dl import extractor, config
from gallery_dl.extractor.common import Message

from config import settings
from utils.logger import logger

@dataclass
class PinItem:
    """Represents a single Pin or sub-item in a carousel."""
    pin_id: str
    pin_url: str
    title: str = ""
    description: str = ""
    domain: Optional[str] = None
    source_link: Optional[str] = None
    pinterest_orig_url: str = ""
    pinterest_width: int = 0
    pinterest_height: int = 0
    file_index: int = 1
    total_files: int = 1

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def identifier(self) -> str:
        """Unique identifier per image file in a pin."""
        if self.total_files > 1:
            return f"{self.pin_id}_{self.file_index}"
        return self.pin_id


class BoardParser:
    """Parser for Pinterest boards and pins."""

    def __init__(self, cookies_path: Optional[Path] = None):
        self.cookies_path = cookies_path or settings.cookies_path
        self._configure_gallery_dl()

    def _configure_gallery_dl(self):
        """Configure gallery-dl global settings for Pinterest."""
        # Use cookies if available
        if self.cookies_path and Path(self.cookies_path).is_file():
            logger.info(f"Loaded Pinterest cookies from {self.cookies_path}")
            config.set(("extractor", "pinterest"), "cookies", str(self.cookies_path))
        
        # Enable sections extraction
        config.set(("extractor", "pinterest"), "sections", True)
        # Enable carousel extraction
        config.set(("extractor", "pinterest"), "stories", True)
        config.set(("extractor", "pinterest"), "videos", False)  # Focus on images

    @staticmethod
    def clean_source_link(raw_link: Optional[str]) -> Optional[str]:
        """Clean and normalize source link, unwrap Pinterest redirects and strip tracking parameters."""
        if not raw_link or not isinstance(raw_link, str):
            return None

        raw_link = raw_link.strip()
        if not raw_link or raw_link == "None":
            return None

        # Check for Pinterest outbound redirect like pinterest.com/sent/?url=...
        parsed = urllib.parse.urlparse(raw_link)
        if "pinterest." in parsed.netloc and parsed.path.startswith(("/sent", "/offsite")):
            query = urllib.parse.parse_qs(parsed.query)
            if "url" in query and query["url"]:
                raw_link = query["url"][0]
                parsed = urllib.parse.urlparse(raw_link)

        # Strip standard marketing tracking params (utm_*, ref, spm, etc.)
        query_params = urllib.parse.parse_qsl(parsed.query, keep_blank_values=False)
        cleaned_params = [
            (k, v) for k, v in query_params
            if not (k.lower().startswith("utm_") or k.lower() in ("spm", "ref", "fbclid", "gclid"))
        ]
        new_query = urllib.parse.urlencode(cleaned_params)
        clean_url = urllib.parse.urlunparse((
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            new_query,
            parsed.fragment
        ))
        return clean_url

    def parse(self, url: str, limit: Optional[int] = None) -> List[PinItem]:
        """
        Parse Pinterest board or pin URL and return list of PinItem.
        Handles shortlinks (pin.it) and board sections recursively.
        """
        logger.info(f"Starting board parsing for: {url}")
        max_limit = limit or settings.max_pins_per_board
        pins: List[PinItem] = []
        queue = [url]
        seen_urls = set()
        seen_items = set()

        while queue and len(pins) < max_limit:
            current_url = queue.pop(0)
            if current_url in seen_urls:
                continue
            seen_urls.add(current_url)

            ext = extractor.find(current_url)
            if not ext:
                logger.warning(f"No extractor found for URL: {current_url}")
                continue

            try:
                ext.initialize()
            except Exception as e:
                logger.error(f"Failed to initialize extractor for {current_url}: {e}")
                continue

            try:
                for item in ext.items():
                    msg = item[0]
                    val = item[1]
                    extra = item[2] if len(item) > 2 else {}

                    # Handle queued sub-items (e.g. board sections or pin.it redirects)
                    if msg == Message.Queue:
                        queue_url = val
                        if queue_url not in seen_urls:
                            queue.append(queue_url)
                        continue

                    # Handle image URL messages
                    if msg == Message.Url:
                        img_url = val
                        pin_data = extra if isinstance(extra, dict) else {}
                        pin_id = str(pin_data.get("id") or "")
                        if not pin_id:
                            continue

                        file_index = int(pin_data.get("num") or 1)
                        total_files = int(pin_data.get("count") or 1)
                        item_key = (pin_id, file_index)
                        if item_key in seen_items:
                            continue
                        seen_items.add(item_key)

                        title = str(pin_data.get("title") or pin_data.get("grid_title") or "").strip()
                        description = str(pin_data.get("description") or "").strip()
                        domain = pin_data.get("domain")
                        raw_link = pin_data.get("link")
                        source_link = self.clean_source_link(raw_link)
                        width = int(pin_data.get("width") or 0)
                        height = int(pin_data.get("height") or 0)

                        pin_item = PinItem(
                            pin_id=pin_id,
                            pin_url=f"https://www.pinterest.com/pin/{pin_id}/",
                            title=title,
                            description=description,
                            domain=domain,
                            source_link=source_link,
                            pinterest_orig_url=img_url,
                            pinterest_width=width,
                            pinterest_height=height,
                            file_index=file_index,
                            total_files=total_files
                        )
                        pins.append(pin_item)

                        if len(pins) >= max_limit:
                            logger.info(f"Reached limit of {max_limit} pins.")
                            break

            except Exception as e:
                logger.error(f"Error extracting items from {current_url}: {e}")

        logger.info(f"Successfully parsed {len(pins)} pin items.")
        return pins
