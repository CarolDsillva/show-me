"""Run each demo bug N times on the live site, and write the public results table.

Real runs on real Vultr Inference, against the current baseline. Each one that reproduces files a
real issue on our fork. Nothing here decides a result: it records what the server's code decided,
with a link to each run. It never approves anything. It can resume: runs already in the file count.

Usage: python evals/run_benchmark.py <runs per bug> [sample id ...]
"""

import datetime as dt
import json
import pathlib
import sys
import time
import urllib.error
import urllib.request

BASE = "https://137-220-32-178.sslip.io"
OUT = pathlib.Path("frontend/public/results.json")
TIMEOUT_S = 1500
BUGS = ["tag-count-link", "tag-search-url", "notes-link-http", "tag-order", "logout-tasks-page"]
FINAL = {"FIX_VERIFIED", "NO_VERIFIED_FIX", "NOT_REPRODUCED", "REPRODUCED", "FAILED", "APPROVED", "REJECTED"}


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=30) as response:
        return json.load(response)


def post(path, body):
    request = urllib.request.Request(BASE + path, json.dumps(body).encode(), {"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def run_one(sample):
    while True:
        try:
            task = post("/api/tasks", {"text": sample["text"]})
            break
        except urllib.error.HTTPError as error:
            if error.code != 429:
                raise
            print("  rate limited, waiting 5 minutes", flush=True)
            time.sleep(300)
    started = time.time()
    while time.time() - started < TIMEOUT_S:
        task = get(f"/api/tasks/{task['id']}")
        if task["status"] in ("done", "awaiting_approval"):
            break
        time.sleep(15)
    return {
        "task": task["id"], "outcome": task["outcome"], "seconds": round(time.time() - started),
        "tokens": task.get("tokens_used"), "cost_usd": task.get("cost_usd"), "issue": task["issue_url"],
    }


def main():
    runs = int(sys.argv[1])
    wanted = sys.argv[2:] or BUGS
    samples = {s["id"]: s for s in get("/api/samples")}
    baseline = get("/api/health").get("target_commit")
    data = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {"bugs": {}}
    if data.get("target_commit") != baseline:
        data = {"bugs": {}}  # Results from an older baseline don't count.
    data["target_commit"] = baseline
    for round_number in range(runs):
        for bug in wanted:
            entry = data["bugs"].setdefault(bug, {"title": samples[bug]["title"], "upstream": samples[bug].get("upstream"), "runs": []})
            if len(entry["runs"]) > round_number:
                continue
            result = run_one(samples[bug])
            entry["runs"].append(result)
            data["updated_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
            OUT.write_text(json.dumps(data, indent=1), encoding="utf-8", newline="\n")
            print(bug, json.dumps(result), flush=True)
    for bug, entry in data["bugs"].items():
        outcomes = [r["outcome"] for r in entry["runs"]]
        print(bug, {o: outcomes.count(o) for o in sorted(set(outcomes))}, flush=True)


if __name__ == "__main__":
    main()
