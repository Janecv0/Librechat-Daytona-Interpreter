from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from importlib import import_module
from io import BytesIO
from pathlib import PurePosixPath
import json
from typing import Any

from .file_ids import normalize_workspace_path


@dataclass(slots=True)
class FileEntry:
    path: str
    name: str
    size: int | None = None


class DaytonaGateway:
    def __init__(self, api_key: str, api_url: str | None = None) -> None:
        daytona_module = None
        try:
            daytona_module = import_module("daytona_sdk")
        except ImportError:
            try:
                daytona_module = import_module("daytona")
            except ImportError as exc:  # pragma: no cover - runtime environment concern
                raise RuntimeError("daytona-sdk package is required.") from exc

        if daytona_module is None:  # pragma: no cover - defensive fallback
            raise RuntimeError("daytona-sdk package is required.")

        self._module = daytona_module
        daytona_cls = getattr(daytona_module, "Daytona")
        config_cls = getattr(daytona_module, "DaytonaConfig")
        config_kwargs: dict[str, Any] = {"api_key": api_key}
        if api_url:
            config_kwargs["api_url"] = api_url
        config = config_cls(**config_kwargs)
        self._client = daytona_cls(config)

    @staticmethod
    def _field(value: Any, keys: tuple[str, ...]) -> Any:
        if isinstance(value, dict):
            for key in keys:
                if key in value:
                    return value[key]
            return None
        for key in keys:
            if hasattr(value, key):
                return getattr(value, key)
        return None

    @staticmethod
    def _call_with_variants(target: Any, variants: list[tuple[tuple[Any, ...], dict[str, Any]]]) -> Any:
        last_error: Exception | None = None
        for args, kwargs in variants:
            try:
                return target(*args, **kwargs)
            except TypeError as exc:
                last_error = exc
                continue
        if last_error is None:
            raise RuntimeError("No call variants were available.")
        raise last_error

    @classmethod
    def _to_text(cls, value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            return value
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        if isinstance(value, (int, float, bool)):
            return str(value)
        if isinstance(value, list):
            parts = [cls._to_text(item) for item in value]
            non_empty = [part for part in parts if part]
            if non_empty:
                return "\n".join(non_empty)
            return json.dumps(value, ensure_ascii=False, default=str)
        if isinstance(value, dict):
            for key in ("stdout", "output", "result", "text", "message", "content"):
                if key in value:
                    nested = cls._to_text(value.get(key))
                    if nested:
                        return nested
            return json.dumps(value, ensure_ascii=False, default=str)

        for key in ("stdout", "output", "result", "text", "message", "content"):
            if hasattr(value, key):
                nested = cls._to_text(getattr(value, key))
                if nested:
                    return nested
        if hasattr(value, "additional_properties"):
            nested = cls._to_text(getattr(value, "additional_properties"))
            if nested:
                return nested
        return str(value)

    def _get_sandbox(self, sandbox_id: str) -> Any:
        getter = getattr(self._client, "get", None)
        if callable(getter):
            return getter(sandbox_id)

        sandboxes = getattr(self._client, "sandboxes", None)
        if sandboxes is not None and hasattr(sandboxes, "get"):
            return sandboxes.get(sandbox_id)

        raise RuntimeError("Daytona client does not expose sandbox getter.")

    @staticmethod
    def _get_fs(sandbox: Any) -> Any:
        fs = getattr(sandbox, "fs", None)
        if fs is None:
            raise RuntimeError("Sandbox does not expose file system operations.")
        return fs

    def create_sandbox(self, language: str) -> str:
        creator = getattr(self._client, "create", None)
        if not callable(creator):
            raise RuntimeError("Daytona client does not expose sandbox creation.")

        create_params_classes = [
            getattr(self._module, "CreateSandboxFromSnapshotParams", None),
            getattr(self._module, "CreateSandboxBaseParams", None),
            getattr(self._module, "CreateSandboxParams", None),
        ]
        variants: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        for create_params_cls in create_params_classes:
            if create_params_cls is None:
                continue
            for kwargs in ({"language": language}, {"lang": language}):
                try:
                    variants.append(((create_params_cls(**kwargs),), {}))
                except (TypeError, ValueError):
                    continue
        variants.extend(
            [
                ((), {"language": language}),
                ((), {"lang": language}),
                (({"language": language},), {}),
            ]
        )

        sandbox = self._call_with_variants(creator, variants)
        sandbox_id = self._field(sandbox, ("id", "sandbox_id", "sandboxId"))
        if sandbox_id is None:
            raise RuntimeError("Unable to resolve sandbox id from Daytona response.")
        return str(sandbox_id)

    def delete_sandbox(self, sandbox_id: str) -> None:
        deleter = getattr(self._client, "delete", None)
        if callable(deleter):
            try:
                sandbox = self._get_sandbox(sandbox_id)
                deleter(sandbox)
            except Exception:
                deleter(sandbox_id)
            return

        sandboxes = getattr(self._client, "sandboxes", None)
        if sandboxes is not None and hasattr(sandboxes, "delete"):
            sandboxes.delete(sandbox_id)
            return

        raise RuntimeError("Daytona client does not expose sandbox deletion.")

    def run_code(self, sandbox_id: str, language: str, code: str) -> dict[str, Any]:
        sandbox = self._get_sandbox(sandbox_id)
        if language == "python":
            interpreter = getattr(sandbox, "code_interpreter", None) or getattr(sandbox, "codeInterpreter", None)
            if interpreter is not None:
                run_interpreter = getattr(interpreter, "run_code", None) or getattr(interpreter, "runCode", None)
                if callable(run_interpreter):
                    try:
                        result = self._call_with_variants(
                            run_interpreter,
                            [((code,), {}), ((), {"code": code})],
                        )
                        stdout_text = self._to_text(self._field(result, ("stdout", "output", "result"))) or ""
                        stderr_text = self._to_text(self._field(result, ("stderr",))) or ""
                        error_value = self._field(result, ("error",))
                        error_text = self._to_text(error_value)
                        if error_text:
                            stderr_text = f"{stderr_text}\n{error_text}".strip()
                        success = not bool(error_value)
                        exit_code = 0 if success else 1
                        status = "completed" if success else "failed"
                        output = stdout_text if stdout_text else stderr_text
                        return {
                            "stdout": stdout_text,
                            "stderr": stderr_text,
                            "code": exit_code,
                            "status": status,
                            "output": output,
                        }
                    except Exception:
                        # Fall back to process.code_run mapping below.
                        pass

        process = getattr(sandbox, "process", None)
        if process is None:
            raise RuntimeError("Sandbox does not expose process API.")
        code_run = getattr(process, "code_run", None)
        if not callable(code_run):
            raise RuntimeError("Sandbox process does not expose code_run().")

        code_run_params_cls = getattr(self._module, "CodeRunParams", None)
        variants: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        if code_run_params_cls is not None:
            for kwargs in ({"language": language}, {"lang": language}):
                try:
                    variants.append(((code,), {"params": code_run_params_cls(**kwargs)}))
                except TypeError:
                    continue
        variants.extend(
            [
                ((code,), {"language": language}),
                ((code,), {"lang": language}),
                ((code,), {}),
            ]
        )

        result = self._call_with_variants(code_run, variants)
        stdout = self._field(result, ("stdout", "out", "standard_output", "standardOutput"))
        stderr = self._field(result, ("stderr", "err", "standard_error", "standardError"))
        result_text = self._field(result, ("result",))
        artifacts = self._field(result, ("artifacts",))
        exit_code = self._field(result, ("code", "exit_code", "exitCode", "return_code", "returnCode"))
        status = self._field(result, ("status", "state"))
        output = self._field(result, ("output",))

        if isinstance(exit_code, str) and exit_code.lstrip("-").isdigit():
            exit_code = int(exit_code)

        stdout_text = self._to_text(stdout) or ""
        stderr_text = self._to_text(stderr) or ""
        result_output_text = self._to_text(result_text) or ""
        artifacts_stdout_text = self._to_text(self._field(artifacts, ("stdout", "output", "result")))
        output_text = self._to_text(output) or ""

        if not stdout_text and result_output_text:
            stdout_text = result_output_text
        if not stdout_text and output_text:
            stdout_text = output_text
        if not stdout_text and artifacts_stdout_text:
            stdout_text = artifacts_stdout_text
        if not stdout_text:
            # Last-resort fallback for SDK responses where output is only present in nested metadata.
            result_fallback_text = self._to_text(result)
            if result_fallback_text and result_fallback_text not in {"{}", "[]"}:
                stdout_text = result_fallback_text

        if not output_text and stdout_text:
            output_text = stdout_text
        if output is None:
            output = output_text
        if status is None:
            status = "completed" if exit_code in (None, 0) else "failed"

        return {
            "stdout": stdout_text,
            "stderr": stderr_text,
            "code": exit_code if isinstance(exit_code, int) or exit_code is None else None,
            "status": str(status),
            "output": output,
        }

    def upload_file(self, sandbox_id: str, destination_path: str, content: bytes) -> None:
        sandbox = self._get_sandbox(sandbox_id)
        fs = self._get_fs(sandbox)
        path = normalize_workspace_path(destination_path)
        bytes_io = BytesIO(content)

        direct_attempts = [
            ("upload_file", (content, path), {}),
            ("upload_file", (path, content), {}),
            ("upload_file", (), {"path": path, "data": content}),
            ("upload_file", (), {"remote_path": path, "data": content}),
            ("upload_file", (), {"destination": path, "data": content}),
            ("upload_file", (), {"path": path, "content": content}),
            ("upload", (path, content), {}),
            ("upload", (), {"path": path, "data": content}),
            ("write_file", (path, content), {}),
            ("write", (path, content), {}),
        ]

        for method_name, args, kwargs in direct_attempts:
            method = getattr(fs, method_name, None)
            if not callable(method):
                continue
            try:
                method(*args, **kwargs)
                return
            except TypeError:
                continue

        stream_attempts = [
            ("upload_file", (), {"path": path, "file": bytes_io}),
            ("upload_file", (), {"remote_path": path, "file": bytes_io}),
            ("upload", (), {"path": path, "file": bytes_io}),
        ]
        for method_name, args, kwargs in stream_attempts:
            method = getattr(fs, method_name, None)
            if not callable(method):
                continue
            bytes_io.seek(0)
            try:
                method(*args, **kwargs)
                return
            except TypeError:
                continue

        temp_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(delete=False) as tmp_file:
                tmp_file.write(content)
                temp_path = tmp_file.name

            local_path_attempts = [
                ("upload_file", (temp_path, path), {}),
                ("upload_file", (), {"local_path": temp_path, "remote_path": path}),
                ("upload_file", (), {"source": temp_path, "destination": path}),
                ("upload", (temp_path, path), {}),
                ("upload", (), {"local_path": temp_path, "remote_path": path}),
                ("put_file", (temp_path, path), {}),
            ]
            for method_name, args, kwargs in local_path_attempts:
                method = getattr(fs, method_name, None)
                if not callable(method):
                    continue
                try:
                    method(*args, **kwargs)
                    return
                except TypeError:
                    continue
        finally:
            if temp_path and os.path.exists(temp_path):
                os.unlink(temp_path)

        raise RuntimeError("Unable to upload file with available Daytona SDK methods.")

    def list_files(self, sandbox_id: str, directory: str = "/workspace") -> list[FileEntry]:
        sandbox = self._get_sandbox(sandbox_id)
        fs = self._get_fs(sandbox)
        target_dir = normalize_workspace_path(directory)

        attempts = [
            ("list_files", (target_dir,), {}),
            ("list_files", (), {"path": target_dir}),
            ("list", (target_dir,), {}),
            ("list", (), {"path": target_dir}),
            ("list_directory", (target_dir,), {}),
            ("ls", (target_dir,), {}),
        ]

        raw_entries: Any = None
        for method_name, args, kwargs in attempts:
            method = getattr(fs, method_name, None)
            if not callable(method):
                continue
            try:
                raw_entries = method(*args, **kwargs)
                break
            except TypeError:
                continue

        if raw_entries is None:
            raise RuntimeError("Unable to list files with available Daytona SDK methods.")

        if isinstance(raw_entries, dict):
            for key in ("files", "items", "entries", "data"):
                if isinstance(raw_entries.get(key), list):
                    raw_entries = raw_entries[key]
                    break

        if not isinstance(raw_entries, list):
            if hasattr(raw_entries, "__iter__"):
                raw_entries = list(raw_entries)
            else:
                raw_entries = [raw_entries]

        files: list[FileEntry] = []
        for item in raw_entries:
            path = self._field(item, ("path", "full_path", "fullPath", "location"))
            name = self._field(item, ("name", "filename"))
            size = self._field(item, ("size", "bytes"))
            is_dir = bool(self._field(item, ("is_dir", "isDir", "directory")))
            item_type = self._field(item, ("type",))
            if isinstance(item_type, str) and item_type.lower() in {"dir", "directory"}:
                is_dir = True
            if is_dir:
                continue

            if not path and name:
                path = f"{target_dir}/{name}"
            if not path:
                continue

            normalized_path = normalize_workspace_path(str(path))
            file_name = str(name) if name else PurePosixPath(normalized_path).name
            parsed_size = None
            if isinstance(size, int):
                parsed_size = size
            elif isinstance(size, str) and size.isdigit():
                parsed_size = int(size)
            files.append(FileEntry(path=normalized_path, name=file_name, size=parsed_size))

        files.sort(key=lambda item: item.path)
        return files

    def download_file(self, sandbox_id: str, path: str) -> bytes:
        sandbox = self._get_sandbox(sandbox_id)
        fs = self._get_fs(sandbox)
        normalized = normalize_workspace_path(path)

        attempts = [
            ("download_file", (normalized,), {}),
            ("download_file", (), {"path": normalized}),
            ("download", (normalized,), {}),
            ("download", (), {"path": normalized}),
            ("read_file", (normalized,), {}),
            ("read", (normalized,), {}),
            ("get_file", (normalized,), {}),
        ]
        for method_name, args, kwargs in attempts:
            method = getattr(fs, method_name, None)
            if not callable(method):
                continue
            try:
                content = method(*args, **kwargs)
            except TypeError:
                continue
            if isinstance(content, bytes):
                return content
            if isinstance(content, str):
                if os.path.exists(content):
                    with open(content, "rb") as fp:
                        return fp.read()
                return content.encode("utf-8")
            if hasattr(content, "read"):
                data = content.read()
                if isinstance(data, bytes):
                    return data
                if isinstance(data, str):
                    return data.encode("utf-8")

        temp_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(delete=False) as tmp_file:
                temp_path = tmp_file.name

            local_attempts = [
                ("download_file", (normalized, temp_path), {}),
                ("download_file", (), {"remote_path": normalized, "local_path": temp_path}),
                ("download", (normalized, temp_path), {}),
                ("download", (), {"remote_path": normalized, "local_path": temp_path}),
                ("get_file", (normalized, temp_path), {}),
            ]
            for method_name, args, kwargs in local_attempts:
                method = getattr(fs, method_name, None)
                if not callable(method):
                    continue
                try:
                    method(*args, **kwargs)
                except TypeError:
                    continue
                if os.path.exists(temp_path):
                    with open(temp_path, "rb") as fp:
                        return fp.read()
        finally:
            if temp_path and os.path.exists(temp_path):
                os.unlink(temp_path)

        raise RuntimeError("Unable to download file with available Daytona SDK methods.")

    def delete_file(self, sandbox_id: str, path: str) -> None:
        sandbox = self._get_sandbox(sandbox_id)
        fs = self._get_fs(sandbox)
        normalized = normalize_workspace_path(path)

        attempts = [
            ("delete_file", (normalized,), {}),
            ("delete_file", (), {"path": normalized}),
            ("delete", (normalized,), {}),
            ("remove", (normalized,), {}),
            ("rm", (normalized,), {}),
        ]

        for method_name, args, kwargs in attempts:
            method = getattr(fs, method_name, None)
            if not callable(method):
                continue
            try:
                method(*args, **kwargs)
                return
            except TypeError:
                continue

        raise RuntimeError("Unable to delete file with available Daytona SDK methods.")
