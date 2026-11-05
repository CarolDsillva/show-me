"""Public API. Ticket, page and output text is untrusted, and clients must render it as text."""

from __future__ import annotations

import asyncio
import datetime as dt
import json

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from app.core.worker import BudgetUsedUp, QueueFull
from app.db.store import DONE
from app.tickets.intake import MAX_LENGTH, MIN_LENGTH, SAMPLES, clean_ticket

router = APIRouter(prefix="/api")
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


class TicketRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=MIN_LENGTH, max_length=MAX_LENGTH)


def client_ip(request: Request) -> str:
    """Caddy appends the real client address, so the last entry is the one to trust."""
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[-1].strip()[:64]
    return request.client.host if request.client else "unknown"


def task_view(task: dict) -> dict:
    return {
        "id": task["id"], "kind": task["kind"], "status": task["status"], "outcome": task["outcome"],
        "outcome_rule": task["outcome_rule"], "issue_url": task["issue_url"], "pull_request_url": None,
        "diff": None, "explanation": task["explanation"], "created_at": task["created_at"],
        "ticket": task["ticket"],
    }


def get_task_or_404(request: Request, task_id: str) -> dict:
    task = request.app.state.store.get_task(task_id)
    if task is None:
        raise HTTPException(404, "Unknown task")
    return task


@router.post("/tasks", status_code=202)
def create_task(body: TicketRequest, request: Request) -> dict:
    app_state = request.app.state
    ip = client_ip(request)
    hour_ago = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1)).isoformat()
    if app_state.store.count_recent_from(ip, hour_ago) >= app_state.settings.tickets_per_ip_per_hour:
        raise HTTPException(429, "Too many tickets from this address. Try again later.")
    try:
        ticket = clean_ticket(body.text)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    try:
        task_id = app_state.worker.submit(ticket, ip)
    except QueueFull as error:
        raise HTTPException(429, "The queue is full. Try again in a few minutes.") from error
    except BudgetUsedUp as error:
        raise HTTPException(429, "Today's model budget is used up.") from error
    return task_view(app_state.store.get_task(task_id))


@router.get("/samples")
def samples() -> list[dict]:
    return SAMPLES


@router.get("/tasks/{task_id}")
def get_task(task_id: str, request: Request) -> dict:
    return task_view(get_task_or_404(request, task_id))


@router.get("/tasks/{task_id}/events")
async def task_events(task_id: str, request: Request) -> StreamingResponse:
    get_task_or_404(request, task_id)
    store = request.app.state.store

    async def stream():
        last_seq = -1
        while not await request.is_disconnected():
            for event in store.events_after(task_id, last_seq):
                last_seq = event["seq"]
                yield f"id: {event['seq']}\ndata: {json.dumps(event)}\n\n"
            if store.get_task(task_id)["status"] == DONE and not store.events_after(task_id, last_seq):
                return
            await asyncio.sleep(0.5)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-store"})


@router.get("/tasks/{task_id}/manifest")
def task_manifest(task_id: str, request: Request) -> dict:
    get_task_or_404(request, task_id)
    manifest = request.app.state.evidence.read_manifest(task_id)
    if manifest is None:
        raise HTTPException(404, "No manifest yet")
    return manifest


@router.get("/tasks/{task_id}/artifacts/{sha256}")
def task_artifact(task_id: str, sha256: str, request: Request) -> Response:
    get_task_or_404(request, task_id)
    data = request.app.state.evidence.get_blob(task_id, sha256)
    if data is None:
        raise HTTPException(404, "Unknown artifact")
    is_png = data.startswith(PNG_MAGIC)
    return Response(
        content=data,
        media_type="image/png" if is_png else "application/octet-stream",
        headers={
            "Content-Disposition": f'{"inline" if is_png else "attachment"}; filename="{sha256[:16]}{".png" if is_png else ".bin"}"',
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
        },
    )


@router.get("/health")
def health(request: Request) -> dict:
    settings = request.app.state.settings
    return {"status": "ok", "commit": settings.commit, "fix_phase_enabled": settings.fix_phase_enabled}


@router.get("/health/host")
def health_host(request: Request) -> dict:
    return request.app.state.worker.sandbox.host()
