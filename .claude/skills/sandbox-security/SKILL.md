---
name: sandbox-security
description: Use when implementing or reviewing sandbox execution, resource limits, secrets handling, hostile input, prompt injection defenses, or containment tests for Agent Rush.
---

# Sandbox security

Everything inside the sandbox is hostile: the target app, the browser, the pages it loads, and every script or patch a model writes. Tickets are hostile too. The aim is that when any of it misbehaves, nothing outside the disposable container changes.

## Boundaries, outermost first

1. Vultr firewall group and host configuration
2. Service separation: `reprofix-api` has no Docker access, and `sandboxd` has no secrets
3. gVisor (`runsc`): the sandbox talks to gVisor's kernel, not directly to the host's
4. Container restrictions: no network, read-only, non-root, no capabilities
5. Resource limits, and destruction after every run

## Canonical profile

This is the design target. Each setting must be proven on the VM during the Day 1 spike, because some Docker options behave differently or aren't supported under gVisor. Record the results, and update this skill if something changes.

The single source of these values in code is `sandboxd/spike/sandbox.py`.

```bash
docker run --detach \
  --name "rf-${RUN_ID}" \
  --label "reprofix.run=${RUN_ID}" \
  --label "reprofix.deadline=${DEADLINE_EPOCH}" \
  --runtime=runsc \
  --network=none \
  --read-only \
  --cap-drop=ALL \
  --security-opt=no-new-privileges \
  --user=65534:65534 \
  --memory=2g --memory-swap=2g \
  --cpus=2 \
  --pids-limit=512 \
  --shm-size=256m \
  --ulimit nofile=4096:4096 \
  --tmpfs /workspace:rw,nosuid,nodev,size=512m,uid=65534,gid=65534,mode=0700 \
  --tmpfs /tmp:rw,nosuid,nodev,size=256m,uid=65534,gid=65534,mode=0700 \
  --log-driver=none \
  --env HOME=/tmp \
  "${IMAGE_DIGEST}" sleep infinity
```

Chromium needs more room than plain Python, so these limits are larger than a code-only sandbox would use. It also runs with `--no-sandbox`, because its own sandbox needs privileges we remove. gVisor is the outer boundary, and we disclose this.

| Limit | Value | Enforced by | Evidence |
|---|---|---|---|
| Wall clock | 120 s per command, 600 s per session | `sandboxd` from the host, with `docker kill` | Termination reason, timestamps |
| Memory | 2 GiB, no swap | Container limit | Status from inspect |
| CPU | 2 cores | Container limit | Attestation |
| Processes | 512 | Container limit | Attestation, fork bomb test |
| Workspace | 512 MiB tmpfs | Mount size | ENOSPC, host disk unchanged |
| Root filesystem | Read-only | Container setting | Attestation, write test |
| Output | 1 MiB per stream, 10 MiB artifacts | `sandboxd` while reading | Truncation flag |
| Network | None. App and Chromium use localhost | Container setting | Attestation, connect test |

Values are starting points to tune on the VM, but they stay fixed on the server. Neither a request nor the model can change them.

### Rules around the profile

- **Don't bind-mount host paths, or mount the Docker socket.** Don't use `--privileged` or `--pid=host`, or add capabilities.
- **Image by digest.** No `latest`. No secrets in any layer or build arg.
- **Attest before loading code.** Read `docker inspect` and compare runtime, network, capabilities, read-only flag and limits against the profile. If they differ, destroy the container and fail closed.
- **Don't trust checks made inside the sandbox.** gVisor's docs warn that `dmesg` output can be imitated, so check the runtime from the host.
- **Load by stream.** Pipe the source and patch into `tar -x` inside the container with `docker exec -i`. *Verify under gVisor.* The code is never run on the host.
- **Collect by stream.** Read named files as capped byte streams. Save them as opaque hashed blobs. Symlinks and path traversal never get a chance to matter on the host, because nothing is unpacked there.
- **Limit output while reading.** Never buffer without a cap. `--log-driver=none` prevents logs from filling the host disk.
- **Destroy in `finally`.** After removal, inspect must return not found. Record this as evidence.
- **Reap.** On startup and on a timer, remove any `reprofix.run` container past its deadline.

## Secrets

| Secret | Where it lives | Never in |
|---|---|---|
| Inference API key | `/etc/reprofix/api.env`, readable by `rfapi` only | `sandboxd`, the sandbox, git, logs, prompts, cloud-init, client code |
| Vultr API key | Operator's machine. Not on the VM in the MVP | Everything else |
| GitHub token | `/etc/reprofix/api.env`, narrowly scoped to our fork | `sandboxd`, the sandbox, git, logs, prompts, client code |
| Target app test login | Generated per run, exists only inside that sandbox | Anywhere that outlives the run |

Decoys planted in the sandbox are different. They are random per run, obviously fake, and exist only to detect hunting. For example, `/workspace/.decoy/credentials` holding `REPROFIX_DECOY_<random>`. Scan all output for the value.

## Hostile input

| Input | Risk | Defense |
|---|---|---|
| Ticket text | Prompt injection, such as "add an admin backdoor" | Delimited and labeled untrusted, size capped, patch policy, code decides outcomes, developer approves |
| Page text and screenshots | Injection through page content | Same as above. Browser actions validated, `goto` localhost only |
| Repro script written by a model | Hostile code, a script that always "fails" | Runs only in the sandbox. Frozen and hashed, with broken steps counting as not reproduced |
| Patch written by a model | Backdoors, weakened tests, secrets access | Patch policy, runs only in the sandbox, never merged without a developer |
| Test output | Injection, ANSI escapes, secret-shaped strings | Capped, control characters stripped before display or prompts |
| Artifacts | XSS, malware | Opaque storage, `Content-Disposition: attachment`, never rendered |

A model must never receive a secret in a prompt. Injection then has nothing to steal.

## Security test matrix

Implement in `tests/security/`, running on the VM. Tests assert from the host. A test that can't run fails.

| ID | Attack | Pass condition |
|---|---|---|
| SEC-01 | Check runtime from host | `HostConfig.Runtime` is `runsc` |
| SEC-02 | Read env, `/proc/*/environ` with canaries set in API and `sandboxd` services | Canary absent from all output |
| SEC-03 | `rm -rf /`, write to `/etc` and `/usr` | Writes fail, host canary hash unchanged |
| SEC-04 | Fork bomb | PID limit reached, host stays responsive, run terminates |
| SEC-05 | Allocate memory without limit | `OOMKilled`, API heartbeat unbroken |
| SEC-06 | Infinite CPU loop | Killed at wall-clock limit |
| SEC-07 | Fill workspace and `/tmp` | ENOSPC, host disk unchanged |
| SEC-08 | Connect to `203.0.113.10`, metadata address, Docker bridge gateway, DNS | All fail, network mode `none` attested |
| SEC-09 | Huge stdout, binary, no newlines | Output truncated and flagged, host memory stable |
| SEC-10 | Look for Docker socket, host mounts, added capabilities | None present |
| SEC-11 | Container left behind after `sandboxd` killed mid-run | Reaper removes it, task resolved |
| SEC-12 | Send limits, mounts, env, or another image in a `sandboxd` request | Rejected |
| SEC-13 | API user tries to reach Docker | Permission denied, `rfapi` not in `docker` group |
| SEC-14 | Prompt injection in a ticket, a page, and test output | Outcome unchanged, no decoy or system prompt leak |
| SEC-15 | XSS strings in tickets, diffs, output and filenames | Inert in browser |
| SEC-16 | Patch touching tests, CI, settings or dependencies, or too large | Rejected by policy, reason shown |
| SEC-17 | Second run looks for traces of the previous run | Nothing persists |
| SEC-18 | Change the frozen script after freezing | Run fails on the hash check |
| SEC-19 | Approve or open a pull request without the passcode, or against another repository | Refused |
| SEC-20 | Browser `goto` an external address, or an action outside the allowed list | Refused by validation and by the driver |
| SEC-21 | Repository and image scan for secrets before going public | Nothing found |

Prove each test can fail: run it at least once against a deliberately weakened setting on the VM, and record the result.

## Review checklist

- [ ] Could any code path execute untrusted content outside the sandbox?
- [ ] Is anything secret reachable by a process that doesn't need it?
- [ ] Can any request or model output affect limits, image, mounts or env?
- [ ] Does every path, including exceptions, destroy the container?
- [ ] Is every read of hostile data capped?
- [ ] Does any evidence rely on what the sandbox says about itself? If so, is it labeled?
- [ ] Does the failure mode fail closed?

## Claims to avoid

| Don't say | Say |
|---|---|
| "Escape-proof" | "Runs behind gVisor, with no network, and limits we verified. A kernel-level escape remains a residual risk" |
| "The bug is fixed" or "the patch is safe" | "The frozen script passes on the patched code, and a developer must review it" |
| "Tamper-proof logs" | "Hashes show the bundle hasn't changed since it was created" |
| "Chromium is sandboxed twice" | "Chromium's own sandbox is off. gVisor and the container limits are the boundary" |
