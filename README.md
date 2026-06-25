# narvi-demo-maker-llm
End user non-coding agentic AI workflow to design and build interactive STEM demos in realtime.

**DemoBuilder** lets non-programmers (educators, students) create self-contained,
PhET-style interactive HTML demos through a guided, conversational,
human-in-the-loop workflow. You describe what you want in plain language; the
agent proposes a plan, generates a live demo, and iterates with you until it's
right — then hands you a single `.html` file you can publish to GitHub Pages or
email.

> **Status: Stage 4 — feature-complete & polished.**
> The full workflow (Elicit → Scope → confirm → Generate → Review → Diagnose →
> Fix → Extend → Finalize, with the Review↔Fix and Extend→Scope loops) runs
> through the real UI, backed by a real model. Providers are config-selected
> (`azure | ollama | openai`, decoupled from environment); a real non-fallback
> demo was generated through both Azure (gpt-4o) and local Ollama. Polish:
> spec-named `.html` download, error handling (backend-down banner + retry),
> placeholder/fallback surfacing, active-provider badge, "New demo" restart, and a
> pytest suite. See [Roadmap](#roadmap).

---

## What you see

A single page with three coordinated panels:

1. **Chat** — the agent asks one question per stage; you answer in plain language.
2. **Workflow** — a live flowchart of the graph that highlights *where you are*
   and shows when the system is waiting on you. The topology comes from the real
   graph (`graph.get_graph().draw_mermaid()`); the highlight comes from runtime
   state.
3. **Preview** — the generated demo rendered live in a sandboxed
   `<iframe srcDoc=… sandbox="allow-scripts">`, so you can interact with it and
   then describe what to change.

## Architecture

```
┌─────────────────────────── Browser (React + Vite + TS) ───────────────────────────┐
│  CopilotKit v2 provider  ─ HttpAgent ─►  POST /agent  (AG-UI SSE)                   │
│   • useAgent      → live shared state (STATE_SNAPSHOT / STATE_DELTA) → panels       │
│   • useInterrupt  → renders the agent's questions; resolve() resumes the graph      │
│   • mermaid       → renders /mermaid topology, highlights the active node           │
│   • <iframe srcDoc sandbox="allow-scripts">  → isolated demo preview                │
└───────────────────────────────────────┬────────────────────────────────────────────┘
                                         │  HTTP / SSE  (CORS: localhost:5173)
┌────────────────────────────────────────▼───────────────────────────────────────────┐
│  Backend — FastAPI (Python only, no Node runtime)                                   │
│   ag-ui-langgraph adapter  →  runs the CompiledStateGraph IN-PROCESS,               │
│                               emits AG-UI events (state deltas + interrupts)         │
│   LangGraph StateGraph:  START → elicit → generate_stub → END                        │
│   AsyncSqliteSaver checkpointer (thread_id per session)  →  data/checkpoints.sqlite  │
│   LLM factory (role → model; provider profiles: azure / ollama / openai)             │
└──────────────────────────────────────────────────────────────────────────────────────┘
```

**Why Python-only is possible.** The backend uses the
[`ag-ui-langgraph`](https://pypi.org/project/ag-ui-langgraph/) adapter, which runs
the compiled graph in-process and exposes it over an AG-UI SSE endpoint —
emitting `STATE_SNAPSHOT` / `STATE_DELTA` and `on_interrupt` events with no
JSON-Patch bookkeeping on our side. The React app connects **directly** to that
endpoint via CopilotKit v2's `agents__unsafe_dev_only` prop + an `@ag-ui/client`
`HttpAgent` — **no Node `CopilotRuntime`**.

> ⚠️ **Production note.** `agents__unsafe_dev_only` is the shipped *dev/prototype*
> no-Node path. For production, switch to `selfManagedAgents` and secure the
> FastAPI `/agent` endpoint yourself (auth, CORS, rate limiting), **or** put a
> thin Node `CopilotRuntime` in front for CopilotKit's managed features. The
> backend stays 100% Python in every case.

### Stack & pinned versions

All dependencies are permissively licensed (MIT / BSD-3 / Apache-2.0) — no
copyleft. (`@ag-ui/*` and `aiosqlite` report a *null* license field in registry
metadata but are MIT; allowlist them if your SCA gate flags "unknown".)

| Backend (PyPI)                | Pin            | Frontend (npm)              | Pin       |
|-------------------------------|----------------|-----------------------------|-----------|
| langgraph                     | 1.2.6          | react / react-dom           | ^19.2.7   |
| langgraph-checkpoint-sqlite   | 3.1.0          | vite                        | ^8.0.16   |
| ag-ui-langgraph[fastapi]      | 0.0.42         | typescript                  | ~6.0.2    |
| ag-ui-protocol                | 0.1.19         | @copilotkit/react-core      | 1.61.0    |
| fastapi                       | 0.137.2        | @copilotkit/react-ui        | 1.61.0    |
| uvicorn[standard]             | 0.49.0         | @ag-ui/client               | 0.0.57    |
| sse-starlette                 | 3.4.4          | mermaid                     | ^11.15.0  |
| langchain-openai / -ollama    | 1.3.2 / 1.1.0  |                             |           |

## Prerequisites

- **Python 3.11+** (tested on 3.12)
- **Node 20.19+ or 22.12+** (Vite 8 requirement; tested on 22.20)
- *(Stage 3+ only)* **Ollama** for local LLM dev, or Azure OpenAI credentials

## Setup

```bash
# 1) Backend: virtualenv + pinned deps
python -m venv .venv
# Windows:
.venv\Scripts\pip install -r backend\requirements.txt
# macOS / Linux:
.venv/bin/pip install -r backend/requirements.txt

# 2) Frontend deps
npm --prefix frontend install

# 3) Config (optional for Stage 1 — no LLM needed)
cp .env.example .env
```

## Run

One command starts both servers:

```bash
python run.py
```

Then open **http://localhost:5173**. (Backend serves on http://localhost:8000.)

On macOS/Linux you can also use `make dev`. To run the servers separately (e.g.
backend with autoreload):

```bash
.venv/bin/python -m uvicorn app.main:app --app-dir backend --reload --port 8000
npm --prefix frontend run dev
```

### Try the loop

1. The agent asks what you want to build (the **elicit** node is highlighted and
   "waiting on you" in the flowchart).
2. Type a description and **Send**.
3. The graph resumes, generates the demo, and the **Preview** panel renders it;
   the flowchart highlight advances to the end.

## Model-role config

`backend/app/llm/models.yaml` maps **roles → tiers → concrete models**, organized
by **provider** (not by environment — the two are orthogonal). `LLM_PROFILE`
(`azure | ollama | openai`) picks which provider is active; per-role/tier swaps are
a one-line change. Where inference runs only affects cost/latency/quality/secrets,
never the workflow's behavior or output.

| Role / stage           | Tier   | Why                                  |
|------------------------|--------|--------------------------------------|
| routing, scope         | cheap  | high call volume, low difficulty     |
| review, diagnose       | mid    | balance                              |
| generation, fix        | strong | code quality matters most            |

The factory (`backend/app/llm/factory.py`) returns a `BaseChatModel` per role, so
graph nodes stay provider-agnostic. Config values of the form `env:NAME` are read
from the environment at runtime, so secrets-adjacent values (Azure deployment
names) live in `.env` (`AZURE_DEPLOYMENT_CHEAP` / `_MID` / `_STRONG`) rather than
in `models.yaml`.

> **Behind a TLS-intercepting proxy?** The backend injects
> [`truststore`](https://pypi.org/project/truststore/) at startup so HTTPS
> provider calls (e.g. Azure OpenAI) validate against the OS trust store instead
> of certifi's bundle — otherwise they fail with `CERTIFICATE_VERIFY_FAILED` and
> silently fall back to placeholder demos. It's a no-op on machines without an
> intercepting proxy.

## Project structure

```
backend/
  app/
    main.py            FastAPI app: lifespan, CORS, AG-UI /agent mount, aux routes
    config.py          env-driven settings
    graph/
      state.py         DemoState (spec, plan, html, issues, stage, done)
      nodes.py         the 9 nodes (elicit, scope, plan_review, generate,
                       review, diagnose, fix, extend, finalize)
      build.py         StateGraph assembly + conditional/cyclic edges
      templates.py     deterministic fallbacks (used when no LLM is reachable)
    llm/
      factory.py       role → model factory (azure / ollama / openai)
      models.yaml      role/tier/model mapping
      prompts.py       prompt templates for Scope/Generate/Fix
      complete.py      LLM call helper (returns None → node uses fallback)
    data/              checkpoints.sqlite (runtime, git-ignored)
  tests/               pytest suite (graph routing, llm helpers) — runs offline
  requirements.txt     pinned deps  (+ requirements-dev.txt for pytest)
  pyproject.toml
frontend/
  src/
    main.tsx, App.tsx, providers.tsx, config.ts
    components/        ChatPanel, FlowPanel, PreviewPanel
  package.json, vite.config.ts, tsconfig*.json
run.py                 one-command launcher (backend + frontend)
.env.example
```

## Backend API (for reference)

| Method | Path                          | Purpose                                       |
|--------|-------------------------------|-----------------------------------------------|
| POST   | `/agent`                      | AG-UI SSE stream (state deltas + interrupts)  |
| GET    | `/agent/health`               | adapter health                                |
| GET    | `/health`                     | app health                                    |
| GET    | `/mermaid`                    | real graph topology (`draw_mermaid()`)        |
| GET    | `/thread/{id}/status`         | `next` / current stage / pending interrupt    |
| GET    | `/thread/{id}/artifact`       | current generated HTML (JSON)                 |
| GET    | `/thread/{id}/download`       | current artifact as a downloadable `.html`    |
| GET    | `/config`                     | active provider + model per role (no secrets) |

## Testing

```bash
.venv\Scripts\pip install -r backend\requirements-dev.txt   # adds pytest
pytest backend/tests
```

The suite forces the LLM offline (deterministic fallbacks), so it runs with no
network/provider and covers interrupt/resume, the conditional/cyclic routing,
`extract_html`, the fallback templates, and `env:` resolution.

## Troubleshooting

- **The Preview shows a "placeholder — no LLM reached" banner** (or no provider
  badge in the header). The chosen provider isn't reachable, so the workflow fell
  back to a built-in template. Check `GET /config` for the active provider/models,
  then: is the provider running (Ollama)? Are the Azure deployment names / api-key
  right? Behind a proxy (see below)?
- **Azure calls fail with `CERTIFICATE_VERIFY_FAILED`** behind a TLS-intercepting
  proxy. The app injects `truststore` to use the OS trust store; ensure the
  corporate root CA is installed in the OS store. For the `az` CLI, set
  `REQUESTS_CA_BUNDLE` to the exported root CA.
- **"Can't reach the backend" banner.** The backend isn't running — start it with
  `python run.py` and click **Retry**.
- **Local Ollama is slow.** Big models are CPU-bound; `ollama pull qwen2.5-coder:7b`
  is faster and better at HTML, or switch to `LLM_PROFILE=azure`.

## Roadmap

- **Stage 1 ✅** Walking skeleton: 2-node graph, interrupt/resume, flowchart from
  the real graph, sandboxed preview — all end-to-end, Python-only.
- **Stage 2 ✅** All nodes (Elicit → Scope → Generate → Review → Diagnose → Fix →
  Extend → Finalize) with the conditional/cyclic edges, real prompts, choice/text
  interrupts, conversation transcript, and `.html` download. Runs with or without
  a model via deterministic fallbacks.
- **Stage 3 ✅** Real LLM wiring verified end-to-end: provider-based profiles
  (azure/ollama/openai, decoupled from environment), Azure deployments sourced from
  `.env`, OS-trust-store TLS via `truststore`. Confirmed a real (non-fallback) demo
  generated through both Azure (gpt-4o, ~8s) and local Ollama (qwen2.5, slower).
- **Stage 4 ✅** Polish: `.html` download (spec-named) + `/download` endpoint,
  frontend error handling (backend-down banner + retry, flowchart recovery),
  fallback/placeholder surfacing, active-provider badge, "New demo" restart, and a
  pytest suite.

## License

MIT — see [LICENSE](LICENSE). Dependencies are MIT / BSD-3 / Apache-2.0.
