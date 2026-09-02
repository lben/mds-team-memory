from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import config
from .routers import (
    admin,
    auth,
    documents,
    expertise,
    feed,
    graph,
    impact,
    items,
    notifications,
    profile,
    questions,
    scratchpad,
)
from .routers import search as search_router

app = FastAPI(title="MDS Team Knowledge")

for r in (
    auth.router,
    profile.router,
    expertise.router,
    feed.router,
    items.router,
    questions.router,
    search_router.router,
    scratchpad.router,
    documents.router,
    graph.router,
    impact.router,
    notifications.router,
    notifications.ws_router,
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

    # Namespaces the server owns. The SPA has no client-side route under these,
    # so a request that gets here is for something that does not exist — and it
    # must say so. Answering it with index.html returns HTTP 200 and HTML to a
    # caller expecting JSON, which surfaces as a JSON parse error instead of a
    # 404 and makes a removed or mistyped endpoint look like it still works.
    SERVER_NAMESPACES = ("api/", "ws/")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        if full_path.startswith(SERVER_NAMESPACES):
            raise HTTPException(404, "Not found")
        candidate = FRONTEND_DIST / full_path
        if full_path and candidate.is_file() and candidate.resolve().is_relative_to(FRONTEND_DIST):
            return FileResponse(candidate)
        return FileResponse(FRONTEND_DIST / "index.html")
