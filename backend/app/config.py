"""Central configuration for the DemoBuilder backend.

Everything environment-specific (which LLM provider, allowed origins,
checkpoint location) is read from the environment here so the rest of the code
never touches ``os.environ`` directly. Secrets come from a repo-root ``.env``
(see ``.env.example``); ``.env`` is git-ignored and must never be committed.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Use the operating system's trust store for TLS verification. This machine sits
# behind a TLS-intercepting proxy whose root CA is in the Windows store but NOT in
# certifi's bundle — without this, outbound HTTPS (e.g. Azure OpenAI) fails with
# CERTIFICATE_VERIFY_FAILED and the LLM call silently falls back to a placeholder.
# Injecting at import time (before any httpx client is built) fixes it; it's a
# harmless no-op on machines without an intercepting proxy.
try:
    import truststore

    truststore.inject_into_ssl()
except Exception:  # truststore optional — fall back to certifi
    pass

# Resolve key paths relative to this file so the app works regardless of CWD.
APP_DIR = Path(__file__).resolve().parent          # backend/app
BACKEND_DIR = APP_DIR.parent                        # backend
REPO_ROOT = BACKEND_DIR.parent                      # repo root

# Load the repo-root .env (if present). Values already in the environment win.
load_dotenv(REPO_ROOT / ".env")

# --- Paths ---------------------------------------------------------------
DATA_DIR = APP_DIR / "data"
CHECKPOINT_DB = Path(
    os.getenv("CHECKPOINT_DB", str(DATA_DIR / "checkpoints.sqlite"))
)

# --- AG-UI / graph -------------------------------------------------------
# The agent id the frontend connects to. Must match the key used in the
# React <CopilotKit agents__unsafe_dev_only={{ [AGENT_NAME]: ... }}> provider.
AGENT_NAME = os.getenv("AGENT_NAME", "demobuilder")

# --- CORS ----------------------------------------------------------------
# The Vite dev server origin. Never use "*" together with credentials.
ALLOWED_ORIGINS = [
    o.strip()
    for o in os.getenv(
        "ALLOWED_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
    ).split(",")
    if o.strip()
]

# --- LLM selection -------------------------------------------------------
# Selects which PROVIDER profile in models.yaml is active: azure | ollama | openai.
# Provider and environment are orthogonal; this only picks where inference runs.
LLM_PROFILE = os.getenv("LLM_PROFILE", "ollama").lower()
MODELS_CONFIG = Path(os.getenv("MODELS_CONFIG", str(APP_DIR / "llm" / "models.yaml")))
