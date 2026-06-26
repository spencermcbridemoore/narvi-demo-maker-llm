# DemoBuilder edge image: a custom Caddy (with per-IP rate limiting) that serves
# the built Vite SPA AND reverse-proxies the API — same-origin, automatic HTTPS.
# Build context is the REPO ROOT (see deploy/docker-compose.yml).

# --- Stage 1: build the static frontend ------------------------------------
# SAME-ORIGIN: frontend/.env.production (committed) sets VITE_BACKEND_URL empty,
# which `vite build` loads automatically, making every API call relative
# (/agent, /config, /mermaid, /thread/*). The shipped SPA therefore talks to
# whatever origin serves it (Caddy) — never http://localhost:8000.
# NODE_OPTIONS gives the build heap headroom (m3.small is 6 GB; pair with swap).
FROM node:22-alpine AS web
WORKDIR /app
ENV NODE_OPTIONS=--max-old-space-size=4096
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build
# -> /app/dist (index.html + hashed /assets/*); this stage is discarded.

# --- Stage 2: compile a custom Caddy with the rate-limit module ------------
# xcaddy fetches Go modules over HTTPS — works on the Jetstream2 instance.
# Pin to a commit SHA (append @<sha>) for a fully reproducible build if desired;
# the module has no semver tags so the bare path tracks master.
FROM caddy:2.11.4-builder-alpine AS caddybuild
RUN xcaddy build --with github.com/mholt/caddy-ratelimit

# --- Stage 3: runtime — custom Caddy + baked SPA ---------------------------
FROM caddy:2.11.4-alpine
COPY --from=caddybuild /usr/bin/caddy /usr/bin/caddy
COPY --from=web /app/dist /srv
# The Caddyfile is bind-mounted read-only at /etc/caddy/Caddyfile by compose,
# and TLS certs persist in the /data volume.
