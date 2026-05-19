from __future__ import annotations

import asyncio
from typing import Any, Optional

from pyrogram import Client

from db import Database
from downloaders import process_task


class QueueManager:
    def __init__(self, app: Client, db: Database, max_workers: int):
        self.app = app
        self.db = db
        self.queue: asyncio.Queue[tuple[int, Any]] = asyncio.Queue()
        self.max_workers = max_workers
        self.workers: list[asyncio.Task[None]] = []
        self.cancelled: set[int] = set()

    async def start(self) -> None:
        for idx in range(self.max_workers):
            self.workers.append(asyncio.create_task(self._worker(idx)))

    async def stop(self) -> None:
        for worker in self.workers:
            worker.cancel()
        await asyncio.gather(*self.workers, return_exceptions=True)

    async def enqueue(self, task_id: int, status_message: Any) -> int:
        position = self.queue.qsize() + 1
        await self.queue.put((task_id, status_message))
        return position

    def cancel(self, task_id: int) -> None:
        self.cancelled.add(task_id)
        task = self.db.get_task(task_id)
        if task and task.status == "queued":
            self.db.set_finished(task_id, "cancelled", "Cancelled before start")

    def is_cancelled(self, task_id: int) -> bool:
        task = self.db.get_task(task_id)
        return task_id in self.cancelled or bool(task and task.status == "cancelled")

    async def _worker(self, idx: int) -> None:
        while True:
            task_id, status_message = await self.queue.get()
            try:
                task = self.db.get_task(task_id)
                if not task or self.is_cancelled(task_id):
                    continue
                user = self.db.get_user(task.user_id)
                if not user:
                    self.db.set_finished(task_id, "failed", "User no longer exists")
                    continue
                await process_task(self.app, self.db, task, user, status_message, self.is_cancelled)
            finally:
                self.cancelled.discard(task_id)
                self.queue.task_done()
