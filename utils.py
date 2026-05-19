from __future__ import annotations

import os
import re
import urllib.parse
from pathlib import Path
from typing import Optional
from urllib.request import Request, urlopen

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._()\- ]+")


def sanitize_filename(name: str, fallback: str = "download") -> str:
    name = os.path.basename(name).strip().replace("\x00", "")
    name = _SAFE_NAME.sub("_", name)
    name = name.strip(". ")
    return name[:180] or fallback


def validate_http_url(url: str) -> bool:
    try:
        parsed = urllib.parse.urlparse(url.strip())
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
    except Exception:
        return False


def safe_join(root: Path, *parts: str) -> Path:
    root = root.resolve()
    path = root.joinpath(*parts).resolve()
    if path != root and root not in path.parents:
        raise ValueError("Path escapes allowed folder")
    return path


def human_size(size: Optional[int]) -> str:
    if size is None:
        return "unknown"
    value = float(size)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def format_eta(seconds: Optional[float]) -> str:
    if seconds is None:
        return "unknown"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


def head_url(url: str, timeout: int = 10) -> tuple[Optional[str], Optional[int], Optional[str]]:
    req = Request(url, method="HEAD", headers={"User-Agent": "telegram-drive-bot/1.0"})
    with urlopen(req, timeout=timeout) as resp:
        filename = None
        cd = resp.headers.get("Content-Disposition", "")
        match = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)', cd)
        if match:
            filename = urllib.parse.unquote(match.group(1))
        if not filename:
            path_name = urllib.parse.urlparse(resp.url).path.rsplit("/", 1)[-1]
            filename = urllib.parse.unquote(path_name) if path_name else "download"
        size = int(resp.headers["Content-Length"]) if resp.headers.get("Content-Length") else None
        ctype = resp.headers.get("Content-Type")
        return sanitize_filename(filename), size, ctype


def is_probably_direct_url(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    ext = Path(parsed.path).suffix.lower()
    direct_exts = {
        ".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz", ".pdf",
        ".mp4", ".mkv", ".mov", ".mp3", ".flac", ".wav", ".iso",
        ".apk", ".exe", ".dmg", ".pkg", ".jpg", ".png", ".webp",
    }
    return ext in direct_exts
