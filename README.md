# LibreChat Daytona Code Interpreter Adapter

FastAPI service that exposes a LibreChat Code Interpreter-compatible HTTP API and executes code/files inside Daytona sandboxes.

Flow:

`LibreChat -> HTTP adapter (this service) -> Daytona sandbox`

No MCP implementation is included.

## Features

- `POST /exec` for code execution (`py|python`, `js|javascript`, `ts|typescript`)
- `POST /upload` for multipart file uploads
- `GET /files/{session_id}` for workspace listing
- `GET /download/{session_id}/{fileId}` for file streaming
- `DELETE /files/{session_id}/{fileId}` for file deletion
- `GET /healthz` health probe
- API key auth via `x-api-key`
- Session mapping backed by Redis (`REDIS_URL`) or in-memory fallback
- Idle sandbox cleanup (`SESSION_TTL_SECONDS`)
- Path traversal protection (confined to `/workspace`)
- Upload size limit (`UPLOAD_MAX_BYTES`, default 20 MB)

## Environment Variables

Copy `.env.example` to `.env` and set values:

- `ADAPTER_API_KEY` (required)
- `DAYTONA_API_KEY` (required)
- `DAYTONA_API_URL` (optional)
- `REDIS_URL` (optional)
- `SESSION_TTL_SECONDS` (optional, default `1800`)
- `UPLOAD_MAX_BYTES` (optional, default `20971520`)
- `CLEANUP_INTERVAL_SECONDS` (optional, default `60`)

## Python Version

Use Python **3.12** for local runtime compatibility with `daytona-sdk` and for parity with the Docker image.

## Run Locally (Python)

```bash
python -m venv .venv
. .venv/Scripts/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

## Run with Docker

```bash
docker build -t librechat-daytona-adapter .
docker run --rm -p 8000:8000 --env-file .env librechat-daytona-adapter
```

## Run with Docker Compose

Adapter only:

```bash
docker compose up --build
```

Adapter + Redis:

```bash
docker compose --profile redis up --build
```

If Redis is enabled, set `REDIS_URL` in `.env` (for compose, typically `redis://redis:6379/0`).

## LibreChat Configuration

Point LibreChat to this adapter:

- `LIBRECHAT_CODE_BASEURL=http://<adapter-host>`
- `LIBRECHAT_CODE_API_KEY=<same ADAPTER_API_KEY value>`

## API Examples

Run code:

```bash
curl -X POST "http://localhost:8000/exec" \
  -H "Content-Type: application/json" \
  -H "x-api-key: $ADAPTER_API_KEY" \
  -d '{"code":"print(2+2)","lang":"python"}'
```

Upload files:

```bash
curl -X POST "http://localhost:8000/upload" \
  -H "x-api-key: $ADAPTER_API_KEY" \
  -F "files=@./example.csv"
```

List files:

```bash
curl -H "x-api-key: $ADAPTER_API_KEY" \
  "http://localhost:8000/files/<session_id>"
```

Download file:

```bash
curl -L -H "x-api-key: $ADAPTER_API_KEY" \
  "http://localhost:8000/download/<session_id>/<fileId>" \
  -o downloaded.bin
```

Delete file:

```bash
curl -X DELETE -H "x-api-key: $ADAPTER_API_KEY" \
  "http://localhost:8000/files/<session_id>/<fileId>"
```

Health:

```bash
curl http://localhost:8000/healthz
```

## Tests

```bash
pytest
```

Tests use a mocked Daytona gateway and verify:

- auth requirement
- session creation in `/exec`
- upload/list/download/delete flow
- upload size limit handling
- language mismatch behavior (`409`)
