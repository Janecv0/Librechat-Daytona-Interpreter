from __future__ import annotations

from .errors import APIError


def validate_api_key(received_key: str | None, expected_key: str) -> None:
    if not received_key or received_key != expected_key:
        raise APIError(
            status_code=401,
            code="unauthorized",
            message="Invalid or missing x-api-key header.",
        )

