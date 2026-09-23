#!/usr/bin/env python3
"""
Pinterest Original Finder
Main entry point for CLI and Telegram Bot execution.
"""
import argparse
import sys
from pathlib import Path
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeRemainingColumn

from config import settings
from core.pipeline import BoardPipeline
from bot.telegram_bot import run_bot
from utils.logger import logger

console = Console()

def run_cli(board_url: str, limit: int = None, output_dir: str = None, cookies: str = None):
    """Execute pipeline in CLI mode."""
    console.print(Panel.fit(
        "[bold cyan]Pinterest Original Finder[/bold cyan]\n"
        "[dim]Automatically discover true author originals, compare resolutions & package into ZIP[/dim]",
        border_style="cyan"
    ))

    cookies_path = Path(cookies) if cookies else settings.cookies_path
    out_dir = Path(output_dir) if output_dir else settings.output_dir

    pipeline = BoardPipeline(output_dir=out_dir, cookies_path=cookies_path)
    processed_results = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeRemainingColumn(),
        console=console
    ) as progress:
        task = progress.add_task("[yellow]Parsing board...", total=100)

        def on_progress(current: int, total: int, result):
            if task is not None:
                progress.update(
                    task,
                    total=total,
                    completed=current,
                    description=f"[green]Processing pin {current}/{total}..."
                )
            if result:
                processed_results.append(result)

        pkg_result = pipeline.run(board_url, limit=limit, on_progress=on_progress)

    if pkg_result.total_pins == 0:
        console.print("[red]❌ No pins were found. Please check the URL or provide cookies for private boards.[/red]")
        sys.exit(1)

    # Render summary table
    table = Table(title="Pin Processing Summary", show_header=True, header_style="bold magenta")
    table.add_column("Pin ID", style="dim", width=16)
    table.add_column("Title", width=25)
    table.add_column("Status / Source", width=22)
    table.add_column("Pinterest Res", width=14)
    table.add_column("Final Res", width=14)
    table.add_column("Higher?", width=10)
    table.add_column("Size", justify="right", width=10)

    for r in processed_results:
        higher_tag = f"[bold green]+{r.resolution_increase_pct}%[/bold green]" if r.is_higher_res else "[dim]Fallback[/dim]"
        size_str = f"{r.file_size_bytes / 1024:.1f} KB" if r.file_size_bytes else "0 KB"
        table.add_row(
            r.pin_id,
            (r.title[:22] + "...") if len(r.title) > 25 else (r.title or "-"),
            r.source_channel,
            r.pinterest_resolution,
            r.final_resolution,
            higher_tag,
            size_str
        )

    console.print(table)

    summary_panel = Panel(
        f"[bold]Total Pins:[/bold] {pkg_result.total_pins}\n"
        f"[bold]Higher-Res Found:[/bold] [green]{pkg_result.higher_res_count}[/green]\n"
        f"[bold]Total Archive Size:[/bold] {pkg_result.total_size_mb} MB\n"
        f"[bold]ZIP Location:[/bold] [underline cyan]{pkg_result.zip_path.resolve()}[/underline cyan]\n"
        f"[bold]Report CSV:[/bold] {pkg_result.report_csv_path.resolve()}\n"
        f"[bold]Report JSON:[/bold] {pkg_result.report_json_path.resolve()}",
        title="[bold green]Packaging Complete[/bold green]",
        border_style="green"
    )
    console.print(summary_panel)


def main():
    parser = argparse.ArgumentParser(
        description="Pinterest Original Finder - Parse boards, find author full-res originals, and export to ZIP."
    )
    parser.add_argument("url", nargs="?", help="Pinterest board URL, pin URL, or pin.it short link")
    parser.add_argument("-l", "--limit", type=int, default=None, help="Limit number of pins to process")
    parser.add_argument("-o", "--output", type=str, default=None, help="Output directory for ZIP and reports")
    parser.add_argument("-c", "--cookies", type=str, default=None, help="Path to cookies.txt for private boards")
    parser.add_argument("--bot", action="store_true", help="Launch Telegram Bot mode")
    parser.add_argument("--token", type=str, default=None, help="Telegram Bot Token (or set TELEGRAM_BOT_TOKEN env)")

    args = parser.parse_args()

    if args.bot:
        run_bot(token=args.token)
    elif args.url:
        run_cli(
            board_url=args.url,
            limit=args.limit,
            output_dir=args.output,
            cookies=args.cookies
        )
    else:
        parser.print_help()
        sys.exit(0)


if __name__ == "__main__":
    main()
