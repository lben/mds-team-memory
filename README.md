# MDS Team Memory

Team knowledge base MVP: quick capture, keyword search (SQLite FTS5), Q&A with
expertise routing, private scratchpads, document passages with exact locators,
automatic knowledge graphs, and outcome-based impact tracking.

- Backend: Python 3.12, FastAPI, SQLAlchemy, Alembic, SQLite + FTS5
- Frontend: Vue 3, TypeScript, Vite, Cytoscape.js
- One FastAPI process serves both the API and the compiled UI

## Development

```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
./.venv/bin/alembic -c backend/alembic.ini upgrade head
./.venv/bin/uvicorn app.main:app --app-dir backend --reload   # API on :8000

cd frontend && npm install && npm run dev                     # UI on :5173 (proxies /api)
```

For a production-like run, `npm run build` then open `http://127.0.0.1:8000`.

## Tests

```bash
./.venv/bin/python -m pytest backend/tests -q          # API workflow tests
./.venv/bin/python -m pytest e2e -q                    # Playwright browser journey (needs npm run build)
```

Playwright browsers install once with `./.venv/bin/playwright install chromium`.

## Deployment (UAT / PROD)

Configure once: copy `tools/deploy.example.toml` to `tools/deploy.toml` and set
the destinations. Then:

```bash
python tools/deploy.py uat    # or: prod
```

This builds the frontend, assembles a Node-free release, and copies it to the
target. Server-side steps are in `SERVER_SETUP.md`. On first use the app asks
the installer to create the first admin account under **Expertise Routing**.
