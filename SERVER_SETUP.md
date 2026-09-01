# MDS Team Knowledge — server setup

The application server is a plain RedHat box (tested against RHEL 8.10) and an
ordinary user account on it. Deploying, running, restarting and rolling back all
happen as that user — no root, no sudo, no systemd — and the server never needs
Node, because the frontend is compiled on the dev machine and shipped already
built. The one thing an administrator has to do, once, is open the port.

What the account needs:

- SSH access, ideally with key-based login — a deploy runs several commands and
  will otherwise ask for your password each time.
- `uv` on the PATH, able to produce a Python 3.12 interpreter — RHEL 8 ships
  3.6, so uv either downloads one or uses a 3.12 already installed. Check before
  the first deploy with `ssh <server> uv python find 3.12`. If your `uv.toml`
  sets `python-downloads = "never"`, or the server cannot reach the internet,
  point `python` in `tools/deploy.toml` at an interpreter that exists, e.g.
  `python = "/usr/bin/python3.12"`. A deploy
  runs over a non-interactive SSH session, which may not source the profile that
  puts `~/.local/bin` on the PATH — if `ssh <server> uv --version` fails while an
  interactive login works, set the full path as `uv` in `tools/deploy.toml`.
- A writable directory, e.g. `/home/deployer/apps/mds-uat`. The deploy creates
  it on first run.
- A free TCP port above 1024 (8000 by default). Binding a lower port needs root.
  Opening that port in firewalld is the one step here an administrator has to do,
  once: without it the deploy still reports success — it health-checks the server
  from the server — but nobody else can reach the app.
- Ideally `loginctl enable-linger <user>` (the user can normally run this for
  themselves). It keeps the user's processes alive after the SSH session that
  started them ends, on hosts configured with `KillUserProcesses=yes`, and it is
  what makes the `@reboot` crontab line below dependable.

Everything below is driven from the Windows work machine, in PowerShell, with
`tools/deploy.py` and `tools/serverctl.py`, run through the same uv-managed
Python 3.12 you develop with. Commands shown as shell run **on the RedHat
server**, either through `serverctl` or over `ssh`. See the Deployment section
of `README.md` for the one-time `tools/deploy.toml` setup.

## What a deploy leaves on the server

```
<root>/
  mdsctl.sh          the control script; replaced by every deploy
  app.env            port, uv path and MDS_* settings, written by the deploy
  uv.toml            optional: your company uv configuration (see below)
  releases/<stamp>/  one unpacked release with its own .venv and RELEASE.txt,
                     which names the target and when it was built
  current -> releases/<stamp>      the release being served
  previous -> releases/<stamp>     the one before it, for rollback
  data/              database and uploads, shared by every release
  backups/<stamp>/   database copy taken before that release's migration
  run/app.pid        pid of the running server
  logs/app.log       its output
```

Releases are self-contained and disposable; `data/` is the only directory worth
backing up. Old releases are pruned after a successful deploy (five are kept,
`keep_releases` in `tools/deploy.toml`), except the current and previous ones,
which are never removed. The database copies in `backups/` are never pruned —
they are small and they are the only way back from a bad migration, so clear
them out yourself when they are no longer worth keeping. `logs/app.log` is
appended to forever; truncate it when it gets large (`: > logs/app.log`, which
the running server copes with).

## Deploying

From the Windows machine, in PowerShell:

```powershell
uv run --python 3.12 tools\deploy.py         # UAT, the default
uv run --python 3.12 tools\deploy.py prod    # asks you to type 'prod' to confirm
```

A deploy builds the frontend, uploads one archive, creates the new release's
environment with uv while the old release keeps serving, then stops the server,
backs up the database, migrates it, swaps `current` over and starts the new
release. It waits for `/api/health` to answer before calling it a success.

If any step fails, the deploy puts the server back the way it found it — the old
release restarted, or never stopped at all if the failure came before that — and
exits non-zero. The code comes back; `app.env` does not, since it is replaced at
the start of a deploy, so a settings change survives a failed one.

One thing a failed deploy cannot undo is a migration that got part of the way.
Revisions are applied one at a time, and a revision that fails half way through
can still leave its earlier statements behind — a failed migration was observed
leaving a new table in place while the recorded schema version stayed where it
was. The old code is restarted against whatever schema it reached, which is
usually harmless, and the deploy prints where the copy of the database taken
just beforehand is. That is what `backups/` is for; see Rolling back. Existing data is preserved throughout; migrations upgrade it in
place.

## The first deploy

Create the first administrator once the deploy has finished. Contributors sign
themselves up in the app; admins are CLI-only. `ssh` ships with Windows, so this
runs in the same PowerShell session:

```powershell
ssh -t deployer@server bash /home/deployer/apps/mds-uat/mdsctl.sh manage create-admin
```

Run `manage` through `mdsctl.sh` rather than calling `manage.py` directly:
`mdsctl.sh` points it at the shared `data/` directory. A bare
`python manage.py create-admin` inside a release directory would read and write
a different, empty database and the account would not exist as far as the
running app is concerned.

Then open `http://<server>:8000`.

## Day-to-day control

From the Windows machine (each command takes an optional `uat` / `prod`, default
`uat`):

```powershell
uv run --python 3.12 tools\serverctl.py                # what is deployed, running, healthy
uv run --python 3.12 tools\serverctl.py start          # after a server reboot
uv run --python 3.12 tools\serverctl.py restart
uv run --python 3.12 tools\serverctl.py stop
uv run --python 3.12 tools\serverctl.py health         # exits non-zero if it is not serving
uv run --python 3.12 tools\serverctl.py logs --lines 200
uv run --python 3.12 tools\serverctl.py releases
uv run --python 3.12 tools\serverctl.py rollback prod  # back to the previous release
```

The same commands work while logged in on the server itself, which is the way
back if the Windows machine is not to hand:

```bash
bash ~/apps/mds-uat/mdsctl.sh start
bash ~/apps/mds-uat/mdsctl.sh status
```

`start` waits for the server to answer `/api/health` and fails loudly with the
last lines of the log if it does not, so a successful `start` means the app is
actually up rather than merely launched.

### After a reboot

Nothing starts the app automatically — that is the trade for not having root.
Either run `uv run --python 3.12 tools\serverctl.py start` when you notice, or have the
server user's own crontab do it (no root needed, `crontab -e` on the server):

```cron
@reboot /bin/bash /home/deployer/apps/mds-uat/mdsctl.sh start >> /home/deployer/apps/mds-uat/logs/cron.log 2>&1
```

## Rolling back

`uv run --python 3.12 tools\serverctl.py rollback` stops the server, points `current` back
at the previous release and starts it.

Rolling back does not make the release you are leaving the next rollback target.
That matters after a failed deploy, which rolls back on its own: `previous` keeps
naming the release that was working, so a second rollback cannot put the broken
one back. To go forward again, fix the problem and deploy.

The schema is not rolled back with the code. If the release you are leaving ran
a migration, the database copy taken just before it is in
`<root>/backups/<stamp>/`. Restore it by hand only if the older code cannot read
the newer schema, and replace the whole set of files rather than just the
database — SQLite keeps recent writes in the `-wal` file, and leaving a newer
one next to an older database corrupts it:

In PowerShell, stop the server:

```powershell
uv run --python 3.12 tools\serverctl.py stop uat
```

then on the server:

```bash
rm -f ~/apps/mds-uat/data/mds.sqlite3 ~/apps/mds-uat/data/mds.sqlite3-wal ~/apps/mds-uat/data/mds.sqlite3-shm
cp ~/apps/mds-uat/backups/<stamp>/* ~/apps/mds-uat/data/
```

Uploaded files are not part of that copy; only the database is.

## Company package index

If your uv configuration is not already in `~/.config/uv/uv.toml`, put the
`uv.toml` at the deploy root (`<root>/uv.toml`). uv discovers it from any
release directory beneath, and deploys never overwrite it.

## Resetting a UAT instance

To wipe a test instance and rebuild it at the current schema:

```powershell
uv run --python 3.12 tools\serverctl.py stop uat
ssh -t deployer@server bash /home/deployer/apps/mds-uat/mdsctl.sh manage reset-database
uv run --python 3.12 tools\serverctl.py start uat
```

It prints what will be destroyed and asks you to type `reset` to confirm
(`--yes` skips the prompt for scripted use). It deletes the database and all
uploaded files rather than downgrading, so it works from any previous schema
version. Create an admin again afterwards.

**Never run this against production.** Check the database path it prints before
confirming.

## Configuration

Port, bind address, uv path, Python version and how many releases to keep are
set per target in `tools/deploy.toml` on the dev machine and written into
`<root>/app.env` by each deploy. Application settings go in that target's
`[uat.env]` / `[prod.env]` table and land in the same file:

| Variable | Default | Purpose |
| --- | --- | --- |
| `MDS_DATA_DIR` | `<root>/data` | SQLite database and uploaded files; set by the deploy |
| `MDS_SECURE_COOKIES` | `0` | set `1` when serving over HTTPS |
| `MDS_SIMILARITY_THRESHOLD` | `0.95` | duplicate-grouping similarity (0–1) |
| `MDS_COOCCURRENCE_MIN` | `1` | co-mentions before a concept link is suggested; raise it if the map gets noisy |
| `MDS_MAX_UPLOAD_BYTES` | `26214400` | upload size limit (25 MB) |
| `MDS_SESSION_HOURS` | `12` | how long a signed-in session lasts |

Editing `app.env` on the server works until the next deploy replaces it, so put
anything you want to keep in `tools/deploy.toml`.
