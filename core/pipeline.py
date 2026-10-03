"""
Orchestration pipeline that brings together parser, downloader, and packager.
Reusable by both CLI and Telegram Bot.
"""
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Callable, List
import urllib.parse
import threading

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
        import threading
        self.output_dir = output_dir or settings.output_dir
        self.download_dir = settings.download_dir
        self.parser = BoardParser(cookies_path=cookies_path)
        self.downloader = ImageDownloader(output_dir=self.download_dir)
        self.packager = Packager(output_dir=self.output_dir)
        self.cancel_event = threading.Event()

    def cancel(self):
        """Signal pipeline to cancel immediately."""
        self.cancel_event.set()
        logger.info("BoardPipeline received cancellation signal.")

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
        on_progress: Optional[ProgressCallback] = None,
        cancel_event: Optional[threading.Event] = None
    ) -> PackageResult:
        """
        Run the complete pipeline:
        1. Parse board items
        2. Process each pin (outbound link -> reverse search -> originals fallback)
        3. Package images and generate reports into ZIP
        """
        if cancel_event:
            self.cancel_event = cancel_event

        board_slug = self.extract_slug(board_url)
        logger.info(f"Pipeline started for: {board_url} (Slug: {board_slug})")

        # Step 1: Parse pins
        pins = self.parser.parse(board_url, limit=limit)
        total_pins = len(pins)
        logger.info(f"Pipeline: {total_pins} pins retrieved to process.")

        if self.cancel_event.is_set():
            logger.info("Pipeline was cancelled before download started.")
            return PackageResult(
                zip_path=Path(""),
                all_zip_parts=[],
                total_pins=total_pins,
                success_count=0,
                higher_res_count=0,
                total_size_mb=0.0
            )

        # Determine board slug (prefer actual board name from PinItem)
        if pins and pins[0].board_name:
            board_slug = pins[0].board_name
            logger.info(f"Using board name for packaging and files: '{board_slug}'")

        results: List[DownloadResult] = []
        if total_pins == 0:
            logger.warning("No pins found in the specified board/URL.")
            return self.packager.package([], board_slug=board_slug)

        # Step 2: Download and select highest resolution (Concurrent Worker Pool)
        from concurrent.futures import ThreadPoolExecutor

        concurrency = 6 if total_pins >= 20 else (4 if total_pins >= 4 else 1)
        logger.info(f"Processing {total_pins} pins with {concurrency} parallel workers...")

        completed_count = 0
        lock = threading.Lock()

        def _worker(pin: PinItem) -> Optional[DownloadResult]:
            if self.cancel_event.is_set():
                return None
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
            if self.cancel_event.is_set():
                return None
            with lock:
                completed_count += 1
                current_idx = completed_count
            if on_progress and not self.cancel_event.is_set():
                try:
                    on_progress(current_idx, total_pins, res)
                except Exception as cb_err:
                    logger.debug(f"Progress callback error: {cb_err}")
            return res

        if concurrency > 1:
            with ThreadPoolExecutor(max_workers=concurrency) as executor:
                futures = []
                for p in pins:
                    if self.cancel_event.is_set():
                        break
                    futures.append(executor.submit(_worker, p))
                import time
                for f in futures:
                    if self.cancel_event.is_set():
                        for pending in futures:
                            pending.cancel()
                        break
                    try:
                        while not f.done():
                            if self.cancel_event.is_set():
                                for pending in futures:
                                    pending.cancel()
                                break
                            time.sleep(0.1)
                        if self.cancel_event.is_set():
                            break
                        r = f.result()
                        if r:
                            results.append(r)
                    except Exception as fe:
                        logger.error(f"Worker task error: {fe}")
        else:
            for p in pins:
                if self.cancel_event.is_set():
                    break
                r = _worker(p)
                if r:
                    results.append(r)

        # Handle cancellation
        if self.cancel_event.is_set():
            logger.info("Pipeline was cancelled by user. Cleaning up raw downloaded files...")
            try:
                from utils.cleanup import storage_manager
                storage_manager.cleanup_staging_dirs()
                download_fps = [r.final_filepath for r in results if r and r.final_filepath]
                storage_manager.cleanup_raw_downloads(download_fps)
            except Exception as cl_err:
                logger.warning(f"Post-cancel cleanup error: {cl_err}")
            return PackageResult(
                zip_path=Path(""),
                all_zip_parts=[],
                total_pins=total_pins,
                success_count=len([r for r in results if r and r.status == "success"]),
                higher_res_count=0,
                total_size_mb=0.0
            )

        # Step 3: Package into ZIP and generate reports
        pkg_result = self.packager.package(results, board_slug=board_slug)
        logger.info(
            f"Pipeline finished! Generated {len(pkg_result.all_zip_parts)} ZIP(s). "
            f"Total size: {pkg_result.total_size_mb} MB"
        )

        # Step 4: Automatic disk space management (auto-cleanup raw uncompressed files)
        try:
            from utils.cleanup import storage_manager
            storage_manager.cleanup_staging_dirs()
            if settings.auto_cleanup_raw_downloads:
                download_fps = [r.final_filepath for r in results if r.final_filepath]
                storage_manager.cleanup_raw_downloads(download_fps)
            storage_manager.enforce_storage_quota()
        except Exception as cl_err:
            logger.warning(f"Post-pipeline cleanup error: {cl_err}")

        return pkg_result

