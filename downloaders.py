from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, Callable, Optional

import yt_dlp
from pyrogram import Client

from config import config
from db import Database
from file_manager import make_unique_path, move_to_drive, user_drive_dir, user_local_dir
from models import Task, User
from utils import format_eta, human_size, sanitize_filename


class DownloadCancelled(Exception):
    pass


class Progress:
    def __init__(self) -> None:
        self.percent: Optional[float] = None
        self.downloaded: Optional[int] = None
        self.total: Optional[int] = None
        self.speed: Optional[float] = None
        self.eta: Optional[float] = None
        self.text = "Starting..."

    def render(self, prefix: str) -> str:
        pct = f"{self.percent:.1f}%" if self.percent is not None else "unknown"
        speed = f"{human_size(int(self.speed))}/s" if self.speed else "unknown"
        eta = format_eta(self.eta)
        size = human_size(self.downloaded)
        total = human_size(self.total)
        return f"{prefix}\nProgress: {pct}\nDownloaded: {size} / {total}\nSpeed: {speed}\nETA: {eta}"


async def safe_edit(message: Any, text: str) -> None:
    try:
        await message.edit_text(text)
    except Exception as exc:
        if exc.__class__.__name__ == "FloodWait":
            await asyncio.sleep(getattr(exc, "value", 5))
        # Ignore unchanged-message and transient edit errors.


async def progress_reporter(message: Any, progress: Progress, prefix: str, done: asyncio.Event) -> None:
    last = ""
    while not done.is_set():
        text = progress.render(prefix)
        if text != last:
            await safe_edit(message, text)
            last = text
        try:
            await asyncio.wait_for(done.wait(), timeout=config.progress_update_seconds)
        except asyncio.TimeoutError:
            pass


def get_yt_formats(url: str) -> dict[str, Any]:
    ydl_opts = {"quiet": True, "no_warnings": True, "skip_download": True}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)

    formats: list[dict[str, Any]] = []
    seen: set[str] = set()

    for f in info.get("formats", []):
        fmt_id = str(f.get("format_id") or "")
        if not fmt_id or fmt_id in seen:
            continue
        seen.add(fmt_id)

        height = f.get("height")
        ext = f.get("ext") or "?"
        vcodec = f.get("vcodec")
        acodec = f.get("acodec")
        size = f.get("filesize") or f.get("filesize_approx")
        note = f.get("format_note") or ""

        label_parts = [fmt_id]

        if height:
            label_parts.append(f"{height}p")
        elif vcodec == "none":
            label_parts.append("audio")

        label_parts.append(ext)

        if note:
            label_parts.append(str(note))

        if size:
            label_parts.append(human_size(size))

        formats.append(
            {
                "format_id": fmt_id,
                "label": " | ".join(label_parts),
                "height": height or 0,
                "filesize": size,
                "vcodec": vcodec,
                "acodec": acodec,
            }
        )

    formats.sort(key=lambda x: (x["height"], x["filesize"] or 0), reverse=True)

    return {
        "title": info.get("title") or "video",
        "duration": info.get("duration"),
        "filesize": info.get("filesize") or info.get("filesize_approx"),
        "webpage_url": info.get("webpage_url") or url,
        "formats": formats[:20],
    }


async def process_task(
    app: Client,
    db: Database,
    task: Task,
    user: User,
    message: Any,
    is_cancelled: Callable[[int], bool],
) -> None:
    db.set_started(task.id)
    done = asyncio.Event()
    progress = Progress()
    reporter = asyncio.create_task(progress_reporter(message, progress, f"Task #{task.id} running", done))
    try:
        if task.type == "ytdlp":
            await download_ytdlp(db, task, user, progress, is_cancelled, message)
        elif task.type == "direct":
            await download_direct(db, task, user, progress, is_cancelled)
        elif task.type == "telegram":
            await download_telegram(app, db, task, user, progress, is_cancelled, message)
        else:
            raise RuntimeError(f"Unsupported task type: {task.type}")
        done.set()
        await reporter
        fresh = db.get_task(task.id)
        if task.type == "direct":
            await safe_edit(message, f"Task #{task.id} completed.\nSaved to:\n{fresh.drive_path if fresh else 'Drive'}")
        db.set_finished(task.id, "completed")
    except DownloadCancelled:
        done.set()
        await reporter
        db.set_finished(task.id, "cancelled", "Cancelled by user")
        await safe_edit(message, f"Task #{task.id} cancelled.")
    except Exception as exc:
        done.set()
        await reporter
        db.set_finished(task.id, "failed", str(exc)[:1000])
        await safe_edit(message, f"Task #{task.id} failed:\n{exc}")


async def download_ytdlp(
    db: Database,
    task: Task,
    user: User,
    progress: Progress,
    is_cancelled: Callable[[int], bool],
    message: Any,
) -> None:
    local_dir = user_local_dir(user)
    local_template = str(local_dir / "%(title).180B [%(id)s].%(ext)s")

    def hook(d: dict[str, Any]) -> None:
        if is_cancelled(task.id):
            raise DownloadCancelled()
        progress.downloaded = d.get("downloaded_bytes") or d.get("fragment_index")
        progress.total = d.get("total_bytes") or d.get("total_bytes_estimate")
        progress.speed = d.get("speed")
        progress.eta = d.get("eta")
        if progress.total and progress.downloaded:
            progress.percent = min(100.0, progress.downloaded / progress.total * 100)
        progress.text = d.get("status", "downloading")

    ydl_opts = {
        "format": task.selected_format or "bestvideo+bestaudio/best",
        "outtmpl": local_template,
        "merge_output_format": "mp4",
        "restrictfilenames": False,
        "noplaylist": True,
        "progress_hooks": [hook],
        "quiet": True,
        "no_warnings": True,
    }

    before = set(local_dir.glob("*"))
    await asyncio.to_thread(_run_ytdlp, task.source, ydl_opts)
    after = set(local_dir.glob("*"))
    new_files = [p for p in after - before if p.is_file()]
    if not new_files:
        new_files = sorted(local_dir.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True)[:1]
    if not new_files:
        raise RuntimeError("yt-dlp finished but no file was found")
    local_path = max(new_files, key=lambda p: p.stat().st_mtime)
    if local_path.stat().st_size > config.max_file_size_bytes:
        local_path.unlink(missing_ok=True)
        raise RuntimeError("Downloaded file exceeds MAX_FILE_SIZE_MB")
    file_size = local_path.stat().st_size
    await safe_edit(message, f"sending to drive... {human_size(file_size)}")
    dest = move_to_drive(local_path, user)
    await safe_edit(message, f"Task #{task.id} completed.\nSaved to Drive:\n{dest.name}\nSize: {human_size(file_size)}")
    db.update_task(task.id, filename=dest.name, local_path=str(local_path), drive_path=str(dest))


def _run_ytdlp(url: str, ydl_opts: dict[str, Any]) -> None:
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])


async def download_direct(
    db: Database,
    task: Task,
    user: User,
    progress: Progress,
    is_cancelled: Callable[[int], bool],
) -> None:
    drive_dir = user_drive_dir(user)
    filename = sanitize_filename(task.filename or "download")
    dest = make_unique_path(drive_dir, filename)
    cmd = [
        "wget",
        "--continue",
        "--tries=3",
        "--timeout=30",
        "--user-agent=telegram-drive-bot/1.0",
        "--output-document",
        str(dest),
        task.source,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    start = time.monotonic()
    last_size = 0
    try:
        while True:
            if is_cancelled(task.id):
                proc.terminate()
                raise DownloadCancelled()
            try:
                rc = await asyncio.wait_for(proc.wait(), timeout=config.progress_update_seconds)
                break
            except asyncio.TimeoutError:
                pass
            current = dest.stat().st_size if dest.exists() else 0
            progress.downloaded = current
            progress.total = None
            progress.speed = max(0.0, (current - last_size) / config.progress_update_seconds)
            progress.eta = None
            last_size = current
            if current > config.max_file_size_bytes:
                proc.terminate()
                dest.unlink(missing_ok=True)
                raise RuntimeError("Download exceeds MAX_FILE_SIZE_MB")
        if rc != 0:
            stderr = (await proc.stderr.read()).decode(errors="replace") if proc.stderr else ""
            raise RuntimeError(f"wget failed with exit code {rc}: {stderr[-500:]}")
        if dest.exists() and dest.stat().st_size > config.max_file_size_bytes:
            dest.unlink(missing_ok=True)
            raise RuntimeError("Download exceeds MAX_FILE_SIZE_MB")
        db.update_task(task.id, filename=dest.name, drive_path=str(dest))
    finally:
        if proc.returncode is None:
            proc.kill()


async def download_telegram(
    app: Client,
    db: Database,
    task: Task,
    user: User,
    progress: Progress,
    is_cancelled: Callable[[int], bool],
    message: Any,
) -> None:
    payload = json.loads(task.source)
    msg = await app.get_messages(payload["chat_id"], payload["message_id"])
    local_dir = user_local_dir(user)
    filename = sanitize_filename(task.filename or "telegram_file")
    local_path = make_unique_path(local_dir, filename)

    async def tg_progress(current: int, total: int) -> None:
        progress.downloaded = current
        progress.total = total
        progress.percent = (current / total * 100) if total else None
        if is_cancelled(task.id):
            raise DownloadCancelled()

    downloaded = await app.download_media(msg, file_name=str(local_path), progress=tg_progress)
    if not downloaded:
        raise RuntimeError("Telegram download failed")
    p = Path(downloaded)
    if p.stat().st_size > config.max_file_size_bytes:
        p.unlink(missing_ok=True)
        raise RuntimeError("Downloaded file exceeds MAX_FILE_SIZE_MB")
    file_size = p.stat().st_size
    await safe_edit(message, f"sending to drive... {human_size(file_size)}")
    dest = move_to_drive(p, user, filename)
    await safe_edit(message, f"Task #{task.id} completed.\nSaved to Drive:\n{dest.name}\nSize: {human_size(file_size)}")
    db.update_task(task.id, filename=dest.name, local_path=str(p), drive_path=str(dest))
