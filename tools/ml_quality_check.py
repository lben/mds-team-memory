"""Run the frozen authored corpus through offline inference and public app APIs.

Each case gets a fresh migrated database. One production inference child is reused
across cases; the normal worker drains each phase. Exit 0 means the corpus gate
passed, 1 means quality failed, and 2 means execution or required evidence was
incomplete. Version 1 requires every assertion; version 2 reports the frozen
numeric quality gates separately from demonstrated withdrawal behavior.
The original small corpus cannot establish the statistical release-quality gate.
An expanded corpus also grades every published prediction against frozen labels.
"""

import argparse
import base64
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import traceback


FIXTURE_SHA256 = "d5621a5bc88fa67a341b0750bf0fe78d8eb8bcc1d96c71047cdbc37c1cd17289"
CATEGORIES = ("concepts", "aliases", "relationships", "expertise")
CASE_TIMEOUT_SECONDS = 1200


def normalize(value):
    return " ".join(value.casefold().split())


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def load_cases(path, expected_sha256=None):
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != (expected_sha256 or FIXTURE_SHA256):
        raise ValueError("The case file differs from the frozen, pre-inference corpus")
    corpus = json.loads(raw)
    if corpus.get("version") == 1:
        if hashlib.sha256(raw).hexdigest() != FIXTURE_SHA256 or len(corpus.get("cases", [])) != 24:
            raise ValueError("Expected the frozen version-1 corpus with 24 cases")
    elif corpus.get("version") == 2:
        if not expected_sha256 or corpus.get("split") not in {"heldout", "development"} or not corpus.get("cases"):
            raise ValueError("Expanded evaluation needs a frozen hash, declared split and cases")
        ids, texts = set(), set()
        for case in corpus["cases"]:
            identity = case["id"]
            if not isinstance(identity, str) or not re.fullmatch(r"[a-z0-9_]+", identity) or identity in ids:
                raise ValueError("Case identities must be unique, safe directory names")
            ids.add(identity)
            bodies = tuple(normalize(post["body"]) for post in case["posts"])
            if not bodies or bodies in texts:
                raise ValueError("Duplicated source scenarios cannot count as independent cases")
            texts.add(bodies)
            for category in CATEGORIES:
                positive = case["expect"][category]
                negative = case["expect"]["absent_" + category]
                if len(positive) + len(negative) != 1:
                    raise ValueError("Select exactly one independent decision per case/category before inference")
                if category in case.get("hard_negative", []) and not negative:
                    raise ValueError("A positive decision cannot be a hard negative")
                if not isinstance(case["allowed"][category], list):
                    raise ValueError("Every category needs exhaustive allowed-prediction labels")
                for assertion in case.get("retract", {}).get(category, []):
                    if not case.get("actions") or assertion not in negative:
                        raise ValueError("Retraction checks must identify a selected final negative with actions")
            for index, post in enumerate(case["posts"]):
                if post["kind"] == "answer":
                    parent = post.get("parent")
                    if type(parent) is not int or not 0 <= parent < index or case["posts"][parent]["kind"] != "question":
                        raise ValueError("Answers must reference a preceding question")
                if post.get("accepted") and post["kind"] != "answer":
                    raise ValueError("Only an answer can be accepted")
            for action in case.get("actions", []):
                if type(action["post"]) is not int or not 0 <= action["post"] < len(case["posts"]):
                    raise ValueError("An action must reference an existing post")
    else:
        raise ValueError("Unsupported evaluation corpus version")
    return raw, corpus


class RecordedInference:
    """Record unchanged production predictions and sampled process-tree usage."""

    def __init__(self, models):
        from app.ml.worker import InferenceProcess

        self.child = InferenceProcess(models)
        self.path = None
        self.phase = None
        self.deadline = float("inf")
        self.calls = 0
        self.attempts = 0
        self.peak_rss = 0
        self.case_peak_rss = 0
        self.max_affinity = 0
        self.cpu_seconds = {}

    def check_memory(self):
        import psutil

        if time.monotonic() >= self.deadline:
            raise TimeoutError("The case exceeded its 20-minute execution limit")
        parent = psutil.Process()
        rss = 0
        for process in [parent, *parent.children(recursive=True)]:
            try:
                rss += process.memory_info().rss
                affinity = len(process.cpu_affinity())
                self.max_affinity = max(self.max_affinity, affinity)
                if affinity > 4:
                    raise RuntimeError("An evaluation process escaped the four-CPU affinity limit")
                cpu = process.cpu_times()
                key = (process.pid, process.create_time())
                self.cpu_seconds[key] = max(self.cpu_seconds.get(key, 0), cpu.user + cpu.system)
            except psutil.NoSuchProcess:
                pass
        self.peak_rss = max(self.peak_rss, rss)
        self.case_peak_rss = max(self.case_peak_rss, rss)
        self.child.check_memory()

    def analyze(self, body, heartbeat):
        started = time.monotonic()
        self.attempts += 1

        def pulse():
            self.check_memory()
            heartbeat()

        record = {"phase": self.phase, "text": body,
                  "text_sha256": hashlib.sha256(body.encode()).hexdigest()}
        try:
            result, metadata = self.child.analyze(body, pulse)
            record["metadata"] = dict(zip(("model_version", "embedding_version", "dimensions"), metadata))
            record["result"] = {**result, "chunks": [
                {"start": chunk["start"], "end": chunk["end"],
                 "vector_base64": base64.b64encode(chunk["vector"]).decode("ascii")}
                for chunk in result["chunks"]]}
            self.calls += 1
            return result, metadata
        except Exception as error:
            record["error"] = f"{type(error).__name__}: {error}"
            raise
        finally:
            record["seconds"] = time.monotonic() - started
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, ensure_ascii=False) + "\n")

    def close(self):
        self.child.close()


def case_database(source_root, directory):
    """Rebind only after every previous case client and session has closed."""
    from sqlalchemy import create_engine, event

    path = directory / "mds.sqlite3"
    os.environ.update(MDS_DATA_DIR=str(directory), MDS_DATABASE_URL=f"sqlite:///{path}",
                      MDS_SECURE_COOKIES="0")
    from app import config, db

    config.DATA_DIR, config.UPLOAD_DIR, config.DB_PATH = directory, directory / "uploads", path
    config.DATABASE_URL, config.SECURE_COOKIES = os.environ["MDS_DATABASE_URL"], False
    db.engine.dispose()
    engine = create_engine(config.DATABASE_URL, connect_args={"check_same_thread": False})
    event.listen(engine, "connect", db._set_sqlite_pragma)
    db.engine = engine
    db.SessionLocal.configure(bind=engine)
    from alembic import command
    from alembic.config import Config

    migration = Config(str(source_root / "backend/alembic.ini"))
    migration.set_main_option("script_location", str(source_root / "backend/alembic"))
    command.upgrade(migration, "head")
    from app.main import app

    return app, path, engine


def request(client, method, path, trace, *, expected=200, **kwargs):
    response = client.request(method, path, **kwargs)
    trace.append({"method": method, "path": path, "status": response.status_code})
    if response.status_code != expected:
        raise RuntimeError(f"{method} {path}: HTTP {response.status_code}: {response.text[:1000]}")
    return response.json()


def create_posts(case, clients, trace, after_post=None):
    posts = []
    for index, post in enumerate(case["posts"]):
        client = clients[post["actor"]]
        if post["visibility"] == "private":
            if post["kind"] != "note":
                raise ValueError("The public app exposes private notes through scratchpads only")
            pad = request(client, "POST", "/api/scratchpad", trace,
                          json={"name": f"Evaluation private note {index + 1}"})
            request(client, "PUT", f"/api/scratchpad/{pad['id']}", trace, json={"content": post["body"]})
            posts.append({"id": pad["id"], "storage_kind": "scratchpad", "actor": post["actor"]})
            if after_post:
                after_post(index)
            continue
        if post["kind"] == "note":
            item = request(client, "POST", "/api/capture", trace, data={"body": post["body"]})["item"]
        elif post["kind"] == "question":
            item = request(client, "POST", "/api/questions", trace, json={"body": post["body"]})
        elif post["kind"] == "answer":
            parent = posts[post["parent"]]
            item = request(client, "POST", f"/api/questions/{parent['id']}/answers", trace,
                           json={"body": post["body"]})
        else:
            raise ValueError(f"Unsupported post kind: {post['kind']}")
        posts.append({"id": item["id"], "storage_kind": "item", "actor": post["actor"]})
        for actor in post.get("helped_by", []):
            request(clients[actor], "POST", f"/api/items/{item['id']}/helped", trace)
        if post.get("accepted"):
            parent = posts[post["parent"]]
            request(clients[parent["actor"]], "POST", f"/api/questions/{parent['id']}/accept", trace,
                    json={"answer_id": item["id"]})
        if after_post:
            after_post(index)
    return posts


def apply_actions(case, posts, clients, trace):
    for action in case.get("actions", []):
        post = posts[action["post"]]
        client = clients[post["actor"]]
        if post["storage_kind"] != "item":
            raise ValueError("Frozen edit/delete cases must refer to public items")
        if action["type"] == "edit":
            request(client, "PUT", f"/api/items/{post['id']}", trace, json={"body": action["body"]})
        elif action["type"] == "delete":
            route = "questions" if case["posts"][action["post"]]["kind"] == "question" else "items"
            request(client, "DELETE", f"/api/{route}/{post['id']}", trace)
            post["deleted"] = True
        else:
            raise ValueError(f"Unsupported mutation: {action['type']}")


def drain(path, models, inference, phase):
    from app.ml.worker import Supervisor

    supervisor = Supervisor(path, models, threading.Event())
    unused = supervisor.inference
    supervisor.inference = inference
    inference.phase = phase
    started, calls = time.monotonic(), inference.calls
    try:
        code = supervisor.run("drain")
        if code:
            raise RuntimeError(f"Production worker drain failed with exit code {code}")
        return {"seconds": time.monotonic() - started, "inference_calls": inference.calls - calls,
                "lock_retries": dict(supervisor.lock_retries)}
    finally:
        # Each worker releases its own lease; only the outer owner closes models.
        supervisor.inference = unused
        supervisor.close()


def diagnostics(path, destination):
    tables = ("ml_sources", "ml_findings", "ml_evidence", "ml_overrides", "ml_jobs", "ml_state", "ml_budget")
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        result = {table: [dict(row) for row in connection.execute(f"SELECT * FROM {table}")]
                  for table in tables}
        result["embeddings"] = [dict(row) for row in connection.execute(
            "SELECT key,source_kind,source_id,source_hash,generation,start,end,dimensions,length(vector) AS bytes FROM ml_embeddings")]
        result["integrity_check"] = connection.execute("PRAGMA integrity_check").fetchone()[0]
    write_json(destination, result)
    return result


def observe(case, posts, clients, reader, labels, trace):
    from app.db import SessionLocal
    from app.ml import effective

    concepts = request(reader, "GET", "/api/graph/concepts", trace)
    by_id = {row["id"]: row["name"] for row in concepts}
    graph = request(reader, "GET", "/api/graph/global", trace, params={"show_weak": "true"})
    if graph.get("omitted_nodes") or graph.get("omitted_edges"):
        raise RuntimeError("Public graph is truncated; exhaustive assertion grading would be incomplete")
    evidence = [request(reader, "GET", f"/api/graph/links/{link_id}/evidence", trace)
                for link_id in sorted({edge["link_id"] for edge in graph["edges"]})]
    claims = [claim for detail in evidence for claim in detail["claims"]
              if claim["kind"] == "relationship" and claim["state"] == "active"
              and claim["origin"] == "automatic"]
    # Exact active terms disambiguate identity. Search alone also performs
    # stemming and must not turn a loose lexical match into an alias assertion.
    with SessionLocal() as db:
        terms = [{"name": row.display, "concept_id": row.concept_id, "is_canonical": row.is_canonical}
                 for row in effective.terms(db) if row.concept_id in by_id]
    names = {term["name"] for term in terms}
    for category in CATEGORIES:
        for key in (category, "absent_" + category):
            for assertion in case["expect"][key]:
                if category == "concepts":
                    names.add(assertion)
                elif category == "aliases":
                    names.update((assertion["canonical"], assertion["alias"]))
                elif category == "relationships":
                    names.update((assertion["head"], assertion["tail"]))
                else:
                    names.add(assertion["concept"])
    searches = {name: request(reader, "GET", "/api/search", trace, params={"q": name})
                for name in sorted(names)}
    aliases = [{"canonical": by_id[term["concept_id"]], "canonical_id": term["concept_id"],
                "alias": term["name"], "public_search_confirmed": any(
                    row["id"] == term["concept_id"] for row in searches[term["name"]]["concepts"])}
               for term in terms if not term["is_canonical"]]
    directory = request(reader, "GET", "/api/expertise", trace)
    expertise = [{"actor": labels.get(row["label"]), "label": row["label"], "concept": area}
                 for row in directory for area in row["areas"]]
    details = []
    for index, post in enumerate(posts):
        if post["storage_kind"] == "scratchpad":
            content = request(clients[post["actor"]], "GET", "/api/scratchpad", trace)
        else:
            route = "questions" if case["posts"][index]["kind"] == "question" else "items"
            content = request(reader, "GET", f"/api/{route}/{post['id']}", trace,
                              expected=404 if post.get("deleted") else 200)
        details.append({"post": index, "storage_kind": post["storage_kind"], "response": content})
    return {"concepts": concepts, "terms": terms, "aliases": aliases, "relationships": claims,
            "expertise": expertise, "graph": graph, "link_evidence": evidence,
            "details": details, "searches": searches,
            "revision": request(reader, "GET", "/api/ml/revision", trace)}


def present(category, assertion, observed, *, forbidden=False):
    def ids(name):
        return {term["concept_id"] for term in observed["terms"] if normalize(term["name"]) == normalize(name)}

    if category == "concepts":
        return [row for row in observed["concepts"] if row["id"] in ids(assertion)]
    if category == "aliases":
        canonical = ids(assertion["canonical"])
        if forbidden:
            return [{"shared_concept_id": identity}
                    for identity in sorted(canonical & ids(assertion["alias"]))]
        return [row for row in observed["aliases"] if row["canonical_id"] in canonical
                and normalize(row["alias"]) == normalize(assertion["alias"])
                and row["public_search_confirmed"]]
    if category == "relationships":
        return [row for row in observed["relationships"]
                if row["src_id"] in ids(assertion["head"]) and row["dst_id"] in ids(assertion["tail"])
                and normalize(row["predicate"]).replace(" ", "_") == assertion["predicate"]]
    expected_ids = ids(assertion["concept"])
    return [row for row in observed["expertise"] if row["actor"] == assertion["actor"]
            and expected_ids.intersection(ids(row["concept"]))]


def labelled_matches(category, assertion, observed, aliases):
    """Match names against frozen gold equivalence, never predicted identity."""
    def names(name):
        equivalents = {normalize(name)}
        while True:
            expanded = set(equivalents)
            for pair in aliases:
                terms = {normalize(pair["canonical"]), normalize(pair["alias"])}
                if equivalents & terms:
                    expanded.update(terms)
            if expanded == equivalents:
                return equivalents
            equivalents = expanded

    if category == "concepts":
        permitted = names(assertion)
        return [row for row in observed[category] if normalize(row["name"]) in permitted]
    if category == "aliases":
        permitted = names(assertion["canonical"])
        if normalize(assertion["alias"]) not in permitted:
            return []
        return [row for row in observed[category]
                if normalize(row["canonical"]) in permitted and normalize(row["alias"]) in permitted
                and normalize(row["canonical"]) != normalize(row["alias"])
                and row["public_search_confirmed"]]
    if category == "relationships":
        by_id = {row["id"]: normalize(row["name"]) for row in observed["concepts"]}
        heads, tails = names(assertion["head"]), names(assertion["tail"])
        return [row for row in observed[category]
                if by_id.get(row["src_id"]) in heads and by_id.get(row["dst_id"]) in tails
                and normalize(row["predicate"]).replace(" ", "_") == assertion["predicate"]]
    permitted = names(assertion["concept"])
    return [row for row in observed[category] if row["actor"] == assertion["actor"]
            and normalize(row["concept"]) in permitted]


def grade(case, observed, version=1):
    checks = []
    for category in CATEGORIES:
        for expected in (True, False):
            for assertion in case["expect"][category if expected else "absent_" + category]:
                if version == 2 and expected:
                    found = labelled_matches(category, assertion, observed, case["allowed"]["aliases"])
                    if category == "aliases":
                        identities = {row["shared_concept_id"] for row in
                                      present(category, assertion, observed, forbidden=True)}
                        found = [row for row in found if row["canonical_id"] in identities]
                else:
                    found = present(category, assertion, observed, forbidden=not expected)
                checks.append({"category": category, "assertion": assertion,
                               "expected_present": expected, "observed": found,
                               "passed": bool(found) == expected})
    return checks


def audit_predictions(case, observed):
    """Grade the complete active output, including unselected extra predictions."""
    audit = {}
    for category in CATEGORIES:
        permitted = []
        for assertion in case["allowed"][category]:
            permitted.extend(labelled_matches(category, assertion, observed, case["allowed"]["aliases"]))
        audit[category] = [{"prediction": row, "correct": row in permitted}
                           for row in observed[category]]
    return audit


def withdrawal_status(row):
    if not row["absent_after_actions"]:
        return "FAILED"
    return "DEMONSTRATED" if row["initially_present"] else "UNTRIGGERED"


def case_status(checks, retractions, version=1):
    if any(not check["passed"] for check in checks):
        return "FAIL"
    if version == 2:
        return "FAIL" if any(withdrawal_status(row) == "FAILED" for row in retractions) else "PASS"
    if any(withdrawal_status(row) != "DEMONSTRATED" for row in retractions):
        return "INCOMPLETE"
    return "PASS"


def run_case(case, source_root, models, directory, inference, version=1):
    from fastapi.testclient import TestClient

    directory.mkdir(mode=0o700)
    inference.path = directory / "predictions.jsonl"
    inference.deadline = time.monotonic() + CASE_TIMEOUT_SECONDS
    inference.case_peak_rss = 0
    started, initial_calls = time.monotonic(), inference.calls
    cpu_before = sum(inference.cpu_seconds.values())
    result = {"id": case["id"], "domain": case["domain"], "status": "ERROR", "api_trace": [],
              "database": str(directory / "mds.sqlite3"), "expect": case["expect"], "phases": {}}
    engine = path = None
    try:
        app, path, engine = case_database(source_root, directory)
        actors = sorted({post["actor"] for post in case["posts"]}
                        | {actor for post in case["posts"] for actor in post.get("helped_by", [])})
        with ExitStack() as stack:
            clients = {actor: stack.enter_context(TestClient(app)) for actor in actors}
            labels = {}
            for actor, client in clients.items():
                account = request(client, "POST", "/api/auth/signup", result["api_trace"],
                                  json={"username": actor, "password": secrets.token_urlsafe(24)})
                if account["is_admin"]:
                    raise RuntimeError("The evaluation must use ordinary contributor accounts")
                labels[account["label"]] = actor
            reader = stack.enter_context(TestClient(app))
            post_drains = []

            def after_post(index):
                post_drains.append({"post": index, **drain(path, models, inference, f"post_{index + 1}")})

            posts = create_posts(case, clients, result["api_trace"], after_post)
            result["posts"] = posts
            result["private_note_mapping"] = "Private notes use the public scratchpad API; no private item creation API exists."
            result["phases"]["initial"] = {"post_drains": post_drains,
                "seconds": sum(row["seconds"] for row in post_drains),
                "inference_calls": sum(row["inference_calls"] for row in post_drains)}
            initial = observe(case, posts, clients, reader, labels, result["api_trace"])
            result["phases"]["initial"]["observed"] = initial
            diagnostics(path, directory / "initial-diagnostics.json")
            final = initial
            if case.get("actions"):
                apply_actions(case, posts, clients, result["api_trace"])
                result["phases"]["after_actions"] = drain(path, models, inference, "after_actions")
                final = observe(case, posts, clients, reader, labels, result["api_trace"])
                result["phases"]["after_actions"]["observed"] = final
                targets = (case.get("retract", {}) if version == 2 else
                           {category: case["expect"]["absent_" + category] for category in CATEGORIES})
                result["retractions"] = [
                    {"category": category, "assertion": assertion,
                     "initially_present": bool(present(category, assertion, initial, forbidden=True)),
                     "absent_after_actions": not bool(present(category, assertion, final, forbidden=True))}
                    for category in CATEGORIES for assertion in targets.get(category, [])]
            result["checks"] = grade(case, final, version)
            if version == 2:
                result["prediction_audit"] = audit_predictions(case, final)
            if result.get("retractions"):
                if version == 2:
                    for row in result["retractions"]:
                        row["status"] = withdrawal_status(row)
                    statuses = {row["status"] for row in result["retractions"]}
                    result["retraction_status"] = next(state for state in ("FAILED", "UNTRIGGERED", "DEMONSTRATED")
                                                       if state in statuses)
                else:
                    demonstrated = all(withdrawal_status(row) == "DEMONSTRATED" for row in result["retractions"])
                    result["retraction_status"] = "DEMONSTRATED" if demonstrated else "NOT_DEMONSTRATED"
            result["status"] = case_status(result["checks"], result.get("retractions", []), version)
            if any(not row["correct"] for rows in result.get("prediction_audit", {}).values() for row in rows):
                result["status"] = "FAIL"
            inference.check_memory()
        stored = diagnostics(path, directory / "final-diagnostics.json")
        if stored["integrity_check"] != "ok" or stored["ml_jobs"]:
            raise RuntimeError("The case database is not intact or the production queue did not drain")
        private_ids = {row["id"] for row in result["posts"] if row["storage_kind"] == "scratchpad"}
        if any(row["id"] in private_ids for row in stored["ml_sources"]):
            raise RuntimeError("Private scratchpad content entered derived source storage")
    except Exception as error:
        result["status"] = "ERROR"
        result["error"] = f"{type(error).__name__}: {error}"
        result["traceback"] = traceback.format_exc()
        try:
            inference.close()
        except Exception as failure:
            result["inference_cleanup_error"] = f"{type(failure).__name__}: {failure}"
        if path is not None:
            try:
                diagnostics(path, directory / "error-diagnostics.json")
            except Exception as failure:
                result["diagnostic_error"] = f"{type(failure).__name__}: {failure}"
    finally:
        if engine is not None:
            engine.dispose()
        result["seconds"] = time.monotonic() - started
        result["inference_calls"] = inference.calls - initial_calls
        result["sampled_peak_tree_rss_bytes"] = inference.case_peak_rss
        result["sampled_cpu_seconds"] = max(0, sum(inference.cpu_seconds.values()) - cpu_before)
        write_json(directory / "report.json", result)
    return result


def summarize(corpus, results):
    summary = {}
    for category in CATEGORIES:
        summary[category] = {}
        for polarity, expected in (("positive", True), ("negative", False)):
            field = category if expected else "absent_" + category
            total = sum(len(case["expect"][field]) for case in corpus["cases"])
            checks = [check for result in results if result["status"] != "ERROR"
                      for check in result.get("checks", [])
                      if check["category"] == category and check["expected_present"] == expected]
            summary[category][polarity] = {"assertions": total,
                "passed": sum(check["passed"] for check in checks),
                "failed": sum(not check["passed"] for check in checks), "not_evaluated": total - len(checks)}
    return summary


def release_quality(corpus, results):
    from app.ml.evaluation import PRECISION_TARGETS, summarize as summarize_decisions

    by_id = {result["id"]: result for result in results}
    decisions, audit = [], {category: [] for category in CATEGORIES}
    missing = []
    for case in corpus["cases"]:
        result = by_id.get(case["id"])
        if not result or result["status"] == "ERROR":
            missing.append(case["id"])
            continue
        for check in result["checks"]:
            category = check["category"]
            decisions.append({"id": case["id"] + ":" + category, "group_id": case["id"],
                              "category": category, "gold": check["expected_present"],
                              "applied": bool(check["observed"]), "split": corpus.get("split", "heldout"),
                              "hard_negative": category in case.get("hard_negative", [])})
        for category in CATEGORIES:
            audit[category].extend(result["prediction_audit"][category])
    categories = {}
    for category in CATEGORIES:
        selected = [row for row in decisions if row["category"] == category]
        summary = summarize_decisions(selected, PRECISION_TARGETS[category])
        correct = sum(row["correct"] for row in audit[category])
        total = len(audit[category])
        precision = correct / total if total else None
        summary["all_applied_predictions"] = {"count": total, "correct": correct, "incorrect": total - correct,
                                              "precision": precision}
        if precision is None or precision < PRECISION_TARGETS[category]:
            summary["threshold_failures"].append("full-output precision is below target or undefined")
            if summary["status"] == "pass":
                summary["status"] = "fail"
        categories[category] = summary
    statuses = {row["status"] for row in categories.values()}
    status = ("INCOMPLETE" if missing else
              "INSUFFICIENT_EVIDENCE" if "insufficient" in statuses or corpus.get("split") == "development" else
              "FAIL" if "fail" in statuses else "PASS")
    return {"status": status, "categories": categories, "missing_cases": missing,
            "split": corpus.get("split", "heldout"),
            "development_can_establish_release_quality": False,
            "population": "Frozen authored scenarios; no workplace accuracy claim.",
            "intervals": "Wilson intervals use one preselected decision per independent case/category. "
                         "The separate complete-output precision audit includes correlated predictions; "
                         "it does not add independent samples."}, decisions


def lifecycle_quality(results):
    checks = [{"case": result["id"], **row, "status": withdrawal_status(row)}
              for result in results if result["status"] != "ERROR"
              for row in result.get("retractions", [])]
    categories = {category: {status: sum(row["category"] == category and row["status"] == status
                                        for row in checks)
                             for status in ("DEMONSTRATED", "FAILED", "UNTRIGGERED")}
                  for category in CATEGORIES}
    status = ("FAIL" if any(row["FAILED"] for row in categories.values()) else
              "NOT_DEMONSTRATED" if any(not row["DEMONSTRATED"] for row in categories.values()) else "PASS")
    return {"status": status, "categories": categories, "checks": checks,
            "scope": "Withdrawal after source changes only. Each finding category needs an active-to-absent "
                     "application scenario. Untriggered checks establish final absence, not withdrawal."}


def evaluation_status(quality, lifecycle):
    if "FAIL" in (quality, lifecycle):
        return "FAIL"
    if quality != "PASS":
        return quality
    return "PASS" if lifecycle == "PASS" else "INCOMPLETE"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", type=Path, required=True, help="Prepared local model directory containing models.json")
    parser.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--cases", type=Path, help="Frozen fixture; defaults to backend/tests/fixtures/ml_cross_domain.json")
    parser.add_argument("--expected-sha256", help="Hash frozen before inference; required for an expanded corpus")
    parser.add_argument("--output-parent", type=Path, required=True, help="Parent for a new private evaluation directory")
    args = parser.parse_args(argv)
    if sys.platform != "linux":
        parser.error("Real evaluation requires Linux CPU affinity and the supported offline ML environment")
    source_root, models = args.source_root.resolve(), args.models.resolve()
    if not (source_root / "backend/app/main.py").is_file():
        raise ValueError("--source-root must identify the application source tree")
    from deploylib import local_sqlite_filesystem

    filesystem = local_sqlite_filesystem(args.output_parent)
    cases = (args.cases or source_root / "backend/tests/fixtures/ml_cross_domain.json").resolve()
    fixture_raw, corpus = load_cases(cases, args.expected_sha256)
    fixture_sha256 = hashlib.sha256(fixture_raw).hexdigest()
    manifest_raw = (models / "models.json").read_bytes()
    manifest = json.loads(manifest_raw)
    sys.path.insert(0, str(source_root / "backend"))
    from app.ml.runtime import configure_cpu
    from app.ml.worker import lower_priority
    from app.ml.queue import sqlite_is_safe

    if not sqlite_is_safe(sqlite3.sqlite_version_info):
        raise RuntimeError(f"SQLite {sqlite3.sqlite_version} lacks the required WAL-reset fix")
    configure_cpu()
    lower_priority()
    args.output_parent.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(args.output_parent).free < 2 * 1024**3:
        raise RuntimeError("Evaluation output would start below the 2 GiB free-space reserve")
    os.umask(0o077)
    output = Path(tempfile.mkdtemp(prefix="ml-quality-", dir=args.output_parent)).resolve()
    (output / "cases.json").write_bytes(fixture_raw)
    (output / "models.json").write_bytes(manifest_raw)
    source_files = sorted((source_root / "backend/app").rglob("*.py")) + sorted((source_root / "backend/alembic").rglob("*.py"))
    report = {"status": "RUNNING", "output": str(output), "fixture_sha256": fixture_sha256,
              "fixture_purpose": corpus["purpose"], "training": False,
              "models_manifest_sha256": hashlib.sha256(manifest_raw).hexdigest(),
              "model_pins": {role: {key: entry.get(key) for key in ("repository", "revision", "license")}
                             for role, entry in manifest["models"].items()},
              "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "source_hash_scope": "Python files under backend/app and backend/alembic; dependencies and other files excluded",
              "source_sha256": {str(path.relative_to(source_root)): hashlib.sha256(path.read_bytes()).hexdigest()
                                for path in source_files},
              "runtime": {"python": sys.version, "sqlite": sqlite3.sqlite_version,
                          "sqlite_filesystem": filesystem,
                          "allowed_cpus": sorted(os.sched_getaffinity(0)),
                          "offline_environment": {key: os.environ.get(key) for key in (
                              "HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "CUDA_VISIBLE_DEVICES")}},
              "limits": ["Authored scenarios are not representative workplace accuracy.",
                         "Assertion counts are not complete precision; all observed automatic findings are retained for inspection.",
                         "Names match only exact case-insensitive canonical terms or active aliases; no paraphrase equivalence is inferred.",
                         "Posts are created in fixture order, with a production worker drain after each post and its outcomes.",
                         "API calls use the real ASGI app through TestClient, not a separate web server or browser.",
                         "Private notes use the app's private scratchpad flow.",
                         "Runtime offline flags do not replace operating-system network isolation."],
              "statistical_release_gate": {"status": "INSUFFICIENT_EVIDENCE", "authored_scenarios": len(corpus["cases"]),
                  "required_independent_decisions_per_category": 300,
                  "reason": "Full independent quality evidence has not been established."},
              "cases": []}
    started = time.monotonic()
    inference = RecordedInference(models)
    write_json(output / "report.json", report)
    print(json.dumps({"output": str(output), "fixture_sha256": fixture_sha256}), flush=True)
    try:
        for case in corpus["cases"]:
            result = run_case(case, source_root, models, output / case["id"], inference, corpus["version"])
            report["cases"].append(result)
            report["assertions"] = summarize(corpus, report["cases"])
            write_json(output / "report.json", report)
            print(json.dumps({"case": case["id"], "status": result["status"], "seconds": result["seconds"]}), flush=True)
        if (models / "models.json").read_bytes() != manifest_raw or cases.read_bytes() != fixture_raw:
            raise RuntimeError("Model manifest or frozen cases changed during evaluation")
        statuses = {case["status"] for case in report["cases"]}
        report["status"] = next((state for state in ("ERROR", "FAIL", "INCOMPLETE") if state in statuses), "PASS")
    except BaseException as error:
        report["status"] = "ERROR"
        report["error"] = f"{type(error).__name__}: {error}"
        report["traceback"] = traceback.format_exc()
    finally:
        try:
            inference.close()
        except Exception as error:
            report["status"] = "ERROR"
            report["inference_cleanup_error"] = f"{type(error).__name__}: {error}"
        report["seconds"] = time.monotonic() - started
        report["resources"] = {"memory_limit_bytes": inference.child.limit,
                               "sampled_peak_tree_rss_bytes": inference.peak_rss,
                               "maximum_process_cpu_affinity_count": inference.max_affinity,
                               "sampled_cpu_seconds": sum(inference.cpu_seconds.values()),
                               "attempted_inference_calls": inference.attempts,
                               "completed_inference_calls": inference.calls}
        report["assertions"] = summarize(corpus, report["cases"])
        if corpus["version"] == 2:
            gate, decisions = release_quality(corpus, report["cases"])
            report["statistical_release_gate"] = gate
            report["lifecycle_gate"] = lifecycle_quality(report["cases"])
            with (output / "decisions.jsonl").open("w", encoding="utf-8") as stream:
                for row in decisions:
                    stream.write(json.dumps(row) + "\n")
            if report["status"] != "ERROR":
                report["status"] = evaluation_status(gate["status"], report["lifecycle_gate"]["status"])
        write_json(output / "report.json", report)
    print(json.dumps({"status": report["status"], "report": str(output / "report.json")}), flush=True)
    return {"PASS": 0, "FAIL": 1, "INCOMPLETE": 2, "INSUFFICIENT_EVIDENCE": 2, "ERROR": 2}[report["status"]]


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"ML quality check: {type(error).__name__}: {error}", file=sys.stderr)
        raise SystemExit(2)
