"""Print /etc/reprofix/api.env to stdout for deploy.sh, which pipes it to the VM over SSH.

Reads .secrets/ and creates the operator passcode once. Run it only through deploy.sh, because
its output holds real secrets. Errors name a missing key, and never print a value.
"""

import pathlib
import re
import secrets
import sys

SECRETS = pathlib.Path(".secrets")


def read_env(path: pathlib.Path) -> dict[str, str]:
    values = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            match = re.match(r"\s*([A-Z_]+)\s*=(.*)$", line)
            if match:
                values[match.group(1)] = match.group(2).strip()
    return values


def main() -> None:
    target_commit, deployed_commit = sys.argv[1:3]
    vultr = read_env(SECRETS / "vultr.env")
    api_path = SECRETS / "api.env"
    api = read_env(api_path)
    if not vultr.get("VULTR_INFERENCE_API_KEY"):
        sys.exit("VULTR_INFERENCE_API_KEY is missing from .secrets/vultr.env")
    if not api.get("GITHUB_TOKEN"):
        # Everything up to filing still works and can be tested. Filing then fails, and says so.
        print("WARNING: GITHUB_TOKEN is missing from .secrets/api.env, so filing issues will fail", file=sys.stderr)
    if not api.get("OPERATOR_PASSCODE"):
        api["OPERATOR_PASSCODE"] = secrets.token_urlsafe(18)
        with api_path.open("a", encoding="utf-8") as handle:
            handle.write(f"\nOPERATOR_PASSCODE={api['OPERATOR_PASSCODE']}\n")
    lines = {
        "VULTR_INFERENCE_API_KEY": vultr["VULTR_INFERENCE_API_KEY"],
        "GITHUB_TOKEN": api.get("GITHUB_TOKEN", ""),
        "OPERATOR_PASSCODE": api["OPERATOR_PASSCODE"],
        "TARGET_REPO": "CarolDsillva/linkding",
        "TARGET_COMMIT": target_commit,
        "DEPLOYED_COMMIT": deployed_commit,
        "FIX_PHASE_ENABLED": "false",
        "PRIMARY_MODEL": "qwen3.8-flash-next",
        "FALLBACK_MODEL": "qwen3.8-27b",
    }
    # Bytes, because text mode on Windows would add a carriage return to every value.
    sys.stdout.buffer.write("".join(f"{key}={value}\n" for key, value in lines.items()).encode())


if __name__ == "__main__":
    main()
