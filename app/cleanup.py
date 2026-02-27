from __future__ import annotations

import asyncio
import logging
import time
from contextlib import suppress

from .session_store import SessionStore

logger = logging.getLogger(__name__)


class SessionCleanupWorker:
    def __init__(
        self,
        store: SessionStore,
        gateway: object,
        ttl_seconds: int,
        interval_seconds: int,
    ) -> None:
        self._store = store
        self._gateway = gateway
        self._ttl_seconds = ttl_seconds
        self._interval_seconds = interval_seconds
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run(), name="session-cleanup-worker")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop_event.set()
        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _run(self) -> None:
        while not self._stop_event.is_set():
            await self.cleanup_once()
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._interval_seconds)
            except asyncio.TimeoutError:
                continue

    async def cleanup_once(self) -> None:
        now = time.time()
        records = await self._store.all_records()
        for record in records:
            idle_seconds = now - record.last_access
            if idle_seconds <= self._ttl_seconds:
                continue
            try:
                self._gateway.delete_sandbox(record.sandbox_id)
            except Exception as exc:  # pragma: no cover - depends on external failures
                logger.warning(
                    "Failed to delete Daytona sandbox '%s' for session '%s': %s",
                    record.sandbox_id,
                    record.session_id,
                    exc,
                )
            await self._store.delete(record.session_id)

