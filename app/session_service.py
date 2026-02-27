from __future__ import annotations

import time
from typing import Callable
from uuid import uuid4

from .errors import APIError
from .session_store import SessionRecord, SessionStore


class SessionService:
    def __init__(
        self,
        store: SessionStore,
        gateway: object,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._store = store
        self._gateway = gateway
        self._clock = clock

    def _now(self) -> float:
        return self._clock()

    async def get_or_create_exec_session(self, session_id: str | None, language: str) -> SessionRecord:
        if session_id:
            existing = await self._store.get(session_id)
            if existing is not None:
                if existing.language != language:
                    raise APIError(
                        status_code=409,
                        code="session_language_mismatch",
                        message=(
                            f"Session '{session_id}' already uses language '{existing.language}', "
                            f"but request asked for '{language}'."
                        ),
                    )
                await self.touch(session_id)
                return existing
            return await self._create_session(session_id, language)

        generated_session_id = str(uuid4())
        return await self._create_session(generated_session_id, language)

    async def get_or_create_upload_session(
        self,
        session_id: str | None,
        default_language: str = "python",
    ) -> SessionRecord:
        if session_id:
            existing = await self._store.get(session_id)
            if existing is not None:
                await self.touch(session_id)
                return existing
            return await self._create_session(session_id, default_language)

        generated_session_id = str(uuid4())
        return await self._create_session(generated_session_id, default_language)

    async def require_session(self, session_id: str) -> SessionRecord:
        existing = await self._store.get(session_id)
        if existing is None:
            raise APIError(
                status_code=404,
                code="session_not_found",
                message=f"Session '{session_id}' does not exist.",
            )
        await self.touch(session_id)
        return existing

    async def touch(self, session_id: str) -> None:
        await self._store.touch(session_id, self._now())

    async def delete_session(self, session_id: str) -> None:
        await self._store.delete(session_id)

    async def _create_session(self, session_id: str, language: str) -> SessionRecord:
        sandbox_id = self._gateway.create_sandbox(language)
        record = SessionRecord(
            session_id=session_id,
            sandbox_id=sandbox_id,
            language=language,
            last_access=self._now(),
        )
        await self._store.upsert(record)
        return record

