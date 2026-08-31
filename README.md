# MDS Team Knowledge

Team knowledge base MVP built around a single main window: a knowledge graph on
top that grows with every contribution, one input whose text becomes a Search,
an Ask, or a Capture, and two columns below — latest knowledge on the left,
questions on the right (questions matching your expertise first; while
searching, matched questions with accepted answers first). Scratchpad,
Documents, and the Leaderboard are separate screens. Search uses SQLite FTS5
with concept-alias expansion; recognition is outcome-based.

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

## Resetting an instance

For testing a new build against a clean slate:

```bash
python manage.py reset-database        # prompts before deleting anything
python manage.py reset-database --yes  # for scripts; no prompt
```

It lists what will be destroyed, deletes the database and every uploaded file,
then migrates back up to the current schema. Because it deletes rather than
downgrades, it works from any previous schema version — including one the
current build has never seen. Stop the application first, and create an admin
again afterwards.

**This destroys all data.** Without `--yes` it requires you to type `reset` at a
terminal, and it refuses outright when there is no terminal.

The **Expertise Routing** link stays visible in the sidebar for everyone, but
opening it asks for admin credentials. The concept, alias, and expertise-mapping
tools appear only after a successful sign-in, in that browser.

## The knowledge graph

Concepts and their aliases are defined by an admin; any contribution, answer, or
extracted document passage mentioning one is tagged automatically. When team content mentions two
concepts together (once by default, `MDS_COOCCURRENCE_MIN`), a link between
them is **suggested** and drawn dashed for everyone; raise the threshold if the
map gets noisy. Solid edges are confirmed; dashed edges are automatically detected.

The graph lives at the top of the Home page: the full map by default, focused on
the concepts a search mentions. Admins curate links, concepts, and relationship
types from the table on the Expertise Routing page:

- **Approve** a suggested link to make it solid, or **reject** it to hide it from
  the map. A rejected link is not forgotten: it stays in the table, its
  occurrence count keeps rising as new content mentions both concepts, and it can
  be inspected and re-approved at any time.
- **Occurrences** opens the actual contributions and document passages behind a
  link. Anyone can read this evidence view; only admins can change anything.
- Add a link by hand by picking two concepts, a relationship type, and a note
  that becomes its recorded evidence.
- **Concepts** and **Relationship types** are managed in the other tabs.
  Renaming a relationship type updates every link using it; a type can only be
  deleted once nothing uses it. `related to` and `corroborates` are built in and
  protected.

Private scratchpad content never contributes to a link, a count, or the evidence
view, and no action on this page can delete a teammate's contribution.

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
