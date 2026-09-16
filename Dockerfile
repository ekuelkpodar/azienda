# Azienda — multi-stage: build frontend SPA, install backend, serve same-origin.
ARG PYTHON_VERSION=3.12

# ---- frontend builder ----
FROM node:22-alpine AS frontend-builder
WORKDIR /fe
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci --no-audit --no-fund || npm install --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---- backend deps ----
FROM python:${PYTHON_VERSION}-slim AS backend-base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_SYSTEM_PYTHON=1
RUN pip install --no-cache-dir uv
WORKDIR /app
COPY backend/pyproject.toml ./
RUN uv pip install --no-cache -r pyproject.toml

# ---- runtime ----
FROM backend-base AS runtime
COPY backend/app ./app
COPY --from=frontend-builder /fe/dist ./static
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
