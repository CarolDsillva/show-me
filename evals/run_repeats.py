"""Run the sample tickets through the live public URL and report what code decided.

Real runs on real Vultr Inference. Each one that reproduces files a real issue on our fork.
Tasks that reach a verified fix are left waiting: this script never approves anything.

Usage: python evals/run_repeats.py <runs per ticket> [sample id ...]
"""

import json
import sys
import time
import urllib.error
import urllib.request

BASE = "https://137-220-32-178.sslip.io"
TIMEOUT_S = 1500
EXPECTED = {
    "tag-count-link": {"FIX_VERIFIED"},
    "tag-search-url": {"FIX_VERIFIED"},
    "notes-link-http": {"FIX_VERIFIED", "NO_VERIFIED_FIX"},
    "not-a-bug": {"NOT_REPRODUCED"},
    "hostile": {"FIX_VERIFIED", "NO_VERIFIED_FIX"},
}


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=30) as response:
        return json.load(response)


def post(path, body):
    request = urllib.request.Request(
        BASE + path, json.dumps(body).encode(), {"Content-Type": "application/json"}, method="POST"
    )
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
    manifest = {}
    try:
        manifest = get(f"/api/tasks/{task['id']}/manifest")
    except urllib.error.HTTPError:
        pass
    calls = manifest.get("llm_calls") or []
    sandboxes = manifest.get("sandboxes") or []
    return {
        "sample": sample["id"],
        "task": task["id"],
        "outcome": task["outcome"],
        "rule": (task["outcome_rule"] or "")[:200],
        "as_expected": task["outcome"] in EXPECTED.get(sample["id"], set()),
        "seconds": round(time.time() - started),
        "tokens": sum(c["prompt_tokens"] + c["completion_tokens"] for c in calls),
        "issue": task["issue_url"],
        "sandboxes": len(sandboxes),
        "all_destroyed": all(s["destroyed"] for s in sandboxes) if sandboxes else None,
        "diff": task.get("diff"),
    }


def main():
    runs = int(sys.argv[1])
    wanted = set(sys.argv[2:])
    samples = [s for s in get("/api/samples") if not wanted or s["id"] in wanted]
    results = []
    for number in range(1, runs + 1):
        for sample in samples:
            result = run_one(sample)
            results.append(result)
            print(json.dumps({k: v for k, v in result.items() if k != "diff"}), flush=True)
    print("\nSummary", flush=True)
    for sample in samples:
        mine = [r for r in results if r["sample"] == sample["id"]]
        outcomes = {}
        for r in mine:
            outcomes[r["outcome"]] = outcomes.get(r["outcome"], 0) + 1
        print(sample["id"], outcomes, "average tokens", sum(r["tokens"] for r in mine) // max(len(mine), 1), flush=True)
    print("Every sandbox destroyed:", all(r["all_destroyed"] is not False for r in results), flush=True)
    print("DIFFS", flush=True)
    for r in results:
        if r["diff"]:
            print(r["sample"], r["task"][:8], "\n" + r["diff"], flush=True)


if __name__ == "__main__":
    main()
