#!/usr/bin/env bash
# Deploy the working tree to the VM: code, image, services and Caddy. Secrets stay off every
# command line, and go to the VM through stdin. sandboxd never receives them.
set -euo pipefail

cd "$(dirname "$0")/.."
# shellcheck disable=SC1091
. .secrets/vm.env

KEY="$HOME/.ssh/reprofix_ed25519"
SSH_OPTS=(-i "$KEY" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=20)
TARGET_COMMIT=46e02034697046f4b7da5977a03a9de6c48173e8
HOST="${VM_IP//./-}.sslip.io"
COMMIT="$(git rev-parse --short HEAD)$(git diff --quiet HEAD 2>/dev/null || echo -dirty)"

python scripts/render_api_env.py "$TARGET_COMMIT" "$COMMIT" | ssh "${SSH_OPTS[@]}" "root@${VM_IP}" \
  'umask 027; cat > /etc/reprofix/api.env.new && chown root:rfapi /etc/reprofix/api.env.new && mv /etc/reprofix/api.env.new /etc/reprofix/api.env'

(cd frontend && npm ci --no-audit --no-fund --silent && npm run --silent build)
ssh "${SSH_OPTS[@]}" "root@${VM_IP}" 'install -d -m 0755 /srv/reprofix/frontend && find /srv/reprofix/frontend -mindepth 1 -delete'
tar -cf - -C frontend/dist . | ssh "${SSH_OPTS[@]}" "root@${VM_IP}" 'tar -xf - -C /srv/reprofix/frontend --no-same-owner && chmod -R a+rX /srv/reprofix/frontend'

ssh "${SSH_OPTS[@]}" "root@${VM_IP}" bash -s <<'EOF'
set -euo pipefail
if [ -d /opt/reprofix/backend ]; then
  rm -rf /opt/reprofix-prev
  mkdir -p /opt/reprofix-prev
  for name in backend sandboxd sandbox-images deploy; do cp -a "/opt/reprofix/$name" "/opt/reprofix-prev/$name"; done
  cp /etc/reprofix/sandboxd.env /etc/reprofix/sandboxd.env.prev
  # A tag stops the build below from removing the image the previous deployment needs.
  previous="$(sed -n 's/^REPROFIX_IMAGE=//p' /etc/reprofix/sandboxd.env)"
  docker image inspect "$previous" > /dev/null 2>&1 && docker tag "$previous" reprofix/target-linkding:previous
fi
EOF

tar -cf - --exclude='__pycache__' --exclude='*.egg-info' --exclude='backend/tests' --exclude='sandboxd/tests' \
    backend sandboxd sandbox-images deploy/reprofix-sandboxd.service deploy/reprofix-api.service deploy/Caddyfile \
  | ssh "${SSH_OPTS[@]}" "root@${VM_IP}" \
    'mkdir -p /opt/reprofix && rm -rf /opt/reprofix/backend /opt/reprofix/sandboxd /opt/reprofix/sandbox-images /opt/reprofix/deploy && tar -xf - -C /opt/reprofix'

# shellcheck disable=SC2087
ssh "${SSH_OPTS[@]}" "root@${VM_IP}" bash -s <<EOF
set -euo pipefail
cd /opt/reprofix
docker build -q -t reprofix/target-linkding:baseline sandbox-images/target-linkding > /tmp/reprofix-build.log 2>&1 \
  || { tail -n 30 /tmp/reprofix-build.log; exit 1; }
IMAGE="\$(docker inspect --format '{{.Id}}' reprofix/target-linkding:baseline)"
echo "Image: \$IMAGE"
echo "REPROFIX_IMAGE=\$IMAGE" > /etc/reprofix/sandboxd.env
chown root:root /etc/reprofix/sandboxd.env
chmod 0644 /etc/reprofix/sandboxd.env

[ -d venv-sandboxd ] || python3 -m venv venv-sandboxd
venv-sandboxd/bin/pip install --quiet -r sandboxd/requirements.txt
[ -d venv-api ] || python3 -m venv venv-api
venv-api/bin/pip install --quiet ./backend
rm -rf backend/build

chmod -R a+rX /opt/reprofix
chown root:rfapi /etc/reprofix/api.env
chmod 0640 /etc/reprofix/api.env
[ -f /srv/reprofix/canary ] || head -c 64 /dev/urandom | base64 > /srv/reprofix/canary
install -d -o root -g root -m 0755 /srv/reprofix/frontend

install -m 0644 deploy/reprofix-sandboxd.service deploy/reprofix-api.service /etc/systemd/system/
mkdir -p /etc/systemd/system/caddy.service.d
printf '[Service]\nEnvironment=REPROFIX_HOST=$HOST\n' > /etc/systemd/system/caddy.service.d/reprofix.conf
install -m 0644 deploy/Caddyfile /etc/caddy/Caddyfile
systemctl daemon-reload
systemctl enable --quiet reprofix-sandboxd reprofix-api caddy
systemctl restart reprofix-sandboxd
systemctl restart reprofix-api
systemctl restart caddy
sleep 4
systemctl is-active reprofix-sandboxd reprofix-api caddy
curl -fsS http://127.0.0.1:8000/api/health
echo
EOF
echo "Public host: https://${HOST}"
