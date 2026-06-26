#!/usr/bin/env bash
# Manual provisioning + bring-up for DemoBuilder on an Ubuntu host (an alternative
# to deploy/cloud-init.yaml, e.g. for an already-running instance).
#
# Run from anywhere as a sudo-capable user, AFTER placing your secret .env at the
# repo root:
#     sudo bash deploy/setup.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

echo "==> 1/4  4 GB swap (so the in-container npm build can't OOM on 6 GB RAM)"
if ! swapon --show 2>/dev/null | grep -q /swapfile; then
  fallocate -l 4G /swapfile || dd if=/dev/zero of=/swapfile bs=1M count=4096
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  grep -q /swapfile /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

echo "==> 2/4  Docker Engine + Compose v2 (skipped if already installed)"
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sh
  usermod -aG docker "${SUDO_USER:-ubuntu}" || true
  systemctl enable --now docker
fi

echo "==> 3/4  checking the secret .env"
if [ ! -f "$REPO_ROOT/.env" ]; then
  echo "ERROR: $REPO_ROOT/.env is missing." >&2
  echo "       cp .env.example .env  and set AZURE_* plus SITE_ADDRESS=<your FQDN>," >&2
  echo "       ACME_EMAIL, and ALLOWED_ORIGINS=https://<your FQDN>." >&2
  exit 1
fi

echo "==> 4/4  building + starting the stack"
docker compose -f deploy/docker-compose.yml up -d --build
docker compose -f deploy/docker-compose.yml ps
echo
echo "Done. Watch TLS issuance:  docker compose -f deploy/docker-compose.yml logs -f caddy"
