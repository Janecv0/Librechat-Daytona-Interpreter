from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol

try:
    import redis.asyncio as redis_async
except ImportError:  # pragma: no cover - dependency controls this path
    redis_async = None


@dataclass(slots=True)
class SessionRecord:
    session_id: str
    sandbox_id: str
    language: str
    last_access: float


class SessionStore(Protocol):
    async def get(self, session_id: str) -> SessionRecord | None: ...

    async def upsert(self, record: SessionRecord) -> None: ...

    async def touch(self, session_id: str, last_access: float) -> None: ...

    async def delete(self, session_id: str) -> None: ...

    async def all_records(self) -> list[SessionRecord]: ...

    async def close(self) -> None: ...


class MemorySessionStore:
    def __init__(self) -> None:
        self._records: dict[str, SessionRecord] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def _clone(record: SessionRecord) -> SessionRecord:
        return SessionRecord(
            session_id=record.session_id,
            sandbox_id=record.sandbox_id,
            language=record.language,
            last_access=record.last_access,
        )

    async def get(self, session_id: str) -> SessionRecord | None:
        async with self._lock:
            record = self._records.get(session_id)
            return self._clone(record) if record else None

    async def upsert(self, record: SessionRecord) -> None:
        async with self._lock:
            self._records[record.session_id] = self._clone(record)

    async def touch(self, session_id: str, last_access: float) -> None:
        async with self._lock:
            record = self._records.get(session_id)
            if record is None:
                return
            record.last_access = last_access

    async def delete(self, session_id: str) -> None:
        async with self._lock:
            self._records.pop(session_id, None)

    async def all_records(self) -> list[SessionRecord]:
        async with self._lock:
            return [self._clone(record) for record in self._records.values()]

    async def close(self) -> None:
        return None


class RedisSessionStore:
    def __init__(self, redis_url: str) -> None:
        if redis_async is None:
            raise RuntimeError("redis package is required for REDIS_URL-backed sessions.")
        self._redis = redis_async.from_url(redis_url, decode_responses=True)
        self._index_key = "librechat_ci:sessions"

    def _session_key(self, session_id: str) -> str:
        return f"librechat_ci:session:{session_id}"

    @staticmethod
    def _to_record(session_id: str, raw: dict[str, str]) -> SessionRecord | None:
        if not raw:
            return None
        sandbox_id = raw.get("sandbox_id")
        language = raw.get("language")
        last_access_raw = raw.get("last_access")
        if not sandbox_id or not language or last_access_raw is None:
            return None
        return SessionRecord(
            session_id=session_id,
            sandbox_id=sandbox_id,
            language=language,
            last_access=float(last_access_raw),
        )

    async def get(self, session_id: str) -> SessionRecord | None:
        raw = await self._redis.hgetall(self._session_key(session_id))
        return self._to_record(session_id, raw)

    async def upsert(self, record: SessionRecord) -> None:
        session_key = self._session_key(record.session_id)
        await self._redis.hset(
            session_key,
            mapping={
                "sandbox_id": record.sandbox_id,
                "language": record.language,
                "last_access": str(record.last_access),
            },
        )
        await self._redis.sadd(self._index_key, record.session_id)

    async def touch(self, session_id: str, last_access: float) -> None:
        session_key = self._session_key(session_id)
        if not await self._redis.exists(session_key):
            return
        await self._redis.hset(session_key, mapping={"last_access": str(last_access)})

    async def delete(self, session_id: str) -> None:
        await self._redis.delete(self._session_key(session_id))
        await self._redis.srem(self._index_key, session_id)

    async def all_records(self) -> list[SessionRecord]:
        session_ids = await self._redis.smembers(self._index_key)
        if not session_ids:
            return []

        pipeline = self._redis.pipeline()
        ordered_session_ids = sorted(session_ids)
        for session_id in ordered_session_ids:
            pipeline.hgetall(self._session_key(session_id))
        rows = await pipeline.execute()

        records: list[SessionRecord] = []
        for session_id, raw in zip(ordered_session_ids, rows):
            record = self._to_record(session_id, raw)
            if record is not None:
                records.append(record)
        return records

    async def close(self) -> None:
        await self._redis.aclose()


def create_session_store(redis_url: str | None) -> SessionStore:
    if redis_url:
        return RedisSessionStore(redis_url)
    return MemorySessionStore()

