"""Small SQLite queue. No transaction spans source reading or inference."""
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
import secrets
import sqlite3
import time

from sqlalchemy import text
from sqlalchemy.orm import Session

LEASE_SECONDS = 120
WAL_HIGH_WATER = 64 * 1024 * 1024
SOURCE_KINDS = {"item", "passage", "profile", "vocabulary"}


@dataclass(frozen=True)
class Claim:
    source_kind: str
    source_id: str
    generation: int
    lease_token: str
    lease_until: float
    worker_token: str


def sqlite_is_safe(version: tuple[int, ...]) -> bool:
    # SQLite documents these fixed branches at https://sqlite.org/wal.html#walreset
    return version >= (3, 51, 3) or (3, 50, 7) <= version < (3, 51) or (3, 44, 6) <= version < (3, 45)


def connect_worker(path: str | Path) -> sqlite3.Connection:
    if not sqlite_is_safe(sqlite3.sqlite_version_info):
        raise RuntimeError(f"ML worker requires SQLite with the WAL-reset fix; found {sqlite3.sqlite_version}")
    conn = sqlite3.connect(str(path), timeout=0.25, isolation_level=None)
    try:
        conn.execute("PRAGMA busy_timeout=250")
        conn.execute("PRAGMA foreign_keys=ON")
        if conn.execute("PRAGMA journal_mode=WAL").fetchone()[0] != "wal":
            raise RuntimeError("ML worker requires a local, file-backed WAL database")
        conn.execute("PRAGMA wal_autocheckpoint=1000")
        return conn
    except BaseException:
        conn.close()
        raise


@contextmanager
def _write(conn: sqlite3.Connection):
    if conn.in_transaction:
        raise RuntimeError("Queue operation requires a connection without an open transaction")
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def acquire_worker(conn: sqlite3.Connection, lease_seconds: float = LEASE_SECONDS) -> str | None:
    token, now = secrets.token_hex(24), time.time()
    with _write(conn):
        changed = conn.execute("""UPDATE ml_state SET worker_token=?, worker_lease_until=?, status='idle'
          WHERE id=1 AND (worker_token IS NULL OR worker_lease_until<=?)""",
          (token, now + lease_seconds, now)).rowcount
    return token if changed else None


def renew_worker(conn: sqlite3.Connection, token: str, lease_seconds: float = LEASE_SECONDS) -> bool:
    now = time.time()
    with _write(conn):
        changed = conn.execute("""UPDATE ml_state SET worker_lease_until=?
          WHERE id=1 AND worker_token=? AND worker_lease_until>?""", (now + lease_seconds, token, now)).rowcount
    return bool(changed)


def release_worker(conn: sqlite3.Connection, token: str) -> None:
    with _write(conn):
        conn.execute("""UPDATE ml_state SET worker_token=NULL, worker_lease_until=NULL, status='stopped'
          WHERE id=1 AND worker_token=?""", (token,))


def _worker_owned(conn: sqlite3.Connection, token: str, now: float) -> bool:
    return conn.execute("SELECT 1 FROM ml_state WHERE id=1 AND worker_token=? AND worker_lease_until>?",
                        (token, now)).fetchone() is not None


def claim_next(conn: sqlite3.Connection, worker_token: str, lease_seconds: float = LEASE_SECONDS) -> Claim | None:
    now, token = time.time(), secrets.token_hex(24)
    with _write(conn):
        if not _worker_owned(conn, worker_token, now):
            raise RuntimeError("Worker lease has expired")
        row = conn.execute("""SELECT source_kind, source_id, generation FROM ml_jobs
          WHERE available_at<=? AND (lease_token IS NULL OR lease_until<=?)
          ORDER BY priority, available_at, created_at, source_kind, source_id LIMIT 1""", (now, now)).fetchone()
        if row is None:
            return None
        conn.execute("""UPDATE ml_jobs SET lease_token=?, lease_until=?
          WHERE source_kind=? AND source_id=?""", (token, now + lease_seconds, row[0], row[1]))
        conn.execute("UPDATE ml_state SET status='running' WHERE id=1")
    return Claim(*row, token, now + lease_seconds, worker_token)


def renew_claim(conn: sqlite3.Connection, claim: Claim, lease_seconds: float = LEASE_SECONDS) -> bool:
    now = time.time()
    with _write(conn):
        if not _worker_owned(conn, claim.worker_token, now):
            return False
        changed = conn.execute("""UPDATE ml_jobs SET lease_until=? WHERE source_kind=? AND source_id=?
          AND generation=? AND lease_token=? AND lease_until>?""",
          (now + lease_seconds, claim.source_kind, claim.source_id, claim.generation, claim.lease_token, now)).rowcount
    return bool(changed)


CLAIM_WHERE = """source_kind=:source_kind AND source_id=:source_id AND generation=:generation
  AND lease_token=:lease_token AND lease_until>:now
  AND EXISTS(SELECT 1 FROM ml_state WHERE id=1 AND worker_token=:worker_token AND worker_lease_until>:now)"""


def owns_claim(db: Session, claim: Claim) -> bool:
    """Call within BEGIN IMMEDIATE, then validate the source and write in this session."""
    return db.execute(text("SELECT 1 FROM ml_jobs WHERE " + CLAIM_WHERE),
                      {**asdict(claim), "now": time.time()}).first() is not None


def complete_claim(db: Session, claim: Claim) -> bool:
    """Delete only this generation. Commit with canonical writes in the caller."""
    params = {**asdict(claim), "now": time.time()}
    changed = db.execute(text("DELETE FROM ml_jobs WHERE " + CLAIM_WHERE), params).rowcount
    # A concurrent edit keeps its generation and becomes claimable immediately.
    db.execute(text("""UPDATE ml_jobs SET lease_token=NULL, lease_until=NULL
      WHERE source_kind=:source_kind AND source_id=:source_id AND lease_token=:lease_token"""), params)
    return bool(changed)


def retry_delay(attempts: int) -> float:
    return min(300.0, 2.0 ** min(max(attempts - 1, 0), 9))


def retry_claim(conn: sqlite3.Connection, claim: Claim, error: str) -> bool:
    now = time.time()
    with _write(conn):
        row = conn.execute("""SELECT generation, attempts FROM ml_jobs
          WHERE source_kind=? AND source_id=? AND lease_token=?""",
          (claim.source_kind, claim.source_id, claim.lease_token)).fetchone()
        if row is None:
            return False
        if row[0] != claim.generation:
            conn.execute("""UPDATE ml_jobs SET lease_token=NULL, lease_until=NULL
              WHERE source_kind=? AND source_id=? AND lease_token=?""",
              (claim.source_kind, claim.source_id, claim.lease_token))
        else:
            attempts = min(row[1] + 1, 1_000_000)
            conn.execute("""UPDATE ml_jobs SET lease_token=NULL, lease_until=NULL,
              attempts=?, error=?, available_at=?, updated_at=?
              WHERE source_kind=? AND source_id=? AND lease_token=?""",
              (attempts, str(error)[:2000], now + retry_delay(attempts), now,
               claim.source_kind, claim.source_id, claim.lease_token))
    return True


def enqueue(db: Session, source_kind: str, source_id: str, priority: int = 0) -> None:
    if source_kind not in SOURCE_KINDS or not source_id:
        raise ValueError("A supported source kind and nonempty source id are required")
    db.execute(text("""INSERT INTO ml_jobs(source_kind,source_id,priority,available_at,created_at,updated_at)
      VALUES (:kind,:id,:priority,:now,:now,:now)
      ON CONFLICT(source_kind,source_id) DO UPDATE SET generation=ml_jobs.generation+1,
        priority=min(ml_jobs.priority,excluded.priority), available_at=excluded.available_at,
        attempts=0, error=NULL, updated_at=excluded.updated_at"""),
      {"kind": source_kind, "id": source_id, "priority": priority, "now": time.time()})


def request_backfill(db: Session) -> None:
    db.execute(text("""UPDATE ml_state SET backfill_kind='item',backfill_cursor='',
      backfill_generation=backfill_generation+1 WHERE id=1"""))


def backfill_page(conn: sqlite3.Connection, worker_token: str) -> int:
    """Persist the cursor with at most 25 enqueues; live work always wins conflicts."""
    sources = {"item": ("knowledge_items", "visibility='team'", "passage"),
               "passage": ("document_passages", "1", "profile"),
               "profile": ("profiles", "account_id IS NOT NULL", None)}
    now = time.time()
    with _write(conn):
        if not _worker_owned(conn, worker_token, now):
            raise RuntimeError("Worker lease has expired")
        kind, cursor = conn.execute("SELECT backfill_kind,backfill_cursor FROM ml_state WHERE id=1").fetchone()
        while kind is not None:
            table, condition, next_kind = sources[kind]
            rows = conn.execute(f"SELECT id FROM {table} WHERE id>? AND {condition} ORDER BY id LIMIT 25", (cursor,)).fetchall()
            conn.executemany("""INSERT INTO ml_jobs(source_kind,source_id,priority,available_at,created_at,updated_at)
              VALUES (?,?,10,?,?,?) ON CONFLICT(source_kind,source_id) DO NOTHING""",
              [(kind, row[0], now, now, now) for row in rows])
            cursor = rows[-1][0] if len(rows) == 25 else ""
            kind = kind if len(rows) == 25 else next_kind
            conn.execute("UPDATE ml_state SET backfill_kind=?, backfill_cursor=? WHERE id=1", (kind, cursor))
            if rows:
                return len(rows)
    return 0


def checkpoint_between_batches(conn: sqlite3.Connection) -> bool:
    """Return True when uncheckpointed WAL pressure requires worker backoff."""
    if conn.in_transaction:
        raise RuntimeError("Checkpoint requires a connection without an open transaction")
    path = next(row[2] for row in conn.execute("PRAGMA database_list") if row[1] == "main")
    wal = Path(path + "-wal")
    if not wal.exists() or wal.stat().st_size == 0:
        return False
    busy, log_pages, checkpointed = conn.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
    # SQLite reuses the allocated WAL; file size alone would pause forever after
    # a successful checkpoint. Only a still-blocked large WAL requires backoff.
    return wal.exists() and wal.stat().st_size >= WAL_HIGH_WATER and (bool(busy) or log_pages > checkpointed)
