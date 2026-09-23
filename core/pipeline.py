"""
Orchestration pipeline that brings together parser, downloader, and packager.
Reusable by both CLI and Telegram Bot.
"""
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Callable, List
import urllib.parse

from core.board_parser import BoardParser, PinItem
from core.downloader import ImageDownloader, DownloadResult
from core.packager import Packager, PackageResult
from config import settings
from utils.logger import logger

ProgressCallback = Callable[[int, int, Optional[DownloadResult]], None]

class BoardPipeline:
    """End-to-end processing pipeline for Pinterest boards."""

    def __init__(
        self,
        output_dir: Optional[Path] = None,
        cookies_path: Optional[Path] = None,
        saucenao_api_key: Optional[str] = None
    ):
        self.output_dir = output_dir or settings.output_dir
        self.download_dir = settings.download_dir
        self.parser = BoardParser(cookies_path=cookies_path)
        self.downloader = ImageDownloader(output_dir=self.download_dir)
        self.packager = Packager(output_dir=self.output_dir)

    @staticmethod
    def extract_slug(url: str) -> str:
        """Extract a clean slug from a Pinterest URL."""
        parsed = urllib.parse.urlparse(url)
        parts = [p for p in parsed.path.split("/") if p]
        if len(parts) >= 2:
            return f"{parts[0]}_{parts[1]}"
        elif len(parts) == 1:
            return parts[0]
        return "pinterest_board"

    def run(
        self,
        board_url: str,
        limit: Optional[int] = None,
        on_progress: Optional[ProgressCallback] = None
    ) -> PackageResult:
        """
        Run the complete pipeline:
        1. Parse board items
        2. Process each pin (outbound link -> reverse search -> originals fallback)
        3. Package images and generate reports into ZIP
        """
        board_slug = self.extract_slug(board_url)
        logger.info(f"Pipeline started for: {board_url} (Slug: {board_slug})")

        # Step 1: Parse pins
        pins = self.parser.parse(board_url, limit=limit)
        total_pins = len(pins)
        logger.info(f"Pipeline: {total_pins} pins retrieved to process.")

        results: List[DownloadResult] = []
        if total_pins == 0:
            logger.warning("No pins found in the specified board/URL.")
            return self.packager.package([], board_slug=board_slug)

        # Step 2: Download and select highest resolution (Concurrent Worker Pool)
        import threading
        from concurrent.futures import ThreadPoolExecutor

        concurrency = 6 if total_pins >= 20 else (4 if total_pins >= 4 else 1)
        logger.info(f"Processing {total_pins} pins with {concurrency} parallel workers...")

        completed_count = 0
        lock = threading.Lock()

        def _worker(pin: PinItem) -> DownloadResult:
            nonlocal completed_count
            try:
                res = self.downloader.process_pin(pin)
            except Exception as e:
                logger.error(f"Error processing pin {pin.identifier}: {e}")
                res = DownloadResult(
                    pin_id=pin.pin_id,
                    pin_url=pin.pin_url,
                    title=pin.title,
                    source_link=pin.source_link,
                    pinterest_orig_url=pin.pinterest_orig_url,
                    status="failed",
                    status_label=f"处理异常: {e}"
                )
            with lock:
                completed_count += 1
                current_idx = completed_count
            if on_progress:
                try:
                    on_progress(current_idx, total_pins, res)
                except Exception as cb_err:
                    logger.debug(f"Progress callback error: {cb_err}")
            return res

        if concurrency > 1:
            with ThreadPoolExecutor(max_workers=concurrency) as executor:
                results = list(executor.map(_worker, pins))
        else:
            results = [_worker(p) for p in pins]

        # Step 3: Package into ZIP and generate reports
        pkg_result = self.packager.package(results, board_slug=board_slug)
        logger.info(
            f"Pipeline finished! Generated {len(pkg_result.all_zip_parts)} ZIP(s). "
            f"Total size: {pkg_result.total_size_mb} MB"
        )
        return pkg_result
