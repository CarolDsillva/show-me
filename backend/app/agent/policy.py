"""Patch policy. The model proposes exact text edits, and code turns them into a diff.

Deterministic. A patch that fails any rule is rejected, and the reason is shown.
"""

from __future__ import annotations

import dataclasses
import difflib
import fnmatch
import hashlib
import re

MAX_FILES = 5
MAX_CHANGED_LINES = 150
ALLOWED = (re.compile(r"^bookmarks/.+\.py$"), re.compile(r"^bookmarks/templates/.+$"))
FORBIDDEN = (
    "bookmarks/migrations/*", "bookmarks/settings/*", "bookmarks/tests/*", "bookmarks/tests_e2e/*",
    "*/test_*", "*/conftest.py", "bookmarks/management/*", "bookmarks/wsgi.py", "bookmarks/urls.py",
)


# A fix to a web app has no need for these, and they could fake or interrupt the tests. This is a
# fixed list, so it raises the bar and doesn't prove a patch is harmless. A person still reads it.
SUSPICIOUS = re.compile(
    r"os\._exit|sys\.exit|\bos\.system\b|\bos\.kill\b|\bsubprocess\b|\bsocket\b|\bpytest\b|\bunittest\b"
    r"|\beval\s*\(|\bexec\s*\(|__import__|\bimportlib\b|\bbuiltins\b|\bctypes\b|\bpickle\b"
)


@dataclasses.dataclass(frozen=True)
class Edit:
    path: str
    old_text: str
    new_text: str


@dataclasses.dataclass
class Patch:
    diff: str
    sha256: str
    files: dict[str, str]
    changed_lines: int
    reasons: list[str]

    @property
    def passed(self) -> bool:
        return not self.reasons


def path_allowed(path: str) -> bool:
    if ".." in path.split("/") or path.startswith("/") or "\\" in path:
        return False
    if not any(pattern.match(path) for pattern in ALLOWED):
        return False
    return not any(fnmatch.fnmatch(path, pattern) for pattern in FORBIDDEN)


def parse_edits(raw: object) -> list[Edit]:
    if not isinstance(raw, list) or not raw:
        raise ValueError("A proposal needs at least one edit")
    edits = []
    for item in raw[:50]:
        if not isinstance(item, dict):
            raise ValueError("Each edit must be an object")
        path, old, new = item.get("path"), item.get("old_text"), item.get("new_text")
        if not all(isinstance(value, str) for value in (path, old, new)):
            raise ValueError("Each edit needs path, old_text and new_text as strings")
        edits.append(Edit(path, old, new))
    return edits


def _file_diff(path: str, before: str, after: str) -> str:
    lines = list(difflib.unified_diff(
        before.splitlines(keepends=True), after.splitlines(keepends=True), f"a/{path}", f"b/{path}",
    ))
    return "".join(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n" for line in lines)


def build_patch(edits: list[Edit], read_source) -> Patch:
    """Apply edits to pristine text only. Nothing is executed or written to disk here."""
    reasons: list[str] = []
    originals: dict[str, str] = {}
    updated: dict[str, str] = {}
    for edit in edits:
        if not path_allowed(edit.path):
            reasons.append(f"{edit.path}: this path isn't allowed to change")
            continue
        if edit.path not in originals:
            try:
                originals[edit.path] = read_source(edit.path)
            except Exception:
                reasons.append(f"{edit.path}: this file couldn't be read at the pinned commit")
                continue
            updated[edit.path] = originals[edit.path]
        occurrences = updated[edit.path].count(edit.old_text) if edit.old_text else 0
        if occurrences != 1:
            reasons.append(f"{edit.path}: old_text must occur exactly once, found {occurrences}")
            continue
        updated[edit.path] = updated[edit.path].replace(edit.old_text, edit.new_text)
    changed = {path: text for path, text in updated.items() if text != originals[path]}
    if not changed and not reasons:
        reasons.append("The edits change nothing")
    if len(changed) > MAX_FILES:
        reasons.append(f"The patch changes {len(changed)} files, and the limit is {MAX_FILES}")
    diff = "".join(_file_diff(path, originals[path], changed[path]) for path in sorted(changed))
    changed_lines = sum(
        1 for line in diff.splitlines()
        if line[:1] in ("+", "-") and not line.startswith(("+++", "---"))
    )
    for line in diff.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            match = SUSPICIOUS.search(line)
            if match:
                reasons.append(f"“{match.group(0).strip()}” is not allowed in a fix")
                break
    if changed_lines > MAX_CHANGED_LINES:
        reasons.append(f"The patch changes {changed_lines} lines, and the limit is {MAX_CHANGED_LINES}")
    return Patch(diff, hashlib.sha256(diff.encode()).hexdigest(), changed, changed_lines, reasons)
