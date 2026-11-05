#!/usr/bin/env bash
# Prepare the Ubuntu 24.04 VM. Run as root. Safe to run more than once.
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

apt-get update
apt-get -y upgrade
apt-get install -y ca-certificates curl gnupg git sqlite3 python3-venv unattended-upgrades

# Docker Engine, from docs.docker.com/engine/install/ubuntu
if ! command -v docker >/dev/null; then
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  codename="$(. /etc/os-release && echo "$VERSION_CODENAME")"
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${codename} stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin
fi

# gVisor, from gvisor.dev/docs/user_guide/install
if ! command -v runsc >/dev/null; then
  curl -fsSL https://gvisor.dev/archive.key | gpg --dearmor --yes -o /usr/share/keyrings/gvisor-archive-keyring.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/gvisor-archive-keyring.gpg] https://storage.googleapis.com/gvisor/releases release main" \
    > /etc/apt/sources.list.d/gvisor.list
  apt-get update
  apt-get install -y runsc
fi
runsc install
systemctl restart docker

# Caddy, from caddyserver.com/docs/install
if ! command -v caddy >/dev/null; then
  apt-get install -y debian-keyring debian-archive-keyring apt-transport-https
  curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/gpg.key | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update
  apt-get install -y caddy
fi

# Service users. Only rfsandboxd can reach Docker, which is root-equivalent.
getent group rfsock >/dev/null || groupadd --system rfsock
id rfapi >/dev/null 2>&1 || useradd --system --create-home --shell /usr/sbin/nologin rfapi
id rfsandboxd >/dev/null 2>&1 || useradd --system --create-home --shell /usr/sbin/nologin rfsandboxd
usermod -aG rfsock rfapi
usermod -aG rfsock,docker rfsandboxd
gpasswd -d rfapi docker >/dev/null 2>&1 || true

install -d -o root -g rfapi -m 0750 /etc/reprofix
install -d -o rfapi -g rfapi -m 0750 /var/lib/reprofix
install -d -o root -g root -m 0755 /srv/reprofix

# SSH keys only
cat > /etc/ssh/sshd_config.d/10-reprofix.conf <<'EOF'
PasswordAuthentication no
KbdInteractiveAuthentication no
EOF
systemctl reload ssh

# The host firewall sits behind the Vultr firewall group.
if command -v ufw >/dev/null; then
  ufw allow 22/tcp
  ufw allow 80/tcp
  ufw allow 443/tcp
  ufw --force enable
fi

# Fail loudly if the result isn't what we expect.
docker run --runtime=runsc --rm hello-world >/dev/null
if id -nG rfapi | grep -qw docker; then
  echo "rfapi must not be in the docker group" >&2
  exit 1
fi

echo "Docker: $(docker --version)"
echo "gVisor: $(runsc --version | head -1)"
echo "Caddy:  $(caddy version)"
echo "Host setup complete."
