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
./.venv/bin/python manage.py create-admin                     # admin accounts are CLI-only
./.venv/bin/uvicorn app.main:app --app-dir backend --reload   # API on :8000

cd frontend && npm install && npm run dev                     # UI on :5173 (proxies /api)
```

For a production-like run, `npm run build` then open `http://127.0.0.1:8000`.

## Administrator accounts

Admins are created only from the command line, on the machine running the app —
there is no sign-up or first-run setup in the UI:

```bash
python manage.py create-admin                 # prompts for username and password
python manage.py create-admin --username jane # prompts for the password only
python manage.py list-admins                  # show existing accounts
```

The password is never echoed and never appears in shell history. Run
`create-admin` again whenever you need another admin; usernames must be unique
and passwords must be at least 8 characters. Run the database migrations first —
the command tells you if the database is not initialised yet.

The **Expertise Routing** link stays visible in the sidebar for everyone, but
opening it asks for admin credentials. The concept, alias, and expertise-mapping
tools appear only after a successful sign-in, in that browser.

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
target. Server-side steps are in `SERVER_SETUP.md`.

Administrator accounts are created only with `python manage.py create-admin` on
the server — there is no sign-up in the UI. See **Administrator accounts** above.
