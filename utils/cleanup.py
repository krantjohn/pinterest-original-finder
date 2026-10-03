"""
Storage management and automatic cleanup utility.
Prevents server disk exhaustion by:
- Cleaning up uncompressed raw images after packaging into ZIP
- Expiring old ZIP archives based on TTL (24h retention)
- Enforcing maximum storage quota (LRU deletion)
- Periodic background cleanup task
"""
import os
import shutil
import time
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

from config import settings
from utils.logger import logger


class StorageManager:
    """Manages disk space, auto-cleans old downloads and expires ZIP archives."""

    def __init__(
        self,
        download_dir: Optional[Path] = None,
        output_dir: Optional[Path] = None,
        retention_hours: Optional[int] = None,
        max_storage_mb: Optional[int] = None
    ):
        self.download_dir = download_dir or settings.download_dir
        self.output_dir = output_dir or settings.output_dir
        self.retention_hours = retention_hours or settings.zip_retention_hours
        self.max_storage_mb = max_storage_mb or settings.max_output_storage_mb

    def cleanup_raw_downloads(self, filepaths: Optional[List[str]] = None) -> Tuple[int, float]:
        """
        Delete raw uncompressed images that have already been packaged into the ZIP archive.
        If filepaths is provided, deletes those files.
        Otherwise deletes files in download_dir older than 2 hours.
        Returns: (deleted_count, freed_mb)
        """
        count = 0
        bytes_freed = 0

        if filepaths:
            for fp in filepaths:
                try:
                    p = Path(fp)
                    if p.is_file() and p.parent.resolve() == self.download_dir.resolve():
                        sz = p.stat().st_size
                        p.unlink()
                        count += 1
                        bytes_freed += sz
                except Exception as e:
                    logger.debug(f"Failed to delete download file {fp}: {e}")
        else:
            now = time.time()
            max_age = 2 * 3600  # 2 hours
            try:
                for p in self.download_dir.glob("*.*"):
                    if p.is_file():
                        try:
                            if now - p.stat().st_mtime > max_age:
                                sz = p.stat().st_size
                                p.unlink()
                                count += 1
                                bytes_freed += sz
                        except Exception:
                            pass
            except Exception as e:
                logger.warning(f"Error scanning download_dir: {e}")

        freed_mb = round(bytes_freed / (1024 * 1024), 2)
        if count > 0:
            logger.info(f"Cleaned up {count} raw download files ({freed_mb} MB freed).")
        return count, freed_mb

    def cleanup_staging_dirs(self) -> int:
        """Delete temporary .staging_* directories in output_dir."""
        count = 0
        try:
            for p in self.output_dir.glob(".staging_*"):
                if p.is_dir():
                    try:
                        shutil.rmtree(p)
                        count += 1
                    except Exception as e:
                        logger.debug(f"Failed to remove staging dir {p}: {e}")
        except Exception as e:
            logger.warning(f"Error scanning staging dirs: {e}")
        return count

    def cleanup_expired_archives(self, max_age_hours: Optional[int] = None) -> Tuple[int, float]:
        """
        Delete ZIP archives older than retention_hours (default 24h).
        Returns: (deleted_count, freed_mb)
        """
        hours = max_age_hours if max_age_hours is not None else self.retention_hours
        max_age_seconds = hours * 3600
        now = time.time()
        count = 0
        bytes_freed = 0

        try:
            for p in self.output_dir.glob("*.zip"):
                if p.is_file():
                    try:
                        age = now - p.stat().st_mtime
                        if age > max_age_seconds:
                            sz = p.stat().st_size
                            p.unlink()
                            count += 1
                            bytes_freed += sz
                            logger.info(f"Deleted expired ZIP archive: {p.name} (age: {age/3600:.1f}h)")
                    except Exception as e:
                        logger.debug(f"Failed to delete expired ZIP {p}: {e}")
        except Exception as e:
            logger.warning(f"Error cleaning expired archives: {e}")

        freed_mb = round(bytes_freed / (1024 * 1024), 2)
        return count, freed_mb

    def enforce_storage_quota(self) -> Tuple[int, float]:
        """
        Enforce max_storage_mb. If output_dir exceeds limit,
        deletes oldest ZIP archives first until usage drops below 75% of limit.
        """
        limit_bytes = self.max_storage_mb * 1024 * 1024
        target_bytes = int(limit_bytes * 0.75)
        count = 0
        bytes_freed = 0

        try:
            zip_files = []
            total_bytes = 0
            for p in self.output_dir.glob("*.zip"):
                if p.is_file():
                    try:
                        sz = p.stat().st_size
                        mtime = p.stat().st_mtime
                        zip_files.append((mtime, sz, p))
                        total_bytes += sz
                    except Exception:
                        pass

            if total_bytes > limit_bytes:
                logger.warning(
                    f"Output directory size ({total_bytes / 1024 / 1024:.1f} MB) exceeds "
                    f"quota ({self.max_storage_mb} MB). Purging oldest archives..."
                )
                # Sort by mtime ascending (oldest first)
                zip_files.sort(key=lambda x: x[0])
                for mtime, sz, p in zip_files:
                    if total_bytes <= target_bytes:
                        break
                    try:
                        p.unlink()
                        total_bytes -= sz
                        bytes_freed += sz
                        count += 1
                        logger.info(f"Purged old archive to enforce quota: {p.name} ({sz / 1024 / 1024:.1f} MB)")
                    except Exception:
                        pass
        except Exception as e:
            logger.warning(f"Error enforcing storage quota: {e}")

        freed_mb = round(bytes_freed / (1024 * 1024), 2)
        return count, freed_mb

    def run_full_cleanup(self) -> Dict[str, Any]:
        """Run complete sweep: staging dirs, raw downloads, expired ZIPs, and quota enforcement."""
        staging_count = self.cleanup_staging_dirs()
        raw_count, raw_freed = self.cleanup_raw_downloads()
        expired_count, expired_freed = self.cleanup_expired_archives()
        quota_count, quota_freed = self.enforce_storage_quota()

        total_freed_mb = round(raw_freed + expired_freed + quota_freed, 2)
        stats = self.get_storage_stats()

        return {
            "staging_deleted": staging_count,
            "raw_downloads_deleted": raw_count,
            "archives_deleted": expired_count + quota_count,
            "freed_mb": total_freed_mb,
            **stats
        }

    def get_storage_stats(self) -> Dict[str, Any]:
        """Return disk and application storage statistics."""
        def dir_size(d: Path) -> int:
            total = 0
            if d.is_dir():
                for entry in d.rglob("*"):
                    if entry.is_file():
                        try:
                            total += entry.stat().st_size
                        except Exception:
                            pass
            return total

        downloads_bytes = dir_size(self.download_dir)
        output_bytes = dir_size(self.output_dir)
        zip_count = len(list(self.output_dir.glob("*.zip")))

        # System disk free space
        try:
            total_b, used_b, free_b = shutil.disk_usage(self.output_dir)
            disk_total_gb = round(total_b / (1024 ** 3), 1)
            disk_free_gb = round(free_b / (1024 ** 3), 1)
            disk_used_pct = round((used_b / total_b) * 100, 1)
        except Exception:
            disk_total_gb = 0.0
            disk_free_gb = 0.0
            disk_used_pct = 0.0

        return {
            "downloads_size_mb": round(downloads_bytes / (1024 * 1024), 2),
            "output_size_mb": round(output_bytes / (1024 * 1024), 2),
            "zip_count": zip_count,
            "disk_free_gb": disk_free_gb,
            "disk_total_gb": disk_total_gb,
            "disk_used_pct": disk_used_pct,
            "retention_hours": self.retention_hours
        }


storage_manager = StorageManager()
