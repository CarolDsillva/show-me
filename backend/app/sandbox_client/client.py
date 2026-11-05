"""Client for sandboxd over its Unix socket. Requests name a profile, never limits."""

from __future__ import annotations

import httpx

PROFILE = "browser-session"


class SandboxUnavailable(RuntimeError):
    """Fail closed. There is no fallback that runs anything outside the sandbox."""


class SessionEnded(RuntimeError):
    """The sandbox is gone, for example because gVisor died at a limit."""


class SandboxClient:
    def __init__(self, socket_path: str, transport: httpx.BaseTransport | None = None) -> None:
        self.http = httpx.Client(
            transport=transport or httpx.HTTPTransport(uds=socket_path),
            base_url="http://sandboxd",
            timeout=httpx.Timeout(300, connect=10),
        )

    def start_session(self, session_key: str) -> dict:
        response = self._post("/v1/sessions", {"session_key": session_key, "profile": PROFILE, "target_ref": "baseline"})
        if response.status_code != 201:
            raise SandboxUnavailable(f"sandboxd returned {response.status_code}: {response.text[:300]}")
        return response.json()

    def act(self, session_id: str, action: dict) -> dict:
        response = self._post(f"/v1/sessions/{session_id}/actions", action)
        if response.status_code == 409:
            raise SessionEnded(response.text[:300])
        if response.status_code == 422:
            return {"ok": False, "error": f"Action refused: {response.text[:300]}", "refused": True}
        if response.status_code != 200:
            raise SandboxUnavailable(f"sandboxd returned {response.status_code}")
        return response.json()

    def end_session(self, session_id: str) -> dict:
        response = self.http.delete(f"/v1/sessions/{session_id}")
        response.raise_for_status()
        return response.json()

    def replay(self, run_key: str, script: str, script_sha256: str) -> dict:
        response = self._post("/v1/runs", {
            "run_key": run_key, "profile": PROFILE, "target_ref": "baseline", "kind": "replay",
            "script": script, "script_sha256": script_sha256,
        })
        if response.status_code != 200:
            raise SandboxUnavailable(f"sandboxd returned {response.status_code}: {response.text[:300]}")
        return response.json()

    def host(self) -> dict:
        response = self.http.get("/v1/host")
        response.raise_for_status()
        return response.json()

    def _post(self, path: str, body: dict) -> httpx.Response:
        try:
            return self.http.post(path, json=body)
        except httpx.TransportError as error:
            raise SandboxUnavailable(f"sandboxd unreachable: {type(error).__name__}") from error
