from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(slots=True)
class User:
    telegram_id: int
    username: str
    gmail: str
    drive_folder_path: str
    created_at: str


@dataclass(slots=True)
class Task:
    id: int
    user_id: int
    type: str
    source: str
    status: str
    selected_format: Optional[str]
    filename: Optional[str]
    local_path: Optional[str]
    drive_path: Optional[str]
    created_at: str
    started_at: Optional[str]
    finished_at: Optional[str]
    error: Optional[str]
