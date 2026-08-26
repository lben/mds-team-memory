# MDS Team Memory — server setup

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

4. Open `http://<server>:8000`. Ordinary users never see an Admin section. An
   administrator signs in by going to `/admin/expertise` directly; the Admin
   navigation appears only after a successful sign-in, in that browser only.

## Configuration (environment variables, all optional)

| Variable | Default | Purpose |
| --- | --- | --- |
| `MDS_DATA_DIR` | `./data` next to the app | SQLite database and uploaded files |
| `MDS_SECURE_COOKIES` | `0` | set `1` when serving over HTTPS |
| `MDS_SIMILARITY_THRESHOLD` | `0.95` | duplicate-grouping similarity (0–1) |
| `MDS_COOCCURRENCE_MIN` | `3` | co-mentions needed for an inferred concept link |
| `MDS_MAX_UPLOAD_BYTES` | `26214400` | upload size limit (25 MB) |

The `data/` folder holds everything worth backing up.
