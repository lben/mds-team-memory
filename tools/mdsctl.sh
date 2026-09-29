#!/usr/bin/env bash
# Control script for MDS Team Knowledge on the application server.
#
# It is uploaded to <root>/mdsctl.sh by tools/deploy.py and replaced by every
# deploy. Run it directly on the server (`bash ~/apps/mds-uat/mdsctl.sh start`)
# or through tools/serverctl.py from the dev machine.
#
# Everything here runs as an unprivileged user: no sudo, no systemd, no port
# below 1024. The layout it owns:
#
#   <root>/mdsctl.sh          this script
#   <root>/app.env            PORT/BIND/UV/... written by the deploy tool
#   <root>/releases/<stamp>/  one unpacked release, with its own .venv
#   <root>/current            symlink to the serving release
#   <root>/previous           symlink to the release before it (rollback target)
#   <root>/data               database and uploads, shared by every release
#   <root>/backups/<stamp>/   database copy taken before each migration
#   <root>/run/app.pid        pid of the running server
#   <root>/logs/app.log       its stdout and stderr

set -eu

ROOT="$(cd "$(dirname "$0")" && pwd)"
RELEASES="$ROOT/releases"
CURRENT="$ROOT/current"
PREVIOUS="$ROOT/previous"
BACKUPS="$ROOT/backups"
PIDFILE="$ROOT/run/app.pid"
LOG="$ROOT/logs/app.log"
ML_PIDFILE="$ROOT/run/ml.pid"
ML_LOG="$ROOT/logs/ml.log"

# Defaults for a hand-made server folder; deploy.py overwrites them in app.env.
PORT=8000
BIND=0.0.0.0
UV=uv
PYTHON_VERSION=3.12
KEEP=5
ENV_FILE="${MDS_ENV_FILE:-$ROOT/app.env}"
if [ -f "$ENV_FILE" ]; then
  set -a
  . "$ENV_FILE"
  set +a
fi
export MDS_DATA_DIR="${MDS_DATA_DIR:-$ROOT/data}"
export UV_PYTHON_DOWNLOADS=never

# The address to check the server on: the one it was told to bind, except for
# the wildcards, where 127.0.0.1 is the address that actually answers.
case "$BIND" in
  0.0.0.0|::|"") HEALTH_URL="http://127.0.0.1:$PORT/api/health" ;;
  *) HEALTH_URL="http://$BIND:$PORT/api/health" ;;
esac

mkdir -p "$RELEASES" "$BACKUPS" "$ROOT/run" "$ROOT/logs" "$MDS_DATA_DIR"

die() { echo "mdsctl: $*" >&2; exit 1; }

release_dir() {
  [ -n "${1:-}" ] || die "$2 needs a release stamp"
  case "$1" in */*|..|.) die "invalid release stamp '$1'";; esac
  echo "$RELEASES/$1"
}

current_release() {
  [ -L "$CURRENT" ] || die "no current release; run a deploy first"
  readlink "$CURRENT"
}

# This check belongs to the controller, not the target release's deploylib:
# a rollback target can predate the schema and have no compatibility check.
# Read only schema metadata and literal capability declarations; never import
# target application code or restore an older database over human writes.
require_compatible_database() {
  local release interpreter
  release="$1"
  [ -d "$release" ] || die "$release does not exist"
  if [ -x "$release/.venv/bin/python" ]; then
    interpreter="$release/.venv/bin/python"
  elif [ -x "$CURRENT/.venv/bin/python" ]; then
    interpreter="$CURRENT/.venv/bin/python"
  else
    interpreter="$("$UV" python find --offline --no-python-downloads "$PYTHON_VERSION")" \
      || die "cannot inspect database compatibility without an installed Python"
  fi
  "$interpreter" -I - "$release" <<'PY' || die "database compatibility check failed; no process or release link was changed by this command"
import ast
import os
from pathlib import Path
import sqlite3
import sys

release = Path(sys.argv[1]).resolve()
url = os.environ.get("MDS_DATABASE_URL", "sqlite:///" + str(Path(os.environ["MDS_DATA_DIR"]) / "mds.sqlite3"))
if not url.startswith("sqlite:///") or "?" in url or url.endswith(":memory:"):
    sys.exit("Compatibility check requires a file-backed sqlite:/// database URL without query parameters.")
database = Path(url[len("sqlite:///"):])
if not database.is_absolute():
    database = release / database
try:
    if not database.exists():
        sys.exit(0)
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=5) as db:
        explicit_topics = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='topic_confirmations'").fetchone()
    if explicit_topics:
        def literal(path, name):
            for node in ast.parse(path.read_text(encoding="utf-8")).body:
                if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
                    return ast.literal_eval(node.value)
            return None

        contract = literal(release / "backend/app/topic_feedback.py", "CONTRACT_VERSION")
        migration = literal(release / "backend/alembic/versions/0015_explicit_topic_feedback.py", "revision")
        if contract != "explicit-topic-feedback-v1" or migration != "0015":
            raise ValueError("target does not declare the explicit-topic-feedback-v1 / 0015 contract")
except (OSError, sqlite3.Error, ValueError, SyntaxError) as error:
    sys.exit("Cannot use release {} with database {}: {}. Choose a release supporting explicit topic feedback; preserve the shared database and its human confirmations.".format(release.name, database, error))
PY
}

cmd_compatible() {
  local release
  release="$(release_dir "${1:-}" compatible)"
  require_compatible_database "$release"
  echo "database is compatible with $1"
}

# The command line of a running process. /proc is always there on Linux, and
# procps (ps) is not: a minimal RedHat install can lack it, and falling back to
# an empty answer would make every pid look dead and start a second server.
process_args() {
  if [ -r "/proc/$1/cmdline" ]; then
    tr '\0' ' ' < "/proc/$1/cmdline"
  else
    ps -p "$1" -o args= 2>/dev/null
  fi
}

# Prints the pid of our own server, or fails. Checking the command line as well
# as the pid matters: a stale pid file can name a pid the kernel has since
# handed to somebody else's process, and stop must never kill that.
running_pid() {
  [ -f "$PIDFILE" ] || return 1
  pid="$(cat "$PIDFILE" 2>/dev/null || true)"
  case "$pid" in ''|*[!0-9]*) return 1;; esac
  # Matched on this target's own release path, which is in the uvicorn argv[0]:
  # it tells our server apart from another target's on the same host, and unlike
  # the port it cannot change under a running process. Matching the port here
  # would make a changed port look like a stopped server and start a second one
  # against the same database.
  args="$(process_args "$pid")"
  case "$args" in *"$RELEASES/"*) ;; *) return 1 ;; esac
  case "$args" in *app.main:app*) ;; *) return 1 ;; esac
  echo "$pid"
}

running_ml_pid() {
  [ -f "$ML_PIDFILE" ] || return 1
  local pid args
  pid="$(cat "$ML_PIDFILE" 2>/dev/null || true)"
  case "$pid" in ''|*[!0-9]*) return 1;; esac
  args="$(process_args "$pid")"
  case "$args" in "$RELEASES/"*"/.ml-venv/bin/python -m app.ml.worker --assets "*) ;; *) return 1;; esac
  echo "$pid"
}

cmd_ml_stop() {
  local pid i
  if ! pid="$(running_ml_pid)"; then
    rm -f "$ML_PIDFILE"
    echo "ML: stopped"
    return 0
  fi
  kill "$pid" 2>/dev/null || true
  i=0
  while [ "$i" -lt 20 ] && running_ml_pid >/dev/null; do
    i=$((i + 1))
    sleep 1
  done
  if running_ml_pid >/dev/null; then
    kill -9 "$pid" 2>/dev/null || true
    sleep 1
  fi
  running_ml_pid >/dev/null && die "ML worker $pid did not stop; database operation refused"
  rm -f "$ML_PIDFILE"
  echo "ML: stopped (pid $pid)"
}

cmd_ml_start() {
  local release assets pid args i
  release="$(current_release)"
  require_compatible_database "$release"
  if [ ! -f "$release/ml-runtime.json" ]; then
    running_ml_pid >/dev/null && die "a worker from another release is running; stop it first"
    echo "ML: not configured for this release"
    return 0
  fi
  if pid="$(running_ml_pid)"; then
    args="$(process_args "$pid")"
    case "$args" in "$release/.ml-venv/bin/python "*) echo "ML: running (pid $pid)"; return 0;; esac
    die "a worker from another release is running; stop it before activation"
  fi
  "$release/.ml-venv/bin/python" "$release/tools/deploylib.py" preflight --root "$ROOT" --release "$release" >/dev/null
  assets="$("$release/.venv/bin/python" "$release/tools/deploylib.py" ml-runtime --root "$ROOT" --release "$release")"
  rm -f "$ML_PIDFILE"
  (
    cd "$release" || exit 1
    export PYTHONPATH="$release/backend" HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
    export OMP_NUM_THREADS=4 MKL_NUM_THREADS=4 TOKENIZERS_PARALLELISM=false PYTHONDONTWRITEBYTECODE=1
    nohup "$release/.ml-venv/bin/python" -m app.ml.worker --assets "$assets" >>"$ML_LOG" 2>&1 </dev/null &
    echo $! >"$ML_PIDFILE"
  )
  i=0
  while [ "$i" -lt 20 ]; do
    sleep 1
    running_ml_pid >/dev/null || break
    if PYTHONPATH="$release/backend" "$release/.venv/bin/python" -m app.ml.worker --status 2>/dev/null \
       | "$release/.venv/bin/python" -c 'import json,sys; s=json.load(sys.stdin); sys.exit(0 if s["worker_active"] else 1)' 2>/dev/null; then
      echo "ML: started for $(basename "$release") (pid $(cat "$ML_PIDFILE"))"
      return 0
    fi
    i=$((i + 1))
  done
  tail -n 20 "$ML_LOG" >&2 || true
  cmd_ml_stop
  die "ML worker did not acquire its queue lease; web capture remains available"
}

cmd_ml_status() {
  local release pid args
  release="$(current_release)"
  if [ ! -f "$release/ml-runtime.json" ]; then
    echo "ML: not configured for this release"
    return 0
  fi
  if pid="$(running_ml_pid)"; then
    args="$(process_args "$pid")"
    case "$args" in "$release/.ml-venv/bin/python "*) echo "ML: running (pid $pid)";;
      *) die "ML worker belongs to another release; restart is required";; esac
  else
    echo "ML: stopped; queued work is retained"
  fi
  PYTHONPATH="$release/backend" "$release/.venv/bin/python" -m app.ml.worker --status
}

cmd_ml_logs() {
  [ -f "$ML_LOG" ] || { echo "no ML log yet"; return 0; }
  tail -n "${1:-100}" "$ML_LOG"
}

health_once() {
  if command -v curl >/dev/null 2>&1; then
    curl -fsS --max-time 5 "$HEALTH_URL" >/dev/null 2>&1
  else
    [ -L "$CURRENT" ] || return 1
    "$(current_release)/.venv/bin/python" - "$HEALTH_URL" <<'PY' >/dev/null 2>&1
import sys, urllib.request
urllib.request.urlopen(sys.argv[1], timeout=5).read()
PY
  fi
}

# Our own process is checked before the health URL, and not the other way round:
# if the port is already held by something else, ours dies on the bind and the
# answer on that URL comes from the other process. Asking about health first
# would report a successful start of a server that is not running.
wait_healthy() {
  i=0
  while [ "$i" -lt 40 ]; do
    # The pause comes first so that a server dying on the bind has exited by the
    # time we look, and our process is checked again after the health answer,
    # because that answer may have come from whatever already holds the port.
    sleep 1
    running_pid >/dev/null || return 1
    if health_once && running_pid >/dev/null; then return 0; fi
    i=$((i + 1))
  done
  return 1
}

cmd_start() {
  release="$(current_release)"
  require_compatible_database "$release"
  if pid="$(running_pid)"; then
    health_once || die "pid $pid is running but not answering $HEALTH_URL"
    echo "already running (pid $pid)"
    cmd_ml_start
    return 0
  fi
  [ -x "$release/.venv/bin/uvicorn" ] || die "$release has no .venv; run 'setup' for it"
  rm -f "$PIDFILE"
  # nohup plus all three descriptors redirected is what keeps the server alive
  # after the ssh session that started it goes away.
  # Backgrounding one simple command keeps $! the pid of nohup, which execs
  # uvicorn in place: the pid file names the server, not a wrapper shell.
  (
    cd "$release" || exit 1
    nohup "$release/.venv/bin/uvicorn" app.main:app \
      --app-dir backend --host "$BIND" --port "$PORT" \
      >>"$LOG" 2>&1 </dev/null &
    echo $! >"$PIDFILE"
  )
  if wait_healthy; then
    echo "started $(basename "$release") on $BIND:$PORT (pid $(cat "$PIDFILE"))"
    cmd_ml_start
  else
    tail -n 20 "$LOG" >&2 || true
    cmd_stop >/dev/null 2>&1 || true
    die "server did not become healthy at $HEALTH_URL; last log lines above"
  fi
}

cmd_stop() {
  cmd_ml_stop
  if ! pid="$(running_pid)"; then
    rm -f "$PIDFILE"
    echo "not running"
    return 0
  fi
  kill "$pid" 2>/dev/null || true
  i=0
  # running_pid, not kill -0: a pid that has exited but not yet been reaped
  # still answers kill -0, and such a process holds neither the port nor
  # anything else worth waiting for.
  while [ "$i" -lt 15 ] && running_pid >/dev/null; do
    i=$((i + 1))
    sleep 1
  done
  if running_pid >/dev/null; then
    kill -9 "$pid" 2>/dev/null || true
    sleep 1
  fi
  # Saying "stopped" about a process that survived SIGKILL would hide the reason
  # the next start fails: that process still holds the port.
  running_pid >/dev/null && die "pid $pid would not die; it still holds port $PORT"
  rm -f "$PIDFILE"
  echo "stopped (pid $pid)"
}

cmd_restart() {
  require_compatible_database "$(current_release)"
  cmd_stop
  cmd_start
}

cmd_status() {
  if [ -L "$CURRENT" ]; then
    echo "release: $(basename "$(readlink "$CURRENT")")"
  else
    echo "release: none deployed"
  fi
  if [ -L "$PREVIOUS" ] && [ "$(readlink "$PREVIOUS")" != "$(readlink "$CURRENT")" ]; then
    echo "rollback target: $(basename "$(readlink "$PREVIOUS")")"
  fi
  if pid="$(running_pid)"; then
    echo "process: running (pid $pid)"
  else
    echo "process: stopped"
  fi
  if health_once; then
    echo "health: ok on $HEALTH_URL"
  else
    echo "health: not answering on $HEALTH_URL"
  fi
  echo "data: $MDS_DATA_DIR"
  if [ -L "$CURRENT" ]; then cmd_ml_status; fi
}

cmd_health() {
  health_once || die "no healthy server at $HEALTH_URL"
  echo "ok"
}

cmd_logs() {
  [ -f "$LOG" ] || { echo "no log yet; the server has not been started here"; return 0; }
  tail -n "${1:-100}" "$LOG"
}

cmd_releases() {
  for path in "$RELEASES"/*/; do
    [ -d "$path" ] || continue
    name="$(basename "$path")"
    # Both markers, appended: after a rollback one release is current and the
    # rollback target at once, and showing only the second would hide what is
    # actually serving.
    mark=""
    [ -L "$CURRENT" ] && [ "$(readlink "$CURRENT")" = "${path%/}" ] && mark=" (current)"
    [ -L "$PREVIOUS" ] && [ "$(readlink "$PREVIOUS")" = "${path%/}" ] && [ -z "$mark" ] \
      && mark=" (rollback target)"
    echo "$name$mark"
  done
}

cmd_unpack() {
  release="$(release_dir "${1:-}" unpack)"
  archive="$RELEASES/${1}.tar.gz"
  [ -f "$archive" ] || die "$archive not found"
  rm -rf "$release"
  mkdir -p "$release"
  tar -xzf "$archive" -C "$release"
  rm -f "$archive"
  echo "unpacked $1"
}

cmd_setup() {
  release="$(release_dir "${1:-}" setup)"
  if [ -L "$CURRENT" ] && [ "$(current_release)" = "$release" ]; then
    die "setup requires a new release; the current runtime must remain immutable"
  fi
  if [ -L "$PREVIOUS" ] && [ "$(readlink "$PREVIOUS")" = "$release" ]; then
    die "setup cannot change the rollback release"
  fi
  if [ -n "${MDS_ML_GENERATION:-}" ]; then
    interpreter="$("$UV" python find --offline --no-python-downloads "$PYTHON_VERSION")" \
      || die "install approved uv and Python 3.12 with SQLite 3.51.3+ before offline ML setup"
    "$interpreter" "$release/tools/deploylib.py" ml-setup --root "$ROOT" --release "$release" \
      --generation "$MDS_ML_GENERATION" --managed-root "${MDS_ML_ROOT:-$ROOT/ml}" --uv "$UV"
    return
  fi
  # cd into the release so uv finds the uv.toml placed anywhere above it.
  ( cd "$release" \
    && "$UV" venv --no-python-downloads --python "$PYTHON_VERSION" "$release/.venv" \
    && "$UV" pip install --python "$release/.venv/bin/python" -r "$release/requirements.txt" )
  echo "environment ready for $1"
}

# Keep a recovery snapshot before migration. Code rollback never restores it:
# doing so could discard human writes made after deployment.
cmd_backup() {
  [ -n "${1:-}" ] || die "backup needs a release stamp"
  cmd_ml_stop
  db="$MDS_DATA_DIR/mds.sqlite3"
  if [ ! -f "$db" ]; then
    echo "no database yet; nothing to back up"
    return 0
  fi
  running_pid >/dev/null && die "stop the server before backing up the database"
  dest="$BACKUPS/$1"
  mkdir -p "$dest"
  for suffix in "" "-wal" "-shm"; do
    [ -f "$db$suffix" ] && cp "$db$suffix" "$dest/"
  done
  echo "database backed up to $dest"
}

# manage.py must run with the same MDS_DATA_DIR as the server, or it quietly
# reads and writes a database inside the release directory that nothing serves.
cmd_manage() {
  release="$(current_release)"
  if [ "${1:-}" = "reset-database" ]; then cmd_stop; fi
  "$release/.venv/bin/python" "$release/manage.py" "$@"
}

cmd_migrate() {
  release="$(release_dir "${1:-}" migrate)"
  require_compatible_database "$release"
  cmd_ml_stop
  running_pid >/dev/null && die "stop the server before migration"
  "$release/.venv/bin/alembic" -c "$release/backend/alembic.ini" upgrade head
  echo "migrated to the schema of $1"
}

cmd_activate() {
  release="$(release_dir "${1:-}" activate)"
  require_compatible_database "$release"
  cmd_ml_stop
  running_pid >/dev/null && die "stop the server before activation"
  # Going back to the release 'previous' already names is a rollback: keep it
  # where it is. Rotating would make the release we are abandoning — typically
  # one that just failed to start — the next rollback target.
  if [ -L "$CURRENT" ] && [ "$(readlink "$CURRENT")" != "$release" ] \
     && ! { [ -L "$PREVIOUS" ] && [ "$(readlink "$PREVIOUS")" = "$release" ]; }; then
    ln -sfn "$(readlink "$CURRENT")" "$PREVIOUS"
  fi
  ln -sfn "$release" "$CURRENT"
  echo "current -> $1"
}

cmd_rollback() {
  [ -L "$PREVIOUS" ] || die "no previous release to roll back to"
  target="$(basename "$(readlink "$PREVIOUS")")"
  require_compatible_database "$(release_dir "$target" rollback)"
  cmd_stop
  cmd_activate "$target"
  cmd_start
}

cmd_preflight() {
  local release interpreter
  release="$(current_release)"
  require_compatible_database "$release"
  interpreter="$("$UV" python find --offline --no-python-downloads "$PYTHON_VERSION")"
  "$interpreter" "$release/tools/deploylib.py" preflight --root "$ROOT" --release "$release"
}

# Old releases are kept so rollback has somewhere to go; the current and
# previous ones are never deleted regardless of how far down the list they are.
# Database backups are never pruned: they are small, and they are the only way
# back from a migration the previous release cannot read.
cmd_prune() {
  keep="${1:-$KEEP}"
  protected=""
  [ -L "$CURRENT" ] && protected="$protected $(readlink "$CURRENT")"
  [ -L "$PREVIOUS" ] && protected="$protected $(readlink "$PREVIOUS")"
  index=0
  for path in $(ls -1d "$RELEASES"/*/ 2>/dev/null | sort -r); do
    path="${path%/}"
    index=$((index + 1))
    [ "$index" -le "$keep" ] && continue
    case " $protected " in *" $path "*) continue;; esac
    rm -rf "$path"
    echo "removed old release $(basename "$path")"
  done
}

command="${1:-}"
[ $# -gt 0 ] && shift
case "$command" in
  start|stop|restart|status|health|logs|releases|manage|unpack|setup|backup|migrate|activate|rollback|prune|ml-start|ml-stop|ml-status|ml-logs|preflight|compatible)
    "cmd_${command//-/_}" "$@"
    ;;
  *)
    die "usage: mdsctl.sh {start|stop|restart|status|health|logs|releases|rollback|ml-start|ml-stop|ml-status|ml-logs|preflight|manage ...|unpack|setup|backup|migrate|activate|prune}"
    ;;
esac
