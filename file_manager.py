from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from config import config
from models import User
from utils import human_size, safe_join, sanitize_filename


@dataclass(slots=True)
class FileEntry:
    name: str
    path: Path
    size: int
    modified: float


def user_local_dir(user: User) -> Path:
    path = safe_join(config.local_download_root, user.gmail)
    path.mkdir(parents=True, exist_ok=True)
    return path


def user_drive_dir(user: User) -> Path:
    path = Path(user.drive_folder_path).resolve()
    root = config.drive_root.resolve()
    if path != root and root not in path.parents:
        raise ValueError("User drive folder is outside DRIVE_ROOT")
    path.mkdir(parents=True, exist_ok=True)
    return path


def make_unique_path(folder: Path, filename: str) -> Path:
    filename = sanitize_filename(filename)
    target = safe_join(folder, filename)
    if not target.exists():
        return target
    stem = target.stem
    suffix = target.suffix
    for i in range(1, 1000):
        candidate = safe_join(folder, f"{stem}_{i}{suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError("Could not create a unique filename")


def move_to_drive(local_path: Path, user: User, preferred_name: str | None = None) -> Path:
    drive_dir = user_drive_dir(user)
    final_name = preferred_name or local_path.name
    dest = make_unique_path(drive_dir, final_name)
    shutil.move(str(local_path), str(dest))
    return dest


def list_files(folder: Path, limit: int = 30) -> list[FileEntry]:
    folder = folder.resolve()
    if not folder.exists():
        return []
    items: list[FileEntry] = []
    for p in folder.iterdir():
        if p.is_file():
            st = p.stat()
            items.append(FileEntry(p.name, p, st.st_size, st.st_mtime))
    return sorted(items, key=lambda x: x.modified, reverse=True)[:limit]


def delete_user_file(user: User, area: str, filename: str) -> None:
    folder = user_drive_dir(user) if area == "drive" else user_local_dir(user)
    target = safe_join(folder, sanitize_filename(filename))
    if target.exists() and target.is_file():
        target.unlink()


def render_file_list(title: str, files: list[FileEntry]) -> str:
    if not files:
        return f"{title}\nNo files found."
    lines = [title]
    for f in files:
        lines.append(f"- {f.name} | {human_size(f.size)}")
    return "\n".join(lines)
