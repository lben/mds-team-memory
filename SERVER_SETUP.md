# MDS Team Knowledge — server setup

The server only needs **Python 3.12+**. No Node, Docker, or admin rights required.
Works on Windows 11 (PowerShell), macOS, and Linux.

## First-time setup

From the deployed folder:

```powershell
# Windows PowerShell
py -3.12 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

```bash
# macOS / Linux
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Every deploy (including the first)

1. Apply database migrations (existing data is preserved):

   ```powershell
   .venv\Scripts\alembic -c backend\alembic.ini upgrade head
   ```

2. Create an administrator account (first deploy only — repeat any time you need
   another admin). The password is prompted for and never echoed:

   ```powershell
   .venv\Scripts\python manage.py create-admin
   ```

   `manage.py list-admins` shows the accounts that already exist.

3. Start (or restart) the application — one process serves the API and the UI:

   ```powershell
   .venv\Scripts\uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port 8000
   ```

4. Open `http://<server>:8000`. The **Expertise Routing** link stays visible in
   the sidebar, but opening it asks for admin credentials; the mapping tools
   appear only after a successful sign-in, in that browser only.

## Resetting a test instance

To wipe a UAT instance and rebuild it at the current schema:

```powershell
.venv\Scripts\python manage.py reset-database
```

It prints what will be destroyed and asks you to type `reset` to confirm
(`--yes` skips the prompt for scripted use). It deletes the database and all
uploaded files rather than downgrading, so it works from any previous schema
version. Stop the application first, then create an admin and restart.

**Never run this against production.** Check the database path it prints before
confirming.

## Configuration (environment variables, all optional)

| Variable | Default | Purpose |
| --- | --- | --- |
| `MDS_DATA_DIR` | `./data` next to the app | SQLite database and uploaded files |
| `MDS_SECURE_COOKIES` | `0` | set `1` when serving over HTTPS |
| `MDS_SIMILARITY_THRESHOLD` | `0.95` | duplicate-grouping similarity (0–1) |
| `MDS_COOCCURRENCE_MIN` | `1` | co-mentions before a concept link is suggested; raise it if the map gets noisy |
| `MDS_MAX_UPLOAD_BYTES` | `26214400` | upload size limit (25 MB) |

The `data/` folder holds everything worth backing up.
