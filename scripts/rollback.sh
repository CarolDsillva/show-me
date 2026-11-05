#!/usr/bin/env bash
# Put the previous deployment back: code, image and sandboxd's image setting. deploy.sh keeps
# one previous copy. The frontend and data stay as they are.
set -euo pipefail

cd "$(dirname "$0")/.."
# shellcheck disable=SC1091
. .secrets/vm.env

ssh -i "$HOME/.ssh/reprofix_ed25519" -o BatchMode=yes -o ConnectTimeout=20 "root@$VM_IP" bash -s <<'EOF'
set -euo pipefail
[ -d /opt/reprofix-prev/backend ] || { echo "No previous deployment to roll back to."; exit 1; }
[ -f /etc/reprofix/sandboxd.env.prev ] || { echo "No previous image setting."; exit 1; }
image="$(sed -n 's/^REPROFIX_IMAGE=//p' /etc/reprofix/sandboxd.env.prev)"
docker image inspect "$image" > /dev/null
for name in backend sandboxd sandbox-images deploy; do
  rm -rf "/opt/reprofix/$name"
  cp -a "/opt/reprofix-prev/$name" "/opt/reprofix/$name"
done
cp /etc/reprofix/sandboxd.env.prev /etc/reprofix/sandboxd.env
systemctl restart reprofix-sandboxd
systemctl restart reprofix-api
sleep 4
systemctl is-active reprofix-sandboxd reprofix-api
echo "Rolled back to image $image"
EOF
