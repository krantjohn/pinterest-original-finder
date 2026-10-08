"""
Yande.re high-resolution original illustration extractor & reverse searcher.
Features:
- Fast ID extraction from captions/text
- Direct visual similarity search via yande.re /post/similar
- Multi-engine backup search via IQDB and SauceNAO
- Direct download of highest-resolution lossless originals (file_url / jpeg_url)
- Clean formatting for Telegram document delivery
"""
import asyncio
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple
import urllib.parse

import httpx
from bs4 import BeautifulSoup

from config import settings
from utils.logger import logger


@dataclass
class YandePost:
    """Yande.re post metadata."""
    id: int
    file_url: str
    jpeg_url: str
    sample_url: str
    preview_url: str
    file_size: int
    file_ext: str
    width: int
    height: int
    rating: str
    author: str
    source: str
    tags: str
    score: int
    md5: str
    post_url: str

    @property
    def size_mb(self) -> float:
        return round(self.file_size / (1024 * 1024), 2)

    @property
    def rating_desc(self) -> str:
        r = (self.rating or "").lower()
        if r == "s":
            return "🟢 Safe (全年龄)"
        elif r == "q":
            return "🟡 Questionable (轻度敏感)"
        elif r == "e":
            return "🔴 Explicit (18+ / R18)"
        return f"⚪ {self.rating}"

    def format_caption(self) -> str:
        """Format an informative HTML caption for Telegram document message."""
        tags_list = [t for t in self.tags.split() if t]
        tags_preview = " ".join(f"#{t}" for t in tags_list[:8])
        if len(tags_list) > 8:
            tags_preview += f" 等 {len(tags_list)} 个标签"

        source_line = ""
        if self.source:
            clean_source = self.source.strip()
            if clean_source.startswith("http"):
                parsed = urllib.parse.urlparse(clean_source)
                domain = parsed.netloc.lower().replace("www.", "")
                source_line = f"🔗 原作来源: <a href=\"{clean_source}\">{domain}</a>\n"
            else:
                source_line = f"🔗 原作来源: <code>{clean_source}</code>\n"

        author_display = self.author or "未知"

        caption = (
            f"🎨 <b>Yande.re #{self.id}</b>\n"
            f"📐 原始分辨率: <code>{self.width} × {self.height}</code>\n"
            f"💾 文件大小: <code>{self.size_mb} MB ({self.file_ext.upper()})</code>\n"
            f"🔞 内容分级: {self.rating_desc}\n"
            f"👤 作者/画师: <code>{author_display}</code>\n"
            f"{source_line}"
            f"🌐 Yande 页面: <a href=\"{self.post_url}\">yande.re/post/show/{self.id}</a>\n"
            f"🏷️ 标签: {tags_preview}"
        )
        return caption


class YandeFinder:
    """Specialized engine to discover and download true originals from yande.re."""

    def __init__(self, saucenao_api_key: Optional[str] = None):
        self.saucenao_key = saucenao_api_key or settings.saucenao_api_key
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8",
        }
        self._csrf_token: Optional[str] = None
        self._csrf_lock = asyncio.Lock()

    def extract_id_from_text(self, text: str) -> Optional[int]:
        """Extract Yande post ID from message text, caption or URL."""
        if not text:
            return None

        patterns = [
            re.compile(r"yande\.re/post/show/(\d+)", re.I),
            re.compile(r"yande(?:\.re)?[:\s_#]+(\d{4,8})", re.I),
            re.compile(r"(?:^|\s)#(?:yande[_\s]*)?(\d{4,8})\b", re.I),
            re.compile(r"\b(?:id|post)[:\s#]+(\d{4,8})\b", re.I),
            re.compile(r"^\s*(\d{5,8})\s*$"),  # bare post id
        ]
        for p in patterns:
            m = p.search(text)
            if m:
                try:
                    return int(m.group(1))
                except ValueError:
                    pass
        return None

    async def get_post_by_id(self, post_id: int) -> Optional[YandePost]:
        """Query yande.re post.json API for exact post metadata."""
        api_url = f"https://yande.re/post.json?tags=id:{post_id}"
        try:
            async with httpx.AsyncClient(headers=self.headers, timeout=15.0) as client:
                resp = await client.get(api_url)
                if resp.status_code == 200:
                    data = resp.json()
                    if data and isinstance(data, list) and len(data) > 0:
                        p = data[0]
                        return YandePost(
                            id=int(p["id"]),
                            file_url=p.get("file_url") or p.get("jpeg_url", ""),
                            jpeg_url=p.get("jpeg_url", ""),
                            sample_url=p.get("sample_url", ""),
                            preview_url=p.get("preview_url", ""),
                            file_size=int(p.get("file_size") or 0),
                            file_ext=p.get("file_ext", "jpg"),
                            width=int(p.get("width") or 0),
                            height=int(p.get("height") or 0),
                            rating=p.get("rating", "s"),
                            author=p.get("author", ""),
                            source=p.get("source", ""),
                            tags=p.get("tags", ""),
                            score=int(p.get("score") or 0),
                            md5=p.get("md5", ""),
                            post_url=f"https://yande.re/post/show/{p['id']}"
                        )
        except Exception as e:
            logger.warning(f"Error querying yande.re post {post_id}: {e}")
        return None

    async def _get_csrf_token(self, client: httpx.AsyncClient) -> Optional[str]:
        """Fetch and cache CSRF token from yande.re/post/similar."""
        async with self._csrf_lock:
            if self._csrf_token:
                return self._csrf_token
            try:
                r = await client.get("https://yande.re/post/similar")
                m = re.search(r'name="authenticity_token"\s+value="([^"]+)"', r.text)
                if m:
                    self._csrf_token = m.group(1)
            except Exception as e:
                logger.debug(f"Failed to extract authenticity_token: {e}")
            return self._csrf_token

    async def search_yande_similar(self, img_bytes: bytes) -> List[int]:
        """Search yande.re's official /post/similar feature using uploaded image."""
        try:
            async with httpx.AsyncClient(headers=self.headers, timeout=30.0, follow_redirects=True) as client:
                token = await self._get_csrf_token(client)
                files = {"file": ("query.jpg", img_bytes, "image/jpeg")}
                data = {"authenticity_token": token} if token else {}
                r = await client.post("https://yande.re/post/similar", files=files, data=data)
                if r.status_code == 200:
                    matches = [int(x) for x in re.findall(r"/post/show/(\d+)", r.text)]
                    # Deduplicate preserving order
                    seen = set()
                    ordered = []
                    for m in matches:
                        if m not in seen:
                            seen.add(m)
                            ordered.append(m)
                    if ordered:
                        logger.info(f"yande.re /post/similar found matching IDs: {ordered[:3]}")
                        return ordered
        except Exception as e:
            logger.debug(f"yande.re /post/similar search error: {e}")
            # Invalidate cached token on error
            self._csrf_token = None
        return []

    async def search_iqdb(self, img_bytes: bytes) -> List[int]:
        """Search IQDB (iqdb.org) for yande.re matches."""
        try:
            url = "https://iqdb.org/"
            files = {"file": ("query.jpg", img_bytes, "image/jpeg")}
            data = {"service[]": ["1", "2", "3", "4", "5", "6", "11", "13"]}
            async with httpx.AsyncClient(headers=self.headers, timeout=25.0) as client:
                r = await client.post(url, files=files, data=data)
                if r.status_code == 200:
                    soup = BeautifulSoup(r.text, "html.parser")
                    yande_ids = []
                    for table in soup.find_all("table"):
                        txt = table.text
                        if "yande.re" in txt:
                            m_id = re.search(r"post/show/(\d+)", str(table))
                            m_sim = re.search(r"(\d+)%\s*similarity", txt)
                            sim = int(m_sim.group(1)) if m_sim else 0
                            if m_id and sim >= 45:
                                yande_ids.append(int(m_id.group(1)))
                    if yande_ids:
                        logger.info(f"IQDB found yande.re matches: {yande_ids}")
                        return yande_ids
        except Exception as e:
            logger.debug(f"IQDB search error: {e}")
        return []

    async def search_saucenao(self, img_bytes: bytes) -> List[int]:
        """Search SauceNAO if API key available and parse booru / yande links."""
        if not self.saucenao_key:
            return []
        try:
            api_url = "https://saucenao.com/search.php"
            params = {
                "db": "999",
                "output_type": "2",
                "testmode": "1",
                "numres": "6",
                "api_key": self.saucenao_key
            }
            files = {"file": ("query.jpg", img_bytes, "image/jpeg")}
            async with httpx.AsyncClient(headers=self.headers, timeout=20.0) as client:
                resp = await client.post(api_url, params=params, files=files)
                if resp.status_code == 200:
                    data = resp.json()
                    yande_ids = []
                    for item in data.get("results", []):
                        sim = float(item.get("header", {}).get("similarity", 0))
                        if sim < 60.0:
                            continue
                        ext_urls = item.get("data", {}).get("ext_urls", [])
                        for u in ext_urls:
                            m = re.search(r"yande\.re/post/show/(\d+)", u)
                            if m:
                                yande_ids.append(int(m.group(1)))
                    if yande_ids:
                        logger.info(f"SauceNAO found yande.re matches: {yande_ids}")
                        return yande_ids
        except Exception as e:
            logger.debug(f"SauceNAO search error: {e}")
        return []

    async def find_post_from_image(self, img_bytes: bytes) -> Optional[YandePost]:
        """
        Orchestrate multi-step search to identify yande.re post from compressed image:
        1. yande.re /post/similar (most direct & accurate)
        2. IQDB (anime booru index)
        3. SauceNAO (author & multi-engine index)
        """
        # 1. Primary: yande.re official similar
        similar_ids = await self.search_yande_similar(img_bytes)
        for pid in similar_ids[:3]:
            post = await self.get_post_by_id(pid)
            if post:
                return post

        # 2. Secondary: IQDB
        iqdb_ids = await self.search_iqdb(img_bytes)
        for pid in iqdb_ids[:3]:
            post = await self.get_post_by_id(pid)
            if post:
                return post

        # 3. Tertiary: SauceNAO
        saucenao_ids = await self.search_saucenao(img_bytes)
        for pid in saucenao_ids[:3]:
            post = await self.get_post_by_id(pid)
            if post:
                return post

        return None

    async def download_image(self, post: YandePost, target_dir: Path) -> Path:
        """Stream download the full resolution original illustration to disk."""
        target_dir.mkdir(parents=True, exist_ok=True)
        filename = f"yande.re_{post.id}_{post.width}x{post.height}.{post.file_ext}"
        dest_path = target_dir / filename

        # Return cached file if already downloaded and complete
        if dest_path.is_file() and dest_path.stat().st_size > 0:
            if post.file_size == 0 or dest_path.stat().st_size >= post.file_size * 0.95:
                return dest_path

        download_url = post.file_url or post.jpeg_url
        logger.info(f"Downloading Yande #{post.id} original from {download_url}...")

        async with httpx.AsyncClient(headers=self.headers, timeout=60.0, follow_redirects=True) as client:
            async with client.stream("GET", download_url) as response:
                response.raise_for_status()
                with open(dest_path, "wb") as f:
                    async for chunk in response.aiter_bytes(chunk_size=65536):
                        f.write(chunk)

        logger.info(f"Successfully downloaded Yande #{post.id} ({dest_path.stat().st_size / (1024*1024):.2f} MB)")
        return dest_path


# Global singleton instance
yande_finder = YandeFinder()
