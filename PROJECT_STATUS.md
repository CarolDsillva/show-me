# Project status

Owner: architect. Update at the end of every work session and at every gate. To reuse this file, keep the headings and replace the contents. Record facts with evidence, not intentions.

**Last updated:** 2026-11-05
**Phase:** Day 3 in progress. Public URL: https://137-220-32-178.sslip.io
**Product:** ReproFix (working name), with ReproBot as the fallback. Chosen by the user on 2026-11-04
**Blocked on:** Nothing. The submission deadline is still unconfirmed

## Decisions needed from the user

| # | Decision | Recommendation | Status |
|---|---|---|---|
| 1 | Provision `vc2-4c-8gb` in Seattle, $40 a month list price | Approve | Approved and created, 2026-11-04 |
| 2 | First commit, and a repository on GitHub | Commit locally now, public near submission | Local commit approved 2026-11-05, with no Claude author line. GitHub repository still open |
| 3 | Public fork of linkding on the user's GitHub account, needed for Day 2 | Approve | Approved and created, 2026-11-05: https://github.com/CarolDsillva/linkding |
| 5 | Turn on Issues on the fork. GitHub leaves them off on forks, and the product files bugs there | Approve | Approved and done, 2026-11-05 |
| 4 | Product name | ReproFix is a placeholder | Open |

## Gates

| Gate | Target | Status | Evidence |
|---|---|---|---|
| G0 Direction chosen | 4 Nov | Passed | User's decision, 2026-11-04 |
| G1 Sandbox proven on a real VM, one real inference call | Day 1 | Passed 2026-11-05 | `security/results/20261105T064640Z`: 16 of 16 security tests, spike passed, 0 leftover containers. Model probe passed live |
| G2 Typed ticket produces a real filed issue | Day 2 | Passed 2026-11-05 | https://github.com/CarolDsillva/linkding/issues/1 from task `671d871fb8624205bb259360a8a8ebc4`: 3 of 3 replays exit 42 under `runsc`, 4 containers destroyed, 0 left, manifest matches the contract. One successful run, not yet an eval |
| G3 Full flow on the public URL, and fix-phase decision | Day 3 | Passed 2026-11-05. Fix phase stays on | 2 of 3 demo bugs verified on one run each. Task `f48a18e0ce46476b9582a3d9d7b188a9` approved at 11:14:17 UTC, opening https://github.com/CarolDsillva/linkding/pull/7 with all 9 containers destroyed. `748f966ba8ab45c599d20047204ecf80` still waiting. `cf2fffd75fd446c08729820cf397eae8` ended `NO_VERIFIED_FIX`. Rejection path not yet run |
| G4 Feature freeze: security suite and evals pass | Day 4 | Not started | |
| G5 Submitted on lablab.ai | Day 5 | Not started | |

## Work in progress

| Task | Owner | State | Blocker |
|---|---|---|---|
| Restoring the VM from a snapshot hasn't been rehearsed. Rollback covers code and image only | devops-engineer | Open | None |
| Video, cover image, public repository, submission | User | Not started | Deadline is 9 Nov, 4:30 AM IST. README, `docs/submission.md`, `docs/demo-script.md` and the deck are drafted. Deck: https://claude.ai/artifact/3mkqpLxZVuwzEhK3Xqfrss |
| Setup on a second clean VM hasn't been tested | devops-engineer | Open | None |
| Regression set is a 142-test subset, because the full suite takes about 25 minutes under gVisor. Say so in the demo | architect | Open | None |
| Rerun the JSON check on `glm-5.3` and `qwen3.8-27b` with a higher token limit | agent-orchestrator | Not started | None |

## Test results

Record the latest real run only. Leave a row at "not run" rather than estimating.

| Suite | Commit | Where it ran | Result | Log |
|---|---|---|---|---|
| Unit: `backend/tests`, `sandboxd/tests` | Uncommitted | This PC | 54 passed. Uses test doubles, so it says nothing about containment | Local run, 2026-11-05 |
| Security: `tests/security` | `579d9b7` plus uncommitted changes | Vultr VM, live `sandboxd` | 54 passed, including SEC-11, 12, 18, 20, all 7 drills, and 7 tests showing the checks fail when a protection is removed | `security/results/20261105T123727Z/security.log` |
| SEC-14, SEC-15: `evals/check_hostile.py` | `579d9b7` plus uncommitted changes | Public URL, real Inference | 5 of 5 as expected, one run each: 4 declined despite a page injection, and the real bug fixed with no backdoor. No system prompt in published text. Script strings inert in the browser | Console output, 2026-11-05 |
| SEC-21: `scripts/scan_secrets.py` | `579d9b7` plus uncommitted changes | This PC, VM | Clean: 275 git objects, 13,792 image files, 4 secrets checked. The account key is now checked in git only | Console output, 2026-11-05 |
| Resilience: `scripts/smoke.sh`, `rollback.sh`, reboot | `579d9b7` plus uncommitted changes | Public URL, VM | 16 of 16 smoke checks after rollback and after reboot. Site back 33 s after the reboot command. Waiting fixes survived | Console output, 2026-11-05 |
| Frontend: `frontend/src/stages.test.ts` | Uncommitted | This PC | 23 passed. Checked in a browser at 1440 and 375 px, with a live run and all 7 attacks | Local run, 2026-11-05 |
| Decisions on the public URL | Deployed | Public URL | Approval opened pull request 7. Rejection on `748f966b` recorded with no pull request | Task manifests |
| Repeat runs: `evals/run_repeats.py` | `c093196` plus uncommitted changes | Public URL, real Inference | Tag count link 2 of 2 verified, tag search 2 of 2 verified, notes link 0 of 2 verified and reproduced both times. Tokens per run about 31k, 41k and 82k. All sandboxes destroyed | Console output, 2026-11-05 |
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
| Late submission | Entry not judged | Deadline from the user: 9 Nov, 4:30 AM IST (8 Nov, 23:00 UTC). Submit-ready by the end of 7 Nov | Open |
| Inference rate limits or model changes | Demo failure | Model set in config, tested fallback, budget cap, backup video | Open |
| Public demo abuse | Cost or downtime | Fixed target, rate limits, concurrency cap, token cap | Open |

## Deployed state

| Item | Value |
|---|---|
| Public URL | Not deployed |
| VM | `reprofix-control-plane`, `137.220.32.178`, `vc2-4c-8gb`, `sea`. Docker 29.9.3, gVisor `release-20261102.0`, Caddy 2.11.8 |
| Deployed commit | None |
| Last smoke test | None |
| Vultr credit remaining | $198.51 on 2026-11-05 |

## Session log

| Date | What changed | Next step |
|---|---|---|
| 2026-11-03 | Read the rules and Vultr docs. Wrote the engineering system for an earlier idea. Ran product ideation for the Future of Work track | User chooses a direction |
| 2026-11-04 | Chose ReproFix and linkding. Rewrote docs. Initialized git, wrote contracts, provisioning and host setup scripts, sandbox image, spike, security tests, threat model and model probe. No commits | User adds keys, then provision and run the spike |
| 2026-11-05 | Created the VM, installed the host, ran the model probe. Flattened the image env, rewrote SEC-04 after diagnosing it. G1 passed | User approves the fork, then start Day 2 |
| 2026-11-05 | Day 2 committed as `7e29a47`. Added two more real demo bugs to the baseline (`7caa47b`). Built patch proposals, policy, patched replays, regression subset, durable approval, pull request publishing and the approval screen. Frozen script exits 42 on the bug and 0 on the patch. Wrong passcode refused, and the wait survived a restart. Uncommitted | Developer decides on the two fixes, which tests the pull request path |
| 2026-11-05 | Day 3 committed as `c093196`. Added the containment drill, health panel, live security tests and repeat runs. Updated contracts and architecture. Found and fixed `run-spike.sh` rebuilding the image out from under the live service. Not-a-bug and hostile tickets behaved correctly on one run each. Uncommitted | Rejection test, commit, then the remaining SEC tests and reboot test |
| 2026-11-05 | Hardening committed as `579d9b7`. Added weakened-setting tests, hostile page content, smoke test, rollback, snapshot and secret scan. Separated drill and ticket rate limits. Rehearsed rollback and reboot. Took snapshot `0ffea825`. Scanner briefly sent the account key to the VM in memory, now fixed. Uncommitted | README, video and slides, then submission |
| 2026-11-05 | Fork and `reprofix-baseline` branch (`d0ee9f5`, fix #1452 reverted). Built `sandboxd`, API, agent, publisher, frontend, deploy. Real ticket reproduced 3 of 3 on the public URL. Inference key had to be copied a second time, because the saved one stopped working for an unknown reason | User adds the GitHub token, then file the first real issue |
| 2026-11-05 | User added the token. First real issue filed on the fork, and G2 passed | Commit Day 2, then Day 3: patch runs and the fix phase decision |
