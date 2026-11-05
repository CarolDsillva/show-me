"""Day 1 spike: run linkding and Chromium together in the canonical sandbox.

Run on the VM as the sandboxd user: python spike_app.py <results dir>
Writes spike-app.json and linkding.png. Exits non-zero if the product's core path
doesn't work inside the sandbox.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import pathlib
import secrets
import sys
import tarfile
import time

import sandbox as sb

SPIKE_DIR = pathlib.Path(__file__).resolve().parent
MIGRATE_AND_USER = (
    "from django.contrib.auth import get_user_model; import os;"
    "get_user_model().objects.create_superuser(os.environ['SPIKE_USER'], 'spike@example.invalid', os.environ['SPIKE_PASSWORD'])"
)


def tar_of(name: str, content: bytes) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        info = tarfile.TarInfo(name)
        info.size = len(content)
        archive.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


def main() -> int:
    results_dir = pathlib.Path(sys.argv[1])
    image = os.environ["REPROFIX_IMAGE"]
    report: dict = {"image": image, "steps": {}, "ok": False}

    def record(name: str, result: sb.ExecResult) -> None:
        report["steps"][name] = {
            "exit_code": result.exit_code, "timed_out": result.timed_out, "seconds": round(result.duration_s, 2),
        }
        if result.exit_code != 0:
            report["steps"][name]["output_tail"] = result.text[-1500:]

    start = time.monotonic()
    test_user = "spike"
    # Throwaway login for the target app. It exists only inside this container.
    test_password = secrets.token_urlsafe(16)
    env_flags = ["env", f"SPIKE_USER={test_user}", f"SPIKE_PASSWORD={test_password}"]
    run_id = None
    try:
        with sb.Sandbox(image) as box:
            run_id = box.run_id
            report["seconds_to_attested"] = round(time.monotonic() - start, 2)
            report["attested_runtime"] = box.attestation["HostConfig"]["Runtime"]
            report["attested_network"] = box.attestation["HostConfig"]["NetworkMode"]

            result = box.exec(["cp", "-r", "/opt/linkding", "/workspace/app"])
            record("copy_app", result)
            if result.exit_code != 0:
                return finish(report, results_dir)

            marker = secrets.token_hex(8).encode()
            result = box.exec(["tar", "-xf", "-", "-C", "/workspace/app"], stdin=tar_of("spike_marker.txt", marker))
            record("load_by_tar_stream", result)
            check = box.exec(["cat", "/workspace/app/spike_marker.txt"])
            report["tar_stream_verified"] = check.stdout == marker

            result = box.exec(["sh", "-c", f"cd /workspace/app && {' '.join(env_flags)} python manage.py shell -c \"{MIGRATE_AND_USER}\""])
            record("create_test_user", result)
            if result.exit_code != 0:
                return finish(report, results_dir)

            result = box.exec(["sh", "-c", "cd /workspace/app && nohup python manage.py runserver --noreload 127.0.0.1:9090 > /tmp/server.log 2>&1 & echo started"])
            record("start_app", result)

            probe = "import urllib.request,time\nfor _ in range(60):\n  try:\n    print(urllib.request.urlopen('http://127.0.0.1:9090/login/',timeout=2).status); break\n  except Exception: time.sleep(1)\nelse:\n  raise SystemExit(1)\n"
            result = box.exec(["python", "-"], stdin=probe.encode(), timeout=90)
            record("app_responding", result)
            if result.exit_code != 0:
                log = box.exec(["tail", "-c", "1500", "/tmp/server.log"])
                report["server_log_tail"] = log.text
                return finish(report, results_dir)

            script = (SPIKE_DIR / "browser_smoke.py").read_bytes()
            result = box.exec([*env_flags, "python", "-"], stdin=script, timeout=box.profile.command_timeout_s)
            record("browser_smoke", result)
            if result.exit_code == 0:
                report["browser"] = json.loads(result.stdout)
                image_bytes = box.exec(["cat", "/tmp/linkding.png"], max_bytes=box.profile.max_artifact_bytes)
                (results_dir / "linkding.png").write_bytes(image_bytes.stdout)
                report["screenshot_sha256"] = hashlib.sha256(image_bytes.stdout).hexdigest()
                report["screenshot_is_png"] = image_bytes.stdout[:8] == b"\x89PNG\r\n\x1a\n"
            else:
                log = box.exec(["tail", "-c", "1500", "/tmp/server.log"])
                report["server_log_tail"] = log.text
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {error}"

    report["container_destroyed"] = run_id is not None and not sb.exists(run_id)
    report["total_seconds"] = round(time.monotonic() - start, 2)
    browser = report.get("browser", {})
    report["ok"] = bool(
        report.get("tar_stream_verified")
        and report.get("screenshot_is_png")
        and "bookmarks" in browser.get("url", "")
        and str(browser.get("external_navigation", "")).startswith("blocked")
        and report["container_destroyed"]
    )
    return finish(report, results_dir)


def finish(report: dict, results_dir: pathlib.Path) -> int:
    (results_dir / "spike-app.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    print("SPIKE PASSED" if report["ok"] else "SPIKE FAILED")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
