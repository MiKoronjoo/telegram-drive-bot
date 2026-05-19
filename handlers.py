from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from config import config
from db import Database
from downloaders import get_yt_formats
from file_manager import delete_user_file, list_files, render_file_list, user_drive_dir, user_local_dir
from queue_manager import QueueManager
from utils import head_url, human_size, is_probably_direct_url, sanitize_filename, validate_http_url


class PendingStore:
    def __init__(self, ttl_seconds: int = 3600):
        self.ttl_seconds = ttl_seconds
        self.data: dict[str, tuple[float, dict[str, Any]]] = {}

    def put(self, payload: dict[str, Any]) -> str:
        self.cleanup()
        key = uuid.uuid4().hex[:12]
        self.data[key] = (time.time(), payload)
        return key

    def get(self, key: str) -> Optional[dict[str, Any]]:
        item = self.data.get(key)
        if not item:
            return None
        created, payload = item
        if time.time() - created > self.ttl_seconds:
            self.data.pop(key, None)
            return None
        return payload

    def cleanup(self) -> None:
        now = time.time()
        expired = [k for k, (created, _) in self.data.items() if now - created > self.ttl_seconds]
        for k in expired:
            self.data.pop(k, None)


pending = PendingStore()


def is_admin(user_id: int) -> bool:
    return user_id in config.admin_ids


def get_sender_id(message: Message) -> int:
    return int(message.from_user.id) if message.from_user else 0


async def require_user(message: Message, db: Database):
    user_id = get_sender_id(message)
    user = db.get_user(user_id)
    if not user:
        await message.reply_text("Access denied. Ask the admin to whitelist your Telegram ID.")
        return None
    return user


def setup_handlers(app: Client, db: Database, queue_manager: QueueManager) -> None:
    @app.on_message(filters.command("start") & filters.private)
    async def start(_: Client, message: Message) -> None:
        user = await require_user(message, db)
        if not user:
            return
        await message.reply_text(
            "Welcome. Send a yt-dlp supported link, a direct download link, or a Telegram file.\n"
            "Use /help for commands."
        )

    @app.on_message(filters.command("help") & filters.private)
    async def help_cmd(_: Client, message: Message) -> None:
        await message.reply_text(
            "Commands:\n"
            "/start - Start\n"
            "/queue - Show active queue\n"
            "/myfiles - List files in your Drive folder\n"
            "/serverfiles - List files in your local download folder\n"
            "/cancel - Cancel your latest queued/running task\n\n"
            "Admin:\n"
            "/adduser telegram_id username gmail\n"
            "/removeuser telegram_id\n"
            "/users"
        )

    @app.on_message(filters.command("adduser") & filters.private)
    async def add_user(_: Client, message: Message) -> None:
        uid = get_sender_id(message)
        if not is_admin(uid):
            await message.reply_text("Admin only.")
            return
        parts = message.text.split(maxsplit=3) if message.text else []
        if len(parts) != 4:
            await message.reply_text("Usage: /adduser telegram_id username gmail")
            return
        telegram_id = int(parts[1])
        username = parts[2].lstrip("@")
        gmail = parts[3].strip()
        drive_path = str((config.drive_root / gmail).resolve())
        db.add_user(telegram_id, username, gmail, drive_path)
        Path(drive_path).mkdir(parents=True, exist_ok=True)
        (config.local_download_root / gmail).mkdir(parents=True, exist_ok=True)
        await message.reply_text(f"Added user {username} ({telegram_id}) -> {drive_path}")

    @app.on_message(filters.command("removeuser") & filters.private)
    async def remove_user(_: Client, message: Message) -> None:
        uid = get_sender_id(message)
        if not is_admin(uid):
            await message.reply_text("Admin only.")
            return
        parts = message.text.split(maxsplit=1) if message.text else []
        if len(parts) != 2:
            await message.reply_text("Usage: /removeuser telegram_id")
            return
        db.remove_user(int(parts[1]))
        await message.reply_text("User removed.")

    @app.on_message(filters.command("users") & filters.private)
    async def users_cmd(_: Client, message: Message) -> None:
        uid = get_sender_id(message)
        if not is_admin(uid):
            await message.reply_text("Admin only.")
            return
        users = db.list_users()
        if not users:
            await message.reply_text("No users.")
            return
        text = "Users:\n" + "\n".join(f"- {u.telegram_id} @{u.username} {u.gmail}" for u in users)
        await message.reply_text(text[:4000])

    @app.on_message(filters.command("queue") & filters.private)
    async def queue_cmd(_: Client, message: Message) -> None:
        user = await require_user(message, db)
        if not user:
            return
        tasks = db.visible_tasks()
        if not tasks:
            await message.reply_text("Queue is empty.")
            return
        lines = ["Active queue:"]
        for idx, task in enumerate(tasks, start=1):
            owner = "you" if task.user_id == user.telegram_id else str(task.user_id)
            lines.append(f"{idx}. #{task.id} {task.status} {task.type} ({owner})")
        await message.reply_text("\n".join(lines))

    @app.on_message(filters.command("cancel") & filters.private)
    async def cancel_cmd(_: Client, message: Message) -> None:
        user = await require_user(message, db)
        if not user:
            return
        task = db.running_task_for_user(user.telegram_id)
        if not task:
            await message.reply_text("No queued or running task to cancel.")
            return
        queue_manager.cancel(task.id)
        await message.reply_text(f"Cancellation requested for task #{task.id}.")

    @app.on_message(filters.command("myfiles") & filters.private)
    async def my_files(_: Client, message: Message) -> None:
        user = await require_user(message, db)
        if not user:
            return
        files = list_files(user_drive_dir(user))
        markup = delete_markup("drive", files)
        await message.reply_text(render_file_list("Drive files:", files), reply_markup=markup)

    @app.on_message(filters.command("serverfiles") & filters.private)
    async def server_files(_: Client, message: Message) -> None:
        user = await require_user(message, db)
        if not user:
            return
        files = list_files(user_local_dir(user))
        markup = delete_markup("local", files)
        await message.reply_text(render_file_list("Local files:", files), reply_markup=markup)

    @app.on_message(filters.private & filters.text & ~filters.command(["start", "help", "queue", "myfiles", "serverfiles", "cancel", "adduser", "removeuser", "users"]))
    async def text_handler(_: Client, message: Message) -> None:
        user = await require_user(message, db)
        if not user:
            return
        url = (message.text or "").strip()
        if not validate_http_url(url):
            await message.reply_text("Please send a valid http:// or https:// link.")
            return

        if is_probably_direct_url(url):
            await show_direct_prompt(message, url)
            return

        try:
            info = await asyncio.to_thread(get_yt_formats, url)
            key = pending.put({"type": "ytdlp", "url": url})

            buttons = []
            for fmt in info["formats"]:
                fmt_id = fmt["format_id"]
                label = fmt["label"][:48]
                buttons.append([InlineKeyboardButton(label, callback_data=f"yt:{key}:{fmt_id}")])

            if not buttons:
                await message.reply_text("No downloadable formats found.")
                return

            await message.reply_text(
                f"Video detected:\n{info['title']}\nSize: {human_size(info.get('filesize'))}\nChoose format:",
                reply_markup=InlineKeyboardMarkup(buttons),
            )
        except Exception:
            await show_direct_prompt(message, url)

    @app.on_message(filters.private & (filters.document | filters.video | filters.audio))
    async def telegram_file(_: Client, message: Message) -> None:
        user = await require_user(message, db)
        if not user:
            return
        media = message.document or message.video or message.audio
        if not media:
            return
        size = int(getattr(media, "file_size", 0) or 0)
        if size > config.max_file_size_bytes:
            await message.reply_text("File is larger than MAX_FILE_SIZE_MB.")
            return
        filename = sanitize_filename(getattr(media, "file_name", None) or f"telegram_{message.id}")
        payload = {
            "type": "telegram",
            "chat_id": message.chat.id,
            "message_id": message.id,
            "filename": filename,
        }
        key = pending.put(payload)
        await message.reply_text(
            f"Telegram file:\n{filename}\nSize: {human_size(size)}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Save to Drive", callback_data=f"tg:{key}")]]),
        )

    @app.on_callback_query()
    async def callbacks(_: Client, cq: CallbackQuery) -> None:
        user_id = int(cq.from_user.id)
        user = db.get_user(user_id)
        if not user:
            await cq.answer("Access denied", show_alert=True)
            return
        data = cq.data or ""
        try:
            if data.startswith("yt:"):
                _, key, format_id = data.split(":", 2)
                payload = pending.get(key)
                if not payload:
                    await cq.answer("This button expired", show_alert=True)
                    return

                task_id = db.create_task(user.telegram_id, "ytdlp", payload["url"], selected_format=format_id)
                await cq.message.edit_text(f"Task #{task_id} queued.")
                pos = await queue_manager.enqueue(task_id, cq.message)
                await cq.message.edit_text(f"Task #{task_id} queued. Position: {pos}")
                await cq.answer("Queued")
            elif data.startswith("direct:"):
                key = data.split(":", 1)[1]
                payload = pending.get(key)
                if not payload:
                    await cq.answer("This button expired", show_alert=True)
                    return
                task_id = db.create_task(user.telegram_id, "direct", payload["url"], filename=payload["filename"])
                await cq.message.edit_text(f"Task #{task_id} queued.")
                pos = await queue_manager.enqueue(task_id, cq.message)
                await cq.message.edit_text(f"Task #{task_id} queued. Position: {pos}")
                await cq.answer("Queued")
            elif data.startswith("tg:"):
                key = data.split(":", 1)[1]
                payload = pending.get(key)
                if not payload:
                    await cq.answer("This button expired", show_alert=True)
                    return
                source = json.dumps({"chat_id": payload["chat_id"], "message_id": payload["message_id"]})
                task_id = db.create_task(user.telegram_id, "telegram", source, filename=payload["filename"])
                await cq.message.edit_text(f"Task #{task_id} queued.")
                pos = await queue_manager.enqueue(task_id, cq.message)
                await cq.message.edit_text(f"Task #{task_id} queued. Position: {pos}")
                await cq.answer("Queued")
            elif data.startswith("del:"):
                _, area, key = data.split(":", 2)
                payload = pending.get(key)
                if not payload:
                    await cq.answer("This button expired", show_alert=True)
                    return
                delete_user_file(user, area, payload["filename"])
                await cq.answer("Deleted")
                await cq.message.edit_text(f"Deleted {payload['filename']}")
        except Exception as exc:
            await cq.answer("Error", show_alert=True)
            if cq.message:
                await cq.message.reply_text(f"Error: {exc}")

    async def show_direct_prompt(message: Message, url: str) -> None:
        try:
            filename, size, ctype = await asyncio.to_thread(head_url, url)
        except Exception:
            filename, size, ctype = "download", None, None
        if size and size > config.max_file_size_bytes:
            await message.reply_text("Remote file is larger than MAX_FILE_SIZE_MB.")
            return
        key = pending.put({"type": "direct", "url": url, "filename": filename})
        await message.reply_text(
            f"Direct link:\nFilename: {filename}\nSize: {human_size(size)}\nType: {ctype or 'unknown'}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Download", callback_data=f"direct:{key}")]]),
        )


def delete_markup(area: str, files: list[Any]) -> Optional[InlineKeyboardMarkup]:
    rows = []
    for entry in files[:10]:
        key = pending.put({"filename": entry.name})
        rows.append([InlineKeyboardButton(f"Delete {entry.name[:40]}", callback_data=f"del:{area}:{key}")])
    return InlineKeyboardMarkup(rows) if rows else None
