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

from sqlalchemy import create_engine, text as text_sql
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from . import policy
from .sources import finding_key

RECENT = 20
# Finding kinds an operator recognises, in display order; internal bookkeeping kinds are omitted.
FINDING_KINDS = {"concept": "concept", "mention": "topic", "alias": "alternative name", "relationship": "relationship",
                 "association": "related concepts", "expertise": "expertise"}


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

    def finish_job(self, outcome, error=None, created=()):
        job, now = self.state["job"], time.time()
        self.recent.appendleft({"kind": job["kind"], "id": job["id"], "outcome": outcome, "error": error,
                                "finished_at": now, "seconds": round(now - job["claimed_at"], 3),
                                "created": list(created)})  # concepts this job created
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


def _concept_name(db, concept_id):
    row = db.execute("SELECT display FROM concept_terms WHERE concept_id=? AND is_canonical=1", (concept_id,)).fetchone()
    return row[0] if row else "(unnamed concept)"


def _model_headline(label, state):
    """What the model (or, for expertise, the confirmation rule) decided; never a downstream outcome."""
    if label == "related concepts":
        return "Suggested as related by the model" if state in ("active", "weak") else "Not suggested as related"
    if label == "expertise":
        return {"active": "Expertise recognised", "held": "Expertise not recognised yet"}.get(state, "Expertise withdrawn")
    if state == "active":
        return f"{label.capitalize()} confirmed by the model"
    if state in ("held", "weak"):
        return f"{label.capitalize()} not confirmed by the model"
    return f"{label.capitalize()} withdrawn"


def _explain(db, session, key, finding_kind, label, state, payload, features):
    """Headline and reason, from an administrator's decision or the same rules that decided it."""
    # Imported here: app.models binds the database URL at import, and the worker and
    # its tests import this module before choosing their database.
    from . import effective

    # Administrators decide on the finding, its link or its relationship type (see relationships.relationship_claims).
    if finding_kind in ("relationship", "association") and effective.predicate_name(session, payload["predicate"]) is None:
        return "Removed by an administrator", "An administrator removed this relationship type."
    override = db.execute("SELECT mode FROM ml_overrides WHERE key=?", (key,)).fetchone()
    if override:
        return (("Added by an administrator", "An administrator added it by hand.") if override[0] == "pinned"
                else ("Removed by an administrator", "An administrator removed it."))
    if finding_kind in ("relationship", "association"):  # one link decision governs every claim on the pair
        pair = finding_key("relationship_pair", *sorted((payload["src_id"], payload["dst_id"])))
        override = db.execute("SELECT mode FROM ml_overrides WHERE key=?", (pair,)).fetchone()
        if override:
            return (("Link confirmed by an administrator", "An administrator confirmed this link by hand.")
                    if override[0] == "pinned" else ("Link removed by an administrator", "An administrator removed this link."))
    if state == "stale":
        return (f"{label.capitalize()} waiting to be checked again",
                "A post supporting it, or the meaning of one of its names, changed; the worker will check it again.")
    if state == "suppressed":  # without an override, only a blocked name suppresses (adapter._publish_*)
        return "Removed by an administrator", "An administrator blocked this name."
    if finding_kind == "expertise":
        expected, why = policy.assess_expertise(len(features.get("actors", [])), features.get("originals", 0),
                                                features.get("accepted_answers", 0))
    else:
        expected, _, why = policy.assess(finding_kind, effective.evidence_rows(session, key))
    headline = _model_headline(label, state)
    if expected == state:
        return headline, why
    # The stored decision differs from the evidence rules: the rules for publishing names
    # (adapter._publish_alias) decided it, or it has not been re-checked since its evidence
    # or an administrator's decision changed. The report cannot tell which.
    if finding_kind == "alias":
        return headline, "Decided by the rules for publishing names, or not yet re-checked; the worker will check it again."
    return headline, "Not yet re-checked under the current evidence and decisions; the worker will decide again."


def _shown(db, kind, source_id):
    """Whether the source is visible now; private or deleted sources keep evidence until reprocessed."""
    if kind == "item":
        return db.execute("SELECT 1 FROM knowledge_items WHERE id=? AND visibility='team'", (source_id,)).fetchone()
    table = {"passage": "document_passages", "profile": "profiles"}.get(kind)
    return table and db.execute(f"SELECT 1 FROM {table} WHERE id=?", (source_id,)).fetchone()


def decisions(db, session, kind, source_id):
    """Current decisions on the findings this source supports or contradicts, and why."""
    if not _shown(db, kind, source_id):
        return []
    rows = db.execute(f"""SELECT f.key, f.kind, f.state, f.payload, e.features
      FROM ml_evidence e JOIN ml_findings f ON f.key = e.finding_key
      WHERE e.source_kind=? AND e.source_id=? AND f.kind IN ({",".join("?" * len(FINDING_KINDS))})
      GROUP BY f.key""", (kind, source_id, *FINDING_KINDS)).fetchall()
    result = []
    for key, finding_kind, state, payload, features in rows:
        payload = json.loads(payload)
        if finding_kind == "concept":
            name = payload["name"]
        elif finding_kind in ("mention", "expertise"):
            name = _concept_name(db, payload["concept_id"])
        elif finding_kind == "alias":
            name = f"{payload['alias']} → {_concept_name(db, payload['concept_id'])}"
        else:
            predicate = payload["predicate"].replace("_", " ")
            name = f"{_concept_name(db, payload['src_id'])} {predicate} {_concept_name(db, payload['dst_id'])}"
        label = FINDING_KINDS[finding_kind]
        headline, why = _explain(db, session, key, finding_kind, label, state, payload, json.loads(features))
        result.append({"kind": label, "name": name, "state": state, "headline": headline, "why": why})
    order = list(FINDING_KINDS.values())
    return sorted(result, key=lambda item: (order.index(item["kind"]), item["name"]))


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
        # The decision rules read evidence through SQLAlchemy; they share this connection.
        engine = create_engine("sqlite://", creator=lambda: db, poolclass=StaticPool, pool_reset_on_return=None)
        with Session(engine) as session:
            session.connection()  # SQLAlchemy rolls back once while connecting, so begin afterwards
            db.execute("BEGIN")  # one snapshot: the worker commits while the report is read
            return _report(database, db, session, now, limit, offset)


def _report(database, db, session, now, limit, offset):
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
            finished["decisions"] = decisions(db, session, finished["kind"], finished["id"])
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
            for decision in finished["decisions"]:
                lines.append(f"{'':>24}{decision['headline']}: {decision['name']} — {decision['why']}")
            if not finished["decisions"] and finished["kind"] != "vocabulary":
                lines.append(f"{'':>24}no findings from this source")
    return "\n".join(lines)


# What an author sees on their own new contribution. Phases are deliberately few
# and plain; percentages follow the worker's real steps, in claim order.
STEPS = ["parsing sentences", "extracting entities", "extracting relations", "extracting alias definitions",
         "embedding the text"]


def _phase(job):
    """(phase, percent) for the job the worker is processing now."""
    note, stage = job.get("progress") or {}, job["stage"]
    if stage == "saving results":
        return "connecting", 92
    if note.get("step") == "judging concept relevance":
        return "connecting", 85
    if note.get("step") in STEPS:
        index = STEPS.index(note["step"])
        done = (note["window"] - 1 + index / len(STEPS)) / note["windows"]
        return ("categorizing" if index < 2 else "relating"), round(20 + 65 * done)
    if stage == "running inference" and not note:
        return "categorizing", 20
    return "ingesting", 10  # claimed, reading the source, loading models


def _outcome(db, item_id, finding, created):
    """What this contribution changed for one finding, as the team now sees it, or None.

    `created` lists the concepts this contribution's own job created. Only what is
    certain is claimed; anything else is left out.
    """
    from ..models import Concept, ConceptTerm, RelationshipType
    from ..relationships import find_link
    from . import effective  # lazily, as in _explain
    from .models import Override
    from .runtime import normalize

    rows = effective.evidence_rows(db, finding.key)
    mine = [row for row in rows if (row["source_kind"], row["source_id"]) == ("item", item_id)]
    if not mine or finding.state not in ("active", "held") or policy.assess(finding.kind, rows)[0] != finding.state:
        return None  # not current evidence, or not re-decided yet
    others = [row for row in rows if row not in mine]
    payload = json.loads(finding.payload)
    best = max(mine, key=lambda row: row["raw_score"])
    another = {**best, "group_key": "another", "text_hash": "another", "author_id": "another"}
    one_more = policy.assess(finding.kind, rows + [another])[0] == "active"
    published = effective.concepts(db)
    if finding.kind == "concept":
        name = payload["name"]
        if finding.state == "active":
            if finding.canonical_id not in created or not published.filter(Concept.id == finding.canonical_id).first():
                return None
            confirmed = any(row["polarity"] == "positive" for row in others)
            return {"kind": "confirmed_concept" if confirmed else "new_concept", "name": name}
        exists = finding.canonical_id or db.query(ConceptTerm).filter_by(term=normalize(name)).first()
        return {"kind": "noted_concept", "name": name} if one_more and not exists else None
    src, dst = payload["src_id"], payload["dst_id"]
    predicate = effective.predicate_name(db, payload["predicate"])
    if (predicate is None or published.filter(Concept.id.in_((src, dst))).count() != 2
            or db.get(Override, finding_key("relationship_pair", *sorted((src, dst))))):
        return None  # not shown, or an administrator decides this pair
    name = f"{_concept_name_orm(db, src)} {predicate} {_concept_name_orm(db, dst)}"
    link = find_link(db, src, dst)
    if finding.state == "active":
        shown = (link is not None and link.state == "confirmed" and not link.reviewed_by
                 and (link.src_id, link.dst_id) == (src, dst)
                 and db.query(RelationshipType).filter_by(id=link.relationship_type_id, name=predicate).first())
        # Only when this post made it count: without it, the rules would not confirm it.
        if shown and policy.assess(finding.kind, others)[0] != "active":
            return {"kind": "confirmed_connection", "name": name}
        return None
    if link is not None and (link.state == "confirmed" or link.reviewed_by):
        return None  # another statement, or an administrator, already decides this link
    return {"kind": "noted_connection", "name": name} if one_more else None


def _concept_name_orm(db, concept_id):
    from ..models import ConceptTerm

    term = db.query(ConceptTerm).filter_by(concept_id=concept_id, is_canonical=True).first()
    return term.display if term else "(unnamed concept)"


def contributions(db, database, profile_id, item_ids):
    """Progress and outcome of the author's own team contributions; other items are left out."""
    from ..models import Concept, ItemConcept, KnowledgeItem
    from . import effective  # lazily, as in _explain
    from .models import Evidence, Finding, Override

    state = db.execute(text_sql("SELECT automation_enabled, worker_token, worker_lease_until FROM ml_state WHERE id=1")).one()
    now = time.time()
    running = bool(state.worker_lease_until and state.worker_lease_until > now)
    activity = _activity(database, state.worker_token, running) if running else None
    current = activity["job"] if activity and activity["live"] else None
    order = {"new_concept": 0, "confirmed_concept": 1, "confirmed_connection": 2, "tagged": 3,
             "noted_concept": 4, "noted_connection": 5}
    result = {}
    items = db.query(KnowledgeItem).filter(KnowledgeItem.id.in_(item_ids), KnowledgeItem.visibility == "team",
                                           KnowledgeItem.author_profile_id == profile_id)
    for item in items:
        if not (state.automation_enabled and running):
            result[item.id] = {"state": "off"}
            continue
        job = db.execute(text_sql("SELECT lease_token IS NOT NULL AND lease_until > :now FROM ml_jobs "
                                  "WHERE source_kind='item' AND source_id=:id"), {"now": now, "id": item.id}).first()
        if job is not None:
            if job[0] and current and (current["kind"], current["id"]) == ("item", item.id):
                phase, percent = _phase(current)
            else:
                phase, percent = "ingesting", 10 if job[0] else 3
            result[item.id] = {"state": "working", "phase": phase, "percent": percent}
            continue
        outcomes = []
        # A post is processed again after the vocabulary it changed is refreshed, so
        # count what any of its recent jobs created.
        created = {concept for job in (activity or {}).get("recent", [])
                   if (job["kind"], job["id"]) == ("item", item.id) for concept in job.get("created", [])}
        findings = (db.query(Finding).join(Evidence, Evidence.finding_key == Finding.key)
                    .filter(Evidence.source_kind == "item", Evidence.source_id == item.id,
                            Finding.kind.in_(("concept", "relationship")),
                            ~Finding.key.in_(db.query(Override.key))).distinct())
        for finding in findings:
            outcome = _outcome(db, item.id, finding, created)
            if outcome and outcome not in outcomes:
                outcomes.append(outcome)
        named = {outcome["name"] for outcome in outcomes}
        published = effective.concepts(db).with_entities(Concept.id)
        tags = sorted({_concept_name_orm(db, row.concept_id) for row in db.query(ItemConcept).filter(
            ItemConcept.item_id == item.id, ItemConcept.concept_id.in_(published))} - named)
        if tags:
            outcomes.append({"kind": "tagged", "names": tags})
        outcomes.sort(key=lambda outcome: order[outcome["kind"]])
        result[item.id] = {"state": "done", "percent": 100, "outcomes": outcomes}
    return result
