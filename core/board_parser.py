"""
Board parser module using gallery-dl's mature extractor engine.
Parses Pinterest boards (including sections, shortlinks, single pins)
and extracts originals URLs, dimensions, and outbound source links.
"""
from dataclasses import dataclass, asdict
from typing import List, Optional, Generator
import re
import urllib.parse
from pathlib import Path
from typing import List, Optional, Generator
import requests

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
    board_name: str = ""
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

    def _resolve_url(self, url: str) -> str:
        """Resolve shortlinks (like pin.it) to final destination URL."""
        if "pin.it" in url:
            try:
                r = requests.get(
                    url,
                    allow_redirects=True,
                    headers={"User-Agent": settings.user_agent},
                    timeout=settings.request_timeout
                )
                return r.url
            except Exception as e:
                logger.warning(f"Failed to resolve shortlink {url}: {e}")
        return url

    def _fetch_pidgets_pins(self, username: str, board_name: str) -> List[dict]:
        """Fetch pins directly from Pinterest mobile/pidgets API to bypass web guest filter."""
        results = []
        pidget_urls = [
            f"https://api.pinterest.com/v3/pidgets/boards/{username}/{board_name}/pins/",
            f"https://api.pinterest.com/v3/pidgets/users/{username}/pins/"
        ]
        headers = {
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148"
        }
        board_id = None
        seen_pids = set()

        for purl in pidget_urls:
            try:
                r = requests.get(purl, headers=headers, timeout=settings.request_timeout)
                if r.status_code != 200:
                    continue
                data = r.json().get("data", {})
                if "board" in data and "id" in data["board"]:
                    board_id = str(data["board"]["id"])
                
                for p in data.get("pins", []):
                    # For user profile feed, only accept pins pinned to this board
                    if "users" in purl and board_id:
                        if str(p.get("board", {}).get("id")) != board_id:
                            continue
                    
                    pid = str(p.get("id") or "")
                    if pid and pid not in seen_pids:
                        seen_pids.add(pid)
                        results.append(p)
            except Exception as e:
                logger.warning(f"Pidgets API request failed for {purl}: {e}")
        return results

    @staticmethod
    def extract_board_name(url: str) -> str:
        """Extract clean, safe human-readable board name from URL."""
        match = re.search(r"pinterest\.[\w.]+/(?!pin/|today/|ideas/)([^/?#]+)/([^/?#]+)", url)
        if match:
            raw_name = urllib.parse.unquote(match.group(2)).strip()
            clean_name = re.sub(r'[\\/*?:"<>|\r\n\t]', '_', raw_name).strip().strip('. ')
            return clean_name
        return ""

    def parse(self, url: str, limit: Optional[int] = None) -> List[PinItem]:
        """
        Parse Pinterest board or pin URL and return list of PinItem.
        Handles shortlinks (pin.it), board sections, and combines web + mobile APIs.
        """
        resolved_url = self._resolve_url(url)
        current_board_name = self.extract_board_name(resolved_url)
        logger.info(f"Starting board parsing for: {url} (resolved: {resolved_url}, board: '{current_board_name}')")
        max_limit = limit or settings.max_pins_per_board
        pins: List[PinItem] = []
        queue = [resolved_url]
        seen_urls = set()
        seen_items = set()


        # Step 1: Run gallery-dl extraction
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

                    # Handle queued sub-items (e.g. board sections)
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
                            board_name=current_board_name,
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

        board_match = re.search(r"pinterest\.[\w.]+/(?!pin/|today/|ideas/)([^/?#]+)/([^/?#]+)", resolved_url)

        # Step 1.5: If cookies were used, also do an unauthenticated pass to merge any public pins
        if self.cookies_path and Path(self.cookies_path).is_file() and board_match and len(pins) < max_limit:
            try:
                config.set(("extractor", "pinterest"), "cookies", None)
                unauth_ext = extractor.find(resolved_url)
                if unauth_ext:
                    unauth_ext.initialize()
                    for item in unauth_ext.items():
                        if item[0] == Message.Url:
                            pin_data = item[2] if len(item) > 2 and isinstance(item[2], dict) else {}
                            pin_id = str(pin_data.get("id") or "")
                            file_index = int(pin_data.get("num") or 1)
                            item_key = (pin_id, file_index)
                            if not pin_id or item_key in seen_items:
                                continue
                            seen_items.add(item_key)
                            pin_item = PinItem(
                                pin_id=pin_id,
                                pin_url=f"https://www.pinterest.com/pin/{pin_id}/",
                                title=str(pin_data.get("title") or pin_data.get("grid_title") or "").strip(),
                                description=str(pin_data.get("description") or "").strip(),
                                board_name=current_board_name,
                                domain=pin_data.get("domain"),
                                source_link=self.clean_source_link(pin_data.get("link")),
                                pinterest_orig_url=item[1],
                                pinterest_width=int(pin_data.get("width") or 0),
                                pinterest_height=int(pin_data.get("height") or 0),
                                file_index=file_index,
                                total_files=int(pin_data.get("count") or 1)
                            )
                            pins.append(pin_item)
                            if len(pins) >= max_limit:
                                break
            except Exception as e:
                logger.warning(f"Unauthenticated merge pass error: {e}")
            finally:
                config.set(("extractor", "pinterest"), "cookies", str(self.cookies_path))

        # Step 2: Merge mobile pidgets API items for boards
        if board_match and len(pins) < max_limit:
            b_user = urllib.parse.unquote(board_match.group(1))
            b_name = urllib.parse.unquote(board_match.group(2))
            logger.info(f"Checking mobile pidgets feed for board @{b_user}/{b_name}...")
            pidget_pins = self._fetch_pidgets_pins(b_user, b_name)
            pidget_added = 0

            for p in pidget_pins:
                if len(pins) >= max_limit:
                    break
                pin_id = str(p.get("id") or "")
                if not pin_id:
                    continue

                item_key = (pin_id, 1)
                if item_key in seen_items:
                    continue
                seen_items.add(item_key)

                # Find highest resolution image URL
                images = p.get("images", {})
                img_url = None
                img_w = 0
                img_h = 0
                for size_key in ["orig", "originals", "564x", "237x", "236x"]:
                    if size_key in images and isinstance(images[size_key], dict) and "url" in images[size_key]:
                        img_url = images[size_key]["url"]
                        img_w = int(images[size_key].get("width") or 0)
                        img_h = int(images[size_key].get("height") or 0)
                        break

                if not img_url:
                    continue

                # Convert to originals URL
                orig_url = re.sub(r"/[0-9]+x/", "/originals/", img_url)
                title = str(p.get("title") or "").strip()
                description = str(p.get("description") or "").strip()
                domain = p.get("domain")
                raw_link = p.get("link")
                source_link = self.clean_source_link(raw_link)

                pin_item = PinItem(
                    pin_id=pin_id,
                    pin_url=f"https://www.pinterest.com/pin/{pin_id}/",
                    title=title,
                    description=description,
                    board_name=current_board_name,
                    domain=domain,
                    source_link=source_link,
                    pinterest_orig_url=orig_url,
                    pinterest_width=img_w,
                    pinterest_height=img_h,
                    file_index=1,
                    total_files=1
                )
                pins.append(pin_item)
                pidget_added += 1

            if pidget_added > 0:
                logger.info(f"Added {pidget_added} additional pins via mobile pidgets feed (total: {len(pins)}).")

        logger.info(f"Successfully parsed {len(pins)} pin items in total.")
        return pins
