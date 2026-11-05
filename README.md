# ReproFix

A customer writes "clicking that number doesn't show my bookmarks". ReproFix reproduces it in a real browser inside a disposable sandbox on Vultr, freezes the steps as a repeatable script, and files the bug with evidence. It then writes a patch and reruns the same frozen script against the patched app in fresh sandboxes. A developer sees the failing run, the passing run and the diff, and nothing opens until they approve.

**Live demo:** https://137-220-32-178.sslip.io. To attack the sandbox yourself, open https://137-220-32-178.sslip.io/?view=drills.

**Built for** the Vultr Agent Rush hackathon, November 2026.

## Who it's for

Support and engineering teams at software companies. Vague reports bounce between them for days because nobody can reproduce the problem. Coding agents mostly start from a developer's ticket and end with a diff. ReproFix starts from a customer's words, and ends with before-and-after proof from a script the patch can't change.

## How it executes real tasks

1. **Explore.** The model chooses one browser action at a time from a screenshot and a page summary. Code validates every action. It can only use `goto` (paths on the sandbox's own app), `click`, `fill`, `press`, `select`, `wait_for`, `screenshot` and `read_text`.
2. **Freeze.** Code, not the model, renders the steps and the expected behavior into a Playwright script and hashes it. No model text becomes code.
3. **Confirm.** The script replays in three fresh sandboxes. It must end on the symptom exit code all three times, or the outcome is `NOT_REPRODUCED`.
4. **File.** A real issue goes to our fork, with steps, a screenshot and the script.
5. **Patch.** The model reads source and proposes exact text edits. Code builds the diff and applies a fixed policy: only `bookmarks/**/*.py` and templates, never tests, settings, migrations, routing or dependencies, and at most 5 files and 150 changed lines.
6. **Verify.** The same frozen script must pass in three fresh sandboxes on the patched code, with no new failures in a 142-test regression subset. There are at most three attempts.
7. **Approve.** The task waits for a developer, surviving restarts. Approval needs the operator passcode, and opens a real pull request on our fork. Rejection opens nothing.

**Code decides every outcome, and the model only explains it.** See section 6 of [the architecture](docs/architecture.md).

## Vultr services

| Service | Used for |
|---|---|
| Vultr Cloud Compute VM (`vc2-4c-8gb`, Seattle) | The central backend and control plane: API, agent graph, `sandboxd`, evidence store, Caddy with TLS |
| Vultr Serverless Inference (`https://api.vultrinference.com/v1`) | Every agent model call, through one gateway module. Primary model `qwen3.8-flash-next`, fallback `qwen3.8-27b`, both chosen by live test. Each call is recorded in the evidence manifest |
| Vultr Firewall Groups | SSH from the operator's address only, with 80 and 443 public |
| Vultr Snapshots | A recovery point for the VM |

## Architecture

```
 Browser
    | HTTPS
    v
 Caddy (TLS, static frontend)                  Vultr Serverless Inference
    |                                                    ^
    v                                                    | key held only by this service
 reprofix-api  (user: rfapi, not in docker group)        |
    FastAPI --- LangGraph agent --- LLM gateway ---------+
       |              |
       |              +-- outcome rules and patch policy (deterministic)
       +-- SQLite, evidence files
       +-- publisher ------> GitHub, our fork only (token held only by this service)
       |
       | Unix socket: profile names and validated actions only
       v
 sandboxd  (user: rfsandboxd, in docker group, no secrets)
       | Docker API
       v
 +-----------------------------------------------------------+
 | gVisor container, one per session or replay               |
 | network none, read-only root, non-root, no capabilities   |
 | CPU, memory, PID, time, output limits                     |
 |                                                           |
 |   linkding (pinned code, or code + patch)  <-- localhost  |
 |   Chromium + browser driver                               |
 |   throwaway test user                                     |
 +-----------------------------------------------------------+
```

## Containment

| Requirement | How it's enforced | How to verify |
|---|---|---|
| No credentials in the sandbox | Env from an explicit allowlist. `sandboxd` holds no secrets, and only the API process holds keys. The only login inside is a throwaway user made per run | "Steal secrets" drill, `tests/security` SEC-02, `scripts/scan_secrets.py` |
| Limits | 2 GiB memory with no swap, 2 CPUs, 512 PIDs, 512 MiB workspace, read-only root, no network, capabilities dropped, non-root, 120 s per command, 600 s per session, output caps. Set on the server, and no request can change them | The disk, fork, memory and loop drills, SEC-04 to SEC-08, SEC-12 |
| Lifecycle | A fresh container for each session and replay. Destroyed on every path, then confirmed with `docker inspect`. A reaper removes anything overdue | SEC-11, and `destroyed` in every manifest |
| Evidence | Limits and runtime are read back from Docker before any code loads, and a mismatch destroys the container. Each task gets a hashed manifest | `GET /api/tasks/{id}/manifest`, SEC-01 |

Each drill is judged from host-side evidence. For example, the fork bomb needs the kernel's own `pids.events` count, and output from inside the sandbox can only count against "contained".

## Test results

Latest real runs, on 2026-11-05.

| Suite | Where | Result |
|---|---|---|
| Unit tests | Developer PC, using test doubles. Says nothing about containment | 115 passed |
| Security suite | The VM, against live Docker, gVisor and `sandboxd` | 54 passed, including 7 tests showing the checks fail when a protection is removed |
| Hostile tickets and pages | Public URL, real Inference, one run each | 5 of 5 as expected: no backdoor, and no system prompt in published text |
| Secret scan | Git history and the sandbox image | Clean |
| Smoke test | Public URL and VM | 16 of 16, after a rollback and after a reboot |

**Reliability of the fix phase, on three real past bugs, in the latest two runs each:**

| Bug | Reproduced | Fix verified |
|---|---|---|
| Tag count link goes to a broken page | 2 of 2 | 2 of 2 |
| Tag search doesn't update the URL | 2 of 2 | 2 of 2 |
| Links in notes use `http` | 2 of 2 | **0 of 2** |

That's too few runs to call a rate. The third bug shows what happens without a fix: the issue stands, and the outcome says `NO_VERIFIED_FIX`. In one attempt, the regression tests rejected a patch that fixed the symptom but broke an existing test.

Results are in `security/results/`, and the running record is in [PROJECT_STATUS.md](PROJECT_STATUS.md).

## Limits

- **A passing replay proves that this symptom is gone.** It doesn't prove the patch is good, which is why a developer approves it.
- **The demo bugs aren't discoveries.** They're real past bugs in linkding, recreated by reverting their real fixes on our fork.
- **The target is fixed.** The product works on one target app, chosen in server config.
- **A kernel-level escape is a residual risk.** gVisor is the boundary, and Chromium's own sandbox is off because it needs privileges we remove. A gVisor escape followed by a Docker escape would reach the control plane on the same VM.
- **Prompt injection was tested, not solved.** A few fixed attempts failed on one run each. The defense is that code decides outcomes, policy limits patches, and no prompt holds a secret.
- **A malicious patch could fake regression results,** because it shares a process with those tests. It stays contained, and the frozen replay runs separately, but this is another reason for human review.
- **The regression set is a subset.** The full linkding suite takes about 25 minutes under gVisor, so we run 142 tests around the UI we patch.
- **The patch prompt tells the model the app uses Hotwire Turbo.** That is general codebase knowledge, and it likely helps on the two bugs that were fixed.
- **Hashes show a bundle is unchanged since it was created.** They don't show the server creating it was honest.
- **Recovery.** Rollback and reboot were rehearsed. Restoring from a snapshot wasn't, and neither was setup on a second clean VM.

## Setup

Needs a Vultr account with an API key and a Serverless Inference subscription, a GitHub fork of the target with a fine-grained token, Python 3.12 or later, Node 22 or later, and Bash.

These commands built the deployed system. They haven't been repeated on a second clean VM.

1. Copy `deploy/env/vultr.env.example` to `.secrets/vultr.env` and fill it in. Put `GITHUB_TOKEN=...` in `.secrets/api.env`. Never commit either.
2. Generate an SSH key at `~/.ssh/reprofix_ed25519`.
3. Set your own fork in `ALLOWED_REPOSITORIES` in `backend/app/core/config.py`, in `scripts/render_api_env.py`, and in the image URL in `sandbox-images/target-linkding/Dockerfile`. Create a `reprofix-baseline` branch on your fork, and put its commit in the Dockerfile and `scripts/deploy.sh`.
4. Create the VM and firewall:

   ```bash
   python scripts/provision.py --dry-run
   python scripts/provision.py --yes
   ```

5. Install the host:

   ```bash
   bash scripts/setup-vm.sh
   ```

6. Deploy, then check it:

   ```bash
   bash scripts/deploy.sh
   bash scripts/smoke.sh
   ```

## Tests

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e "./backend[test]" -r sandboxd/requirements.txt
.venv/Scripts/python -m pytest backend/tests sandboxd/tests -q --import-mode=importlib
```

On Linux or macOS, use `.venv/bin/python`. These need the deployed VM:

```bash
bash scripts/run-spike.sh
python evals/check_hostile.py
python evals/run_repeats.py 2
python scripts/scan_secrets.py
```

The hostile payloads in `sandboxd/drills.py` and `tests/security/` are for sandbox testing only. Their network targets are reserved addresses and the VM's own metadata service, and the decoy credentials are fake.

## Credits and license

MIT, see [LICENSE](LICENSE). The target app is [linkding](https://github.com/sissbruecker/linkding) by Sascha Ißbrücker, also MIT. Our [demo fork](https://github.com/CarolDsillva/linkding) keeps its license, and its `reprofix-baseline` branch reverts three real fixes. We don't open issues or pull requests on the upstream project.
