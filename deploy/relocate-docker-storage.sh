#!/usr/bin/env bash
# DemoBuilder — relocate Docker's build/image storage onto a data volume.
#
# WHY THIS EXISTS (the headline lesson of the 2026-06-29 deploy):
#   The custom Caddy image (deploy/web.Dockerfile stage 2,
#   `xcaddy build --with github.com/mholt/caddy-ratelimit`) pulls in Caddy's full
#   build graph; the Go compile's transient output overflows m3.small's 20 GB root
#   and the build dies with "no space left on device" mid-compile.
#   The get.docker.com install on Ubuntu 24 uses the *containerd image store*, so
#   moving Docker's `data-root` does NOT move where builds write — the real storage
#   is /var/lib/containerd (a separate service). Relocating /var/lib/containerd onto
#   a Cinder volume is the fix that actually works.
#   (Tell the containerd image store is active when `docker images` prints the
#   DISK USAGE / CONTENT SIZE / IN USE table instead of REPO/TAG/SIZE.)
#
# WHAT THIS DOES (idempotent; safe to re-run):
#   detect the attached blank data disk (NOT hardcoded to /dev/sdb) -> ext4 it
#   (guarded so it can never touch a non-blank / mounted / root / tiny / ambiguous
#   disk) -> mount at /mnt/docker (fstab, by UUID) -> bind-mount /var/lib/containerd
#   onto it AND point Docker data-root at it -> make docker REQUIRE that mount ->
#   restart Docker -> verify the store now lives on the volume. On a fresh box there
#   is nothing to migrate, so we redirect only — NO rsync (an rsync of a live
#   containerd store corrupted the snapshotter last time).
#
# DOUBLES AS THE DISK PREFLIGHT: if no data volume is present and the root disk is
# too small to hold the build, it ABORTS with the fix instead of letting the build
# die 100 lines into a Go compile.
#
# Run as root on the instance (deploy/cloud-init.yaml and deploy/setup.sh call it):
#     sudo bash deploy/relocate-docker-storage.sh
set -euo pipefail

MOUNT_POINT="${DOCKER_DATA_MOUNT:-/mnt/docker}"
VOLUME_LABEL="${VOLUME_LABEL:-demobuilder-docker}"
CONTAINERD_DIR="/var/lib/containerd"
# Wait up to this long (seconds) for an attached volume to appear: provision.sh
# attaches it around boot time, so cloud-init may reach here a few seconds early.
DISK_WAIT_SECS="${DISK_WAIT_SECS:-120}"
# Skip relocation if, after waiting, no data volume is present AND the root disk has
# at least this much free (GB) — relocation is only needed on a small root.
ROOT_FREE_SKIP_GB="${ROOT_FREE_SKIP_GB:-25}"
# Never format a device smaller than this (GB) — guard against grabbing a stray disk.
MIN_DISK_GB="${MIN_DISK_GB:-10}"
# Escape hatch: force a specific data disk (e.g. /dev/sdb) when auto-detection is
# ambiguous. Still subject to the blank/size/non-root mkfs guards below.
DATA_DISK_OVERRIDE="${DATA_DISK_OVERRIDE:-}"

log() { printf '\n[relocate-docker-storage] %s\n' "$*"; }
die() { printf '[relocate-docker-storage] ERROR: %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "must run as root (sudo)."

# Whole-disk kernel name backing a mountpoint, e.g. "sda" for / or "sdb" for a
# bind-mounted dir on the volume. Strips any bind '[/subpath]' suffix from SOURCE.
backing_disk() {
  local mp="$1" src pk
  src="$(findmnt -no SOURCE --target "$mp" 2>/dev/null | head -n1 || true)"
  [ -n "$src" ] || { echo ""; return; }
  src="${src%%[*}"                                   # strip bind '[/subpath]'
  pk="$(lsblk -no PKNAME "$src" 2>/dev/null | head -n1 || true)"
  if [ -n "$pk" ]; then
    echo "$pk"
  else
    lsblk -no KNAME "$src" 2>/dev/null | head -n1 || true   # src is already a whole disk
  fi
}

ROOT_DISK="$(backing_disk / || true)"
[ -n "$ROOT_DISK" ] || die "could not determine the root disk."

root_free_gb() { df -BG --output=avail / | tail -n1 | tr -dc '0-9'; }

# A blank, safe-to-format disk: a block device that is NOT root, not mounted (itself
# or any child), has no partitions, and carries no filesystem/partition signature.
# FAILS CLOSED: if any probe errors, treat the disk as NOT blank so we never mkfs
# over data we couldn't reliably read.
is_blank_disk() {
  local dev="$1" sig childcount mnts wipefs_out wipefs_rc
  [ -b "$dev" ] || return 1
  [ "$dev" != "/dev/$ROOT_DISK" ] || return 1
  mnts="$(lsblk -nro MOUNTPOINT "$dev" 2>/dev/null | tr -d '[:space:]')"
  [ -z "$mnts" ] || return 1
  childcount="$(lsblk -nro NAME "$dev" 2>/dev/null | wc -l | tr -d ' ')"
  [ "${childcount:-0}" -le 1 ] || return 1
  # Primary probe: any wipefs signature => not blank. A probe error (wipefs missing,
  # transient device error) returns nonzero -> assume NOT blank.
  wipefs_out="$(wipefs -n "$dev" 2>/dev/null)"; wipefs_rc=$?
  [ "$wipefs_rc" -eq 0 ] || return 1
  sig="$(printf '%s\n' "$wipefs_out" | grep -vE '^DEVICE|^[[:space:]]*$' || true)"
  [ -z "$sig" ] || return 1
  # Second positive probe: any blkid signature => not blank.
  if blkid -p "$dev" >/dev/null 2>&1; then return 1; fi
  return 0
}

# Is a disk big enough to be our data volume?
big_enough() {
  local bytes
  bytes="$(blockdev --getsize64 "$1" 2>/dev/null || echo 0)"
  [ "${bytes:-0}" -ge $((MIN_DISK_GB * 1024 * 1024 * 1024)) ]
}

# Pick the data disk. Label-first (so a re-run reuses our volume regardless of lsblk
# ordering or whole-disk-vs-partition), else the first blank, big-enough, non-root
# disk. Never returns the root disk. Prints /dev/NAME on success.
find_data_disk() {
  local name type dev
  if [ -n "$DATA_DISK_OVERRIDE" ]; then echo "$DATA_DISK_OVERRIDE"; return 0; fi
  dev="$(blkid -L "$VOLUME_LABEL" 2>/dev/null || true)"   # our labeled volume, wherever it lives
  if [ -n "$dev" ]; then echo "$dev"; return 0; fi
  while read -r name type _; do
    [ "$type" = "disk" ] || continue
    [ "$name" != "$ROOT_DISK" ] || continue
    dev="/dev/$name"
    is_blank_disk "$dev" || continue
    big_enough "$dev" || continue
    echo "$dev"; return 0
  done < <(lsblk -dn -o NAME,TYPE)
  return 1
}

# Count blank, big-enough, non-root disks — used in the MAIN shell to fail closed
# when more than one candidate exists (refuse to guess which to mkfs).
count_blank_disks() {
  local name type dev n=0
  while read -r name type _; do
    [ "$type" = "disk" ] || continue
    [ "$name" != "$ROOT_DISK" ] || continue
    dev="/dev/$name"
    is_blank_disk "$dev" || continue
    big_enough "$dev" || continue
    n=$((n + 1))
  done < <(lsblk -dn -o NAME,TYPE)
  printf '%s' "$n"
}

wait_for_data_disk() {
  local waited=0 dev
  while :; do
    if dev="$(find_data_disk)"; then echo "$dev"; return 0; fi
    [ "$waited" -lt "$DISK_WAIT_SECS" ] || return 1
    sleep 5; waited=$((waited + 5))
    udevadm settle >/dev/null 2>&1 || true            # nudge udev to see a fresh attach
  done
}

# --- 0. Already relocated? (idempotent; also recovers a half-done prior run) ---
if mountpoint -q "$CONTAINERD_DIR"; then
  if ! systemctl is-active --quiet docker; then
    log "$CONTAINERD_DIR already relocated but docker is down — starting it."
    systemctl start docker || die "docker failed to start (already-relocated path); see 'journalctl -u docker'."
  fi
  log "$CONTAINERD_DIR is already a mount point — already relocated. Nothing to do."
  exit 0
fi

# --- 1. Find the data volume (waiting out the provision.sh attach race), else
#        skip on a big root / abort on a small one ------------------------
log "root disk is /dev/$ROOT_DISK; looking for a separate data volume…"
if ! DATA_DISK="$(wait_for_data_disk)"; then
  free_now="$(root_free_gb)"
  if [ "${free_now:-0}" -ge "$ROOT_FREE_SKIP_GB" ]; then
    log "no data volume after ${DISK_WAIT_SECS}s and root has ${free_now} GB free — relocation not needed. Skipping."
    exit 0
  fi
  die "no data volume attached and root has only ${free_now:-?} GB free — the xcaddy build
       will run out of space mid-compile. Attach a Cinder volume (deploy/provision.sh sets
       VOLUME_NAME/VOLUME_SIZE) and re-run, or use a larger root. If a volume IS attached but
       was left half-formatted or carries a foreign filesystem (lsblk shows it but it lacks the
       '$VOLUME_LABEL' label), wipe it and re-run:  sudo wipefs -a <dev>  (find <dev> via
       lsblk -dn -o NAME,SIZE,LABEL). See deploy/README.md §9."
fi
log "using data disk: $DATA_DISK"

# --- 2. Format if blank (multi-guarded; never reformats our volume) ------
existing_label="$(lsblk -no LABEL "$DATA_DISK" 2>/dev/null | head -n1 || true)"
if [ "$existing_label" = "$VOLUME_LABEL" ]; then
  log "$DATA_DISK already carries ext4 label '$VOLUME_LABEL' — not reformatting."
else
  # Guards before the irreversible mkfs -F: genuinely blank, big enough, not root,
  # and unambiguous. -F only suppresses mkfs's whole-disk prompt for non-interactive
  # boot; is_blank_disk is what prevents -F from clobbering real data.
  is_blank_disk "$DATA_DISK" || die "$DATA_DISK is not blank/unmounted — refusing to mkfs (guard)."
  big_enough "$DATA_DISK"    || die "$DATA_DISK is under ${MIN_DISK_GB} GB — refusing to mkfs (guard)."
  nblank="$(count_blank_disks)"
  [ "${nblank:-0}" -le 1 ] || die "multiple blank disks present (${nblank}); refusing to guess which to format. Set DATA_DISK_OVERRIDE=/dev/NAME (see 'lsblk -dn -o NAME,SIZE') and re-run."
  log "formatting $DATA_DISK as ext4 (label '$VOLUME_LABEL')…"
  mkfs.ext4 -F -L "$VOLUME_LABEL" "$DATA_DISK"
  udevadm settle >/dev/null 2>&1 || true   # let udev recreate /dev/disk/by-uuid/<new> before mount-by-UUID
fi

DISK_UUID="$(blkid -s UUID -o value "$DATA_DISK" || true)"
[ -n "$DISK_UUID" ] || die "could not read UUID of $DATA_DISK."

# fstab helper: append only if no entry already targets that mountpoint (idempotent).
fstab_has_target() { awk -v t="$1" '($1 !~ /^#/) && ($2 == t){f=1} END{exit !f}' /etc/fstab; }
fstab_ensure() {
  local line="$1" target="$2"
  fstab_has_target "$target" || printf '%s\n' "$line" >> /etc/fstab
}

# --- 3. Mount the volume at $MOUNT_POINT (persist by UUID; mount before docker) ---
mkdir -p "$MOUNT_POINT"
fstab_ensure \
  "UUID=${DISK_UUID}  ${MOUNT_POINT}  ext4  defaults,nofail,x-systemd.before=docker.service  0  2" \
  "$MOUNT_POINT"
mountpoint -q "$MOUNT_POINT" || mount "$MOUNT_POINT"
mountpoint -q "$MOUNT_POINT" || die "failed to mount $DATA_DISK at $MOUNT_POINT."

# --- 4. Stop Docker before moving its storage ----------------------------
log "stopping docker + containerd before relocating their storage…"
systemctl stop docker.socket >/dev/null 2>&1 || true
systemctl stop docker        >/dev/null 2>&1 || true
systemctl stop containerd    >/dev/null 2>&1 || true

# --- 5. Relocate containerd (THE essential fix) via bind mount -----------
mkdir -p "${MOUNT_POINT}/containerd" "$CONTAINERD_DIR"
fstab_ensure \
  "${MOUNT_POINT}/containerd  ${CONTAINERD_DIR}  none  bind,nofail,x-systemd.requires=${MOUNT_POINT},x-systemd.before=docker.service  0  0" \
  "$CONTAINERD_DIR"
mountpoint -q "$CONTAINERD_DIR" || mount --bind "${MOUNT_POINT}/containerd" "$CONTAINERD_DIR"
mountpoint -q "$CONTAINERD_DIR" || die "failed to bind-mount $CONTAINERD_DIR onto the volume."

# --- 6. Relocate Docker data-root too (best effort; never clobber) -------
mkdir -p "${MOUNT_POINT}/data"
if [ ! -e /etc/docker/daemon.json ]; then
  mkdir -p /etc/docker
  printf '{\n  "data-root": "%s/data"\n}\n' "$MOUNT_POINT" > /etc/docker/daemon.json
  log "set Docker data-root to ${MOUNT_POINT}/data (new /etc/docker/daemon.json)."
else
  log "WARNING: /etc/docker/daemon.json exists — NOT relocating data-root; Docker's overlay2/image store may stay on /dev/$ROOT_DISK (the containerd bind is the essential part). Check: docker info -f '{{.DockerRootDir}}'"
fi

# --- 7. Make docker REQUIRE the containerd volume mount, so a reboot where the
#        Cinder volume reattaches late can't silently start docker on the root disk
#        and recreate the store there. 'nofail' keeps the box bootable; this drop-in
#        restores the hard guarantee that nofail removes.
mkdir -p /etc/systemd/system/docker.service.d
cat > /etc/systemd/system/docker.service.d/10-require-containerd-volume.conf <<EOF
[Unit]
RequiresMountsFor=$CONTAINERD_DIR
EOF

# --- 8. Start Docker and verify the store is on the volume ---------------
systemctl daemon-reload
systemctl start docker || die "docker failed to start after relocating storage onto $MOUNT_POINT; inspect 'journalctl -u docker' and /etc/docker/daemon.json (data-root). The containerd bind is at $CONTAINERD_DIR."
cstore_disk="$(backing_disk "$CONTAINERD_DIR" || true)"
if [ -z "$cstore_disk" ] || [ "$cstore_disk" = "$ROOT_DISK" ]; then
  die "verification failed: $CONTAINERD_DIR is still on the root disk (/dev/$ROOT_DISK)."
fi
# Best-effort: warn (don't fail) if Docker's data-root still resolves to root.
droot="$(docker info -f '{{.DockerRootDir}}' 2>/dev/null || true)"
if [ -n "$droot" ]; then
  droot_disk="$(backing_disk "$droot" || true)"
  if [ -n "$droot_disk" ] && [ "$droot_disk" = "$ROOT_DISK" ]; then
    log "WARNING: Docker data-root ($droot) still resolves to root /dev/$ROOT_DISK."
  fi
fi
log "OK: $CONTAINERD_DIR now lives on /dev/$cstore_disk (root is /dev/$ROOT_DISK). Done."
