from __future__ import annotations

from .errors import APIError

LANGUAGE_MAP = {
    "py": "python",
    "python": "python",
    "js": "javascript",
    "javascript": "javascript",
    "ts": "typescript",
    "typescript": "typescript",
}


def normalize_language(lang: str) -> str:
    key = (lang or "").strip().lower()
    if not key:
        raise APIError(status_code=422, code="invalid_language", message="Field 'lang' is required.")
    normalized = LANGUAGE_MAP.get(key)
    if normalized is None:
        raise APIError(
            status_code=422,
            code="invalid_language",
            message=f"Unsupported language '{lang}'. Allowed: py|python, js|javascript, ts|typescript.",
        )
    return normalized

