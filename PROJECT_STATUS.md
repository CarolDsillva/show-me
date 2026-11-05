# Project status

Owner: architect. Update at the end of every work session and at every gate. To reuse this file, keep the headings and replace the contents. Record facts with evidence, not intentions.

**Last updated:** 2026-11-05
**Phase:** Day 1 complete, Day 2 next
**Product:** ReproFix (working name), with ReproBot as the fallback. Chosen by the user on 2026-11-04
**Blocked on:** Public linkding fork needs the user's approval

## Decisions needed from the user

| # | Decision | Recommendation | Status |
|---|---|---|---|
| 1 | Provision `vc2-4c-8gb` in Seattle, $40 a month list price | Approve | Approved and created, 2026-11-04 |
| 2 | First commit, and a repository on GitHub | Commit locally now, public near submission | Local commit approved 2026-11-05, with no Claude author line. GitHub repository still open |
| 3 | Public fork of linkding on the user's GitHub account, needed for Day 2 | Approve | Open |
| 4 | Product name | ReproFix is a placeholder | Open |

## Gates

| Gate | Target | Status | Evidence |
|---|---|---|---|
| G0 Direction chosen | 4 Nov | Passed | User's decision, 2026-11-04 |
| G1 Sandbox proven on a real VM, one real inference call | Day 1 | Passed 2026-11-05 | `security/results/20261105T064640Z`: 16 of 16 security tests, spike passed, 0 leftover containers. Model probe passed live |
| G2 Typed ticket produces a real filed issue | Day 2 | Not started | |
| G3 Full flow on the public URL, and fix-phase decision | Day 3 | Not started | |
| G4 Feature freeze: security suite and evals pass | Day 4 | Not started | |
| G5 Submitted on lablab.ai | Day 5 | Not started | |

## Work in progress

| Task | Owner | State | Blocker |
|---|---|---|---|
| Sandboxd: treat a dead sandbox as `sandbox_error` (see Day 1 findings) | sandbox-engineer | Not started | None |
| Rerun the JSON check on `glm-5.3` and `qwen3.8-27b` with a higher token limit | agent-orchestrator | Not started | None |

## Test results

Record the latest real run only. Leave a row at "not run" rather than estimating.

| Suite | Commit | Where it ran | Result | Log |
|---|---|---|---|---|
| Unit: `sandboxd/tests` | Uncommitted | This PC | 9 passed. Says nothing about containment | Local run, 2026-11-04 |
| Security: `tests/security` | Uncommitted at run time | Vultr VM | 16 passed | `security/results/20261105T064640Z/security.log` |
| Spike: linkding and Chromium under gVisor | Uncommitted at run time | Vultr VM | Passed: login, screenshot, external navigation blocked, container destroyed, 19 s in total | `security/results/20261105T064640Z/spike-app.json` |
| Model probe | Uncommitted at run time | This PC, live Vultr Inference, one call per check | `qwen3.8-flash-next` passed all 4. `glm-5.3` and `qwen3.8-27b` passed 3 and failed JSON with an empty answer. Cause unconfirmed | Console output, 2026-11-04 |
| Integration, end-to-end, evals | | | Not written | |

Earlier result folders: `20261104T131427Z` is the failed run from before the image fix, and `20261104T131901Z` is the run where only SEC-04 failed.

### Day 1 findings

- **A fork bomb kills the sandbox rather than failing inside it.** gVisor's own threads count toward the 512 PID limit. The host kernel refused forks and the sandbox died after 200 to 250 forks, in about 2.5 s, on 3 of 3 attempts. Host-side proof comes from the cgroup's `pids.events`. SEC-04 now accepts this ending only with that proof.
- **Docker keeps reporting `running` after that crash.** `docker inspect` State can't show whether a sandbox is alive. `sandboxd` must treat exit 128 with `WaitPID` EOF, or a failed exec, as `sandbox_error`. Destruction still worked and was verified.
- **Effective process room is 200 to 250, not 512.** Chromium and linkding fit in the spike. Watch this on heavier pages.
- **Host swap:** the VM has 8 GiB of swap, but the container's swap is capped by `--memory-swap`, and SEC-05 passed.

Also checked locally: Python compiles, shell scripts pass `bash -n`, OpenAPI and JSON schema files parse, and `provision.py --dry-run` confirmed the plan and region against the live Vultr API. `shellcheck` isn't installed here.

## Open questions to verify

| Question | How to verify | Owner |
|---|---|---|
| Exact submission deadline, including time zone | lablab event page, submission form, or Discord | User |
| Can one project enter both challenges, and is there one prize list? | Discord | User |
| Serverless Inference price, included tokens, rate limits | Vultr console | User |
| Vultr metadata address (the doc URL tried returned 404) | SEC-08 probes `169.254.169.254`. Confirm the address from the VM host | security-reviewer |
| TLS on an `sslip.io` hostname, or is a domain needed? | Try on Day 2 | devops-engineer |
| The two other rehearsal bugs. Candidates: upstream PRs #1404 and #1437 | Day 2 | qa-evaluator |

## Risks

| Risk | Impact | Mitigation | Status |
|---|---|---|---|
| Two unreliable steps chained | Demo failure | Frozen replay, real known bugs, small scope, G3 decision with ReproBot fallback | Open |
| Chromium under gVisor is slow or fails | Weak or no demo | Day 1 spike passed under `runsc`, so the runc fallback isn't needed | Closed |
| Deadline unknown | Could lose a day | Submit-ready by the end of Day 4 | Open |
| Inference rate limits or model changes | Demo failure | Model set in config, tested fallback, budget cap, backup video | Open |
| Public demo abuse | Cost or downtime | Fixed target, rate limits, concurrency cap, token cap | Open |

## Deployed state

| Item | Value |
|---|---|
| Public URL | Not deployed |
| VM | `reprofix-control-plane`, `137.220.32.178`, `vc2-4c-8gb`, `sea`. Docker 29.9.3, gVisor `release-20261102.0`, Caddy 2.11.8 |
| Deployed commit | None |
| Last smoke test | None |
| Vultr credit remaining | $198.92 on 2026-11-05 |

## Session log

| Date | What changed | Next step |
|---|---|---|
| 2026-11-03 | Read the rules and Vultr docs. Wrote the engineering system for an earlier idea. Ran product ideation for the Future of Work track | User chooses a direction |
| 2026-11-04 | Chose ReproFix and linkding. Rewrote docs. Initialized git, wrote contracts, provisioning and host setup scripts, sandbox image, spike, security tests, threat model and model probe. No commits | User adds keys, then provision and run the spike |
| 2026-11-05 | Created the VM, installed the host, ran the model probe. Flattened the image env, rewrote SEC-04 after diagnosing it. G1 passed | User approves the fork, then start Day 2 |
