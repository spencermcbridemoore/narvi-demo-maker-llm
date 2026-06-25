"""FastAPI application wrapping the LangGraph workflow.

The app stays 100% Python: the compiled ``StateGraph`` runs in-process and the
``ag-ui-langgraph`` adapter exposes it to the browser over an AG-UI SSE endpoint
(``POST /agent``), automatically emitting ``STATE_SNAPSHOT`` / ``STATE_DELTA`` and
interrupt events. CopilotKit on the frontend connects directly to that endpoint —
no Node runtime.

Everything that needs the compiled graph (which in turn needs the async SQLite
checkpointer, which needs a running event loop) is built inside the lifespan
handler. The auxiliary read-only routes below are declared at module scope and
read ``app.state`` at request time.
"""

from __future__ import annotations

import os
import re
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from ag_ui_langgraph import LangGraphAgent, add_langgraph_fastapi_endpoint

from .config import AGENT_NAME, ALLOWED_ORIGINS, CHECKPOINT_DB, DATA_DIR, LLM_PROFILE
from .graph.build import build_graph


@asynccontextmanager
async def lifespan(app: FastAPI):
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # Long-lived async checkpointer, open for the app's whole lifetime.
    # Production seam: swap these two lines for AsyncPostgresSaver.from_conn_string(DSN).
    async with AsyncSqliteSaver.from_conn_string(str(CHECKPOINT_DB)) as saver:
        graph = build_graph().compile(checkpointer=saver)
        app.state.graph = graph
        # Flowchart topology comes from the REAL graph — never hand-maintained.
        app.state.mermaid = graph.get_graph().draw_mermaid()

        # Mount the AG-UI SSE endpoint at POST /agent (+ GET /agent/health).
        # recursion_limit gives the cyclic Review↔Fix / Extend→Scope loops headroom
        # (interrupts already break runs into small per-resume invocations).
        agent = LangGraphAgent(
            name=AGENT_NAME, graph=graph, config={"recursion_limit": 50}
        )
        add_langgraph_fastapi_endpoint(app, agent, path="/agent")

        yield


app = FastAPI(title="DemoBuilder", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _config(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}


def _collect_interrupts(snapshot) -> list:
    """Pull interrupt payloads from a StateSnapshot (top-level or per-task)."""
    values = list(getattr(snapshot, "interrupts", None) or [])
    if not values:
        for task in getattr(snapshot, "tasks", None) or ():
            values.extend(getattr(task, "interrupts", None) or [])
    return [getattr(it, "value", it) for it in values]


@app.get("/")
async def root():
    """This is the API, not the app. Point a human at the frontend."""
    return {
        "service": "DemoBuilder API",
        "note": "This is the backend API. Open the app in your browser instead:",
        "open_the_app_at": "http://localhost:5173",
        "endpoints": ["/health", "/config", "/mermaid", "POST /agent (AG-UI SSE)"],
        "docs": "/docs",
    }


@app.get("/favicon.ico")
async def favicon():
    return Response(status_code=204)


@app.get("/health")
async def health():
    return {"status": "ok", "agent": AGENT_NAME}


@app.get("/mermaid")
async def mermaid():
    """The graph's Mermaid source, for the frontend flowchart."""
    return {"agent": AGENT_NAME, "mermaid": app.state.mermaid}


@app.get("/thread/{thread_id}/status")
async def status(thread_id: str):
    """Expose ``get_state().next`` + active interrupt so the UI can show the
    current/waiting stage (this is the spec's `.next` / `__interrupt__` seam)."""
    snap = await app.state.graph.aget_state(_config(thread_id))
    prompts = _collect_interrupts(snap)
    values = snap.values or {}
    return {
        "thread_id": thread_id,
        "interrupted": bool(snap.next),
        "next": list(snap.next),
        "stage": values.get("stage"),
        "prompt": prompts[0] if prompts else None,
    }


@app.get("/thread/{thread_id}/artifact")
async def artifact(thread_id: str):
    """Current generated HTML for a thread (download seam for Finalize/Export)."""
    snap = await app.state.graph.aget_state(_config(thread_id))
    return {"thread_id": thread_id, "html": (snap.values or {}).get("html", "")}


def _slug(text: str, default: str = "demo") -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").strip().lower()).strip("-")
    return s[:40] or default


@app.get("/thread/{thread_id}/download")
async def download(thread_id: str):
    """Download the current artifact as a self-contained .html file."""
    snap = await app.state.graph.aget_state(_config(thread_id))
    values = snap.values or {}
    html = values.get("html") or "<!doctype html><title>empty</title>"
    filename = f"{_slug(values.get('spec', ''))}.html"
    return Response(
        content=html,
        media_type="text/html",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/config")
async def config_info():
    """Non-secret view of the active LLM provider + resolved model ids per role.

    Lets the UI show which provider is in use (and helps diagnose the
    "why am I getting placeholders" case). Never returns keys/secrets.
    """
    from .llm.factory import Role, _load_config

    cfg = _load_config()
    block = cfg.get("profiles", {}).get(LLM_PROFILE, {})
    models: dict[str, str] = {}
    for role in Role:
        tier = cfg.get("roles", {}).get(role.value)
        raw = (block.get("tiers") or {}).get(tier, "")
        if isinstance(raw, str) and raw.startswith("env:"):
            raw = os.getenv(raw[4:]) or "<unset>"
        models[role.value] = raw
    return {"profile": LLM_PROFILE, "provider": block.get("provider"), "models": models}
