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

Current deployment branch: `gpt6.1solhigh_bge`. The offline package contains
GLiNER2.5-base, BGE-large-en-v1.5 and spaCy; BGE supplies both embeddings and
concept relevance. See [BGE verification](deployment/BGE_VERIFICATION.md) for
deployment evidence and the failed model-quality and capacity gates.

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

## Accounts

Anyone can create a contributor account from the app itself — the profile button
at the bottom of the sidebar. Until they do, they are identified by a cookie in
that one browser: they can use everything, but their contributions and their
scratchpad live in that browser only and are destroyed by clearing cookies.
Creating an account claims the work already done in that browser, once — the
first account to sign in on a browser absorbs its anonymous contributions, and
nobody after that can.

Signing in changes who the app thinks you are everywhere: attribution, the
leaderboard, the scratchpad and the admin area all follow the account.

Expertise can only be routed to someone with an account, because a name that
lives in one browser disappears with its cookies.

### Administrator accounts

The first admin is created from the command line, on the machine running the
app. Signing yourself up in the UI never grants admin rights; an existing admin
can create further admins from the Expertise Routing page.

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
opening it asks for admin credentials. Someone signed in without admin rights is
told so, rather than being shown an empty page. The concept, alias, and expertise-mapping
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

A fresh pull includes the built UI, numbered model/dependency parts and the
Linux Python/uv bootstrap. Copy `tools/deploy.example.toml` to
`tools/deploy.toml` once and fill in both server sections. That one TOML file
holds SSH usernames/IPs, SSH and application ports, deployment directories,
optional binary paths and application settings. Passwords are entered in the
console and are never saved in the file.

From the repository root on your Windows work machine (with uv installed):

```powershell
git pull
Copy-Item tools/deploy.example.toml tools/deploy.toml # first time only; edit it
.\Update.cmd --check                               # optional local validation
.\Update.cmd                                       # UAT, default
.\Update.cmd PROD                                  # PROD
```

The equivalent command is `uv run --python 3.12 tools/update.py [UAT|PROD]`.
On macOS/Linux, `./Update [UAT|PROD]` is also available. uv installs the pinned
SSH client on the deploying PC on its first run. The server needs RHEL 8.10
x86_64, SSH/SFTP access, bash/tar/sha256sum and writable local storage; it needs
neither Node nor internet access, sudo or systemd. The TOML template explains
these requirements and optional binary overrides.

Update prompts for your server password once and reuses that SSH connection.
The first connection also asks you to trust the displayed SSH host fingerprint,
unless you configured the fingerprint supplied by IT. It transfers the app and
verified model parts, joins and decompresses them on the server, installs the
bundled offline wheels, migrates the shared database, and starts both the web
server and ML worker with `nohup`. It reports success only after the web health
check and worker lease succeed. Unchanged, verified models are reused on later
updates. Closing the deploying console leaves the processes running.

Releases remain side by side, sharing the database and uploads. Preparation
finishes while the old release serves; a failed activation attempts compatible
rollback without restoring a database over human writes. Releases older than
the explicit topic feedback contract (migration 0015) cannot be activated
against a migrated database. Model quality acceptance is still pending; the
deployment package is the current development candidate.

For subsequent operations, the existing native SSH controller remains available:

```powershell
uv run --python 3.12 tools/serverctl.py              # release, process, health
uv run --python 3.12 tools/serverctl.py start        # after a server reboot
uv run --python 3.12 tools/serverctl.py restart prod
uv run --python 3.12 tools/serverctl.py logs --lines 200
uv run --python 3.12 tools/serverctl.py rollback
```

These controller commands use ordinary SSH and may ask for passwords per
command. On the server, run `bash <root>/mdsctl.sh <command>`.
`SERVER_SETUP.md` covers the first administrator, backups and recovery.
