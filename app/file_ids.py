from __future__ import annotations

import base64
import binascii
import posixpath
from pathlib import PurePosixPath

from .errors import APIError

WORKSPACE_ROOT = "/workspace"


def sanitize_upload_filename(filename: str | None) -> str:
    normalized = (filename or "").replace("\\", "/")
    safe_name = PurePosixPath(normalized).name
    if not safe_name or safe_name in {".", ".."}:
        raise APIError(status_code=400, code="invalid_filename", message="Invalid upload filename.")
    return safe_name


def normalize_workspace_path(path: str) -> str:
    raw = (path or "").strip().replace("\\", "/")
    if not raw:
        raise APIError(status_code=400, code="invalid_path", message="A file path is required.")

    if not raw.startswith("/"):
        raw = f"{WORKSPACE_ROOT}/{raw}"

    normalized = posixpath.normpath(raw)
    if not normalized.startswith("/"):
        normalized = f"/{normalized}"

    if normalized != WORKSPACE_ROOT and not normalized.startswith(f"{WORKSPACE_ROOT}/"):
        raise APIError(
            status_code=400,
            code="invalid_path",
            message="Path traversal is not allowed. Paths must stay inside /workspace.",
        )

    return normalized


def encode_file_id(path: str) -> str:
    normalized = normalize_workspace_path(path)
    encoded = base64.urlsafe_b64encode(normalized.encode("utf-8")).decode("ascii")
    return encoded.rstrip("=")


def decode_file_id(file_id: str) -> str:
    if not file_id:
        raise APIError(status_code=400, code="invalid_file_id", message="fileId is required.")
    padding = "=" * (-len(file_id) % 4)
    try:
        decoded = base64.urlsafe_b64decode(file_id + padding).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError) as exc:
        raise APIError(status_code=400, code="invalid_file_id", message="Invalid fileId value.") from exc
    return normalize_workspace_path(decoded)


def resolve_file_reference(file_ref: str) -> str:
    raw = (file_ref or "").strip()
    if not raw:
        raise APIError(status_code=400, code="invalid_file_id", message="fileId is required.")

    if raw.startswith("/") or "/" in raw or "\\" in raw:
        return normalize_workspace_path(raw)

    try:
        return decode_file_id(raw)
    except APIError:
        # Backward-compatible fallback for plain names/relative paths.
        return normalize_workspace_path(raw)

