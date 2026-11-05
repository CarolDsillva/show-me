#!/usr/bin/env bash
# Copy the sandbox work to the VM, build the image, run the containment spike as the
# rfsandboxd service user, and bring the results back to security/results/.
set -euo pipefail

cd "$(dirname "$0")/.."
# shellcheck disable=SC1091
. .secrets/vm.env

KEY="$HOME/.ssh/reprofix_ed25519"
SSH_OPTS=(-i "$KEY" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=20)
REMOTE=/opt/reprofix-spike
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="security/results/${STAMP}"
mkdir -p "$OUT"

tar -cf - --exclude='__pycache__' sandbox-images sandboxd tests/security \
  | ssh "${SSH_OPTS[@]}" "root@${VM_IP}" "rm -rf ${REMOTE} && mkdir -p ${REMOTE} && tar -xf - -C ${REMOTE}"

# The remote exit status is kept so results still come back when a check fails.
remote_status=0
# shellcheck disable=SC2087
ssh "${SSH_OPTS[@]}" "root@${VM_IP}" bash -s <<EOF || remote_status=$?
set -uo pipefail
cd ${REMOTE}
mkdir -p results

docker build -t reprofix/target-linkding:spike sandbox-images/target-linkding > results/image-build.log 2>&1
build_status=\$?
tail -5 results/image-build.log
if [ "\$build_status" -ne 0 ]; then
  echo "Image build failed. See image-build.log."
  chown -R rfsandboxd: ${REMOTE}
  exit 1
fi
IMAGE="\$(docker inspect --format '{{.Id}}' reprofix/target-linkding:spike)"
echo "Image: \$IMAGE"
echo "\$IMAGE" > results/image-id.txt

python3 -m venv .venv
.venv/bin/pip install --quiet pytest==9.0.3
chown -R rfsandboxd: ${REMOTE}

sudo -u rfsandboxd -H env REPROFIX_IMAGE="\$IMAGE" PYTHONPATH=sandboxd/spike \
  .venv/bin/python -m pytest tests/security -v --tb=short --junitxml=results/security.xml \
  2>&1 | tee results/security.log
security_status=\$?

sudo -u rfsandboxd -H env REPROFIX_IMAGE="\$IMAGE" PYTHONPATH=sandboxd/spike \
  .venv/bin/python sandboxd/spike/spike_app.py results \
  2>&1 | tee results/spike-app.log
spike_status=\$?

docker ps -a --filter label=reprofix.run --format '{{.Names}}' > results/leftover-containers.txt
echo "Leftover containers: \$(wc -l < results/leftover-containers.txt)"
{ docker --version; runsc --version; uname -a; free -m; df -h /; } > results/host.txt 2>&1
echo "security_status=\$security_status spike_status=\$spike_status"
[ "\$security_status" -eq 0 ] && [ "\$spike_status" -eq 0 ] && [ ! -s results/leftover-containers.txt ]
EOF

ssh "${SSH_OPTS[@]}" "root@${VM_IP}" "tar -cf - -C ${REMOTE}/results ." | tar -xf - -C "$OUT"
echo "Results saved to ${OUT}"
exit "$remote_status"
