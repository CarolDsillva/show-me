#!/usr/bin/env python3
"""Provision the ReproFix VM on Vultr. Standard library only.

  python scripts/provision.py --dry-run   Check the plan and show what would happen
  python scripts/provision.py --yes       Create anything missing (this costs money)
  python scripts/provision.py --status    Show the VM and account balance

Reads VULTR_API_KEY from .secrets/vultr.env and never prints it. Everything is found
by name first, so running it again reuses existing resources.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time
import urllib.error
import urllib.request

API = "https://api.vultr.com/v2"
ROOT = pathlib.Path(__file__).resolve().parent.parent
SECRETS = ROOT / ".secrets"
SSH_PUBLIC_KEY = pathlib.Path.home() / ".ssh" / "reprofix_ed25519.pub"

REGION = "sea"
PLAN = "vc2-4c-8gb"
OS_NAME = "Ubuntu 24.04 LTS x64"
LABEL = "reprofix-control-plane"
FIREWALL_NAME = "reprofix"
SSH_KEY_NAME = "reprofix"
PUBLIC_PORTS = ("80", "443")


def read_env_file(path: pathlib.Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def api(method: str, path: str, key: str | None = None, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(f"{API}{path}", data=data, method=method)
    request.add_header("Content-Type", "application/json")
    if key:
        request.add_header("Authorization", f"Bearer {key}")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read()
    except urllib.error.HTTPError as error:
        detail = error.read().decode(errors="replace")[:300]
        sys.exit(f"Vultr API {method} {path} failed with {error.code}: {detail}")
    return json.loads(raw) if raw else {}


def check_plan() -> tuple[int, float]:
    plans = api("GET", "/plans?per_page=500")["plans"]
    plan = next((p for p in plans if p["id"] == PLAN), None)
    if plan is None:
        sys.exit(f"Plan {PLAN} no longer exists.")
    if REGION not in plan["locations"]:
        sys.exit(f"Plan {PLAN} is not available in {REGION}.")
    systems = api("GET", "/os?per_page=500")["os"]
    os_entry = next((o for o in systems if o["name"] == OS_NAME), None)
    if os_entry is None:
        sys.exit(f"OS {OS_NAME} not found.")
    print(f"Plan:   {PLAN}, {plan['vcpu_count']} vCPU, {plan['ram']} MB, {plan['disk']} GB")
    print(f"Region: {REGION}")
    print(f"OS:     {OS_NAME} (id {os_entry['id']})")
    print(f"Cost:   ${plan['monthly_cost']} a month list price")
    return os_entry["id"], plan["monthly_cost"]


def current_ip() -> str:
    with urllib.request.urlopen("https://api.ipify.org", timeout=15) as response:
        return response.read().decode().strip()


def find_instance(key: str) -> dict | None:
    instances = api("GET", f"/instances?label={LABEL}", key)["instances"]
    return instances[0] if instances else None


def ensure_ssh_key(key: str) -> str:
    for entry in api("GET", "/ssh-keys?per_page=500", key)["ssh_keys"]:
        if entry["name"] == SSH_KEY_NAME:
            print(f"SSH key: reusing {entry['id']}")
            return entry["id"]
    public = SSH_PUBLIC_KEY.read_text(encoding="utf-8").strip()
    created = api("POST", "/ssh-keys", key, {"name": SSH_KEY_NAME, "ssh_key": public})
    print("SSH key: created")
    return created["ssh_key"]["id"]


def ensure_firewall(key: str, operator_ip: str) -> str:
    group_id = None
    for group in api("GET", "/firewalls?per_page=500", key)["firewall_groups"]:
        if group["description"] == FIREWALL_NAME:
            group_id = group["id"]
            print(f"Firewall: reusing {group_id}")
    if group_id is None:
        group_id = api("POST", "/firewalls", key, {"description": FIREWALL_NAME})["firewall_group"]["id"]
        print("Firewall: created")

    rules = api("GET", f"/firewalls/{group_id}/rules?per_page=500", key)["firewall_rules"]
    wanted = [("22", operator_ip, 32, "SSH from operator")]
    wanted += [(port, "0.0.0.0", 0, "Public web") for port in PUBLIC_PORTS]

    # A stale SSH rule from an old operator IP must not stay open.
    for rule in rules:
        if rule["port"] == "22" and rule["subnet"] != operator_ip:
            api("DELETE", f"/firewalls/{group_id}/rules/{rule['id']}", key)
            print(f"Firewall: removed old SSH rule for {rule['subnet']}")

    for port, subnet, size, notes in wanted:
        exists = any(r["port"] == port and r["subnet"] == subnet and r["ip_type"] == "v4" for r in rules)
        if not exists:
            api("POST", f"/firewalls/{group_id}/rules", key, {
                "ip_type": "v4", "protocol": "tcp", "port": port,
                "subnet": subnet, "subnet_size": size, "notes": notes,
            })
            print(f"Firewall: allowed TCP {port} from {subnet}/{size}")
    return group_id


def wait_until_ready(key: str, instance_id: str) -> dict:
    for _ in range(90):
        instance = api("GET", f"/instances/{instance_id}", key)["instance"]
        if instance["status"] == "active" and instance["main_ip"] != "0.0.0.0":
            return instance
        time.sleep(10)
    sys.exit("The VM did not become active in 15 minutes. Check the Vultr console.")


def save_vm(instance: dict) -> None:
    SECRETS.mkdir(exist_ok=True)
    (SECRETS / "vm.env").write_text(
        f"VM_IP={instance['main_ip']}\nVM_ID={instance['id']}\n", encoding="utf-8", newline="\n"
    )


def show_account(key: str) -> None:
    account = api("GET", "/account", key)["account"]
    credit = -float(account["balance"]) - float(account["pending_charges"])
    print(f"Credit remaining after pending charges: ${credit:.2f}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--yes", action="store_true", help="Create missing resources")
    mode.add_argument("--status", action="store_true")
    args = parser.parse_args()

    key = read_env_file(SECRETS / "vultr.env").get("VULTR_API_KEY")

    if args.dry_run:
        check_plan()
        print(f"SSH public key: {'found' if SSH_PUBLIC_KEY.exists() else 'MISSING'} at {SSH_PUBLIC_KEY}")
        print(f"Vultr API key: {'found' if key else 'MISSING'} in .secrets/vultr.env")
        if key:
            show_account(key)
            instance = find_instance(key)
            print(f"VM: {'already exists at ' + instance['main_ip'] if instance else 'would be created'}")
        print("Nothing was created.")
        return

    if not key:
        sys.exit("VULTR_API_KEY is missing. Copy deploy/env/vultr.env.example to .secrets/vultr.env and fill it in.")

    if args.status:
        show_account(key)
        instance = find_instance(key)
        if instance:
            print(f"VM {LABEL}: {instance['status']}, {instance['power_status']}, {instance['main_ip']}")
            save_vm(instance)
        else:
            print(f"VM {LABEL}: does not exist")
        return

    if not SSH_PUBLIC_KEY.exists():
        sys.exit(f"SSH public key not found at {SSH_PUBLIC_KEY}.")

    os_id, _ = check_plan()
    show_account(key)
    ssh_key_id = ensure_ssh_key(key)
    firewall_id = ensure_firewall(key, current_ip())

    instance = find_instance(key)
    if instance:
        print(f"VM: reusing {instance['id']}")
    else:
        instance = api("POST", "/instances", key, {
            "region": REGION, "plan": PLAN, "os_id": os_id, "label": LABEL,
            "hostname": "reprofix", "sshkey_id": [ssh_key_id],
            "firewall_group_id": firewall_id, "backups": "disabled", "enable_ipv6": False,
        })["instance"]
        print(f"VM: created {instance['id']}, waiting until active")
    instance = wait_until_ready(key, instance["id"])
    save_vm(instance)
    print(f"VM ready at {instance['main_ip']}. Saved to .secrets/vm.env.")


if __name__ == "__main__":
    main()
