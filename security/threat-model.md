# Threat model

Version 1, 2026-11-04. Owner: security-reviewer. Nothing below has been tested on the VM yet, so check `PROJECT_STATUS.md` for real results.

## What we protect

| Asset | Why it matters |
|---|---|
| Vultr Inference key | Spending and account access |
| GitHub token for the fork | Could alter or deface the fork |
| The VM and control plane | Everything depends on them |
| Evidence integrity | The product's claims rest on it |
| Developer's approval | The only route to a pull request |
| Budget and availability | A public demo can be abused |

## Who attacks, and how

| Attacker | Route | Goal |
|---|---|---|
| Anyone on the public URL | Ticket text | Run up costs, inject instructions, take the demo down |
| A hostile ticket or page | Text the model reads | Make the model write a backdoor, or lie in a report |
| A confused or manipulated model | Scripts and patches it writes | Damage, exfiltration, passing its own test |
| Code in the target app | Runs in the sandbox | Escape, exfiltrate, exhaust resources |
| A visitor to the approval screen | The approve endpoint | Open a pull request without review |

## Threats and controls

| ID | Threat | Control | Test |
|---|---|---|---|
| T1 | Sandboxed code reaches the host | gVisor, non-root, no capabilities, no mounts, read-only root | SEC-01, 03, 10 |
| T2 | Sandboxed code reads real secrets | Secrets absent from `sandboxd` and the sandbox, env allowlist | SEC-02 |
| T3 | Exfiltration or metadata access | `--network=none` | SEC-08 |
| T4 | Resource exhaustion | Memory, PID, CPU, disk, output and wall-clock limits, all fixed on the server | SEC-04, 05, 06, 07 |
| T5 | State leaks between runs | Fresh container, tmpfs only, destruction verified | SEC-17 |
| T6 | Leftover containers | Removal on every path, reaper, session deadline label | SEC-11 |
| T7 | API compromise leads to root | API user has no Docker access, and `sandboxd` accepts profile names only | SEC-12, 13 |
| T8 | Prompt injection changes an outcome | Outcomes computed by code | SEC-14 |
| T9 | Injected backdoor in a patch | Patch policy, developer review, no auto-merge | SEC-14, 16 |
| T10 | A patch passes by changing its test | Script frozen before patching and hash-checked, test edits forbidden | SEC-16, 18 |
| T11 | Vacuous repro script | Broken steps count as not reproduced, control check against the upstream fix in evals | Evals |
| T12 | XSS from untrusted text | Text-only rendering, artifacts as attachments | SEC-15 |
| T13 | Unauthorized approval or wrong repository | Operator passcode, fixed target repository | SEC-19 |
| T14 | Agent browses elsewhere | Action validation, `goto` localhost only, no network anyway | SEC-20 |
| T15 | Cost abuse | Rate limits, concurrency cap, token budgets, no free-form repositories | Integration |
| T16 | Secrets in the public repository | `.gitignore`, pre-publication scan | SEC-21 |

## Residual risks, stated openly

- **Single VM.** A gVisor escape followed by a container escape would reach the control plane. Mitigation if time allows: a separate worker VM.
- **Chromium's own sandbox is off.** gVisor and the container limits are the boundary.
- **The target app runs its own hard-coded development secret key.** It's upstream's dev default, it's meaningless outside the sandbox, and it isn't one of our secrets.
- **Developers can approve a bad patch.** Proof shows that a symptom went away, not that the code is good.
- **Evidence hashes** show that a bundle hasn't changed since creation. They can't prove the server was honest when creating it.
