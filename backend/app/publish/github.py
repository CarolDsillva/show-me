"""Publishing to our own fork. Never upstream, and the target comes only from server config.

Untrusted text only ever goes inside code fences, so GitHub can't turn it into mentions,
cross-references to other repositories, links or images.
"""

from __future__ import annotations

import base64
import re

import httpx

from app.core.config import ALLOWED_REPOSITORIES

API = "https://api.github.com"
EVIDENCE_BRANCH = "reprofix-evidence"
LABEL = "reprofix"
BASE_BRANCH = "reprofix-baseline"


class PublishRefused(RuntimeError):
    """The target isn't one of our forks."""


def fenced(text: str, language: str = "text") -> str:
    """A code fence longer than any backtick run inside, so the text can't break out."""
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}{language}\n{text}\n{fence}"


def safe_title(text: str, limit: int = 80) -> str:
    """A title can't be fenced, so remove what GitHub would act on."""
    cleaned = re.sub(r"[@#<>\[\]()`!\r\n\t]", " ", text)
    cleaned = re.sub(r"https?://\S+", " ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()[:limit] or "Reproduced customer bug"


def task_marker(task_id: str) -> str:
    return f"<!-- reprofix-task:{task_id} -->"


class Publisher:
    def __init__(self, token: str, repository: str, transport: httpx.BaseTransport | None = None) -> None:
        if repository not in ALLOWED_REPOSITORIES:
            raise PublishRefused(f"Refusing to publish to {repository}")
        self.repository = repository
        self.http = httpx.Client(
            base_url=API,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "reprofix",
            },
            timeout=60,
            transport=transport,
        )

    def _repo(self, path: str) -> str:
        return f"/repos/{self.repository}{path}"

    def _ensure_evidence_branch(self) -> None:
        if self.http.get(self._repo(f"/branches/{EVIDENCE_BRANCH}")).status_code == 200:
            return
        master = self.http.get(self._repo("/git/ref/heads/master"))
        master.raise_for_status()
        created = self.http.post(self._repo("/git/refs"), json={
            "ref": f"refs/heads/{EVIDENCE_BRANCH}", "sha": master.json()["object"]["sha"],
        })
        if created.status_code not in (201, 422):  # 422: another run just created it.
            created.raise_for_status()

    def upload_screenshot(self, task_id: str, name: str, png: bytes) -> str:
        """Store on an evidence branch, because the API has no issue attachments. Idempotent."""
        if not png.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("Not a PNG")
        if not re.fullmatch(r"[a-z0-9-]{1,40}\.png", name) or not re.fullmatch(r"[a-f0-9]{32}", task_id):
            raise ValueError("Bad evidence path")
        self._ensure_evidence_branch()
        path = f"evidence/{task_id}/{name}"
        url = f"https://raw.githubusercontent.com/{self.repository}/{EVIDENCE_BRANCH}/{path}"
        existing = self.http.get(self._repo(f"/contents/{path}"), params={"ref": EVIDENCE_BRANCH})
        if existing.status_code == 200:
            return url
        response = self.http.put(self._repo(f"/contents/{path}"), json={
            "message": f"Evidence for ReproFix task {task_id}",
            "content": base64.b64encode(png).decode(),
            "branch": EVIDENCE_BRANCH,
        })
        response.raise_for_status()
        return url

    def find_issue(self, task_id: str) -> str | None:
        response = self.http.get(self._repo("/issues"), params={"labels": LABEL, "state": "all", "per_page": 100})
        response.raise_for_status()
        marker = task_marker(task_id)
        for issue in response.json():
            if marker in (issue.get("body") or ""):
                return issue["html_url"]
        return None

    def file_issue(self, task_id: str, title: str, body: str) -> str:
        """One issue per task, even if this runs again after a restart."""
        existing = self.find_issue(task_id)
        if existing:
            return existing
        response = self.http.post(self._repo("/issues"), json={
            "title": safe_title(title), "body": f"{task_marker(task_id)}\n{body}", "labels": [LABEL],
        })
        response.raise_for_status()
        return response.json()["html_url"]


class SourceAndPullRequests:
    """Reads the pinned target source, and opens pull requests. Always within our own fork."""

    def __init__(self, publisher: Publisher, commit: str, base_branch: str = BASE_BRANCH) -> None:
        self.publisher = publisher
        self.http = publisher.http
        self.commit = commit
        self.base_branch = base_branch

    def list_files(self) -> list[str]:
        response = self.http.get(self.publisher._repo(f"/git/trees/{self.commit}"), params={"recursive": "1"})
        response.raise_for_status()
        return [item["path"] for item in response.json()["tree"] if item["type"] == "blob"]

    def read_file(self, path: str) -> str:
        if ".." in path or path.startswith("/"):
            raise ValueError("Bad path")
        response = self.http.get(
            self.publisher._repo(f"/contents/{path}"), params={"ref": self.commit},
            headers={"Accept": "application/vnd.github.raw+json"},
        )
        response.raise_for_status()
        return response.text

    def open_pull_request(self, task_id: str, title: str, body: str, files: dict[str, str]) -> str:
        """One branch and one pull request per task, even if this runs again after a restart."""
        if not re.fullmatch(r"[a-f0-9]{32}", task_id):
            raise ValueError("Bad task ID")
        branch = f"reprofix/fix-{task_id[:16]}"
        owner = self.publisher.repository.split("/")[0]
        existing = self.http.get(self.publisher._repo("/pulls"), params={"head": f"{owner}:{branch}", "state": "all"})
        existing.raise_for_status()
        if existing.json():
            return existing.json()[0]["html_url"]
        ref = self.http.post(self.publisher._repo("/git/refs"), json={"ref": f"refs/heads/{branch}", "sha": self.commit})
        if ref.status_code not in (201, 422):
            ref.raise_for_status()
        for path, content in files.items():
            current = self.http.get(self.publisher._repo(f"/contents/{path}"), params={"ref": branch})
            current.raise_for_status()
            update = self.http.put(self.publisher._repo(f"/contents/{path}"), json={
                "message": f"ReproFix patch for task {task_id}",
                "content": base64.b64encode(content.encode()).decode(),
                "sha": current.json()["sha"],
                "branch": branch,
            })
            update.raise_for_status()
        response = self.http.post(self.publisher._repo("/pulls"), json={
            "title": safe_title(title), "head": branch, "base": self.base_branch, "body": body,
        })
        response.raise_for_status()
        return response.json()["html_url"]


def pull_request_body(*, issue_url: str | None, explanation: str, rule: str, script_sha256: str, patch_sha256: str) -> str:
    issue = f"Bug report: {issue_url}\n\n" if issue_url else ""
    return f"""> Opened by ReproFix after a developer approved it.

{issue}## Result

{rule}.

Script SHA-256: `{script_sha256}`. Patch SHA-256: `{patch_sha256}`.

## Explanation (written by a model, untrusted)

{fenced(explanation)}
"""


def issue_body(
    *, ticket: str, summary: str, script: str, script_sha256: str, replays: list[dict], rule: str,
    repository: str, commit: str, screenshot_url: str | None, manifest_note: str,
) -> str:
    rows = "\n".join(
        f"| {i} | `{r.get('exit_code')}` | {r.get('termination')} | {(r.get('attestation') or {}).get('runtime')} | "
        f"{(r.get('attestation') or {}).get('network_mode')} | {(r.get('destruction') or {}).get('destroyed')} |"
        for i, r in enumerate(replays, start=1)
    )
    picture = f"\n## Failing state\n\n![Screenshot from the failing replay]({screenshot_url})\n" if screenshot_url else ""
    return f"""> Filed automatically by ReproFix. Reproduced in a real browser inside a disposable sandbox.

## Result

{rule}.

| Replay | Exit | Ended as | Runtime | Network | Container destroyed |
|---|---|---|---|---|---|
{rows}

Exit `42` means every step worked and the expected behavior didn't happen.
{picture}
## Customer's words (untrusted)

{fenced(ticket)}

## Summary (written by a model, untrusted)

{fenced(summary)}

## Frozen reproduction

Target: `{repository}` at `{commit}`. Script SHA-256: `{script_sha256}`.

{fenced(script, "python")}

{manifest_note}
"""
