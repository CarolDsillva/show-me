#!/usr/bin/env bash
# Run deploy/setup-host.sh on the VM recorded in .secrets/vm.env.
set -euo pipefail

cd "$(dirname "$0")/.."
# shellcheck disable=SC1091
. .secrets/vm.env

KEY="$HOME/.ssh/reprofix_ed25519"
SSH_OPTS=(-i "$KEY" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=20)

ssh "${SSH_OPTS[@]}" "root@${VM_IP}" 'bash -s' < deploy/setup-host.sh
