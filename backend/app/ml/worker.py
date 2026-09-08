"""One leased supervisor and one reusable, separately monitored inference child."""

import argparse
from collections import Counter
import json
import multiprocessing
import os
from pathlib import Path
import signal
import sqlite3
import sys
import threading
import time

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from . import queue
from .runtime import inference_version

MEMORY_CEILING = 8 * 1024**3


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
    def __init__(self, assets, *, target=_model_loop, limit=None):
        self.assets, self.target = str(assets), target
        self.limit = memory_limit() if limit is None else min(limit, memory_limit())
        self.process = self.connection = None
        self.metadata = None

    def check_memory(self):
        used = process_tree_rss()
        if used > self.limit:
            raise MemoryError(f"ML process RSS {used} exceeds the {self.limit}-byte limit")

    def _receive(self, heartbeat):
        while True:
            heartbeat()
            self.check_memory()
            if self.connection.poll(0.5):
                try:
                    message = self.connection.recv()
                except (EOFError, OSError) as error:
                    raise RuntimeError("Inference child exited before returning a result") from error
                if message[0] == "error":
                    raise RuntimeError(message[1])
                return message
            if not self.process.is_alive():
                raise RuntimeError(f"Inference child exited with code {self.process.exitcode}")

    def analyze(self, body, heartbeat):
        try:
            if self.process is None:
                context = multiprocessing.get_context("spawn")
                self.connection, child_connection = context.Pipe()
                self.process = context.Process(target=_child_entry, args=(child_connection, self.assets, self.target, os.getpid()))
                self.process.start()
                child_connection.close()
                ready = self._receive(heartbeat)
                if ready[0] != "ready" or len(ready) != 4:
                    raise RuntimeError("Invalid inference initialization response")
                self.metadata = ready[1:]
            heartbeat()
            self.check_memory()
            self.connection.send(body)
            message = self._receive(heartbeat)
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
    def __init__(self, path, assets, stop):
        self.path, self.assets, self.stop = path, Path(assets), stop
        self.connection = queue.connect_worker(path)
        self.engine = create_engine("sqlite://", creator=lambda: queue.connect_worker(path), poolclass=NullPool)
        self.inference = InferenceProcess(assets)
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
                    cached = adapter.cached_result(db, previous, version)
                    if cached is not None:
                        result, metadata = cached
            if previous is not None:
                if result is None:
                    result, metadata = self.inference.analyze(previous.text, self.heartbeat)
        self.heartbeat()
        self.apply(previous, result, metadata)

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
        while True:
            self.heartbeat()
            try:
                with Session(self.engine) as db:
                    db.execute(text("BEGIN IMMEDIATE"))
                    with embeddings.reserve_growth(db):
                        adapter.bootstrap(db)
                        previous = db.execute(text("SELECT pipeline_version,decision_policy FROM ml_state WHERE id=1")).one()
                        if previous.pipeline_version != version or previous.decision_policy != "{}":
                            queue.request_backfill(db)
                            db.execute(text("UPDATE ml_state SET pipeline_version=:version,decision_policy='{}' WHERE id=1"),
                                       {"version": version})
                    db.commit()
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
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous[signum] = signal.signal(signum, lambda *_: stop.set())
        supervisor = Supervisor(path, args.assets, stop)
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
