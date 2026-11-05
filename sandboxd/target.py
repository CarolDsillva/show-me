"""Prepares the target app inside a running, already attested sandbox."""

from __future__ import annotations

import dataclasses
import io
import pathlib
import secrets
import tarfile

import sandbox as sb

DRIVER_DIR = pathlib.Path(__file__).resolve().parent / "driver"
RF_DIR = "/workspace/rf"
WAIT_READY = (
    "import urllib.request,time\n"
    "for _ in range(60):\n"
    "  try:\n"
    "    urllib.request.urlopen('http://127.0.0.1:9090/login/',timeout=2); break\n"
    "  except Exception: time.sleep(1)\n"
    "else:\n"
    "  raise SystemExit(1)\n"
)


class TargetError(RuntimeError):
    """The app couldn't be started inside the sandbox."""


class PatchError(RuntimeError):
    """The patch couldn't be applied inside the sandbox."""


@dataclasses.dataclass(frozen=True)
class TestLogin:
    """Generated per run. It exists only inside that one sandbox and never reaches a prompt."""

    user: str
    password: str

    def env(self) -> list[str]:
        return ["env", f"RF_USER={self.user}", f"RF_PASSWORD={self.password}"]


def new_login() -> TestLogin:
    return TestLogin("rf" + secrets.token_hex(4), secrets.token_urlsafe(18))


def tar_of(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for name, content in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            info.mode = 0o600
            archive.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


def _check(result: sb.ExecResult, step: str) -> None:
    if result.exit_code != 0:
        raise TargetError(f"{step} failed with exit {result.exit_code}: {result.text[-600:]}")


def load_files(box: sb.Sandbox, files: dict[str, bytes]) -> None:
    """Stream files in. Nothing from the sandbox is ever unpacked on the host."""
    _check(box.exec(["mkdir", "-p", RF_DIR]), "mkdir")
    _check(box.exec(["tar", "-xf", "-", "-C", RF_DIR], stdin=tar_of(files)), "load files")


def copy_app(box: sb.Sandbox, patch: str | None = None) -> None:
    """Copy the app to tmpfs and apply a patch there. A patch is never applied on the host."""
    _check(box.exec(["cp", "-r", "/opt/linkding", "/workspace/app"]), "copy app")
    if patch:
        applied = box.exec(["patch", "-p1", "--batch", "--forward", "-d", "/workspace/app"], stdin=patch.encode())
        if applied.exit_code != 0:
            raise PatchError(f"The patch didn't apply: {applied.text[-600:]}")


def prepare(box: sb.Sandbox, login: TestLogin, patch: str | None = None) -> None:
    """Prepare the app, seed it, start it and wait until it answers."""
    copy_app(box, patch)
    load_files(box, {
        "seed.py": (DRIVER_DIR / "seed.py").read_bytes(),
        "browser_driver.py": (DRIVER_DIR / "browser_driver.py").read_bytes(),
    })
    _check(box.exec(["sh", "-c", f"cd /workspace/app && {' '.join(login.env())} python {RF_DIR}/seed.py"], timeout=90), "seed")
    _check(
        box.exec(["sh", "-c", "cd /workspace/app && nohup python manage.py runserver --noreload 127.0.0.1:9090 > /tmp/server.log 2>&1 & echo started"]),
        "start app",
    )
    _check(box.exec(["python", "-"], stdin=WAIT_READY.encode(), timeout=90), "wait for app")
