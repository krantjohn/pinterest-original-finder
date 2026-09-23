"""
Packager and report generation module.
Generates report.json, report.csv (with UTF-8 BOM for Excel),
and archives all images and metadata into a standalone ZIP file.
Supports automatic multi-part ZIP splitting if archive exceeds Telegram limits.
"""
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
import csv
import json
import zipfile

from config import settings
from core.downloader import DownloadResult
from utils.logger import logger

@dataclass
class PackageResult:
    """Result of packaging operation."""
    zip_path: Path
    all_zip_parts: List[Path]
    total_pins: int
    success_count: int
    higher_res_count: int
    total_size_bytes: int
    report_json_path: Path
    report_csv_path: Path

    @property
    def total_size_mb(self) -> float:
        return round(self.total_size_bytes / (1024 * 1024), 2)


class Packager:
    """Packages downloaded images and reports into ZIP archive."""

    def __init__(self, output_dir: Optional[Path] = None):
        self.output_dir = output_dir or settings.output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def generate_reports(
        self,
        results: List[DownloadResult],
        report_dir: Path
    ) -> Tuple[Path, Path]:
        """Generate report.json and report.csv in the specified directory."""
        report_dir.mkdir(parents=True, exist_ok=True)
        json_path = report_dir / "report.json"
        csv_path = report_dir / "report.csv"

        # 1. Summary calculations
        total = len(results)
        success = sum(1 for r in results if r.status != "failed" and r.final_filepath)
        higher_res = sum(1 for r in results if r.is_higher_res)
        from_source = sum(1 for r in results if r.source_channel == "source_outbound")
        from_reverse = sum(1 for r in results if r.source_channel == "reverse_search")
        from_fallback = sum(1 for r in results if r.source_channel == "pinterest_fallback")
        failed = sum(1 for r in results if r.status == "failed")
        total_bytes = sum(r.file_size_bytes for r in results)

        summary = {
            "total_pins": total,
            "successful_downloads": success,
            "higher_resolution_found": higher_res,
            "higher_resolution_rate_pct": round((higher_res / total * 100), 1) if total else 0,
            "breakdown": {
                "from_outbound_source": from_source,
                "from_reverse_search": from_reverse,
                "pinterest_original_fallback": from_fallback,
                "failed": failed
            },
            "total_size_bytes": total_bytes,
            "total_size_mb": round(total_bytes / (1024 * 1024), 2),
            "generated_at": datetime.now().isoformat()
        }

        # 2. Write report.json
        report_data = {
            "summary": summary,
            "items": [r.to_dict() for r in results]
        }
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(report_data, f, ensure_ascii=False, indent=2)

        # 3. Write report.csv (with utf-8-sig BOM for Excel compatibility)
        fieldnames = [
            "Pin ID", "标题", "Pin 链接", "最终文件名", "获取渠道",
            "状态说明", "Pinterest 分辨率", "最终分辨率", "分辨率提升 (%)",
            "文件大小 (KB)", "最终原图来源", "原始 Outbound 来源"
        ]
        with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(fieldnames)
            for r in results:
                writer.writerow([
                    r.pin_id,
                    r.title,
                    r.pin_url,
                    r.final_filename or "N/A",
                    r.source_channel,
                    r.status_label,
                    r.pinterest_resolution,
                    r.final_resolution,
                    f"+{r.resolution_increase_pct}%" if r.is_higher_res else "0%",
                    round(r.file_size_bytes / 1024, 1),
                    r.final_source_url,
                    r.source_link or ""
                ])

        logger.info(f"Reports generated: {json_path} and {csv_path}")
        return json_path, csv_path

    def package(
        self,
        results: List[DownloadResult],
        board_slug: str = "pinterest_board",
        max_part_size_mb: Optional[int] = None
    ) -> PackageResult:
        """
        Package images and reports into ZIP file.
        If files exceed max_part_size_mb, creates split parts: {slug}_part1.zip, {slug}_part2.zip.
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_slug = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in board_slug)
        base_name = f"{safe_slug}_{timestamp}"

        # Generate reports in a temporary staging folder
        staging_dir = self.output_dir / f".staging_{base_name}"
        json_path, csv_path = self.generate_reports(results, staging_dir)

        # Gather files to include
        files_to_zip: List[Path] = [json_path, csv_path]
        for r in results:
            if r.final_filepath:
                p = Path(r.final_filepath)
                if p.is_file():
                    files_to_zip.append(p)

        limit_bytes = (max_part_size_mb or settings.telegram_max_filesize_mb) * 1024 * 1024
        total_bytes = sum(f.stat().st_size for f in files_to_zip)

        # 1. ALWAYS create the complete un-split master ZIP
        master_zip = self.output_dir / f"{base_name}.zip"
        with zipfile.ZipFile(master_zip, "w", zipfile.ZIP_DEFLATED) as zf:
            for file_path in files_to_zip:
                zf.write(file_path, arcname=file_path.name)
        logger.info(f"Created complete master ZIP: {master_zip} ({total_bytes / 1024 / 1024:.2f} MB)")

        zip_parts: List[Path] = []

        # If fits within single archive, parts is just master zip
        if total_bytes <= limit_bytes or len(files_to_zip) <= 3:
            zip_parts.append(master_zip)
        else:
            # Multi-part ZIP splitting (specifically for Telegram's 50MB per-document limit)
            logger.info(
                f"Total size ({total_bytes / 1024 / 1024:.2f} MB) exceeds Telegram 50MB limit. "
                f"Generating split parts for Telegram document delivery..."
            )
            part_num = 1
            current_part_files: List[Path] = [json_path, csv_path]
            current_part_size = json_path.stat().st_size + csv_path.stat().st_size

            for file_path in files_to_zip[2:]:
                file_size = file_path.stat().st_size
                if current_part_size + file_size > limit_bytes and len(current_part_files) > 2:
                    # Write current part
                    part_zip = self.output_dir / f"{base_name}_part{part_num}.zip"
                    with zipfile.ZipFile(part_zip, "w", zipfile.ZIP_DEFLATED) as zf:
                        for fp in current_part_files:
                            zf.write(fp, arcname=fp.name)
                    zip_parts.append(part_zip)
                    part_num += 1
                    current_part_files = [json_path, csv_path, file_path]
                    current_part_size = json_path.stat().st_size + csv_path.stat().st_size + file_size
                else:
                    current_part_files.append(file_path)
                    current_part_size += file_size

            if current_part_files:
                part_zip = self.output_dir / f"{base_name}_part{part_num}.zip"
                with zipfile.ZipFile(part_zip, "w", zipfile.ZIP_DEFLATED) as zf:
                    for fp in current_part_files:
                        zf.write(fp, arcname=fp.name)
                zip_parts.append(part_zip)
            logger.info(f"Created {len(zip_parts)} split ZIP archives for Telegram.")

        success_count = sum(1 for r in results if r.status != "failed" and r.final_filepath)
        higher_res_count = sum(1 for r in results if r.is_higher_res)

        return PackageResult(
            zip_path=master_zip,  # ALWAYS the full, un-split, complete ZIP archive!
            all_zip_parts=zip_parts,
            total_pins=len(results),
            success_count=success_count,
            higher_res_count=higher_res_count,
            total_size_bytes=total_bytes,
            report_json_path=json_path,
            report_csv_path=csv_path
        )
