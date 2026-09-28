"""One leased supervisor and one reusable, separately monitored inference child."""

import argparse
from collections import Counter
import json
import math
import multiprocessing
import os
from pathlib import Path
from queue import Empty, Queue
import signal
import sqlite3
import sys
import threading
import time

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from . import queue
from .runtime import inference_version

MEMORY_CEILING = 8 * 1024**3
LOAD_TIMEOUT_SECONDS = 300.0
JOB_TIMEOUT_SECONDS = 1800.0
STORAGE_PAUSE_SECONDS = 300.0


def positive_seconds(value):
    seconds = float(value)
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("Inference deadlines must be finite positive seconds")
    return seconds


class Stopped(RuntimeError):
    pass


class LeaseLost(RuntimeError):
    pass


class AssetsChanged(RuntimeError):
    pass


def database_busy(error):
    code = getattr(error, "sqlite_errorcode", None)
    return code is not None and code & 255 in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)


def database_path(database_url):
    url = make_url(database_url)
    if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:" or url.query:
        raise ValueError("The ML worker requires a local SQLite file URL without query options")
    path = Path(url.database).expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"Migrate the application database before starting ML: {path}")
    return path


def memory_limit():
    limits = [MEMORY_CEILING]
    groups = Path("/proc/self/cgroup")
    if groups.exists():
        for line in groups.read_text().splitlines():
            _, controllers, relative = line.split(":", 2)
            if controllers == "":
                root, filename = Path("/sys/fs/cgroup"), "memory.max"
            elif "memory" in controllers.split(","):
                root, filename = Path("/sys/fs/cgroup/memory"), "memory.limit_in_bytes"
            else:
                continue
            directory = root / relative.lstrip("/")
            while directory.is_relative_to(root):
                candidate = directory / filename
                if candidate.exists():
                    value = candidate.read_text().strip()
                    if value.isdecimal() and int(value) > 0:
                        limits.append(int(value))
                if directory == root:
                    break
                directory = directory.parent
    return min(limits)


def process_tree_rss():
    import psutil

    parent = psutil.Process()
    total = 0
    for process in [parent, *parent.children(recursive=True)]:
        try:
            total += process.memory_info().rss
        except psutil.NoSuchProcess:
            pass
    return total


def lower_priority():
    import psutil

    if hasattr(os, "sched_getaffinity"):
        os.sched_setaffinity(0, sorted(os.sched_getaffinity(0))[:4])
    if hasattr(os, "nice") and os.nice(0) < 10:
        os.nice(10 - os.nice(0))
    process = psutil.Process()
    if hasattr(process, "ionice"):
        try:
            process.ionice(psutil.IOPRIO_CLASS_IDLE)
        except (psutil.AccessDenied, NotImplementedError):
            pass


def _model_loop(connection, assets):
    from .runtime import LocalModels

    models = LocalModels(assets)
    connection.send(("ready", models.version, models.embedding_version, models.dimensions))
    while True:
        body = connection.recv()
        if body is None:
            return
        connection.send(("result", models.analyze(body)))


def _verifier_loop(connection, assets):
    from . import eligibility
    from .runtime import configure_cpu, verified_manifest

    configure_cpu()
    directory = Path(assets).resolve()
    manifest = verified_manifest(directory, ("verifier",))
    files = manifest["models"]["verifier"]["files"]
    if len(files) != 1:
        raise ValueError("The verifier role must contain exactly one model file")
    verifier = eligibility.Verifier(directory / "verifier" / files[0]["path"])
    connection.send(("ready", eligibility.version(manifest["models"]), "", 0))
    while True:
        request = connection.recv()
        if request is None:
            return
        connection.send(("result", verifier.margins(request["text"], request["candidates"])))


def _child_entry(connection, assets, target, parent_pid):
    # A private process group lets shutdown also terminate model descendants.
    if hasattr(os, "setsid"):
        os.setsid()
    try:
        if sys.platform == "linux":
            import ctypes

            signal.signal(signal.SIGTERM, lambda *_: os.killpg(os.getpid(), signal.SIGKILL))
            if ctypes.CDLL(None, use_errno=True).prctl(1, signal.SIGTERM, 0, 0, 0) != 0:
                raise OSError(ctypes.get_errno(), "Cannot arm inference parent-death signal")
            if os.getppid() != parent_pid:
                os.killpg(os.getpid(), signal.SIGKILL)
        target(connection, assets)
    except EOFError:
        pass
    except Exception as error:
        try:
            connection.send(("error", f"{type(error).__name__}: {error}"))
        except (BrokenPipeError, EOFError, OSError):
            pass
    finally:
        connection.close()


class InferenceProcess:
    def __init__(self, assets, *, target=_model_loop, limit=None,
                 load_timeout=LOAD_TIMEOUT_SECONDS, job_timeout=JOB_TIMEOUT_SECONDS):
        self.assets, self.target = str(assets), target
        self.load_timeout, self.job_timeout = positive_seconds(load_timeout), positive_seconds(job_timeout)
        self.limit = memory_limit() if limit is None else min(limit, memory_limit())
        self.process = self.connection = None
        self.metadata = None
        self.exchange_thread = None

    def check_memory(self):
        used = process_tree_rss()
        if used > self.limit:
            raise MemoryError(f"ML process RSS {used} exceeds the {self.limit}-byte limit")

    def _receive(self, heartbeat, deadline, phase, body=None):
        # poll() can become readable before a complete pickled message arrives.
        # Keep both sending and receiving off the heartbeat/deadline loop so a
        # stalled child cannot block supervision inside either pipe operation.
        responses = Queue(maxsize=1)
        connection = self.connection

        def exchange():
            try:
                if body is not None:
                    connection.send(body)
                responses.put((True, connection.recv()))
            except BaseException as error:
                responses.put((False, error))

        self.exchange_thread = threading.Thread(target=exchange, daemon=True, name="ml-inference-exchange")
        self.exchange_thread.start()
        while True:
            heartbeat()
            self.check_memory()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"{phase} exceeded its deadline")
            try:
                success, message = responses.get(timeout=min(0.5, remaining))
            except Empty:
                if not self.process.is_alive():
                    raise RuntimeError(f"Inference child exited with code {self.process.exitcode}")
                continue
            self.exchange_thread.join(timeout=1)
            self.exchange_thread = None
            if time.monotonic() >= deadline:
                raise TimeoutError(f"{phase} exceeded its deadline")
            if not success:
                raise RuntimeError("Inference child exited before returning a result") from message
            if message[0] == "error":
                raise RuntimeError(message[1])
            return message

    def analyze(self, body, heartbeat):
        try:
            if self.process is None:
                deadline = time.monotonic() + self.load_timeout
                context = multiprocessing.get_context("spawn")
                self.connection, child_connection = context.Pipe()
                self.process = context.Process(target=_child_entry, args=(child_connection, self.assets, self.target, os.getpid()))
                self.process.start()
                child_connection.close()
                ready = self._receive(heartbeat, deadline, "Model loading")
                if ready[0] != "ready" or len(ready) != 4:
                    raise RuntimeError("Invalid inference initialization response")
                self.metadata = ready[1:]
            heartbeat()
            self.check_memory()
            message = self._receive(heartbeat, time.monotonic() + self.job_timeout, "Inference", body)
            if message[0] != "result" or len(message) != 2:
                raise RuntimeError("Invalid inference response")
            self.check_memory()
            return message[1], self.metadata
        except BaseException:
            self.close()
            raise

    def close(self):
        import psutil

        process = self.process
        if process is not None:
            try:
                descendants = psutil.Process(process.pid).children(recursive=True)
            except psutil.NoSuchProcess:
                descendants = []
            for action in (signal.SIGTERM, signal.SIGKILL):
                try:
                    if hasattr(os, "killpg"):
                        os.killpg(process.pid, action)
                    elif process.is_alive():
                        process.terminate() if action == signal.SIGTERM else process.kill()
                except ProcessLookupError:
                    if process.is_alive():
                        process.terminate() if action == signal.SIGTERM else process.kill()
                process.join(timeout=2 if action == signal.SIGTERM else 1)
            _, alive = psutil.wait_procs(descendants, timeout=2)
            for item in alive:
                try:
                    if item.status() != psutil.STATUS_ZOMBIE:
                        raise RuntimeError("An inference descendant did not terminate")
                except psutil.NoSuchProcess:
                    pass
            process.close()
        if self.connection is not None:
            self.connection.close()
        if self.exchange_thread is not None:
            self.exchange_thread.join(timeout=1)
            self.exchange_thread = None
        self.process = self.connection = self.metadata = None


def status(path):
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0.25) as connection:
        connection.row_factory = sqlite3.Row
        state = dict(connection.execute("SELECT revision,status,worker_lease_until,backfill_kind FROM ml_state WHERE id=1").fetchone())
        jobs = dict(connection.execute("""SELECT count(*) AS pending, min(created_at) AS oldest_at,
          coalesce(sum(CASE WHEN error IS NOT NULL THEN 1 ELSE 0 END),0) AS failed FROM ml_jobs""").fetchone())
    state["worker_active"] = bool(state["worker_lease_until"] and state["worker_lease_until"] > time.time())
    state.update(jobs, database=str(path), memory_limit_bytes=memory_limit(), cpu_limit=4)
    return state


class Supervisor:
    def __init__(self, path, assets, stop, *, load_timeout=LOAD_TIMEOUT_SECONDS, job_timeout=JOB_TIMEOUT_SECONDS):
        self.path, self.assets, self.stop = path, Path(assets), stop
        self.connection = queue.connect_worker(path)
        self.engine = create_engine("sqlite://", creator=lambda: queue.connect_worker(path), poolclass=NullPool)
        self.inference = InferenceProcess(assets, load_timeout=load_timeout, job_timeout=job_timeout)
        # The verifier cannot share the memory budget with the extraction
        # models, so at most one of the two children is alive (see stage()).
        self.verifier = InferenceProcess(assets, target=_verifier_loop, load_timeout=load_timeout,
                                         job_timeout=job_timeout)
        self.model_version = self.eligibility_version = None
        self.eligibility_cursor = ("", "")
        self.eligibility_paused_until = 0.0
        # (kind, id, content_hash): an edited source gets a new key and is checked again.
        self.unverifiable = set()
        self.token = None
        self.claim = None
        self.next_renewal = 0
        self.worker_deadline = self.claim_deadline = 0
        self.lock_retries = Counter()

    def record_lock_retry(self, operation):
        self.lock_retries[operation] += 1
        print(f"ML SQLite retries: {json.dumps(dict(self.lock_retries), sort_keys=True)}", file=sys.stderr, flush=True)

    def heartbeat(self):
        if self.stop.is_set():
            raise Stopped("Worker stopped")
        if time.monotonic() < self.next_renewal:
            return
        operation = "renew_worker"
        try:
            if not queue.renew_worker(self.connection, self.token):
                raise LeaseLost("Worker lease lost")
            self.worker_deadline = time.time() + queue.LEASE_SECONDS
            if self.claim:
                operation = "renew_claim"
                if not queue.renew_claim(self.connection, self.claim):
                    raise LeaseLost("Source changed or job lease lost")
                self.claim_deadline = time.time() + queue.LEASE_SECONDS
            self.next_renewal = time.monotonic() + 10
        except sqlite3.OperationalError as error:
            if not database_busy(error):
                raise
            self.record_lock_retry(operation)
            if time.time() >= min(self.worker_deadline, self.claim_deadline if self.claim else self.worker_deadline):
                raise LeaseLost("Database contention prevented lease renewal")
            self.next_renewal = time.monotonic() + 1

    def retry(self, error):
        try:
            queue.retry_claim(self.connection, self.claim, f"{type(error).__name__}: {error}")
        except sqlite3.OperationalError as failure:
            if not database_busy(failure):
                raise
            self.record_lock_retry("retry")
            # A locked database keeps the existing durable claim until expiry.

    def apply(self, previous, result, metadata):
        from . import adapter, embeddings
        from .sources import snapshot

        with Session(self.engine, autoflush=False, expire_on_commit=False) as db:
            db.execute(text("BEGIN IMMEDIATE"))
            with embeddings.reserve_growth(db):
                if not queue.owns_claim(db, self.claim):
                    raise LeaseLost("Job changed before application")
                kind, source_id = self.claim.source_kind, self.claim.source_id
                if kind == "vocabulary":
                    adapter.apply_vocabulary(db, source_id)
                elif kind == "profile":
                    adapter.apply_profile(db, source_id)
                else:
                    current = snapshot(db, kind, source_id)
                    if (current.content_hash if current else None) != (previous.content_hash if previous else None):
                        raise LeaseLost("Source changed during inference")
                    if result is not None:
                        models = json.loads((self.assets / "models.json").read_text())["models"]
                        version = inference_version(models)
                        if version != metadata[0] or models["embeddings"]["revision"] != metadata[1]:
                            raise AssetsChanged("Model assets changed during inference")
                    adapter.apply_source(db, kind, source_id, current, result, *metadata)
                if not queue.complete_claim(db, self.claim):
                    raise LeaseLost("Job changed before commit")
            db.commit()

    def stage(self, active):
        """Free the other model child before `active` loads or runs."""
        other = self.verifier if active is self.inference else self.inference
        other.close()

    def process_claim(self):
        from . import adapter
        from .sources import snapshot

        previous, result, metadata = None, None, ("", "", 0)
        if self.claim.source_kind in ("item", "passage"):
            with Session(self.engine) as db:
                previous = snapshot(db, self.claim.source_kind, self.claim.source_id)
                if previous is not None:
                    models = json.loads((self.assets / "models.json").read_text())["models"]
                    version = inference_version(models)
                    cached = adapter.cached_result(db, previous, version, self.eligibility_version)
                    if cached is not None:
                        result, metadata = cached
            if previous is not None:
                if result is None:
                    self.stage(self.inference)
                    result, metadata = self.inference.analyze(previous.text, self.heartbeat)
        self.heartbeat()
        self.apply(previous, result, metadata)

    def verify_eligibility(self, limit=8):
        """Judge concept names of current cached sources that lack this verifier's judgment.

        Runs only when no job is claimable, so a backlog swaps models once. A
        source with a queued job is left to that job; its replay keeps any
        current judgment and the next sweep checks the rest.
        """
        from . import adapter, eligibility, embeddings
        from .sources import snapshot

        if self.eligibility_version is None or time.monotonic() < self.eligibility_paused_until:
            return 0
        rows = []
        # A primary-key cursor visits each cached source once per pass, so a
        # large post-upgrade backlog is not rescanned for every small batch.
        # The LIMIT leaves room for every skipped source, so a batch of only
        # skipped sources means the end of the table: wrap once, then stop.
        for _ in range(3):
            found = self.connection.execute("""SELECT s.kind, s.id, s.content_hash FROM ml_sources s
              WHERE (s.kind, s.id) > (?, ?) AND s.valid=1 AND s.kind IN ('item','passage') AND s.model_version=?
                AND coalesce(json_extract(s.result,'$.eligibility.version'),'')!=?
                AND NOT EXISTS (SELECT 1 FROM ml_jobs j WHERE j.source_kind=s.kind AND j.source_id=s.id)
              ORDER BY s.kind, s.id LIMIT ?""", (*self.eligibility_cursor, self.model_version,
                                                 self.eligibility_version, limit + len(self.unverifiable))).fetchall()
            if found:
                self.eligibility_cursor = tuple(found[-1][:2])
                rows = [tuple(row) for row in found if tuple(row) not in self.unverifiable][:limit]
                if rows:
                    break
            elif self.eligibility_cursor == ("", ""):
                break
            else:
                self.eligibility_cursor = ("", "")
        for kind, source_id, content_hash in rows:
            self.heartbeat()
            with Session(self.engine) as db:
                source = snapshot(db, kind, source_id)
                cached = adapter.cached_result(db, source, self.model_version) if source is not None else None
            if cached is None:
                # A queued job, not this sweep, restores a stale or withdrawn source.
                self.unverifiable.add((kind, source_id, content_hash))
                continue
            result, metadata = cached
            names = eligibility.candidates(source.text, result["concepts"])
            margins = {}
            if names:
                self.stage(self.verifier)
                try:
                    margins, verified = self.verifier.analyze({"text": source.text, "candidates": names}, self.heartbeat)
                except (RuntimeError, TimeoutError, MemoryError) as error:
                    # Leave its concepts held; retry after the worker restarts.
                    print(f"ML eligibility retained for {kind}:{source_id}: {type(error).__name__}: {error}",
                          file=sys.stderr, flush=True)
                    self.unverifiable.add((kind, source_id, content_hash))
                    continue
                if verified[0] != self.eligibility_version:
                    raise AssetsChanged("Verifier assets changed during eligibility checks")
            result["eligibility"] = {"version": self.eligibility_version, "margins": margins}
            self.heartbeat()
            try:
                with Session(self.engine, autoflush=False, expire_on_commit=False) as db:
                    db.execute(text("BEGIN IMMEDIATE"))
                    with embeddings.reserve_growth(db):
                        current = snapshot(db, kind, source_id)
                        queued = db.execute(text("SELECT 1 FROM ml_jobs WHERE source_kind=:kind AND source_id=:id"),
                                            {"kind": kind, "id": source_id}).first()
                        if current is not None and current.content_hash == source.content_hash and not queued:
                            adapter.apply_source(db, kind, source_id, current, result, *metadata)
                    db.commit()
            except (embeddings.StoragePressure, IntegrityError) as error:
                if isinstance(error, IntegrityError) and "storage quota" not in str(error):
                    raise
                # Background allocation pauses while storage is refused; concepts stay held.
                print(f"ML eligibility paused: {type(error).__name__}: {error}", file=sys.stderr, flush=True)
                self.eligibility_paused_until = time.monotonic() + STORAGE_PAUSE_SECONDS
                return 0
        return len(rows)

    def run(self, mode):
        from . import adapter, embeddings, policy

        lower_priority()
        while not self.stop.is_set():
            try:
                self.token = queue.acquire_worker(self.connection)
                break
            except sqlite3.OperationalError as error:
                if not database_busy(error):
                    raise
                self.record_lock_retry("acquire")
                self.stop.wait(1)
        if self.stop.is_set():
            return 0
        if self.token is None:
            raise RuntimeError("Another ML worker holds the lease")
        self.worker_deadline = time.time() + queue.LEASE_SECONDS
        models = json.loads((self.assets / "models.json").read_text())["models"]
        version = inference_version(models) + ":" + policy.VERSION
        self.model_version = inference_version(models)
        if "verifier" in models:
            from . import eligibility

            self.eligibility_version = eligibility.version(models)
        while True:
            self.heartbeat()
            try:
                with Session(self.engine) as db:
                    db.execute(text("BEGIN IMMEDIATE"))
                    with embeddings.reserve_growth(db):
                        adapter.bootstrap(db)
                        generation_ready = embeddings.prepare_generation(db, models["embeddings"]["revision"])
                        previous = db.execute(text("SELECT pipeline_version,decision_policy FROM ml_state WHERE id=1")).one()
                        if previous.pipeline_version != version or previous.decision_policy != "{}":
                            queue.request_backfill(db)
                            db.execute(text("UPDATE ml_state SET pipeline_version=:version,decision_policy='{}' WHERE id=1"),
                                       {"version": version})
                    db.commit()
                if generation_ready:
                    break
            except OperationalError as error:
                if not database_busy(error.orig):
                    raise
                self.record_lock_retry("bootstrap")
                self.stop.wait(1)
        idle = 1
        while not self.stop.is_set():
            self.heartbeat()
            self.inference.check_memory()
            operation = "checkpoint"
            try:
                if queue.checkpoint_between_batches(self.connection):
                    self.record_lock_retry(operation)
                    self.stop.wait(1)
                    continue
                operation = "claim"
                self.claim = queue.claim_next(self.connection, self.token)
                if self.claim is None:
                    operation = "backfill"
                    if queue.backfill_page(self.connection, self.token):
                        continue
                    operation = "housekeeping"
                    with Session(self.engine) as db:
                        db.execute(text("BEGIN IMMEDIATE"))
                        advanced = embeddings.finish_generation(db)
                        db.commit()
                    if advanced:
                        continue
                    operation = "eligibility"
                    if self.verify_eligibility():
                        continue
                    pending = self.connection.execute("SELECT count(*) FROM ml_jobs").fetchone()[0]
                    if mode == "once" or (mode == "drain" and pending == 0):
                        return 0
                    self.stop.wait(idle)
                    idle = min(10, idle * 2)
                    continue
            except (sqlite3.OperationalError, OperationalError) as error:
                if not database_busy(getattr(error, "orig", error)):
                    raise
                self.record_lock_retry(operation)
                self.stop.wait(1)
                continue
            idle = 1
            self.claim_deadline = self.claim.lease_until
            try:
                self.process_claim()
            except Exception as error:
                if database_busy(getattr(error, "orig", error)):
                    self.record_lock_retry("apply")
                if isinstance(error, AssetsChanged):
                    self.inference.close()
                self.retry(error)
                if isinstance(error, Stopped):
                    return 0
                print(f"ML job retained: {type(error).__name__}: {error}", file=sys.stderr, flush=True)
                if mode in ("once", "drain"):
                    return 1
            finally:
                self.claim = None
            if mode == "once":
                return 0
        return 0

    def close(self):
        self.inference.close()
        self.verifier.close()
        try:
            if self.claim:
                self.retry(Stopped("Worker stopped"))
            if self.token:
                try:
                    queue.release_worker(self.connection, self.token)
                except sqlite3.OperationalError as error:
                    if not database_busy(error):
                        raise
                    self.record_lock_retry("release")
                    print("ML worker lease will expire after database contention", file=sys.stderr)
        finally:
            print(f"ML SQLite retries final: {json.dumps(dict(self.lock_retries), sort_keys=True)}", file=sys.stderr, flush=True)
            self.connection.close()
            self.engine.dispose()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--once", action="store_true", help="process one available job")
    actions.add_argument("--drain", action="store_true", help="process the queue and bounded backfill")
    actions.add_argument("--status", action="store_true", help="read queue status without loading models")
    parser.add_argument("--assets", type=Path, help="verified local model asset directory")
    parser.add_argument("--load-timeout-seconds", type=positive_seconds,
                        default=os.environ.get("MDS_ML_LOAD_TIMEOUT_SECONDS", LOAD_TIMEOUT_SECONDS),
                        help="Finite model-load deadline (default 300 s; MDS_ML_LOAD_TIMEOUT_SECONDS)")
    parser.add_argument("--job-timeout-seconds", type=positive_seconds,
                        default=os.environ.get("MDS_ML_JOB_TIMEOUT_SECONDS", JOB_TIMEOUT_SECONDS),
                        help="Finite per-source inference deadline (default 1800 s; MDS_ML_JOB_TIMEOUT_SECONDS)")
    args = parser.parse_args(argv)
    if not args.status and args.assets is None:
        parser.error("--assets is required to run the worker")
    from .. import config

    supervisor = None
    stop = threading.Event()
    previous = {}
    try:
        path = database_path(config.DATABASE_URL)
        if args.status:
            print(json.dumps(status(path), sort_keys=True))
            return 0
        if not (args.assets / "models.json").is_file():
            raise ValueError("--assets must contain a prepared models.json manifest")
        manifest = json.loads((args.assets / "models.json").read_text())
        if "syntax" not in manifest.get("models", {}):
            raise ValueError("models.json must include the syntax model; "
                             "alias and definition extraction is disabled without it")
        if "verifier" not in manifest["models"]:
            raise ValueError("models.json must include the verifier model; "
                             "concepts cannot be published without eligibility checks")
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous[signum] = signal.signal(signum, lambda *_: stop.set())
        supervisor = Supervisor(path, args.assets, stop,
                                load_timeout=args.load_timeout_seconds, job_timeout=args.job_timeout_seconds)
        return supervisor.run("once" if args.once else "drain" if args.drain else "daemon")
    except Stopped:
        return 0
    except Exception as error:
        print(f"ML worker: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    finally:
        if supervisor is not None:
            supervisor.close()
        for signum, handler in previous.items():
            signal.signal(signum, handler)


if __name__ == "__main__":
    raise SystemExit(main())
