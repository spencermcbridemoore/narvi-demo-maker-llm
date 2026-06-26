# DemoBuilder backend image — FastAPI + LangGraph (orchestration + SSE only;
# gpt-5 inference runs on Azure, so this needs no GPU and no local model).
# Build context is the REPO ROOT (see deploy/docker-compose.yml).
FROM python:3.12-slim

# ca-certificates so truststore (the OS trust store, injected in app/config.py)
# can verify Azure OpenAI's public TLS chain inside the container.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install pinned deps first so this layer caches across code changes.
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install -r backend/requirements.txt

# App code ONLY — no secrets. `.env` is git-ignored and excluded by .dockerignore,
# and is never COPYed; it is injected at runtime via compose `env_file`.
COPY backend/ ./backend/

# SQLite checkpoint dir; mounted as a named volume in compose so state survives
# container recreate.
RUN mkdir -p backend/app/data

EXPOSE 8000

# Single uvicorn worker (the SQLite checkpointer is single-writer) — identical
# app entrypoint to local `python run.py`.
CMD ["uvicorn", "app.main:app", "--app-dir", "backend", "--host", "0.0.0.0", "--port", "8000"]
