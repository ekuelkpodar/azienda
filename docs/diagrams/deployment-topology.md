# Deployment topology (docker-compose, local dev)

Target DX: `cp .env.example .env && docker compose up --build`. The frontend SPA is built
into the API image and served same-origin — one artifact, no CORS, cookies work.

```mermaid
flowchart TB
    DEV([developer / browser]) -->|":8000"| API

    subgraph HOST["docker compose — single host"]
        subgraph IMG["api image (multi-stage)"]
            FEBUILD["frontend-builder<br/>node:22 — npm run build"]
            PYBASE["python:3.12-slim + uv<br/>backend deps"]
            RUNTIME["runtime<br/>uvicorn app.main:app<br/>+ /app/static (SPA)"]
            FEBUILD --> RUNTIME
            PYBASE --> RUNTIME
        end
        API["api<br/>FastAPI · serves /api/v1 + SPA fallback"]
        WORKER["worker<br/>ARQ — background jobs"]
        PG[("postgres:16<br/>pgvector/pgvector:pg16<br/>system of record")]
        REDIS[("redis:7<br/>cache · queue · rate limits<br/>denylist · locks")]
        MINIO[("minio<br/>S3-compatible<br/>uploads · exports · artifacts")]
    end

    RUNTIME -.->|builds| API
    RUNTIME -.->|builds| WORKER
    API --> PG
    API --> REDIS
    API --> MINIO
    WORKER --> PG
    WORKER --> REDIS
    WORKER --> MINIO

    DEV -.->|":9001 (console)"| MINIO

    style PG fill:#bfdbfe,stroke:#1e40af
    style API fill:#bbf7d0,stroke:#166534
```

## Notes

- Postgres, Redis, MinIO are **not** publicly exposed in production — ports are dev-only.
- Healthchecks gate `api`/`worker` startup on `postgres`/`redis`.
- CI builds the same image (`docker build -t azienda:ci .`).
- Production step-ups (managed Postgres/Redis, object storage provider, TLS at edge,
  OTel collector) are compose/env changes, not code changes. K8s is explicitly deferred (ADR-001).
