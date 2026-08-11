#!/usr/bin/env bash
#
# One-time preparation of a fresh Ubuntu 22.04/24.04 VM (DigitalOcean, Linode,
# GCP Compute Engine, Hetzner, ...). The Debian-family counterpart of
# ec2_bootstrap.sh, which is Amazon Linux 2023 only — that script calls `dnf`
# and adds `ec2-user` to the docker group, neither of which exists here.
#
# Run as root (most providers drop you in as root) or under sudo:
#     sudo bash scripts/ubuntu_bootstrap.sh
#
# Idempotent: safe to re-run. It does NOT deploy the app — it only makes the
# machine capable of running it. Deploy with docker-compose.prod.yml afterwards.
set -euo pipefail

log() { echo "[bootstrap] $*"; }

if [ "$(id -u)" -ne 0 ]; then
  echo "must run as root: sudo bash $0" >&2
  exit 1
fi

# The account that will own `docker compose` afterwards. SUDO_USER when invoked
# via sudo; otherwise the provider's default unprivileged account if it exists.
# Falls back to empty, in which case you simply stay root.
TARGET_USER="${SUDO_USER:-}"
if [ -z "$TARGET_USER" ] || [ "$TARGET_USER" = "root" ]; then
  for candidate in ubuntu debian admin; do
    if id "$candidate" >/dev/null 2>&1; then TARGET_USER="$candidate"; break; fi
  done
fi

# --------------------------------------------------------------------- swap --
# Same reasoning as ec2_bootstrap.sh: `pip install` of the langchain/langgraph
# tree peaks above 1 GiB and dies with an opaque "Killed" from the OOM reaper.
# On a 2 GB droplet this is headroom rather than a hard requirement, but the
# build is the peak and it costs only disk.
if [ ! -f /swapfile ]; then
  log "creating 4G swapfile"
  # fallocate is instantaneous but produces a file some kernels refuse to swap
  # on; dd is slower and always works. 4 GiB at 128M blocks = 32 writes.
  dd if=/dev/zero of=/swapfile bs=128M count=32 status=none
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
else
  log "swapfile already present"
  swapon /swapfile 2>/dev/null || true
fi

sysctl -w vm.swappiness=10
grep -q '^vm.swappiness' /etc/sysctl.conf || echo 'vm.swappiness=10' >> /etc/sysctl.conf

# ------------------------------------------------------------------- docker --
# Ubuntu's own `docker.io` package ships without the Compose v2 plugin, and the
# distro `docker-compose` is the abandoned v1 (different syntax, no `depends_on:
# condition:` — docker-compose.prod.yml would not parse). The official
# convenience script installs docker-ce plus docker-compose-plugin together,
# which is why it is preferred here over apt.
export DEBIAN_FRONTEND=noninteractive
if ! command -v docker >/dev/null 2>&1; then
  log "installing docker + compose plugin"
  apt-get update -y
  apt-get install -y ca-certificates curl git
  curl -fsSL https://get.docker.com -o /tmp/get-docker.sh
  sh /tmp/get-docker.sh
  systemctl enable --now docker
else
  log "docker already installed"
  apt-get update -y && apt-get install -y git
fi

if ! docker compose version >/dev/null 2>&1; then
  log "compose plugin missing; installing docker-compose-plugin"
  apt-get install -y docker-compose-plugin
fi

if [ -n "$TARGET_USER" ]; then
  log "adding ${TARGET_USER} to the docker group (requires re-login)"
  usermod -aG docker "$TARGET_USER"
fi

# ------------------------------------------------------------ log rotation ---
# Unbounded json-file logs are the classic way a small root volume fills up and
# takes the whole stack down. Cap them globally.
if [ ! -f /etc/docker/daemon.json ]; then
  log "capping container log size"
  mkdir -p /etc/docker
  cat > /etc/docker/daemon.json <<'JSON'
{
  "log-driver": "json-file",
  "log-opts": { "max-size": "10m", "max-file": "3" }
}
JSON
  systemctl restart docker
fi

# ------------------------------------------------------------------ firewall --
# Unlike EC2 there is no security group in front of this box. If ufw is active,
# the stack's only public port still needs opening explicitly.
if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q '^Status: active'; then
  log "ufw is active; allowing 80/tcp"
  ufw allow 80/tcp || true
fi

log "done."
log "IMPORTANT: on a non-AWS host there is no instance role, so S3_ACCESS_KEY /"
log "S3_SECRET_KEY in .env.prod must be filled in with an S3-scoped IAM user's"
log "keys (never an AdministratorAccess key) — or point S3_ENDPOINT_URL at a"
log "self-hosted MinIO instead. Configure it via .env.prod (see .env.prod.example)."
if [ -n "$TARGET_USER" ]; then
  log "Log out and back in as ${TARGET_USER} so the docker group applies."
fi
