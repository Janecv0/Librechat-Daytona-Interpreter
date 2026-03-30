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
    fileId: str
    filename: str
    path: str
    size: int | None = None
    lastModified: str
    id: str | None = None
    file_id: str | None = None
    name: str | None = None


class UploadFileDescriptor(BaseModel):
    fileId: str
    filename: str
    id: str | None = None
    file_id: str | None = None


class ExecArtifact(BaseModel):
    session_id: str
    sessionId: str | None = None
    files: list[FileDescriptor]


class ExecResponse(BaseModel):
    session_id: str
    sessionId: str | None = None
    run: RunResult
    files: list[FileDescriptor]
    artifact: ExecArtifact | None = None
    content: str | None = None
    stdout: str | None = None
    stderr: str | None = None
    code: int | None = None
    status: str | None = None
    output: Any | None = None


class FilesResponse(BaseModel):
    session_id: str
    sessionId: str | None = None
    files: list[FileDescriptor]


class UploadResponse(BaseModel):
    message: str
    session_id: str
    sessionId: str | None = None
    files: list[UploadFileDescriptor]


class DeleteResponse(BaseModel):
    session_id: str
    sessionId: str | None = None
    deleted: bool
    file: FileDescriptor


class HealthResponse(BaseModel):
    status: str


class ErrorDetails(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorDetails
