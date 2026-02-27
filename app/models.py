from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class ExecRequest(BaseModel):
    code: str
    lang: str
    session_id: str | None = None
    files: list[Any] | None = None
    model_config = ConfigDict(extra="allow")


class RunResult(BaseModel):
    stdout: str | None = None
    stderr: str | None = None
    code: int | None = None
    status: str | None = None
    output: Any | None = None


class FileDescriptor(BaseModel):
    id: str
    name: str
    path: str
    size: int | None = None


class ExecResponse(BaseModel):
    session_id: str
    run: RunResult
    files: list[FileDescriptor]


class FilesResponse(BaseModel):
    session_id: str
    files: list[FileDescriptor]


class DeleteResponse(BaseModel):
    session_id: str
    deleted: bool
    file: FileDescriptor


class HealthResponse(BaseModel):
    status: str


class ErrorDetails(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorDetails

