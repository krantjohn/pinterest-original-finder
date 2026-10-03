"""
Helper utilities for handling and decrypting cookies.
Supports:
- Netscape format (.txt)
- Standard JSON cookie export (Cookie-Editor by Moustachauve)
- Encrypted JSON cookie export (Cookie-Editor by hotcleaner.com)
"""
import json
import base64
from pathlib import Path
from typing import List, Dict, Optional, Tuple

from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from config import settings
from utils.logger import logger


def json_cookies_to_netscape(cookies: List[Dict]) -> str:
    """Convert a list of Chrome/Firefox cookie dicts to Netscape HTTP Cookie File format."""
    lines = ["# Netscape HTTP Cookie File\n"]
    for c in cookies:
        if not isinstance(c, dict):
            continue
        domain = c.get("domain", "")
        if not domain:
            continue
        include_sub = "TRUE" if domain.startswith(".") else "FALSE"
        path = c.get("path", "/")
        secure = "TRUE" if c.get("secure", False) else "FALSE"
        exp = int(c.get("expirationDate") or c.get("expires") or 2147483647)
        name = str(c.get("name", "")).strip()
        val = str(c.get("value", "")).strip()
        if name:
            lines.append(f"{domain}\t{include_sub}\t{path}\t{secure}\t{exp}\t{name}\t{val}\n")
    return "".join(lines)


def decrypt_hotcleaner_data(data_b64: str, password: str) -> Optional[List[Dict]]:
    """Decrypt hotcleaner Cookie-Editor encrypted base64 payload."""
    try:
        raw_bytes = base64.b64decode(data_b64)
        if len(raw_bytes) <= 12:
            return None
        iv = raw_bytes[:12]
        ciphertext = raw_bytes[12:]

        pwd_bytes = password.encode("utf-8")
        salt_bytes = (password + password).encode("utf-8")
        kdf = PBKDF2HMAC(hashes.SHA256(), 32, salt_bytes, 1024)
        key = kdf.derive(pwd_bytes)

        aesgcm = AESGCM(key)
        decrypted_bytes = aesgcm.decrypt(iv, ciphertext, None)
        decrypted_text = decrypted_bytes.decode("utf-8")

        cookies_obj = json.loads(decrypted_text)
        if isinstance(cookies_obj, list):
            return cookies_obj
        return None
    except Exception as e:
        logger.debug(f"Failed to decrypt hotcleaner data with password: {e}")
        return None


def process_uploaded_cookie_file(file_path: Path, password: Optional[str] = None) -> Tuple[bool, str]:
    """
    Process an uploaded cookie file (txt or json).
    Returns (success: bool, message: str).
    """
    try:
        content = file_path.read_text(encoding="utf-8", errors="ignore").strip()
    except Exception as e:
        return False, f"无法读取文件: {e}"

    # 1. Check if Netscape format text
    if content.startswith("# Netscape") or "\tTRUE\t" in content or "\tFALSE\t" in content:
        settings.cookies_path.parent.mkdir(parents=True, exist_ok=True)
        settings.cookies_path.write_text(content, encoding="utf-8")
        from gallery_dl import config
        config.set(("extractor", "pinterest"), "cookies", str(settings.cookies_path))
        return True, "已成功导入 Netscape 格式 Cookies 文件！"

    # 2. Check if JSON format
    try:
        parsed = json.loads(content)
    except Exception:
        # Check if raw cookie string (key=value; ...)
        if "=" in content:
            lines = ["# Netscape HTTP Cookie File\n"]
            for item in content.split(";"):
                item = item.strip()
                if "=" in item:
                    k, v = item.split("=", 1)
                    lines.append(f".pinterest.com\tTRUE\t/\tTRUE\t2147483647\t{k.strip()}\t{v.strip()}\n")
            netscape_str = "".join(lines)
            settings.cookies_path.parent.mkdir(parents=True, exist_ok=True)
            settings.cookies_path.write_text(netscape_str, encoding="utf-8")
            from gallery_dl import config
            config.set(("extractor", "pinterest"), "cookies", str(settings.cookies_path))
            return True, "已成功导入键值对格式 Cookies！"
        return False, "无法解析的 Cookies 格式。"

    # Case A: Standard unencrypted cookie array
    if isinstance(parsed, list):
        netscape_str = json_cookies_to_netscape(parsed)
        settings.cookies_path.parent.mkdir(parents=True, exist_ok=True)
        settings.cookies_path.write_text(netscape_str, encoding="utf-8")
        from gallery_dl import config
        config.set(("extractor", "pinterest"), "cookies", str(settings.cookies_path))
        return True, f"已成功解析并导入 {len(parsed)} 条 JSON Cookies！"

    # Case B: Hotcleaner encrypted format
    if isinstance(parsed, dict) and parsed.get("version") == 2 and "data" in parsed:
        data_b64 = parsed["data"]
        if not password:
            return False, "ENCRYPTED_PASSWORD_REQUIRED"

        cookies_list = decrypt_hotcleaner_data(data_b64, password)
        if cookies_list is None:
            return False, "PASSWORD_INCORRECT"

        netscape_str = json_cookies_to_netscape(cookies_list)
        settings.cookies_path.parent.mkdir(parents=True, exist_ok=True)
        settings.cookies_path.write_text(netscape_str, encoding="utf-8")
        from gallery_dl import config
        config.set(("extractor", "pinterest"), "cookies", str(settings.cookies_path))
        return True, f"解密成功！已导入 {len(cookies_list)} 条 Pinterest 凭证！"

    return False, "未知的 JSON Cookies 格式。"
