#!/usr/bin/env bash
# DemoBuilder — provision a Jetstream2 instance via the OpenStack CLI.
#
# Runs on YOUR machine (laptop), NOT on the instance — contrast deploy/setup.sh,
# which runs ON the instance. This script only does the "rent the VM + networking"
# part: it launches the VM, opens ports 22/80/443, attaches a floating (public)
# IP, and hands the VM deploy/cloud-init.yaml (which installs Docker + clones the
# repo on first boot). It deliberately STOPS before the secret steps — it prints
# the exact scp/compose commands to finish by hand, so the Azure key is never
# touched by this script. Idempotent: re-running reuses existing resources.
#
# Prereqs:
#   - pip install python-openstackclient
#   - A Jetstream2 *application credential* in ~/.config/openstack/clouds.yaml;
#     set OS_CLOUD to its entry name.
#   - Behind a TLS-intercepting proxy (this dev box is)? Export your corporate
#     root CA first, or every call dies with CERTIFICATE_VERIFY_FAILED:
#         export OS_CACERT=/path/to/corp-root-ca.pem
#
# Usage:
#   OS_CLOUD=jetstream2 bash deploy/provision.sh
#   # override any default inline:
#   OS_CLOUD=js2 IMAGE="Featured-Ubuntu22" NETWORK=my-net bash deploy/provision.sh

set -euo pipefail

# --- Config (override via environment) -----------------------------------
: "${OS_CLOUD:?Set OS_CLOUD to your clouds.yaml entry, e.g. OS_CLOUD=jetstream2}"
INSTANCE_NAME="${INSTANCE_NAME:-demobuilder}"
FLAVOR="${FLAVOR:-m3.small}"                       # 2 vCPU / 6 GB / 20 GB
IMAGE="${IMAGE:-Featured-Ubuntu24}"                # see: openstack image list --tag featured
KEYPAIR="${KEYPAIR:-demobuilder-key}"
PUBKEY_PATH="${PUBKEY_PATH:-$HOME/.ssh/id_rsa.pub}"
SECGROUP="${SECGROUP:-web-access}"
NETWORK="${NETWORK:-}"                             # private net; auto-detected if empty
EXTERNAL_NETWORK="${EXTERNAL_NETWORK:-public}"     # floating-IP pool
SSH_USER="${SSH_USER:-ubuntu}"
VOLUME_NAME="${VOLUME_NAME:-demobuilder-docker}"   # Cinder data volume for Docker/containerd build storage
VOLUME_SIZE="${VOLUME_SIZE:-50}"                   # GB; keeps the xcaddy build off the 20 GB root (see deploy/README.md §9)
USER_DATA="${USER_DATA:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/cloud-init.yaml}"

export OS_CLOUD

log()  { printf '\n==> %s\n' "$*"; }
die()  { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

# Poll until a Cinder volume reaches $2 status (waits ~150s), e.g. available/in-use.
# On a terminal error state it prints that status and returns 2 immediately (so the
# caller can report it instead of burning the full timeout); on timeout returns 1.
wait_volume_status() {
  local name="$1" want="$2" waited=0 status
  while [ "$waited" -lt 150 ]; do
    status="$(openstack volume show "$name" -f value -c status 2>/dev/null || true)"
    [ "$status" = "$want" ] && return 0
    case "$status" in error|error_*) printf '%s' "$status"; return 2 ;; esac
    sleep 5; waited=$((waited + 5))
  done
  return 1
}

# --- Preflight -----------------------------------------------------------
command -v openstack >/dev/null 2>&1 || die "openstack CLI not found. pip install python-openstackclient"
[ -f "$USER_DATA" ]    || die "cloud-init not found at $USER_DATA"
[ -f "$PUBKEY_PATH" ]  || die "SSH public key not found at $PUBKEY_PATH (run: ssh-keygen -t ed25519)"

log "Checking OpenStack auth (OS_CLOUD=$OS_CLOUD)"
openstack token issue -f value -c id >/dev/null 2>&1 \
  || die "Auth failed. Check clouds.yaml / app credential (and OS_CACERT if behind a TLS proxy)."

log "Validating flavor + image"
openstack flavor show "$FLAVOR" -f value -c name >/dev/null 2>&1 \
  || die "Flavor '$FLAVOR' not found. List options: openstack flavor list"
openstack image show "$IMAGE" -f value -c name >/dev/null 2>&1 \
  || die "Image '$IMAGE' not found. List options: openstack image list --tag featured"

# --- Networks ------------------------------------------------------------
log "Resolving external (floating-IP) network '$EXTERNAL_NETWORK'"
if ! openstack network show "$EXTERNAL_NETWORK" -f value -c name >/dev/null 2>&1; then
  echo "Not found. Available external networks:"
  openstack network list --external -f value -c Name | sed 's/^/  /'
  die "Set EXTERNAL_NETWORK=<name> to one of the above."
fi

if [ -z "$NETWORK" ]; then
  log "Auto-detecting your private network"
  _nets="$(openstack network list --internal -f value -c Name)"
  _count="$(printf '%s\n' "$_nets" | grep -c . || true)"
  if [ "$_count" = "1" ]; then
    NETWORK="$(printf '%s\n' "$_nets" | head -n1)"
    echo "Using network: $NETWORK"
  else
    echo "Could not pick one automatically. Candidates:"
    printf '%s\n' "$_nets" | sed 's/^/  /'
    die "Set NETWORK=<name> and re-run."
  fi
else
  openstack network show "$NETWORK" -f value -c name >/dev/null 2>&1 \
    || die "Network '$NETWORK' not found. List options: openstack network list"
fi

# --- Keypair -------------------------------------------------------------
log "Ensuring keypair '$KEYPAIR'"
if openstack keypair show "$KEYPAIR" -f value -c name >/dev/null 2>&1; then
  echo "Exists; reusing."
else
  openstack keypair create --public-key "$PUBKEY_PATH" "$KEYPAIR" >/dev/null
  echo "Created from $PUBKEY_PATH"
fi

# --- Security group (22/80/443 in) ---------------------------------------
log "Ensuring security group '$SECGROUP' with 22/80/443 open"
if openstack security group show "$SECGROUP" -f value -c name >/dev/null 2>&1; then
  echo "Exists; assuming rules set (verify: openstack security group rule list $SECGROUP)."
else
  openstack security group create "$SECGROUP" >/dev/null
  for port in 22 80 443; do
    openstack security group rule create --protocol tcp --dst-port "$port:$port" \
      --remote-ip 0.0.0.0/0 "$SECGROUP" >/dev/null
    echo "  opened tcp/$port"
  done
fi

# --- Data volume (Docker/containerd build storage) -----------------------
# The custom Caddy build (xcaddy) overflows the 20 GB root, so on first boot the
# containerd image store is relocated onto this volume by
# deploy/cloud-init.yaml -> deploy/relocate-docker-storage.sh.
log "Ensuring ${VOLUME_SIZE} GB data volume '$VOLUME_NAME'"
if openstack volume show "$VOLUME_NAME" -f value -c name >/dev/null 2>&1; then
  echo "Exists; reusing."
else
  openstack volume create --size "$VOLUME_SIZE" "$VOLUME_NAME" >/dev/null
  _vs="$(wait_volume_status "$VOLUME_NAME" available)" \
    || die "Volume '$VOLUME_NAME' didn't become available (status: ${_vs:-timeout}). Check: openstack volume show $VOLUME_NAME"
  echo "Created ($VOLUME_SIZE GB)."
fi

# --- Instance ------------------------------------------------------------
log "Launching '$INSTANCE_NAME' ($FLAVOR, $IMAGE) on network '$NETWORK'"
if openstack server show "$INSTANCE_NAME" -f value -c name >/dev/null 2>&1; then
  echo "Server '$INSTANCE_NAME' already exists; skipping create."
else
  openstack server create \
    --flavor "$FLAVOR" \
    --image "$IMAGE" \
    --key-name "$KEYPAIR" \
    --security-group "$SECGROUP" \
    --network "$NETWORK" \
    --user-data "$USER_DATA" \
    --wait \
    "$INSTANCE_NAME" >/dev/null
  echo "Server is ACTIVE."
fi

# --- Attach the data volume ----------------------------------------------
# Attach AFTER the server is ACTIVE; first boot's relocate step waits for the disk
# to appear (DISK_WAIT_SECS), so this attach races safely against cloud-init.
log "Attaching data volume '$VOLUME_NAME' to '$INSTANCE_NAME'"
VOL_ID="$(openstack volume show "$VOLUME_NAME" -f value -c id)"
_vol_status="$(openstack volume show "$VOLUME_NAME" -f value -c status 2>/dev/null || true)"
# Detect an existing attachment by the volume's UUID in THIS server's volume list
# (robust to OSC column naming / output formatting).
_attached="$(openstack server volume list "$INSTANCE_NAME" -f value 2>/dev/null || true)"
if printf '%s\n' "$_attached" | grep -q "$VOL_ID"; then
  echo "Already attached to this server."
elif [ "$_vol_status" = "in-use" ]; then
  die "Volume '$VOLUME_NAME' is in-use by another server. Set VOLUME_NAME=<other> and re-run."
else
  # Gate on 'available' so a still-settling / just-reused volume gives a clear error
  # instead of a raw Cinder rejection from 'server add volume'.
  _vs="$(wait_volume_status "$VOLUME_NAME" available)" \
    || die "Volume '$VOLUME_NAME' is not attachable (status: ${_vs:-timeout}); detach/clean it first: openstack volume show $VOLUME_NAME"
  openstack server add volume "$INSTANCE_NAME" "$VOLUME_NAME" >/dev/null
  _vs="$(wait_volume_status "$VOLUME_NAME" in-use)" \
    || die "Volume '$VOLUME_NAME' didn't reach in-use after attach (status: ${_vs:-timeout}). Check: openstack server volume list $INSTANCE_NAME"
  echo "Attached."
fi

# --- Floating IP ---------------------------------------------------------
log "Ensuring a floating (public) IP"
_ports="$(openstack port list --server "$INSTANCE_NAME" -f value -c ID)"
PORT_ID="$(printf '%s\n' "$_ports" | head -n1)"
[ -n "$PORT_ID" ] || die "No network port on $INSTANCE_NAME yet; wait a moment and re-run."

_fips="$(openstack floating ip list --port "$PORT_ID" -f value -c "Floating IP Address")"
FIP="$(printf '%s\n' "$_fips" | head -n1)"
if [ -n "$FIP" ]; then
  echo "Already attached: $FIP"
else
  FIP="$(openstack floating ip create "$EXTERNAL_NETWORK" -f value -c floating_ip_address)"
  openstack server add floating ip "$INSTANCE_NAME" "$FIP"
  echo "Allocated + associated: $FIP"
fi

# --- Next steps (the secret parts — intentionally manual) ----------------
DASHED_IP="${FIP//./-}"
cat <<EOF

============================================================
  Instance ready.   Floating IP:  $FIP
============================================================

Data volume '$VOLUME_NAME' (${VOLUME_SIZE} GB) is attached. First boot relocates
Docker's containerd store onto it (and step 3 below re-runs that idempotently, in
case the first-boot attach raced) so the xcaddy build won't fill the 20 GB root.

Finish by hand (these need your SECRET .env, so they're not scripted):

  1) DNS: point your hostname's A record at $FIP, OR use the no-domain option:
           SITE_ADDRESS=${DASHED_IP}.sslip.io

  2) Copy your secret .env up (set SITE_ADDRESS, ACME_EMAIL,
     ALLOWED_ORIGINS=https://<host>, and the AZURE_* values):
           scp .env ${SSH_USER}@${FIP}:/opt/app/.env

  3) Build + start. The relocate step is idempotent (a no-op if first boot already
     did it); running it here guarantees the containerd store is on the volume even
     if the first-boot attach raced, BEFORE the disk-heavy build:
           ssh ${SSH_USER}@${FIP} "cd /opt/app && sudo bash deploy/relocate-docker-storage.sh && docker compose -f deploy/docker-compose.yml up -d --build"

  4) Watch TLS issuance:
           ssh ${SSH_USER}@${FIP} "cd /opt/app && docker compose -f deploy/docker-compose.yml logs -f caddy"

Stuck on first boot (Docker didn't install / repo didn't clone)?
  openstack console log show ${INSTANCE_NAME}      # the cloud-init boot log
EOF
