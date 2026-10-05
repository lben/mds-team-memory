"""What the ML worker is doing now, and a read-only report of its queue.

The worker rewrites a small JSON file beside the database at every step, so
watching it adds no SQLite writes. Readers trust that file only while the
worker's queue lease is active and the file names that lease.
"""

from collections import deque
from contextlib import closing
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import time

RECENT = 20


def activity_path(database):
    database = Path(database)
    return database.with_name(database.name + ".ml-activity.json")


def lease_id(token):
    return hashlib.sha256(token.encode()).hexdigest()[:16] if token else None


class Recorder:
    """Written only from the supervisor's main thread. Never fails a job."""

    def __init__(self, path):
        self.path = Path(path)
        self.recent = deque(maxlen=RECENT)
        self.state = {"pid": os.getpid(), "started_at": time.time(), "lease": None,
                      "activity": None, "job": None, "recent": []}
        self.warned = False

    def write(self):
        self.state["written_at"] = time.time()
        self.state["recent"] = list(self.recent)
        temporary = self.path.with_name(f"{self.path.name}.{os.getpid()}.tmp")
        try:
            temporary.write_text(json.dumps(self.state), encoding="utf-8")
            os.replace(temporary, self.path)
        except OSError as error:
            if not self.warned:
                print(f"ML activity is not being reported: {error}", file=sys.stderr, flush=True)
                self.warned = True

    def lease(self, token):
        self.state["lease"] = lease_id(token)
        self.write()

    def activity(self, name, source=None):
        current = self.state["activity"]
        if current and current["name"] == name and current["source"] == source:
            return
        self.state["activity"] = {"name": name, "source": source, "since": time.time()}
        self.write()

    def start_job(self, claim):
        now = time.time()
        self.state["activity"] = {"name": "processing a job", "source": None, "since": now}
        self.state["job"] = {"kind": claim.source_kind, "id": claim.source_id, "claimed_at": now, "stage": "claimed", "stage_since": now, "progress": None}
        self.write()

    def stage(self, name):
        self.state["job"].update(stage=name, stage_since=time.time(), progress=None)
        self.write()

    def progress(self, note):
        self.state["job"]["progress"] = note
        self.write()

    def finish_job(self, outcome, error=None):
        job, now = self.state["job"], time.time()
        self.recent.appendleft({"kind": job["kind"], "id": job["id"], "outcome": outcome, "error": error,
                                "finished_at": now, "seconds": round(now - job["claimed_at"], 3)})
        self.state["job"] = None
        self.write()

    def stopped(self):
        if self.state["job"] is not None:
            self.finish_job("interrupted", "Worker stopped")
        self.activity("stopped")


def _snippet(value, length=90):
    value = " ".join((value or "").split())
    return value if len(value) <= length else value[:length - 1] + "…"


def source_label(db, kind, source_id):
    """Describe a source without revealing private or deleted content."""
    if kind == "item":
        row = db.execute("SELECT kind, body, visibility FROM knowledge_items WHERE id=?", (source_id,)).fetchone()
        if row is None:
            return "Deleted contribution (withdrawing its findings)"
        if row[2] != "team":
            return "Private contribution (withdrawing its findings)"
        return f"{row[0].capitalize()}: {_snippet(row[1])}"
    if kind == "passage":
        row = db.execute("""SELECT d.filename, p.locator, p.text FROM document_passages p
          JOIN documents d ON d.id=p.document_id WHERE p.id=?""", (source_id,)).fetchone()
        if row is None:
            return "Deleted document passage (withdrawing its findings)"
        return f"{row[0]} · {row[1]}: {_snippet(row[2])}" if row[1] else f"{row[0]}: {_snippet(row[2])}"
    if kind == "profile":
        row = db.execute("SELECT display_name FROM profiles WHERE id=?", (source_id,)).fetchone()
        if row is None:
            return "Deleted profile (withdrawing its expertise)"
        return f"Expertise of {row[0] or 'Browser profile ' + source_id[:4].upper()}"
    return "Concept vocabulary refresh"


def _activity(database, token, running):
    try:
        data = json.loads(activity_path(database).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    data["live"] = bool(running and token and data.get("lease") == lease_id(token))
    return data


def report(database, *, limit=50, offset=0):
    """The queue in processing order, the worker's current step and its recent results."""
    now = time.time()
    with closing(sqlite3.connect(Path(database).as_uri() + "?mode=ro", uri=True, timeout=0.25)) as db:
        token, lease_until = db.execute("SELECT worker_token, worker_lease_until FROM ml_state WHERE id=1").fetchone()
        running = bool(lease_until and lease_until > now)
        busy = "(lease_token IS NOT NULL AND lease_until > :now)"
        totals = dict(zip(("total", "processing", "retrying", "waiting", "reprocessing"), db.execute(f"""
          SELECT count(*), coalesce(sum({busy}),0),
            coalesce(sum(NOT {busy} AND error IS NOT NULL),0),
            coalesce(sum(NOT {busy} AND error IS NULL),0),
            coalesce(sum(priority > 0),0) FROM ml_jobs""", {"now": now}).fetchone()))
        totals["by_kind"] = dict(db.execute(
            "SELECT source_kind, count(*) FROM ml_jobs GROUP BY source_kind ORDER BY source_kind").fetchall())
        # Claim order: available work by priority and age, then work waiting for a retry time.
        rows = db.execute(f"""SELECT source_kind, source_id, {busy}, priority, available_at, attempts, error,
            created_at FROM ml_jobs
          ORDER BY {busy} DESC, available_at > :now, priority, available_at, created_at, source_kind, source_id
          LIMIT :limit OFFSET :offset""", {"now": now, "limit": limit, "offset": offset}).fetchall()
        jobs = []
        for kind, source_id, processing, priority, available_at, attempts, error, created_at in rows:
            state = "processing" if processing else "retrying" if error else "waiting"
            jobs.append({"kind": kind, "id": source_id, "label": source_label(db, kind, source_id), "state": state,
                         "origin": "reprocessing older content" if priority > 0 else "new or changed content",
                         "attempts": attempts, "created_at": created_at, "available_at": available_at, "error": error})
        activity = _activity(database, token, running)
        current = None
        if activity:
            source = activity["activity"] and activity["activity"]["source"]
            if source:
                source["label"] = source_label(db, source["kind"], source["id"])
            if activity["live"] and activity["job"]:
                current = activity["job"]
                current["label"] = source_label(db, current["kind"], current["id"])
                row = db.execute("SELECT attempts FROM ml_jobs WHERE source_kind=? AND source_id=?",
                                 (current["kind"], current["id"])).fetchone()
                current["attempt"] = row[0] + 1 if row else None
            for finished in activity["recent"]:
                finished["label"] = source_label(db, finished["kind"], finished["id"])
    return {"now": now,
            "worker": {"running": running,
                       "pid": activity["pid"] if activity and activity["live"] else None,
                       "started_at": activity["started_at"] if activity and activity["live"] else None},
            "activity": activity and {"live": activity["live"], **(activity["activity"] or {}),
                                      "written_at": activity.get("written_at")},
            "current": current, "totals": totals,
            "jobs": jobs, "offset": offset, "limit": limit,
            "recent": activity["recent"] if activity else []}


def _clock(timestamp):
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")


def _duration(seconds):
    seconds = max(0, int(seconds))
    if seconds < 120:
        return f"{seconds}s"
    if seconds < 7200:
        return f"{seconds // 60}m {seconds % 60:02d}s"
    return f"{seconds // 3600}h {seconds % 3600 // 60:02d}m"


def step_text(progress):
    if not progress:
        return ""
    if progress.get("windows"):
        return f"window {progress['window']} of {progress['windows']}: {progress['step']}"
    return progress["step"]


def retry_text(job, now):
    if job["state"] == "processing":
        return "previous attempt failed"
    return "retry due now" if job["available_at"] <= now else f"next try in {_duration(job['available_at'] - now)}"


def render(data):
    """Plain text for the server console."""
    now, worker, activity, current = data["now"], data["worker"], data["activity"], data["current"]
    lines = []
    if worker["running"] and activity and activity["live"]:
        lines.append(f"Worker: running since {_clock(worker['started_at'])} (pid {worker['pid']})")
    elif worker["running"]:
        lines.append("Worker: running; it has not reported its activity yet")
    else:
        lines.append("Worker: not running; queued work is retained")
    if activity and activity.get("name"):
        source = activity.get("source")
        detail = f" — {source['kind']}: {source['label']}" if source else ""
        if activity["live"]:
            lines.append(f"Now: {activity['name']}{detail} (for {_duration(now - activity['since'])})")
        else:
            lines.append(f"Last reported at {_clock(activity['written_at'])}: {activity['name']}{detail}")
    if current:
        attempt = f", attempt {current['attempt']}" if current.get("attempt") else ""
        lines.append(f"Current job: {current['kind']} {current['id']}{attempt}")
        lines.append(f"  {current['label']}")
        step = step_text(current.get("progress"))
        lines.append(f"  Stage: {current['stage']}{' — ' + step if step else ''} "
                     f"({_duration(now - current['stage_since'])} in stage, {_duration(now - current['claimed_at'])} on this job)")
    totals = data["totals"]
    lines.append(f"Queue: {totals['total']} job{'' if totals['total'] == 1 else 's'} — "
                 f"{totals['processing']} processing, {totals['waiting']} waiting, "
                 f"{totals['retrying']} failed and retrying")
    if totals["by_kind"]:
        kinds = ", ".join(f"{kind} {count}" for kind, count in totals["by_kind"].items())
        lines.append(f"  By source: {kinds}; {totals['reprocessing']} reprocessing older content")
    if data["jobs"]:
        first, last = data["offset"] + 1, data["offset"] + len(data["jobs"])
        lines.append(f"Jobs {first}-{last} of {totals['total']}, in processing order:")
        lines.append(f"  {'#':>5}  {'STATE':<10}  {'KIND':<10}  {'TRIES':>5}  {'QUEUED':>8}  {'WHY QUEUED':<26}  SOURCE")
        for number, job in enumerate(data["jobs"], first):
            lines.append(f"  {number:>5}  {job['state']:<10}  {job['kind']:<10}  {job['attempts']:>5}  "
                         f"{_duration(now - job['created_at']):>8}  {job['origin']:<26}  {job['label']}")
            if job["error"]:
                lines.append(f"  {'':>5}  {retry_text(job, now)}; last error: {_snippet(job['error'], 160)}")
    if data["recent"]:
        lines.append("Recently finished:")
        for finished in data["recent"]:
            error = f" — {_snippet(finished['error'], 120)}" if finished["error"] else ""
            lines.append(f"  {_clock(finished['finished_at'])}  {finished['outcome']:<11}  {finished['kind']:<10}  "
                         f"{finished['seconds']:>7.1f}s  {finished['label']}{error}")
    return "\n".join(lines)
