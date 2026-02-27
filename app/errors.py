from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class APIError(Exception):
    status_code: int
    code: str
    message: str


def error_payload(code: str, message: str) -> dict[str, dict[str, str]]:
    return {"error": {"code": code, "message": message}}

