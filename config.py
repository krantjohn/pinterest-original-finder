"""
Configuration settings for Pinterest Original Finder.
"""
from dataclasses import dataclass, field
import os
from pathlib import Path
from typing import List

# Base directory
BASE_DIR = Path(__file__).resolve().parent

# Auto-load local .env file if present
def _load_env_file():
    env_path = BASE_DIR / ".env"
    if env_path.is_file():
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        os.environ.setdefault(k.strip(), v.strip().strip("'\""))
        except Exception:
            pass

_load_env_file()

# Credible domains sorted by priority / trustworthiness
HIGH_CREDIBILITY_DOMAINS: List[str] = [
    # Primary Art Platforms
    "pixiv.net",
    "artstation.com",
    "x.com",
    "twitter.com",
    "deviantart.com",
    "behance.net",
    "cara.app",
    "bilibili.com",
    "weibo.com",
    "lofter.com",
    # Boorus (contain high-res source metadata)
    "danbooru.donmai.us",
    "safebooru.org",
    "gelbooru.com",
    "yande.re",
    "konachan.com",
    # Official / Photo
    "flickr.com",
    "unsplash.com",
    "500px.com",
    "instagram.com",
    "tumblr.com",
]

@dataclass
class Settings:
    # Project Paths
    base_dir: Path = BASE_DIR
    download_dir: Path = BASE_DIR / "downloads"
    output_dir: Path = BASE_DIR / "output"
    cookies_path: Path = BASE_DIR / "cookies.txt"

    # Network & Crawler Behavior
    request_timeout: float = 20.0
    download_timeout: float = 45.0
    rate_limit_delay_min: float = 0.5
    rate_limit_delay_max: float = 1.5
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )

    # Reverse Search API keys (optional)
    saucenao_api_key: str = os.getenv("SAUCENAO_API_KEY", "")

    # Telegram Bot
    telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    # Max file size in bytes for Telegram Bot upload directly (50MB by default)
    telegram_max_filesize_mb: int = 48

    # Limits
    max_pins_per_board: int = 3000

    # Storage retention & cleanup
    auto_cleanup_raw_downloads: bool = True  # Clean up raw downloaded images after packaging into ZIP
    zip_retention_hours: int = 24           # Retain generated ZIP archives for 24h, then auto-delete
    max_output_storage_mb: int = 5000       # 5GB safety limit for output directory

settings = Settings()


# Ensure working directories exist
settings.download_dir.mkdir(parents=True, exist_ok=True)
settings.output_dir.mkdir(parents=True, exist_ok=True)
