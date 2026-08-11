#!/usr/bin/env bash
#
# One-time preparation of a fresh free-tier EC2 instance (Amazon Linux 2023,
# t3.micro). Paste into the "User data" box at launch, or run manually as ec2-user.
#
# Idempotent: safe to re-run. It does NOT deploy the app — it only makes the
# machine capable of running it. Deploy with docker-compose.prod.yml afterwards.
set -euo pipefail

log() { echo "[bootstrap] $*"; }

# --------------------------------------------------------------------- swap --
# The single most important step. A t3.micro has 1 GiB of RAM; `pip install`
# of the langchain/langgraph tree alone peaks above that and the build dies
# with an opaque "Killed" from the OOM reaper. 4 GB of swap absorbs the build
# peak and the occasional LLM-response spike at runtime.
#
# It costs EBS, not RAM: the free tier includes 30 GB, and this uses 4.
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

# Default of 60 is tuned for desktops. 10 keeps the kernel from paging out the
# resident FastAPI/Celery processes while swap is merely available as headroom.
sysctl -w vm.swappiness=10
grep -q '^vm.swappiness' /etc/sysctl.conf || echo 'vm.swappiness=10' >> /etc/sysctl.conf

# ------------------------------------------------------------------- docker --
if ! command -v docker >/dev/null 2>&1; then
  log "installing docker"
  dnf install -y docker git
  systemctl enable --now docker
  # Lets ec2-user run docker without sudo. Requires a re-login to take effect.
  usermod -aG docker ec2-user
else
  log "docker already installed"
fi

# The compose plugin is not in the AL2023 repos; install the official binary
# into docker's plugin dir so `docker compose` (v2 syntax) works.
COMPOSE_VERSION=v2.32.4
PLUGIN_DIR=/usr/local/lib/docker/cli-plugins
if [ ! -x "${PLUGIN_DIR}/docker-compose" ]; then
  log "installing docker compose ${COMPOSE_VERSION}"
  mkdir -p "${PLUGIN_DIR}"
  curl -fsSL \
    "https://github.com/docker/compose/releases/download/${COMPOSE_VERSION}/docker-compose-linux-$(uname -m)" \
    -o "${PLUGIN_DIR}/docker-compose"
  chmod +x "${PLUGIN_DIR}/docker-compose"
else
  log "docker compose already installed"
fi

# ------------------------------------------------------------ log rotation ---
# Unbounded json-file logs are the classic way a small root volume fills up and
# takes the whole stack down a few weeks after launch. Cap them globally.
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

log "done. Log out and back in so the docker group applies, then deploy with docker-compose.prod.yml."
