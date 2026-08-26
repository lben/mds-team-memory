from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import config
from .db import SessionLocal
from .relationships import ensure_builtin_types
from .routers import admin, documents, graph, impact, items, notifications, profile, questions, scratchpad
from .routers import search as search_router

@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Seed the protected relationship vocabulary on databases that predate it."""
    db = SessionLocal()
    try:
        ensure_builtin_types(db)
    finally:
        db.close()
    yield


app = FastAPI(title="MDS Team Memory", lifespan=lifespan)

for r in (
    profile.router,
    items.router,
    questions.router,
    search_router.router,
    scratchpad.router,
    documents.router,
    graph.router,
    impact.router,
    notifications.router,
    admin.router,
):
    app.include_router(r)


@app.get("/api/health")
def health():
    return {"ok": True}


# Serve the compiled Vue application (frontend/dist) from the same process.
FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        candidate = FRONTEND_DIST / full_path
        if full_path and candidate.is_file() and candidate.resolve().is_relative_to(FRONTEND_DIST):
            return FileResponse(candidate)
        return FileResponse(FRONTEND_DIST / "index.html")
