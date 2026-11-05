#!/usr/bin/env bash
# Check the deployed system from outside, then from the VM. Exits non-zero on any failure.
# It runs one containment drill, because that proves a sandbox can really start.
set -uo pipefail

cd "$(dirname "$0")/.."
# shellcheck disable=SC1091
. .secrets/vm.env

BASE="https://${VM_IP//./-}.sslip.io"
KEY="$HOME/.ssh/reprofix_ed25519"
failures=0

check() {
  if [ "$2" = "$3" ]; then echo "ok    $1"; else echo "FAIL  $1: expected $2, got $3"; failures=$((failures + 1)); fi
}

check "front page" 200 "$(curl -s -m 20 -o /dev/null -w '%{http_code}' "$BASE/")"
check "health" 200 "$(curl -s -m 20 -o /dev/null -w '%{http_code}' "$BASE/api/health")"
check "samples" 200 "$(curl -s -m 20 -o /dev/null -w '%{http_code}' "$BASE/api/samples")"
check "host telemetry" 200 "$(curl -s -m 20 -o /dev/null -w '%{http_code}' "$BASE/api/health/host")"
check "plain HTTP redirects" 308 "$(curl -s -m 20 -o /dev/null -w '%{http_code}' "http://${VM_IP//./-}.sslip.io/")"
check "invalid ticket refused" 422 "$(curl -s -m 20 -o /dev/null -w '%{http_code}' -X POST "$BASE/api/tasks" -H 'content-type: application/json' -d '{"text":"x"}')"
check "API not exposed directly" 000 "$(curl -s -m 5 -o /dev/null -w '%{http_code}' "http://$VM_IP:8000/api/health")"

task="$(curl -s -m 20 -X POST "$BASE/api/drills" -H 'content-type: application/json' -d '{"drill":"network"}' | python -c 'import json,sys;print(json.load(sys.stdin)["id"])')"
outcome=""
for _ in $(seq 1 24); do
  outcome="$(curl -s -m 20 "$BASE/api/tasks/$task" | python -c 'import json,sys;t=json.load(sys.stdin);print(t["outcome"] if t["status"]=="done" else "")')"
  [ -n "$outcome" ] && break
  sleep 5
done
check "a sandbox starts and contains a drill" CONTAINED "$outcome"

ssh -i "$KEY" -o BatchMode=yes -o ConnectTimeout=20 "root@$VM_IP" bash -s <<'EOF' > /tmp/rf-smoke.out 2>&1
for unit in reprofix-sandboxd reprofix-api caddy docker; do echo "$unit $(systemctl is-active "$unit") $(systemctl is-enabled "$unit")"; done
echo "leftover $(docker ps -aq --filter label=reprofix.run | wc -l)"
echo "rfapi-docker $(sudo -u rfapi docker ps > /dev/null 2>&1 && echo allowed || echo denied)"
echo "nobody-socket $(sudo -u nobody test -e /run/reprofix/sandboxd.sock && echo reachable || echo unreachable)"
echo "sandboxd-secrets $(tr '\0' '\n' < /proc/"$(systemctl show -p MainPID --value reprofix-sandboxd)"/environ | grep -c 'INFERENCE\|GITHUB\|PASSCODE')"
EOF
for unit in reprofix-sandboxd reprofix-api caddy docker; do
  check "$unit running and enabled" "$unit active enabled" "$(grep "^$unit " /tmp/rf-smoke.out)"
done
check "no leftover containers" "leftover 0" "$(grep '^leftover' /tmp/rf-smoke.out)"
check "API user has no Docker access" "rfapi-docker denied" "$(grep '^rfapi-docker' /tmp/rf-smoke.out)"
check "other users can't reach sandboxd" "nobody-socket unreachable" "$(grep '^nobody-socket' /tmp/rf-smoke.out)"
check "sandboxd holds no secrets" "sandboxd-secrets 0" "$(grep '^sandboxd-secrets' /tmp/rf-smoke.out)"

echo "Failures: $failures"
exit "$failures"
