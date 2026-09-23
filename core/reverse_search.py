"""
Reverse image search module.
Supports SauceNAO (especially for anime/illustrations/digital art) and Yandex Reverse Image Search,
with domain credibility ranking (Pixiv, Twitter/X, ArtStation, artist portfolios > repost aggregators).
"""
import urllib.parse
import re
from typing import List, Optional
import httpx
from bs4 import BeautifulSoup

import threading
import time

from config import settings, HIGH_CREDIBILITY_DOMAINS
from core.source_finder import CandidateImage
from utils.logger import logger

_search_lock = threading.Lock()
_last_search_timestamp = 0.0

class ReverseSearcher:
    """Reverse image search engine using Yandex and SauceNAO."""

    def __init__(self, saucenao_api_key: Optional[str] = None):
        self.saucenao_key = saucenao_api_key or settings.saucenao_api_key
        self.headers = {
            "User-Agent": settings.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }

    def search(self, image_url: str) -> List[CandidateImage]:
        """Run reverse search across available engines and return ranked image candidates."""
        global _last_search_timestamp
        # Thread-safe rate limiter to avoid 429 flood blocks from search engines
        with _search_lock:
            now = time.time()
            elapsed = now - _last_search_timestamp
            if elapsed < 1.5:
                time.sleep(1.5 - elapsed)
            _last_search_timestamp = time.time()

            logger.info(f"Running reverse image search for image: {image_url}")
            candidates: List[CandidateImage] = []

            # 1. SauceNAO search (prioritized for ACG/Illustrations)
            try:
                saucenao_results = self._search_saucenao(image_url)
                if saucenao_results:
                    candidates.extend(saucenao_results)
            except Exception as e:
                logger.debug(f"SauceNAO search error: {e}")

            # 2. Yandex Reverse Image Search (great general web reverse search)
            try:
                yandex_results = self._search_yandex(image_url)
                if yandex_results:
                    candidates.extend(yandex_results)
            except Exception as e:
                logger.debug(f"Yandex reverse search error: {e}")

            # Rank candidates by domain credibility and resolution hints
            ranked = self._rank_candidates(candidates)
            logger.info(f"Reverse search yielded {len(ranked)} ranked candidates.")
            return ranked

    def _search_saucenao(self, image_url: str) -> List[CandidateImage]:
        """Query SauceNAO API."""
        params = {
            "db": "999",
            "output_type": "2",
            "testmode": "1",
            "numres": "8",
            "url": image_url
        }
        if self.saucenao_key:
            params["api_key"] = self.saucenao_key

        api_url = f"https://saucenao.com/search.php?{urllib.parse.urlencode(params)}"
        candidates: List[CandidateImage] = []

        with httpx.Client(timeout=settings.request_timeout, headers=self.headers) as client:
            resp = client.get(api_url)
            if resp.status_code != 200:
                return candidates

            data = resp.json()
            results = data.get("results", [])

            for item in results:
                header = item.get("header", {})
                similarity = float(header.get("similarity", 0))
                if similarity < 70.0:
                    continue  # Filter low similarity results

                item_data = item.get("data", {})
                ext_urls = item_data.get("ext_urls", [])

                for ext_url in ext_urls:
                    parsed = urllib.parse.urlparse(ext_url)
                    domain = parsed.netloc.lower()

                    # SauceNAO links directly to Pixiv, Twitter, Danbooru, etc.
                    score = self._calculate_domain_credibility(domain)
                    candidates.append(CandidateImage(
                        url=ext_url,
                        source_type="saucenao_result",
                        source_url=ext_url,
                        expected_quality_score=int(score * (similarity / 100.0))
                    ))

        return candidates

    def _search_yandex(self, image_url: str) -> List[CandidateImage]:
        """Search Yandex Images for EXACT duplicates in higher resolutions (Other Sizes)."""
        encoded = urllib.parse.quote(image_url, safe="")
        yandex_url = f"https://yandex.com/images/search?rpt=imageview&url={encoded}"

        candidates: List[CandidateImage] = []
        with httpx.Client(
            timeout=settings.request_timeout,
            headers=self.headers,
            follow_redirects=True
        ) as client:
            resp = client.get(yandex_url)
            if resp.status_code != 200:
                return candidates

            soup = BeautifulSoup(resp.text, "html.parser")
            seen_urls = set()

            # 1. ONLY look in 'CbirOtherSizes' (Другие размеры / All sizes of THIS exact image)
            other_sizes_section = soup.find(class_=re.compile(r"CbirOtherSizes", re.I))
            if other_sizes_section:
                for a_tag in other_sizes_section.find_all("a", href=True):
                    href = a_tag["href"].strip()
                    if not href.startswith("http"):
                        continue

                    # If it is a Twitter image, ensure we request the :orig uncompressed version
                    if "pbs.twimg.com/media/" in href:
                        clean_tw = re.sub(r":(?:small|thumb|large|\d+x\d+)$", "", href)
                        orig_tw = clean_tw + ":orig"
                        if orig_tw not in seen_urls:
                            seen_urls.add(orig_tw)
                            candidates.append(CandidateImage(
                                url=orig_tw,
                                source_type="yandex_exact_other_size",
                                source_url=yandex_url,
                                expected_quality_score=98
                            ))

                    if href not in seen_urls:
                        seen_urls.add(href)
                        parsed = urllib.parse.urlparse(href)
                        domain = parsed.netloc.lower()
                        score = self._calculate_domain_credibility(domain)
                        candidates.append(CandidateImage(
                            url=href,
                            source_type="yandex_exact_other_size",
                            source_url=yandex_url,
                            expected_quality_score=score
                        ))

            # 2. Look in 'CbirSitesList' (Sites where this EXACT image appears)
            sites_section = soup.find(class_=re.compile(r"CbirSitesList", re.I))
            if sites_section:
                for a_tag in sites_section.find_all("a", href=True):
                    href = a_tag["href"].strip()
                    if href.startswith("http") and href not in seen_urls:
                        seen_urls.add(href)
                        parsed = urllib.parse.urlparse(href)
                        domain = parsed.netloc.lower()
                        if any(d in domain for d in HIGH_CREDIBILITY_DOMAINS):
                            score = self._calculate_domain_credibility(domain)
                            candidates.append(CandidateImage(
                                url=href,
                                source_type="yandex_site_match",
                                source_url=href,
                                expected_quality_score=score
                            ))

        return candidates

    def _calculate_domain_credibility(self, domain: str) -> int:
        """Calculate credibility score (1-100) based on domain tier."""
        domain = domain.lower()

        # Tier 1: Authoritative art/photo creator platforms
        tier1 = ["pixiv.net", "artstation.com", "x.com", "twitter.com", "deviantart.com", "behance.net", "cara.app"]
        if any(t in domain for t in tier1):
            return 95

        # Tier 2: Dedicated boorus (track artist & high-res files)
        tier2 = ["danbooru.donmai.us", "gelbooru.com", "safebooru.org", "yande.re", "konachan.com"]
        if any(t in domain for t in tier2):
            return 88

        # Tier 3: Photography and direct portfolio / verified sites
        tier3 = ["flickr.com", "unsplash.com", "500px.com", "reddit.com", "redd.it", "tumblr.com"]
        if any(t in domain for t in tier3):
            return 78

        # Tier 4: Pinterest itself
        if "pinimg.com" in domain or "pinterest.com" in domain:
            return 60

        # Tier 5: Random wallpaper / repost sites
        return 40

    def _rank_candidates(self, candidates: List[CandidateImage]) -> List[CandidateImage]:
        """Deduplicate and sort candidate image URLs by quality score descending."""
        seen = set()
        unique = []
        for c in candidates:
            if c.url not in seen:
                seen.add(c.url)
                unique.append(c)

        unique.sort(key=lambda c: c.expected_quality_score, reverse=True)
        return unique
