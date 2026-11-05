---
name: vultr-deployment
description: Use when provisioning Vultr resources, setting up Serverless Inference, deploying or verifying the Agent Rush backend on a Vultr VM, or handling snapshots, rollback and teardown.
---

# Vultr deployment

Takes the project from an empty account to a public URL. Prices, plans and regions change, so look them up at the time. `vultr.com/pricing` refused automated fetches during research.

**Paid resources and API keys need the user.** Get approval for the plan and region first. Implementation scripts come from `deploy/` and `scripts/` once they exist. The commands below show the required end state.

## 1. Account (user)

1. Register at `https://www.vultr.com/register/` with an email never used on Vultr, and verify it.
2. Apply the hackathon code at `https://console.vultr.com/billing/?promo=HACKATHON`, as described at the lablab redeem link.
3. Confirm $200.00 remaining credit.
4. Keep an eye on the balance daily, and set a billing alert if the console offers one.

## 2. Serverless Inference (user)

1. Console: **Products > Serverless > Inference > Add Serverless Inference**.
2. Enter a label, and acknowledge the models and charges notice. **Check price, included tokens and rate limits here**, and record them in `PROJECT_STATUS.md`.
3. Copy the API key to a password manager. Never paste it into chat or commit it.
4. Test from the operator's machine, with the key in an environment variable:

```bash
curl -sS https://api.vultrinference.com/v1/chat/completions \
  -H "Authorization: Bearer $VULTR_INFERENCE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"<id from /v1/models>","messages":[{"role":"user","content":"Reply with OK"}]}'
```

## 3. The VM

Approved plan: **`vc2-4c-8gb` (4 vCPU, 8 GB, 160 GB, $40 a month list price) in Seattle (`sea`)**. On 2026-11-04, the live model list showed `sea` as the datacenter for the candidate models, so inference should be close. Each sandbox can use 2 GiB, and two run at once. Resize if the spike shows pressure.

Check before changing anything, without an API key:

```bash
curl -sS "https://api.vultr.com/v2/plans?per_page=500"
curl -sS https://api.vultr.com/v2/regions
```

## 4. Provision

The script reads `.secrets/vultr.env`, never prints the key, and reuses anything already created with the same name.

```bash
python scripts/provision.py --dry-run
python scripts/provision.py --yes
python scripts/provision.py --status
```

It creates an SSH key record, a firewall group (TCP 22 from your current IP only, TCP 80 and 443 public), and an Ubuntu 24.04 instance. It keeps `user_data` empty, because instance metadata can expose it. The Vultr API key stays on the operator's machine, and if Vultr API access control is on, your IP must be allowed.

## 5. Host setup

Implemented as the idempotent `deploy/setup-host.sh`. Run it as root over SSH. It installs Docker, gVisor and Caddy, registers `runsc`, creates the service users and directories, turns off SSH password login, and opens ports 80 and 443 in the host firewall.

## 6. Secrets

From the operator's machine, using `scp` and never a command line argument that would land in shell history:

- `/etc/reprofix/api.env`, owner `root:rfapi`, mode `0640`, contains `VULTR_INFERENCE_API_KEY`, `GITHUB_TOKEN` for our fork only, and `OPERATOR_PASSCODE`
- `sandboxd` has no environment file with secrets

## 7. Services

**`reprofix-api.service`:** `User=rfapi`, `EnvironmentFile=/etc/reprofix/api.env`, binds `127.0.0.1` only, `Restart=always`.

**`sandboxd.service`:** `User=rfsandboxd`, no secret environment file, `RuntimeDirectory=sandboxd`, socket `/run/sandboxd/sandboxd.sock` with group `rfsock` and mode `0660`, `Restart=always`.

**Harden both:** `NoNewPrivileges=true`, `ProtectSystem=strict`, `ProtectHome=true`, `PrivateTmp=true`, and only the writable paths each one needs. Confirm `sandboxd` can still reach Docker after hardening.

**Caddy:**

```
your-hostname {
    encode gzip
    handle /api/* {
        reverse_proxy 127.0.0.1:8000
    }
    handle {
        root * /srv/reprofix/frontend
        try_files {path} /index.html
        file_server
    }
}
```

**Hostname:** use a real domain if one is available. Otherwise try `<ip-with-dashes>.sslip.io` on Day 1. If using event streams, confirm that proxy buffering doesn't delay them.

## 8. Deploy

Containment spike: `bash scripts/run-spike.sh` copies the working tree to the VM, builds the sandbox image, runs the security tests as `rfsandboxd`, and brings the results back to `security/results/`.

`scripts/deploy.sh <commit>` should:

1. Check out the exact commit into a new release directory on the VM.
2. Install locked dependencies, and build the frontend or install the CI build.
3. Build the sandbox image if it changed, and record the new digest.
4. Run migrations.
5. Switch the `current` symlink, restart services, and run the smoke test.
6. Roll back automatically if the smoke test fails.

Never edit code on the VM by hand.

## 9. Smoke test

`scripts/smoke-test.sh` must check all of these against the real deployment, and fail on the first miss:

- HTTPS responds, with a valid certificate
- API health returns OK, and reports the deployed commit
- `id rfapi` shows no `docker` group
- The `sandboxd` process environment has no `VULTR_INFERENCE_API_KEY`
- A real sandbox run returns output, and attestation shows `runsc`, network `none` and the limits
- `docker ps -a --filter label=reprofix.run` is empty afterwards
- One real inference call through the gateway succeeds
- Only ports 22, 80 and 443 are reachable from outside, tested from the operator's machine

## 10. Operations

| When | Action |
|---|---|
| Before G4 | Reboot test, rollback rehearsal, and a Vultr snapshot of the working VM |
| Before recording | Fresh deploy, smoke test, disk check, and a demo dry run |
| Daily | Check credit balance, disk use, logs for errors, and token use |
| During judging | Leave it running, and keep checking credit. Credits cover the event, so add a payment method only if the user chooses to keep it running after |
| After judging | **With user approval**, delete the VM, snapshots and firewall group, and revoke the Inference key |

## Troubleshooting first steps

| Symptom | First check |
|---|---|
| Container fails to start under `runsc` | Try removing one option at a time, and read the Docker error. Record any unsupported option in the spike results |
| A limit isn't enforced | Test it on `runc` to see if it's gVisor-specific. Report it to `security-reviewer`. Don't ship silently |
| Inference errors | Live `/v1/models`, then key, then model parameters, then rate limits |
| Certificate failure | Ports 80 and 443 open at the firewall group, then DNS, then CA rate limits |
| Streaming delayed | Caddy buffering and client reconnects |
