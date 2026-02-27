from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from fastapi.testclient import TestClient

os.environ.setdefault("ADAPTER_API_KEY", "import-time-api-key")
os.environ.setdefault("DAYTONA_API_KEY", "import-time-daytona-key")

from app.config import Settings
from app.main import create_app
from app.session_store import MemorySessionStore


@dataclass
class FakeSandbox:
    language: str
    files: dict[str, bytes]


class FakeDaytonaGateway:
    def __init__(self) -> None:
        self._sandboxes: dict[str, FakeSandbox] = {}
        self._counter = 0

    def create_sandbox(self, language: str) -> str:
        self._counter += 1
        sandbox_id = f"sandbox-{self._counter}"
        self._sandboxes[sandbox_id] = FakeSandbox(language=language, files={})
        return sandbox_id

    def delete_sandbox(self, sandbox_id: str) -> None:
        self._sandboxes.pop(sandbox_id, None)

    def run_code(self, sandbox_id: str, language: str, code: str) -> dict[str, Any]:
        sandbox = self._sandboxes[sandbox_id]
        return {
            "stdout": f"ran:{language}:{code}",
            "stderr": "",
            "code": 0,
            "status": "completed",
            "output": f"ran:{language}:{code}",
        }

    def upload_file(self, sandbox_id: str, destination_path: str, content: bytes) -> None:
        sandbox = self._sandboxes[sandbox_id]
        sandbox.files[destination_path] = content

    def list_files(self, sandbox_id: str, _: str = "/workspace") -> list[dict[str, Any]]:
        sandbox = self._sandboxes[sandbox_id]
        return [
            {
                "path": path,
                "name": PurePosixPath(path).name,
                "size": len(content),
            }
            for path, content in sorted(sandbox.files.items())
        ]

    def download_file(self, sandbox_id: str, path: str) -> bytes:
        sandbox = self._sandboxes[sandbox_id]
        return sandbox.files[path]

    def delete_file(self, sandbox_id: str, path: str) -> None:
        sandbox = self._sandboxes[sandbox_id]
        sandbox.files.pop(path, None)


def make_client() -> tuple[TestClient, FakeDaytonaGateway]:
    settings = Settings(
        ADAPTER_API_KEY="test-adapter-key",
        DAYTONA_API_KEY="test-daytona-key",
        SESSION_TTL_SECONDS=1800,
        CLEANUP_INTERVAL_SECONDS=60,
    )
    gateway = FakeDaytonaGateway()
    app = create_app(
        settings=settings,
        store=MemorySessionStore(),
        gateway=gateway,
        enable_cleanup=False,
    )
    return TestClient(app), gateway


def test_auth_required() -> None:
    client, _ = make_client()
    response = client.post("/exec", json={"code": "print(1)", "lang": "python"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_exec_creates_session() -> None:
    client, _ = make_client()
    response = client.post(
        "/exec",
        headers={"x-api-key": "test-adapter-key"},
        json={"code": "print(1)", "lang": "py"},
    )
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body["session_id"], str)
    assert set(body["run"].keys()) == {"stdout", "stderr", "code", "status", "output"}
    assert isinstance(body["files"], list)


def test_upload_list_download_delete_flow() -> None:
    client, _ = make_client()
    headers = {"x-api-key": "test-adapter-key"}

    upload_response = client.post(
        "/upload",
        headers=headers,
        files=[("files", ("example.txt", b"hello daytona", "text/plain"))],
    )
    assert upload_response.status_code == 200
    upload_body = upload_response.json()
    session_id = upload_body["session_id"]
    file_id = upload_body["files"][0]["id"]
    file_path = upload_body["files"][0]["path"]
    assert file_path == "/workspace/example.txt"

    list_response = client.get(f"/files/{session_id}", headers=headers)
    assert list_response.status_code == 200
    listed = list_response.json()["files"]
    assert len(listed) == 1
    assert listed[0]["path"] == "/workspace/example.txt"

    download_response = client.get(f"/download/{session_id}/{file_id}", headers=headers)
    assert download_response.status_code == 200
    assert download_response.content == b"hello daytona"
    assert "attachment; filename=\"example.txt\"" in download_response.headers["content-disposition"]

    delete_response = client.delete(f"/files/{session_id}/{file_id}", headers=headers)
    assert delete_response.status_code == 200
    assert delete_response.json()["deleted"] is True

    list_after_delete = client.get(f"/files/{session_id}", headers=headers)
    assert list_after_delete.status_code == 200
    assert list_after_delete.json()["files"] == []


def test_upload_rejects_oversized_file() -> None:
    client, _ = make_client()
    headers = {"x-api-key": "test-adapter-key"}
    oversized_payload = b"x" * (20 * 1024 * 1024 + 1)

    response = client.post(
        "/upload",
        headers=headers,
        files=[("files", ("large.bin", oversized_payload, "application/octet-stream"))],
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "file_too_large"


def test_session_language_mismatch_returns_409() -> None:
    client, _ = make_client()
    headers = {"x-api-key": "test-adapter-key"}

    first_response = client.post(
        "/exec",
        headers=headers,
        json={"code": "print(1)", "lang": "python"},
    )
    assert first_response.status_code == 200
    session_id = first_response.json()["session_id"]

    second_response = client.post(
        "/exec",
        headers=headers,
        json={"code": "console.log(1)", "lang": "javascript", "session_id": session_id},
    )
    assert second_response.status_code == 409
    assert second_response.json()["error"]["code"] == "session_language_mismatch"

