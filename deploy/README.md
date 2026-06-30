# Deploying DemoBuilder to Jetstream2

A reproducible production deployment using **Docker Compose** as the unit of
deployment. Two containers on one Ubuntu CPU instance:

```
            Internet (HTTPS)
                  │
        ┌─────────▼──────────┐    same-origin, automatic TLS (Let's Encrypt)
        │  caddy  (edge)     │    • serves the built SPA at /
        │  custom image w/   │    • reverse-proxies the API (SSE-safe)
        │  per-IP rate limit │    • per-IP rate limit on /agent
        └─────────┬──────────┘
                  │ backend:8000 (internal compose network only)
        ┌─────────▼──────────┐
        │ backend (uvicorn)  │    FastAPI + LangGraph orchestration + SSE.
        │ python:3.12-slim   │    gpt-5 inference runs on AZURE, not here →
        │ SQLite on a volume │    CPU-only, no GPU, no local model.
        └────────────────────┘
```

The frontend is built with `VITE_BACKEND_URL` empty (`frontend/.env.production`),
so every API call is **relative** → same origin → **no CORS**. This path is
**additive**: local `python run.py` dev is untouched.

> This deploy lives on the `demobuilder-mvp` branch until it's merged to `main`.
> If you provision before merging, clone that branch (the cloud-init has a note).

---

## 0. Prerequisites

- An ACCESS/Jetstream2 allocation and the **Exosphere** web UI (or the OpenStack CLI).
- Your Azure OpenAI resource with **gpt-5** + **gpt-5-mini** deployments and the key.
- A **hostname you control** pointed at the instance's floating IP (for a
  browser-trusted Let's Encrypt cert). No domain? Use a wildcard-DNS hostname like
  `<dashed-floating-ip>.sslip.io` (e.g. `149-165-1-2.sslip.io`) — it resolves to the
  embedded IP, so Let's Encrypt can issue for an IP-only instance.

---

## 1. Cost controls FIRST (this endpoint is open to anyone)

Access is intentionally **open** — no login. The Azure key stays server-side, but
**anyone can drive LLM spend.** You MUST bound it. Do this *before* going public.

> ⚠️ **An Azure Budget ALONE only ALERTS — it does NOT stop spend.** Budgets email
> you after the money is gone. Use the real levers below.

1. **(REQUIRED) Set a low per-deployment TPM/RPM quota** — the real throttle.
   In Azure AI Foundry → your resource → **Deployments** → open `gpt-5` and
   `gpt-5-mini` → **Edit** → set a low **Tokens-Per-Minute (TPM) rate limit**.
   This caps the *burn rate* regardless of traffic. Start conservative (e.g. a few
   thousand TPM) and raise if real usage needs it.
2. **(BUILT IN) Per-IP rate limit on `/agent`** — the only endpoint that triggers
   Azure spend — is enforced by Caddy (`deploy/Caddyfile`, default 40 req/min/IP).
   Cheap defense vs scripted abuse; tune the `events`/`window` there.
3. **(OPTIONAL) Budget-triggered hard-stop.** Wire an Azure **Budget alert** to an
   **Action Group → Logic App / Automation runbook** that *disables the deployment*
   or *rotates the key* when a threshold is hit. This is the only way to truly
   *stop* (not just alert) spend automatically.
4. **Keep per-call output modest** — generation already uses `reasoning_effort:
   minimal`; demos are small single HTML files.

**Accepted tradeoff:** once the TPM quota / rate limit is hit, the app is
unavailable for *everyone* (inherent to an open LLM endpoint). If "open" proves
too open, see [§7 Locking it down](#7-locking-it-down-if-open-is-too-open).

---

## 2. Launch the instance

**Flavor:** `m3.small` = **2 vCPU / 6 GB RAM / 20 GB disk** (plenty — the VM only
orchestrates; gpt-5 runs on Azure). **Image:** Ubuntu 22.04 or 24.04.

**Via Exosphere (easiest):** it auto-assigns a **floating (public) IP** and a
permissive default security group (22/80/443 already open). Paste
[`cloud-init.yaml`](cloud-init.yaml) into the *custom boot script / user-data* box.

**Via the OpenStack CLI** (default is **no ingress** — you must open ports):
```bash
openstack security group create web-access
openstack security group rule create --protocol tcp --dst-port 22:22  --remote-ip 0.0.0.0/0 web-access
openstack security group rule create --protocol tcp --dst-port 80:80  --remote-ip 0.0.0.0/0 web-access
openstack security group rule create --protocol tcp --dst-port 443:443 --remote-ip 0.0.0.0/0 web-access
# launch with this group + a floating IP, then:
openstack floating ip create public
openstack server add floating ip <server> <floating-ip>
```

**DNS:** point your hostname's **A record at the floating IP** *before* first
bring-up, so the ACME (Let's Encrypt) challenge on ports 80/443 succeeds.

---

## 3. Provision

`cloud-init.yaml` (if you pasted it at launch) installs Docker + Compose v2, adds
**4 GB swap** (so the in-container `npm build` can't OOM on 6 GB), and clones the
repo to `/opt/app`. Otherwise SSH in and run:
```bash
# deploy/ lives on the demobuilder-mvp branch; drop --branch once it's on main.
git clone --branch demobuilder-mvp https://github.com/spencermcbridemoore/narvi-demo-maker-llm.git /opt/app
cd /opt/app && sudo bash deploy/setup.sh      # swap + Docker + bring-up
```

---

## 4. Drop in the secret `.env` (never committed, never in an image)

On the instance, create `/opt/app/.env` (copy from `.env.example`) and fill in:
```ini
LLM_PROFILE=azure
AZURE_OPENAI_ENDPOINT=https://<resource>.openai.azure.com/
AZURE_OPENAI_API_KEY=<your key>
AZURE_OPENAI_API_VERSION=2024-12-01-preview
OPENAI_API_VERSION=2024-12-01-preview
AZURE_DEPLOYMENT_CHEAP=gpt-5-mini
AZURE_DEPLOYMENT_MID=gpt-5-mini
AZURE_DEPLOYMENT_STRONG=gpt-5

# Deploy-only:
SITE_ADDRESS=demobuilder.example.org        # your FQDN (or <dashed-ip>.sslip.io)
ACME_EMAIL=you@example.org
ALLOWED_ORIGINS=https://demobuilder.example.org
```
The key is injected at **runtime** via compose `env_file` and is **never** baked
into an image layer (see [§8 Secrets audit](#8-secrets-audit)).

---

## 5. Bring it up + verify

```bash
cd /opt/app
docker compose -f deploy/docker-compose.yml up -d --build
docker compose -f deploy/docker-compose.yml ps          # backend healthy, caddy up
docker compose -f deploy/docker-compose.yml logs -f caddy  # watch cert issuance
```

Verify (the acceptance checklist):
1. **Health/API:** `curl https://$SITE_ADDRESS/health` → `{"status":"ok"}`;
   `curl https://$SITE_ADDRESS/config` → JSON with `"provider":"azure"`.
2. **Same-origin SPA:** open `https://$SITE_ADDRESS` — the page loads, the flowchart
   (`/mermaid`) and provider badge (`/config`) populate (proves API proxying).
3. **SSE doesn't stall:** start a chat; in devtools the `POST /agent` response is
   `text/event-stream` and streams incrementally (proves `flush_interval` + no
   buffering). One **real gpt-5 generation produces a non-fallback demo** in the
   Preview (no "placeholder" banner).
4. **HTTPS / New demo:** the cert is browser-trusted (no warning); "New demo" works
   (`crypto.randomUUID()` is available over HTTPS).
5. **Rate limit:** hammering `/agent` past the limit from one IP returns `429` with
   `Retry-After`; a normal session never trips it.
6. **Persistence:** `docker compose ... restart` — Caddy does **not** re-issue certs
   (the `caddy-data` volume) and prior thread state survives (the `backend-data`
   volume).

---

## 6. TLS notes

- Caddy issues a **Let's Encrypt** cert automatically the moment `SITE_ADDRESS` is a
  real hostname. Needs ports **80** (HTTP-01) and **443** (TLS-ALPN-01) open and DNS
  resolving to the floating IP.
- **IP-only instance?** Use `<dashed-ip>.sslip.io` as `SITE_ADDRESS`.
- **While testing** (DNS/ports not final), uncomment the `acme_ca` *staging* line in
  `deploy/Caddyfile` to avoid Let's Encrypt's rate limits, then switch back.
- The `caddy-data` volume holds the certs + ACME account key — **never** delete it
  casually or you'll re-issue and risk rate limits.

---

## 7. Locking it down (if "open" is too open)

The current model is **open + cost-bounded**. To require a login instead, the clean
path is CopilotKit's **`selfManagedAgents`** (you secure the FastAPI `/agent`
endpoint yourself): add Basic-Auth (Caddy `basic_auth` directive) or a real auth
layer in front of `/agent`, switch `frontend/src/providers.tsx` from
`agents__unsafe_dev_only` to `selfManagedAgents`, and set `ALLOWED_ORIGINS`
accordingly. The Azure key already stays server-side either way.

---

## 8. Secrets audit

The Azure key must never reach the repo or an image layer:
- **Repo:** `.env` is git-ignored; only `.env.example` (key blank) is tracked.
  `git ls-files | grep -E '(^|/)\.env$'` → nothing; `git log --all -- .env` → empty.
- **Image:** the key is injected only at runtime (`env_file`). Confirm it's not baked:
  ```bash
  docker history --no-trunc demobuilder-backend:local   # key absent from layers
  docker run --rm demobuilder-backend:local env | grep AZURE   # prints nothing
  ```
- **Browser bundle:** only `VITE_*` vars are inlined, and the only one we set is the
  (empty) `VITE_BACKEND_URL`. `grep -r AZURE /srv` inside the caddy image → nothing.
- The `.dockerignore` keeps `.env` out of the build context entirely (defense in depth).

---

## 9. Operations

- **Reboot survival:** `restart: unless-stopped` + the enabled Docker service bring
  both containers back after a host reboot. (A manual `docker compose stop` stays
  stopped — that's intended.)
- **Update:** `cd /opt/app && git pull && docker compose -f deploy/docker-compose.yml up -d --build`.
- **Disk (20 GB) — the headline gotcha.** The custom Caddy build (`xcaddy build
  --with caddy-ratelimit`) drags in Caddy's full build graph; its transient Go
  output overflows the 20 GB root with `no space left on device` *mid-compile*.
  **Provisioning now prevents this**: `deploy/provision.sh` creates + attaches a
  50 GB Cinder volume (`VOLUME_NAME` / `VOLUME_SIZE`) and `deploy/cloud-init.yaml`
  runs `deploy/relocate-docker-storage.sh` on first boot to move Docker's build
  storage onto it (manual path: `deploy/setup.sh` does the same).
  - **Critical detail:** this Docker install (get.docker.com on Ubuntu 24) uses the
    **containerd image store**, so moving Docker's `data-root` is *not* enough — the
    build writes to **`/var/lib/containerd`**, which the script bind-mounts onto the
    volume (the part that actually fixes it). Tell the store is containerd-backed when
    `docker images` prints the `DISK USAGE / CONTENT SIZE / IN USE` table.
  - **Verify the fix:** `df -h /var/lib/containerd` shows the volume, not `/dev/sda1`.
  - **Never `rsync` a live containerd store** — it leaves the snapshotter
    inconsistent (`failed to walk ... no such file or directory`). A fresh box has
    nothing to migrate, so the script *redirects only*. If a store is already
    corrupt: `docker builder prune -af` then rebuild.
  - Still prune periodically: `docker image prune` / `docker builder prune`; `df -h /`.
- **Scaling:** single uvicorn worker + SQLite is correct for this size. Don't run
  multiple backend replicas against one SQLite file — use the documented Postgres
  seam (`AsyncPostgresSaver`, a one-line swap in `app/graph/build.py`) instead.

---

## 10. Future upgrade: pre-built images (CI + registry)

Today the instance *builds* both images, which is what makes the disk and npm-OOM
pitfalls possible at all. The cleaner long-term path is to **build in CI and pull**:

- Build `web` + `backend` in **GitHub Actions** (no corporate proxy in CI) and push
  to **GHCR**.
- Swap the `build:` blocks in `deploy/docker-compose.yml` for `image:` refs so the
  instance runs `docker compose pull` instead of `up --build`. This eliminates BOTH
  the `xcaddy` disk wall and the npm heap-OOM, makes deploys near-instant, and frees
  the 20 GB root (the volume relocation above becomes optional).

The Dockerfiles are already distribution-ready (multi-stage, secrets via runtime
`env_file`, no host-path assumptions). The only host dependency is the bind-mounted
`Caddyfile` (`docker-compose.yml`), so the repo still needs to be present on the
host. **Not implemented yet** — revisit if this becomes frequently redeployed.

---

## Files

| File | Purpose |
|------|---------|
| `backend.Dockerfile` | python:3.12-slim + pinned deps + uvicorn (single worker) |
| `web.Dockerfile` | node build (same-origin SPA) → xcaddy build (rate-limit) → caddy + `/srv` |
| `Caddyfile` | static SPA + SSE-safe API proxy + per-IP `/agent` limit + auto-HTTPS |
| `docker-compose.yml` | the 2-service stack; `env_file: ../.env`; volumes; healthcheck |
| `provision.sh` | laptop-side OpenStack CLI: volume + instance + networking + floating IP |
| `cloud-init.yaml` | Jetstream2 first-boot: swap + Docker + clone + containerd relocation |
| `relocate-docker-storage.sh` | mounts the data volume + relocates `/var/lib/containerd` (disk preflight) |
| `setup.sh` | manual provisioning/bring-up alternative |
