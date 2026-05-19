from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()


def _required(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _csv_ints(value: str) -> set[int]:
    return {int(x.strip()) for x in value.split(",") if x.strip()}


@dataclass(frozen=True)
class Config:
    api_id: int
    api_hash: str
    bot_token: str
    admin_ids: set[int]
    db_path: Path
    local_download_root: Path
    drive_root: Path
    max_concurrent_downloads: int
    max_file_size_mb: int
    progress_update_seconds: int = 5

    @property
    def max_file_size_bytes(self) -> int:
        return self.max_file_size_mb * 1024 * 1024


config = Config(
    api_id=int(_required("API_ID")),
    api_hash=_required("API_HASH"),
    bot_token=_required("BOT_TOKEN"),
    admin_ids=_csv_ints(_required("ADMIN_IDS")),
    db_path=Path(os.getenv("DB_PATH", "bot.sqlite3")).resolve(),
    local_download_root=Path(os.getenv("LOCAL_DOWNLOAD_ROOT", "/downloads")).resolve(),
    drive_root=Path(os.getenv("DRIVE_ROOT", "/mnt/gdrive")).resolve(),
    max_concurrent_downloads=int(os.getenv("MAX_CONCURRENT_DOWNLOADS", "1")),
    max_file_size_mb=int(os.getenv("MAX_FILE_SIZE_MB", "2048")),
)
