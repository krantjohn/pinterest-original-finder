"""
High-speed HTTP download server & Cloudflare Tunnel manager.
Enables instant, direct, high-speed mobile browser downloads for large ZIP archives
(e.g., hundreds or thousands of pins, >50MB - several GBs) without Telegram bot file-size limits.
"""
import os
import re
import shutil
import subprocess
import threading
import time
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from typing import Optional

from config import settings
from utils.logger import logger


class QuietHTTPHandler(SimpleHTTPRequestHandler):
    """HTTP handler with suppressed console spam and Range request support."""

    def log_message(self, format, *args):
        # Suppress routine GET request logs
        pass


class TunnelServer:
    """Manages local HTTP file server and Cloudflare Tunnel."""

    _instance: Optional["TunnelServer"] = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self, port: int = 8765):
        if self._initialized:
            return
        self.port = port
        self.output_dir = Path(settings.output_dir).resolve()
        self.http_server: Optional[HTTPServer] = None
        self.http_thread: Optional[threading.Thread] = None
        self.tunnel_proc: Optional[subprocess.Popen] = None
        self.public_url: Optional[str] = None
        self._running = False
        self._initialized = True

    def start(self):
        """Start local HTTP server and Cloudflare tunnel in background."""
        if self._running:
            return

        self._running = True
        self._start_http_server()
        self._start_tunnel()

    def _start_http_server(self):
        """Launch Python HTTP server bound to output directory."""
        try:
            handler_factory = lambda *args, **kwargs: QuietHTTPHandler(
                *args, directory=str(self.output_dir), **kwargs
            )
            self.http_server = HTTPServer(("127.0.0.1", self.port), handler_factory)
            self.http_thread = threading.Thread(
                target=self.http_server.serve_forever,
                daemon=True,
                name="HTTPServerThread"
            )
            self.http_thread.start()
            logger.info(f"Local file server running at http://127.0.0.1:{self.port} serving {self.output_dir}")
        except Exception as e:
            logger.error(f"Failed to start local HTTP server: {e}")

    def _start_tunnel(self):
        """Launch cloudflared tunnel and parse assigned HTTPS URL."""
        cf_bin = shutil.which("cloudflared") or "/usr/local/bin/cloudflared"
        if not os.path.exists(cf_bin):
            logger.warning("cloudflared binary not found. Direct tunnel links will be unavailable.")
            return

        def _run_tunnel():
            while self._running:
                try:
                    logger.info("Starting Cloudflare Tunnel...")
                    self.tunnel_proc = subprocess.Popen(
                        [cf_bin, "tunnel", "--url", f"http://127.0.0.1:{self.port}"],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        bufsize=1
                    )

                    start_time = time.time()
                    for line in self.tunnel_proc.stdout:
                        if not self._running:
                            break
                        # Find https://xxx.trycloudflare.com
                        match = re.search(r"(https://[a-zA-Z0-9.-]+\.trycloudflare\.com)", line)
                        if match and not self.public_url:
                            self.public_url = match.group(1)
                            logger.info(f"Cloudflare Tunnel active! Public URL: {self.public_url}")

                    self.tunnel_proc.wait()
                except Exception as err:
                    logger.error(f"Tunnel process exception: {err}")

                if self._running:
                    time.sleep(3)  # Restart delay

        t = threading.Thread(target=_run_tunnel, daemon=True, name="CloudflaredThread")
        t.start()

    def get_file_url(self, file_path: Path) -> Optional[str]:
        """Generate public direct download URL for a file in output directory."""
        if not self.public_url:
            return None
        file_path = Path(file_path).resolve()
        try:
            rel = file_path.relative_to(self.output_dir)
            import urllib.parse
            encoded_path = urllib.parse.quote(str(rel))
            return f"{self.public_url}/{encoded_path}"
        except ValueError:
            return None

    def stop(self):
        """Clean shutdown of tunnel and HTTP server."""
        self._running = False
        if self.tunnel_proc:
            try:
                self.tunnel_proc.terminate()
            except Exception:
                pass
        if self.http_server:
            try:
                self.http_server.shutdown()
            except Exception:
                pass


tunnel_server = TunnelServer()
