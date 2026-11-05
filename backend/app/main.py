"""reprofix-api: runs as rfapi, which can't reach Docker. It reaches sandboxd only by its socket."""

from __future__ import annotations

import contextlib

from fastapi import FastAPI

from app.api.routes import router
from app.core.config import load_settings
from app.core.worker import Worker
from app.db.store import Store
from app.evidence.signing import Signer
from app.evidence.store import EvidenceStore


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    settings = load_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    app.state.settings = settings
    app.state.store = Store(settings.data_dir / "app.sqlite3")
    app.state.signer = Signer(settings.data_dir / "signing.key")
    app.state.evidence = EvidenceStore(settings.data_dir / "evidence", app.state.signer)
    app.state.worker = Worker(settings, app.state.store, app.state.evidence)
    app.state.worker.recover_unfinished()
    yield


app = FastAPI(title="Show Me API", version="0.2.0", docs_url=None, redoc_url=None, lifespan=lifespan)
app.include_router(router)
