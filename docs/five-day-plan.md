# Five-day execution plan

Two people directing agents. **The submission deadline isn't confirmed**, so the plan makes the entry submittable by the end of Day 4 (Saturday 7 November) and treats Day 5 as polish and submission. If the real deadline is earlier than the end of 8 November, compress from the bottom up.

Each day ends at a gate. If a gate fails, cut scope first. Never cut containment.

**Cut order:** P2 stretch, then P1, then UI polish, then number of sample bugs, then **the fix phase (fall back to ReproBot)**. Rules in `CLAUDE.md` aren't on this list.

## Day 1: Wednesday 4 November. Prove the sandbox on real infrastructure

| Owner | Task |
|---|---|
| User | Enroll on lablab.ai, join Discord, find the deadline, and ask the prize and entry question |
| User | Set up the Vultr account and credit, Serverless Inference subscription, and API keys |
| architect | Rewrite docs, initialize the repository, write `contracts/` |
| devops-engineer | Provision the VM and firewall, install Docker and gVisor, create service users |
| sandbox-engineer | Build the linkding image. Spike: run linkding and Chromium under `runsc` with all limits |
| security-reviewer | Threat model, and SEC-01 to SEC-08 running on the VM |
| agent-orchestrator | Call candidate models live for tools, structured output, images and latency. Choose primary and fallback |

**Gate G1:** on the real VM, Chromium loads linkding in the sandbox, a hostile script is contained with host-side proof, and a real Vultr Inference call succeeds. If gVisor fails, decide on the fallback today.

## Day 2: Thursday 5 November. Walking skeleton to a filed bug

| Owner | Task |
|---|---|
| sandbox-engineer | `sandboxd` sessions and runs, attestation, lifecycle, reaper, output caps, browser driver |
| backend-engineer | FastAPI, SQLite, ticket intake, sandbox client, evidence store, event stream, GitHub publisher |
| agent-orchestrator | LLM gateway and graph: intake, explore, freeze, confirm, file issue |
| qa-evaluator | First integration test. Create the fork baseline with reverted fixes, and the first eval cases |
| devops-engineer | systemd units, Caddy with TLS, deploy script, smoke test |
| frontend-engineer | Submit page and live session view |

**Gate G2:** a typed ticket on the VM produces a real reproduction and a real filed issue on our fork, with the container verified destroyed.

## Day 3: Friday 6 November. Fix phase, or fall back

| Owner | Task |
|---|---|
| agent-orchestrator | Patch proposal, policy, verification loop, approval interrupt, injection defenses |
| backend-engineer | Manifest, rate limits, budgets, restart recovery, pull request publishing |
| sandbox-engineer | Replay and regression runs with patches, host telemetry, containment drill |
| frontend-engineer | Approval screen with side-by-side runs and diff, evidence view, health panel |
| security-reviewer | Hostile tickets and pages, remaining SEC tests except the pre-publication scan |
| qa-evaluator | End-to-end tests on the public URL, full evals |

**Gate G3:** the full flow runs on the public URL. **Decide here:** if fix verification succeeds on fewer than 2 of the 3 rehearsal bugs, set `FIX_PHASE_ENABLED=false` and ship ReproBot.

## Day 4: Saturday 7 November. Harden and freeze

| Owner | Task |
|---|---|
| security-reviewer | Full security suite on the deployed system, and a review of every sensitive area |
| qa-evaluator | All suites and evals on the deployed commit, flakiness check, failure drills |
| Component owners | Fix defects only |
| devops-engineer | Reboot test, VM snapshot, rollback rehearsal |
| architect | README, architecture diagram, draft submission text |
| Team | Rehearse the demo, record a first full video as a safety copy |

**Gate G4 (feature freeze):** security suite passes, evals meet thresholds, and the entry could be submitted now.

## Day 5: Sunday 8 November. Deliver

| Owner | Task |
|---|---|
| Component owners | Blocking defects only, each re-verified |
| Team | Final video under 5 minutes and under 300MB, slides as PDF, cover image |
| qa-evaluator | Final check from a clean browser on the public URL |
| User | Make the repository public, then submit on lablab.ai, well ahead of the deadline |

**Gate G5:** submission confirmed on lablab.ai. Keep the VM running through judging, and check credit balance.

## Daily routine

1. Read `PROJECT_STATUS.md`, and check the gate for today.
2. The architect issues tasks with acceptance tests.
3. Owners deliver handoffs, and the architect integrates.
4. Deploy at least once daily and run the smoke test.
5. Update `PROJECT_STATUS.md` with evidence.
