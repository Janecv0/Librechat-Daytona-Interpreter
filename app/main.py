from __future__ import annotations

import inspect
import json
import logging
from contextlib import asynccontextmanager
from io import BytesIO
from pathlib import PurePosixPath
from typing import Annotated, Any

from fastapi import Depends, FastAPI, File, Form, Header, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse

from .auth import validate_api_key
from .cleanup import SessionCleanupWorker
from .config import Settings, get_settings
from .daytona_gateway import DaytonaGateway
from .errors import APIError, error_payload
from .file_ids import (
    WORKSPACE_ROOT,
    encode_file_id,
    normalize_workspace_path,
    resolve_file_reference,
    sanitize_upload_filename,
)
from .lang import normalize_language
from .models import (
    DeleteResponse,
    ErrorResponse,
    ExecRequest,
    ExecResponse,
    FileDescriptor,
    FilesResponse,
    HealthResponse,
    RunResult,
)
from .session_service import SessionService
from .session_store import SessionStore, create_session_store

logger = logging.getLogger(__name__)


def _get_field(value: Any, keys: tuple[str, ...]) -> Any:
    if isinstance(value, dict):
        for key in keys:
            if key in value:
                return value[key]
        return None
    for key in keys:
        if hasattr(value, key):
            return getattr(value, key)
    return None


def _to_file_descriptor(entry: Any) -> FileDescriptor:
    path = _get_field(entry, ("path", "full_path", "fullPath"))
    name = _get_field(entry, ("name", "filename"))
    size = _get_field(entry, ("size", "bytes"))

    if not path and name:
        path = f"{WORKSPACE_ROOT}/{name}"
    if not path:
        raise APIError(status_code=502, code="daytona_error", message="Daytona returned a file entry without path.")

    normalized_path = normalize_workspace_path(str(path))
    resolved_name = str(name) if name else PurePosixPath(normalized_path).name
    parsed_size: int | None = None
    if isinstance(size, int):
        parsed_size = size
    elif isinstance(size, str) and size.isdigit():
        parsed_size = int(size)

    return FileDescriptor(
        id=encode_file_id(normalized_path),
        name=resolved_name,
        path=normalized_path,
        size=parsed_size,
    )


def _normalize_run_payload(run_payload: Any) -> RunResult:
    if not isinstance(run_payload, dict):
        run_payload = {}

    code = run_payload.get("code")
    if isinstance(code, str) and code.lstrip("-").isdigit():
        code = int(code)
    if not isinstance(code, int):
        code = None

    stdout = run_payload.get("stdout")
    stderr = run_payload.get("stderr")
    status = run_payload.get("status")
    output = run_payload.get("output")
    stdout_text = _coerce_text(stdout) or ""
    stderr_text = _coerce_text(stderr) or ""
    output_text = _coerce_text(output) or ""

    if not stdout_text and output_text:
        stdout_text = output_text
    if output is None and stdout_text:
        output = stdout_text

    return RunResult(
        stdout=stdout_text,
        stderr=stderr_text,
        code=code,
        status=str(status) if status is not None else ("completed" if code in (None, 0) else "failed"),
        output=output if output is not None else stdout_text,
    )


def _extract_session_id_from_files(files_payload: list[Any] | None) -> str | None:
    if not files_payload:
        return None
    for item in files_payload:
        if isinstance(item, dict):
            maybe_session_id = item.get("session_id")
            if isinstance(maybe_session_id, str) and maybe_session_id.strip():
                return maybe_session_id.strip()
    return None


async def _read_upload_bytes(upload: UploadFile, max_bytes: int) -> bytes:
    data = bytearray()
    chunk_size = 1024 * 1024
    while True:
        chunk = await upload.read(chunk_size)
        if not chunk:
            break
        data.extend(chunk)
        if len(data) > max_bytes:
            raise APIError(
                status_code=413,
                code="file_too_large",
                message=f"Upload '{upload.filename or 'unnamed'}' exceeds {max_bytes} bytes limit.",
            )
    return bytes(data)


def _daytona_error(action: str, exc: Exception) -> APIError:
    return APIError(status_code=502, code="daytona_error", message=f"Failed to {action}: {exc}")


def _coerce_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        parts = [_coerce_text(item) for item in value]
        non_empty = [part for part in parts if part]
        if non_empty:
            return "\n".join(non_empty)
        return json.dumps(value, ensure_ascii=False, default=str)
    if isinstance(value, dict):
        for key in ("stdout", "output", "result", "text", "message", "content"):
            if key in value:
                nested = _coerce_text(value.get(key))
                if nested:
                    return nested
        return json.dumps(value, ensure_ascii=False, default=str)
    for key in ("stdout", "output", "result", "text", "message", "content"):
        if hasattr(value, key):
            nested = _coerce_text(getattr(value, key))
            if nested:
                return nested
    return str(value)


def _is_missing_workspace_error(exc: Exception) -> bool:
    message = str(exc).lower()
    missing_signals = ("not found", "does not exist", "no such file", "file not found")
    return "workspace" in message and any(signal in message for signal in missing_signals)


def _safe_list_workspace_files(gateway_client: Any, sandbox_id: str) -> list[Any]:
    try:
        return gateway_client.list_files(sandbox_id, WORKSPACE_ROOT)
    except Exception as exc:
        # Code execution should not fail solely because listing /workspace failed.
        if _is_missing_workspace_error(exc):
            logger.warning(
                "Workspace path '%s' is unavailable in sandbox '%s'. Returning empty files list.",
                WORKSPACE_ROOT,
                sandbox_id,
            )
            return []
        raise


def create_app(
    settings: Settings | None = None,
    store: SessionStore | None = None,
    gateway: Any | None = None,
    enable_cleanup: bool = True,
) -> FastAPI:
    runtime_settings = settings or get_settings()
    runtime_store = store or create_session_store(runtime_settings.REDIS_URL)
    runtime_gateway: Any | None = gateway
    session_service: SessionService | None = (
        SessionService(runtime_store, runtime_gateway) if runtime_gateway is not None else None
    )
    cleanup_worker: SessionCleanupWorker | None = None

    def ensure_gateway() -> Any:
        nonlocal runtime_gateway
        if runtime_gateway is None:
            runtime_gateway = DaytonaGateway(
                api_key=runtime_settings.DAYTONA_API_KEY,
                api_url=runtime_settings.DAYTONA_API_URL,
            )
        return runtime_gateway

    def ensure_session_service() -> SessionService:
        nonlocal session_service
        if session_service is None:
            session_service = SessionService(runtime_store, ensure_gateway())
        return session_service

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        nonlocal cleanup_worker
        if enable_cleanup:
            cleanup_worker = SessionCleanupWorker(
                store=runtime_store,
                gateway=ensure_gateway(),
                ttl_seconds=runtime_settings.SESSION_TTL_SECONDS,
                interval_seconds=runtime_settings.CLEANUP_INTERVAL_SECONDS,
            )
            await cleanup_worker.start()
        try:
            yield
        finally:
            if cleanup_worker is not None:
                await cleanup_worker.stop()
            close_fn = getattr(runtime_store, "close", None)
            if callable(close_fn):
                close_result = close_fn()
                if inspect.isawaitable(close_result):
                    await close_result

    app = FastAPI(title="LibreChat Daytona Code Interpreter Adapter", version="1.0.0", lifespan=lifespan)
    app.state.settings = runtime_settings
    app.state.store = runtime_store
    app.state.gateway = runtime_gateway
    app.state.session_service = session_service

    async def require_api_key(
        x_api_key: Annotated[str | None, Header(alias="x-api-key")] = None,
    ) -> None:
        validate_api_key(x_api_key, runtime_settings.ADAPTER_API_KEY)

    @app.exception_handler(APIError)
    async def api_error_handler(_: Any, exc: APIError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=error_payload(exc.code, exc.message))

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(_: Any, exc: RequestValidationError) -> JSONResponse:
        error_message = "Invalid request body."
        if exc.errors():
            first_error = exc.errors()[0]
            location = ".".join(str(part) for part in first_error.get("loc", []))
            detail = first_error.get("msg", "Invalid value")
            error_message = f"{location}: {detail}" if location else detail
        return JSONResponse(
            status_code=422,
            content=error_payload("validation_error", error_message),
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(_: Any, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled adapter error", exc_info=exc)
        return JSONResponse(
            status_code=500,
            content=error_payload("internal_error", "Internal server error."),
        )

    @app.get("/healthz", response_model=HealthResponse)
    async def healthz() -> HealthResponse:
        return HealthResponse(status="ok")

    @app.post(
        "/exec",
        response_model=ExecResponse,
        responses={401: {"model": ErrorResponse}, 422: {"model": ErrorResponse}, 502: {"model": ErrorResponse}},
    )
    async def exec_code(
        payload: ExecRequest,
        _: None = Depends(require_api_key),
    ) -> ExecResponse:
        service = ensure_session_service()
        gateway_client = ensure_gateway()
        language = normalize_language(payload.lang)
        requested_session_id = payload.session_id or _extract_session_id_from_files(payload.files)
        session = await service.get_or_create_exec_session(requested_session_id, language)

        try:
            run_payload = gateway_client.run_code(session.sandbox_id, language, payload.code)
        except APIError:
            raise
        except Exception as exc:
            raise _daytona_error("execute code in Daytona sandbox", exc) from exc

        try:
            file_entries = _safe_list_workspace_files(gateway_client, session.sandbox_id)
        except Exception as exc:
            raise _daytona_error("list files", exc) from exc

        file_descriptors = [_to_file_descriptor(item) for item in file_entries]
        return ExecResponse(
            session_id=session.session_id,
            run=_normalize_run_payload(run_payload),
            files=file_descriptors,
        )

    @app.post(
        "/upload",
        response_model=FilesResponse,
        responses={401: {"model": ErrorResponse}, 413: {"model": ErrorResponse}, 502: {"model": ErrorResponse}},
    )
    async def upload_files(
        _: None = Depends(require_api_key),
        session_id: Annotated[str | None, Form()] = None,
        files: list[UploadFile] = File(...),
    ) -> FilesResponse:
        service = ensure_session_service()
        gateway_client = ensure_gateway()
        if not files:
            raise APIError(status_code=400, code="no_files", message="At least one file is required.")

        session = await service.get_or_create_upload_session(session_id=session_id, default_language="python")
        uploaded_descriptors: list[FileDescriptor] = []

        for upload in files:
            try:
                payload = await _read_upload_bytes(upload, runtime_settings.UPLOAD_MAX_BYTES)
                safe_name = sanitize_upload_filename(upload.filename or "upload.bin")
                destination_path = normalize_workspace_path(f"{WORKSPACE_ROOT}/{safe_name}")
                gateway_client.upload_file(session.sandbox_id, destination_path, payload)
                uploaded_descriptors.append(
                    FileDescriptor(
                        id=encode_file_id(destination_path),
                        name=safe_name,
                        path=destination_path,
                        size=len(payload),
                    )
                )
            except APIError:
                raise
            except Exception as exc:
                raise _daytona_error(f"upload file '{upload.filename}'", exc) from exc
            finally:
                await upload.close()

        await service.touch(session.session_id)
        return FilesResponse(session_id=session.session_id, files=uploaded_descriptors)

    @app.get(
        "/files/{session_id}",
        response_model=FilesResponse,
        responses={401: {"model": ErrorResponse}, 404: {"model": ErrorResponse}, 502: {"model": ErrorResponse}},
    )
    async def list_files(
        session_id: str,
        _: None = Depends(require_api_key),
    ) -> FilesResponse:
        service = ensure_session_service()
        gateway_client = ensure_gateway()
        session = await service.require_session(session_id)
        try:
            file_entries = _safe_list_workspace_files(gateway_client, session.sandbox_id)
        except Exception as exc:
            raise _daytona_error("list files", exc) from exc
        return FilesResponse(session_id=session.session_id, files=[_to_file_descriptor(item) for item in file_entries])

    @app.get(
        "/download/{session_id}/{file_id}",
        responses={401: {"model": ErrorResponse}, 404: {"model": ErrorResponse}, 502: {"model": ErrorResponse}},
    )
    async def download_file(
        session_id: str,
        file_id: str,
        _: None = Depends(require_api_key),
    ) -> StreamingResponse:
        service = ensure_session_service()
        gateway_client = ensure_gateway()
        session = await service.require_session(session_id)
        target_path = resolve_file_reference(file_id)
        try:
            file_entries = _safe_list_workspace_files(gateway_client, session.sandbox_id)
            descriptors = [_to_file_descriptor(item) for item in file_entries]
        except Exception as exc:
            raise _daytona_error("list files", exc) from exc

        files_by_path = {descriptor.path: descriptor for descriptor in descriptors}
        if target_path not in files_by_path:
            raise APIError(
                status_code=404,
                code="file_not_found",
                message=f"File '{target_path}' does not exist in session '{session_id}'.",
            )

        try:
            payload = gateway_client.download_file(session.sandbox_id, target_path)
        except Exception as exc:
            raise _daytona_error("download file", exc) from exc

        if not isinstance(payload, bytes):
            raise APIError(status_code=502, code="daytona_error", message="Daytona returned non-bytes file payload.")

        descriptor = files_by_path[target_path]
        filename = descriptor.name or PurePosixPath(target_path).name
        headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
        return StreamingResponse(BytesIO(payload), media_type="application/octet-stream", headers=headers)

    @app.delete(
        "/files/{session_id}/{file_id}",
        response_model=DeleteResponse,
        responses={401: {"model": ErrorResponse}, 404: {"model": ErrorResponse}, 502: {"model": ErrorResponse}},
    )
    async def delete_file(
        session_id: str,
        file_id: str,
        _: None = Depends(require_api_key),
    ) -> DeleteResponse:
        service = ensure_session_service()
        gateway_client = ensure_gateway()
        session = await service.require_session(session_id)
        target_path = resolve_file_reference(file_id)
        try:
            file_entries = _safe_list_workspace_files(gateway_client, session.sandbox_id)
            descriptors = [_to_file_descriptor(item) for item in file_entries]
        except Exception as exc:
            raise _daytona_error("list files", exc) from exc

        files_by_path = {descriptor.path: descriptor for descriptor in descriptors}
        descriptor = files_by_path.get(target_path)
        if descriptor is None:
            raise APIError(
                status_code=404,
                code="file_not_found",
                message=f"File '{target_path}' does not exist in session '{session_id}'.",
            )

        try:
            gateway_client.delete_file(session.sandbox_id, target_path)
        except Exception as exc:
            raise _daytona_error("delete file", exc) from exc

        await service.touch(session.session_id)
        return DeleteResponse(session_id=session.session_id, deleted=True, file=descriptor)

    return app


app = create_app()
