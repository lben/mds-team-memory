# One-command update from a fresh pull

Use `tools/deploy.toml` for both UAT and PROD. Copy the example once, enter
`username@server`, SSH/app ports and distinct deployment directories, then run:

```powershell
.\Update.cmd       # UAT (default)
.\Update.cmd PROD  # production
```

Enter the server password once. Models and Linux dependency wheels are
included as verified parts in `deployment/offline/`; the command uploads,
joins, decompresses and installs them. Python 3.12.14 and uv 0.12.10 are bundled,
as is the built UI. Server installation is offline and runs as your SSH user.
The web process and ML worker use nohup and keep running after logout.

Your deploying PC needs Git, uv and internet access for uv's first installation
of the pinned Paramiko client (or a pre-populated uv cache). No npm or Docker
is needed for normal Update runs. Allow roughly 10 GiB free on the PC for the
Git objects and checked-out assets. The Linux server needs RHEL 8.10 x86_64,
SSH/SFTP, bash, tar, sha256sum, a writable local filesystem, and approximately
14 GiB free for first-install staging, including the 2 GiB reserve. Permit the
configured app port through the existing firewall; Update cannot change a
firewall as an ordinary user. Four CPUs and the worker's configured memory
allowance must be available. UAT and PROD on one host need distinct roots and
application ports.

Defaults `uv = "bundled"` and `python = "bundled"` avoid server-side prerequisite
installation. To use IT-managed binaries, set absolute paths in the same TOML;
Python must be 3.12 with SQLite >= 3.51.3. Extra MDS settings belong to
`[uat.env]`/`[prod.env]`; Update owns the data/model path settings.

`--check` validates the TOML, all local part hashes, the UI and its source
fingerprint, and the dependency lock without connecting. Initial SSH host trust
is recorded under `build/update-known-hosts`; a changed key is rejected.
Alternatively configure `host_key_sha256` from IT to avoid the first trust prompt.
Neither passwords nor private server configuration enter the Git repository.

Updates finish preparation before stopping the old release. A failed startup
attempts compatible recovery, preserving the shared database and its human
feedback. Model generations and shared runtimes are pinned per release;
unchanged models are verified and reused on the next update. Success means the
web health check and ML worker lease passed. It does not establish extraction
quality or server throughput; release-quality validation is still pending.

After the first deployment, create the first admin on the server with
`bash <root>/mdsctl.sh manage create-admin`. Admin creation does not affect
whether the app or automatic worker can start. After a server reboot, run
`bash <root>/mdsctl.sh start`; nohup survives logout but does not restart on boot.

The sections below retain the native SSH controller, manual installation and
recovery reference. The manual prerequisites and model-transfer steps apply
when using `tools/deploy.py` directly; `Update` bundles those prerequisites and
performs those transfers automatically.

---

# MDS Team Knowledge — server setup

The application server is a plain RedHat box (tested against RHEL 8.10) and an
ordinary user account on it. Deploying, running, restarting and rolling back all
happen as that user — no root, no sudo, no systemd — and the server never needs
Node, because the frontend is compiled on the dev machine and shipped already
built. The one thing an administrator has to do, once, is open the port.

What the account needs:

- SSH access, ideally with key-based login — a deploy runs several commands and
  will otherwise ask for your password each time.
- `uv` and an approved Python 3.12 already installed. Deployment never downloads
  Python. Check before the first deploy with
  `ssh <server> uv python find --offline --no-python-downloads 3.12`.
  You can point `python` in `tools/deploy.toml` at the installed interpreter,
  e.g. `python = "/home/deployer/python/cpython-3.12/bin/python3.12"`. A deploy
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
  run/ml.pid         pid of the optional ML worker
  logs/ml.log        worker output
  ml/generations/    verified immutable models, wheels and dependency locks
  ml/runtimes/       shared ML environments, keyed by lock and Python identity
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
release. It waits for `/api/health` and, when configured, the matching worker's
queue lease before calling it a success. Both processes stop before the backup
and migration. A worker start failure triggers the same release recovery as a
web start failure.

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

`uv run --python 3.12 tools\serverctl.py rollback` first checks that the previous
release supports the shared database. If compatible, it stops both processes,
points `current` back at that release and starts its web server and worker.
Each release's `ml-runtime.json` pins its own generation and runtime. A later
asset setting in `app.env` does not change those rollback pins.

Once migration 0015 creates the explicit topic feedback schema, a release must
support `explicit-topic-feedback-v1` to use that database. The controller checks
the database schema and target code itself, without relying on an older release's
helper. It refuses incompatible rollback, activation, web startup and worker
startup. Restart and deployment also check before stopping processes. A refused
rollback leaves the current processes, `current` and `previous` links, and human
feedback intact. Direct `stop` remains available when shutdown is needed.

If rollback is refused, inspect `status` and `logs`, then deploy a corrected
release that supports the current schema. Keep using the controller uploaded
with the newer release; do not replace it with an old copy to bypass the check.
You can check a candidate before activation on the server with
`bash <root>/mdsctl.sh compatible <stamp>`.

Rolling back does not make the release you are leaving the next rollback target.
That matters after a failed deploy, which rolls back on its own: `previous` keeps
naming the release that was working, so a second rollback cannot put the broken
one back. To go forward again, fix the problem and deploy.

The schema is not rolled back with the code. The database copy taken before a
migration is kept in `<root>/backups/<stamp>/`; automatic recovery never restores
it. Restoring it would discard later human feedback and other writes, so a
compatibility refusal calls for a corrected release, not a database restore.

For a separate disaster recovery that explicitly accepts losing those writes,
stop both processes and preserve the current database together with its `-wal`
and `-shm` files before restoring anything. Restore the backup as a complete set;
never leave a newer WAL beside an older database. Uploaded files are not part of
the backup; only the database is. Migration 0015 also refuses Alembic downgrade
because that would destroy confirmation history.

## Company package index

If your uv configuration is not already in `~/.config/uv/uv.toml`, put the
`uv.toml` at the deploy root (`<root>/uv.toml`). uv discovers it from any
release directory beneath, and deploys never overwrite it.

## Offline ML installation

ML is configured once per server. Contributors then use the ordinary app.
There is no per-finding admin activation step.

The approved bundle targets RHEL 8.10, x86_64, glibc 2.28 and Python 3.12.
The installed Python must include SQLite 3.51.3 or newer. The current Update
bootstrap supplies Python 3.12.14 with SQLite 3.53.1 and uv separately from the
model/wheel bundle. For manual installation without Update, prepare or transfer
these binaries before setup; manual setup cannot bootstrap through a blocked
download endpoint.

Transfer the trusted parts and manifest into `<root>/ml/transfer/`. Use
`tools/ml_assets.py assemble` and `tools/ml_bundle.py extract` with
`--managed-root <root>/ml` to produce a new `<root>/ml/generations/<name>/`.
Use their `--help` for the archive and manifest arguments. Keep all transfer
staging, unpacked generations and ML environments inside this same managed root.
The extracted generation must contain `bundle.json`, `models/`, `wheels/`, and
`requirements-linux.lock`. The lock must match the one shipped with the release.
The tools verify lengths and hashes before publishing a generation.

Set these entries in the target's environment table in `tools/deploy.toml`:

```toml
[uat.env]
MDS_ML_GENERATION = "/home/deployer/apps/mds-uat/ml/generations/v1"
# MDS_ML_ROOT defaults to /home/deployer/apps/mds-uat/ml
```

Deploy normally. Setup verifies every prepared file and installs only local
wheels, with network access and Python downloads disabled. It creates a separate
ML runtime and installs the web dependency subset into the release's `.venv`.
Generations and completed runtimes are immutable; upgrades use a new generation.
Retain the generations and runtimes referenced by every release you may roll back
to. Release pruning does not delete shared ML assets. Remove an unused generation,
runtime or transfer archive only after checking those release references.

Managed assets, runtime and transfer staging have a combined 16 GiB ceiling.
Every allocation must leave 2 GiB free. Setup includes both extraction and
installation copies in its peak estimate and keeps temporary files in the managed
root. It refuses an upgrade that cannot fit; it does not delete uploads or raise
the budget. The web environment, database and uploads are separate application
storage. Account for their growth independently.

`serverctl.py preflight` reports the actual OS, glibc, SQLite, CPU features,
allowed affinity, local database filesystem, free space and user process limits.
Setup and worker start run these checks too. Unknown or network filesystems are
rejected for ML. The deployment database must remain `MDS_DATA_DIR/mds.sqlite3`,
the file used by backup and reset. The worker uses at most four CPUs from its
allowed affinity, low scheduling priority and the existing 8 GiB process-tree
RSS ceiling. RSS monitoring can briefly overshoot; it is not a hard kernel
memory cap. Use an approved user-level cgroup if a hard quota is required.
The September 29 four-model compatibility check used four CPUs and a 5 GiB
hard container limit without swap. Historical three-model checks also used
4 GiB limits. The earlier combined load check allowed eight CPUs for the web
app, load driver and ML, while verifying that ML stayed within four CPUs;
its total memory limit was 4 GiB. The new verifier package has not repeated
that capacity benchmark. See `deployment/VERIFICATION.md` for current evidence.
These container measurements do not establish production
Xeon throughput or host kernel and filesystem behavior.

```powershell
uv run --python 3.12 tools\serverctl.py ml-status
uv run --python 3.12 tools\serverctl.py ml-stop
uv run --python 3.12 tools\serverctl.py ml-start
uv run --python 3.12 tools\serverctl.py ml-logs --lines 100
uv run --python 3.12 tools\serverctl.py preflight
```

`stop`, `restart`, and `rollback` control both processes. `ml-stop` leaves web
capture, ordinary search and the last published findings available. Durable work
waits for `ml-start`; status explicitly reports a stopped worker. `health` checks
web availability only. Backup, migration, activation and reset stop ML first.
The database backup includes derived ML tables. A database reset deliberately
removes them with the rest of the test data.

The worker uses fixed pretrained models and conservative evidence rules. It does
not train models or fit decision policies. Model scores are not validated
probabilities. Existing fitted policies are ignored; recomputation replaces
their finding scores with the fixed rules while preserving admin overrides.

Grade frozen inference decisions from labeled test cases with `python -m
app.ml.evaluation --help`. An optional `--development-groups` JSON array lists
authored development fixture groups so the evaluator can reject overlap.
The frozen precision, recall and sample-size requirements are in
`ML_IMPLEMENTATION_PLAN.md`. Generated test cases and integration checks do not
establish accuracy on the team's content. Sparse evaluation data reports
insufficient evidence, and admin silence is never a positive label.

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
