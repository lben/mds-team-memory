"""Quota-limited binary embeddings and bounded, approximate candidate retrieval.

Call mutations inside the worker's short BEGIN IMMEDIATE transaction. Similarity
is a retrieval score only; it cannot establish an alias, fact, or expertise.
"""

from contextlib import contextmanager
from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path
import shutil
import struct

from sqlalchemy import text

from .sources import digest, finding_key, snapshot

MAX_BYTES = 4 * 1024**3
FREE_RESERVE = 2 * 1024**3
ROW_OVERHEAD = 2048
CANDIDATE_LIMIT = 256
PAGE_SIZE = 25


class StoragePressure(RuntimeError):
    """Background allocation must pause while existing vectors stay usable."""


def _budget(db):
    return db.execute(text("SELECT * FROM ml_budget WHERE id=1")).mappings().one()


def _free_bytes(db):
    # Creator-based worker engines do not carry their file path in the URL.
    path = next(row[2] for row in db.execute(text("PRAGMA database_list")) if row[1] == "main")
    return shutil.disk_usage(Path(path).resolve().parent if path else Path.cwd()).free


def _check_space(db, total, growth, max_bytes):
    if total > min(MAX_BYTES, max_bytes):
        raise StoragePressure("Embedding quota cannot fit this allocation; current generation retained")
    _check_reserve(db, growth)


def _check_reserve(db, growth):
    if growth > 0 and _free_bytes(db) - growth < FREE_RESERVE:
        raise StoragePressure("Derived allocation would breach the 2 GiB filesystem reserve")


@contextmanager
def reserve_growth(db):
    """Check every charged allocation before the worker commits its transaction.

    The caller must roll back on refusal. Budget triggers include cached source
    results, finding payloads, and profile evidence as well as vector storage.
    Net retraction remains available when the filesystem reserve is depleted.
    """
    db.flush()
    before = _budget(db)
    yield
    db.flush()
    after = _budget(db)
    _check_reserve(db, after["embedding_bytes"] + after["evidence_bytes"]
                   - before["embedding_bytes"] - before["evidence_bytes"])


def _rebuild_estimate(db, dimensions):
    # Accepted tokenizers use at most one token per UTF-8 byte. Sentence-aware
    # 192-token windows advance at least 96 - 24 = 72 tokens. This overestimates
    # even short texts instead of assuming one vector per lifetime post.
    rows = db.execute(text("""SELECT COALESCE(SUM((size+71)/72),0) FROM (
      SELECT length(CAST(body AS BLOB)) AS size FROM knowledge_items WHERE visibility='team'
      UNION ALL SELECT length(CAST(text AS BLOB)) FROM document_passages)""")).scalar_one()
    return int(rows) * (dimensions * 4 + ROW_OVERHEAD)


def _values(vector, dimensions):
    if (type(dimensions) is not int or not 1 <= dimensions <= 1024
            or not isinstance(vector, bytes) or len(vector) != dimensions * 4):
        raise ValueError("Embedding length does not match the selected model")
    values = struct.unpack(f"<{dimensions}f", vector)
    if not all(math.isfinite(value) for value in values) or not any(values):
        raise ValueError("Embeddings must contain finite, nonzero vectors")
    return values


@lru_cache(maxsize=8)
def _planes(generation, dimensions):
    # Stable sparse random hyperplanes need no tensor libraries in the web app.
    seed = hashlib.sha256(f"{generation}:{dimensions}".encode()).digest()
    planes = []
    for bit in range(48):
        block = hashlib.sha256(seed + bit.to_bytes(2, "little")).digest()
        planes.append(tuple((int.from_bytes(block[i:i+2], "little") % dimensions,
                             1 if block[i+2] & 1 else -1) for i in range(0, 24, 3)))
    return tuple(planes)


def _buckets(values, generation):
    bits = sum((sum(values[index] * sign for index, sign in plane) >= 0) << bit
               for bit, plane in enumerate(_planes(generation, len(values))))
    return tuple((bits >> (band * 12)) & 4095 for band in range(4))


def replace_source(db, source, chunks, embedding_version, dimensions, *, max_bytes=MAX_BYTES):
    """Replace one source within one generation, preserving the active rebuild.

    None reuses unchanged-text vectors and updates their source version. The
    caller also stores the raw result, embedding_version, and embedding_count
    in ml_sources, in this same transaction.
    """
    current = snapshot(db, source.kind, source.id)
    if current is None or current.content_hash != source.content_hash:
        raise ValueError("Embedding source is no longer the same team-visible snapshot")
    if not embedding_version or len(embedding_version) > 80 or type(dimensions) is not int or not 1 <= dimensions <= 1024:
        raise ValueError("Unsupported embedding generation or dimensions")
    parameters = {"kind": source.kind, "id": source.id, "generation": embedding_version, "overhead": ROW_OVERHEAD}
    existing = db.execute(text("""SELECT COUNT(*), COALESCE(SUM(length(vector)+:overhead),0)
      FROM ml_embeddings WHERE source_kind=:kind AND source_id=:id AND generation=:generation"""), parameters).one()
    existing_dimensions = db.execute(text("SELECT dimensions FROM ml_embeddings WHERE generation=:generation LIMIT 1"), parameters).scalar()
    if existing_dimensions is not None and existing_dimensions != dimensions:
        raise ValueError("Embedding dimensions changed without a new model generation")
    if chunks is None:
        cached = db.execute(text("SELECT result FROM ml_sources WHERE kind=:kind AND id=:id"), parameters).scalar()
        cached = json.loads(cached) if cached else {}
        if (cached.get("text_hash") != digest(source.text) or cached.get("embedding_version") != embedding_version
                or cached.get("dimensions") != dimensions
                or cached.get("embedding_count") != existing[0]):
            raise ValueError("Cached embeddings are missing or no longer match this text")
        db.execute(text("""UPDATE ml_embeddings SET source_hash=:hash
          WHERE source_kind=:kind AND source_id=:id AND generation=:generation"""), {**parameters, "hash": source.content_hash})
        return
    rows = []
    seen = set()
    for chunk in chunks:
        start, end = chunk["start"], chunk["end"]
        if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(source.text) or start in seen:
            raise ValueError("Embedding windows must have unique, valid source offsets")
        seen.add(start)
        buckets = _buckets(_values(chunk["vector"], dimensions), embedding_version)
        rows.append({"key": finding_key("embedding", source.kind, source.id, embedding_version, start),
                     **parameters, "hash": source.content_hash, "start": start, "end": end,
                     "dimensions": dimensions, "vector": chunk["vector"],
                     **{f"bucket{band}": bucket for band, bucket in enumerate(buckets)}})
    budget = _budget(db)
    staging = budget["staging_generation"]
    if staging and embedding_version not in (budget["active_generation"], staging):
        raise StoragePressure("A different embedding rebuild is still in progress")
    new_bytes = len(rows) * (dimensions * 4 + ROW_OVERHEAD)
    total = budget["embedding_bytes"] - existing[1] + new_bytes
    reserve = budget["staging_reserved_bytes"]
    staged_bytes = budget["staging_bytes"]
    begin_staging = bool(budget["active_generation"] and embedding_version != budget["active_generation"] and not staging)
    if begin_staging:
        staged_bytes = db.execute(text("SELECT COALESCE(SUM(length(vector)+:overhead),0) FROM ml_embeddings WHERE generation=:generation"), parameters).scalar_one()
        reserve = max(_rebuild_estimate(db, dimensions), new_bytes)
        _check_space(db, budget["embedding_bytes"] - staged_bytes + reserve, max(0, reserve - staged_bytes), max_bytes)
        staging = embedding_version
    if staging:
        if embedding_version == staging:
            staged_bytes += new_bytes - existing[1]
        reserve = max(reserve, staged_bytes)
        # Existing-generation growth cannot consume the rebuild reservation.
        _check_space(db, total - staged_bytes + reserve, max(0, new_bytes - existing[1]), max_bytes)
    else:
        _check_space(db, total, max(0, new_bytes - existing[1]), max_bytes)
    if begin_staging:
        db.execute(text("""UPDATE ml_budget SET staging_generation=:generation,
          staging_cursor='',staging_reserved_bytes=:reserve,staging_bytes=:stored WHERE id=1"""),
                   {"generation": staging, "reserve": reserve, "stored": staged_bytes})
    elif staging:
        db.execute(text("UPDATE ml_budget SET staging_reserved_bytes=:reserve,staging_bytes=:stored WHERE id=1"),
                   {"reserve": reserve, "stored": staged_bytes})
    elif not budget["active_generation"]:
        db.execute(text("UPDATE ml_budget SET active_generation=:generation WHERE id=1"), parameters)
    db.execute(text("DELETE FROM ml_embeddings WHERE source_kind=:kind AND source_id=:id AND generation=:generation"), parameters)
    if rows:
        db.execute(text("""INSERT INTO ml_embeddings
          (key,source_kind,source_id,source_hash,generation,start,end,dimensions,vector,bucket0,bucket1,bucket2,bucket3)
          VALUES (:key,:kind,:id,:hash,:generation,:start,:end,:dimensions,:vector,:bucket0,:bucket1,:bucket2,:bucket3)"""), rows)


def invalidate_source(db, kind, source_id):
    """Remove vectors for an absent/private source; no new allocation is needed."""
    parameters = {"kind": kind, "id": source_id, "overhead": ROW_OVERHEAD}
    db.execute(text("""UPDATE ml_budget SET staging_bytes=staging_bytes-(
      SELECT COALESCE(SUM(length(vector)+:overhead),0) FROM ml_embeddings
      WHERE source_kind=:kind AND source_id=:id AND generation=ml_budget.staging_generation) WHERE id=1"""), parameters)
    db.execute(text("DELETE FROM ml_embeddings WHERE source_kind=:kind AND source_id=:id"), parameters)


def nearest(db, vector, generation, exclude_source=None, limit=8):
    """Return at most eight source windows from at most 256 indexed candidates."""
    limit = max(0, min(8, limit))
    if not limit:
        return []
    values = _values(vector, len(vector) // 4)
    if generation != _budget(db)["active_generation"]:
        return []
    buckets = _buckets(values, generation)
    candidates = {}
    for band, bucket in enumerate(buckets):
        rows = db.execute(text(f"""SELECT candidate.* FROM (
          SELECT * FROM ml_embeddings INDEXED BY ix_ml_embeddings_bucket{band}
          WHERE generation=:generation AND dimensions=:dimensions AND bucket{band}=:bucket
            AND NOT(source_kind=:kind AND source_id=:id) ORDER BY key LIMIT {CANDIDATE_LIMIT // 4}
        ) AS candidate JOIN ml_sources AS s ON s.kind=candidate.source_kind AND s.id=candidate.source_id
          AND s.valid=1 AND s.content_hash=candidate.source_hash
        WHERE (candidate.source_kind='item' AND EXISTS (
          SELECT 1 FROM knowledge_items WHERE id=candidate.source_id AND visibility='team'))
          OR (candidate.source_kind='passage' AND EXISTS (
          SELECT 1 FROM document_passages WHERE id=candidate.source_id))"""),
            {"generation": generation, "dimensions": len(values), "bucket": bucket,
             "kind": exclude_source[0] if exclude_source else "", "id": exclude_source[1] if exclude_source else ""}).mappings()
        for row in rows:
            candidates[row["key"]] = row
    norm = math.sqrt(sum(value * value for value in values))
    matches = []
    for row in candidates.values():
        other = _values(row["vector"], row["dimensions"])
        similarity = sum(left * right for left, right in zip(values, other)) / (norm * math.sqrt(sum(value * value for value in other)))
        matches.append({"source_kind": row["source_kind"], "source_id": row["source_id"],
                        "start": row["start"], "end": row["end"], "similarity": min(1.0, max(-1.0, similarity))})
    unique = {}
    for row in sorted(matches, key=lambda row: (-row["similarity"], row["source_kind"], row["source_id"], row["start"])):
        unique.setdefault((row["source_kind"], row["source_id"]), row)
    return list(unique.values())[:limit]


def finish_generation(db):
    """Advance one bounded completion/pruning batch; True means work advanced."""
    budget = _budget(db)
    staging = budget["staging_generation"]
    if staging:
        state = db.execute(text("SELECT backfill_kind,pipeline_version FROM ml_state WHERE id=1")).one()
        if (db.execute(text("SELECT 1 FROM ml_jobs LIMIT 1")).first()
                or state.backfill_kind):
            return False
        model_version = state.pipeline_version.rsplit(":", 1)[0]
        kind, _, cursor = (budget["staging_cursor"] or "item:").partition(":")
        table = "knowledge_items" if kind == "item" else "document_passages"
        eligibility = "AND current.visibility='team'" if kind == "item" else ""
        page = db.execute(text(f"""SELECT :kind AS kind,current.id,s.content_hash,s.valid,s.model_version,s.result,
          (SELECT COUNT(*) FROM ml_embeddings e WHERE e.source_kind=:kind AND e.source_id=current.id
           AND e.generation=:generation AND e.source_hash=s.content_hash) AS stored_count
          FROM {table} AS current LEFT JOIN ml_sources s ON s.kind=:kind AND s.id=current.id
          WHERE current.id > :cursor {eligibility} ORDER BY current.id LIMIT :limit"""),
            {"kind": kind, "generation": staging, "cursor": cursor, "limit": PAGE_SIZE}).mappings().all()
        for row in page:
            result = json.loads(row["result"]) if row["result"] else {}
            if (not row["valid"] or result.get("embedding_version") != staging
                    or row["model_version"] != model_version
                    or result.get("embedding_count") != row["stored_count"]):
                return False
        if page:
            last = page[-1]
            db.execute(text("UPDATE ml_budget SET staging_cursor=:cursor WHERE id=1"), {"cursor": last["kind"] + ":" + last["id"]})
        elif kind == "item":
            db.execute(text("UPDATE ml_budget SET staging_cursor='passage:' WHERE id=1"))
        else:
            db.execute(text("""UPDATE ml_budget SET active_generation=staging_generation,
              staging_generation=NULL,staging_cursor='',staging_reserved_bytes=0,staging_bytes=0 WHERE id=1"""))
        return True
    # Invalid support disappears from reads immediately; reclaim only a bounded
    # batch here, including superseded generations after the atomic switch.
    page = db.execute(text("""SELECT e.key,(generation!=:active
      OR NOT EXISTS (SELECT 1 FROM ml_sources s WHERE s.kind=e.source_kind AND s.id=e.source_id
        AND s.valid=1 AND s.content_hash=e.source_hash)
      OR (e.source_kind='item' AND NOT EXISTS (SELECT 1 FROM knowledge_items WHERE id=e.source_id AND visibility='team'))
      OR (e.source_kind='passage' AND NOT EXISTS (SELECT 1 FROM document_passages WHERE id=e.source_id))) AS obsolete
      FROM ml_embeddings e WHERE e.key>:cursor ORDER BY e.key LIMIT :limit"""),
        {"active": budget["active_generation"], "cursor": budget["staging_cursor"], "limit": CANDIDATE_LIMIT}).all()
    keys = [key for key, obsolete in page if obsolete]
    if keys:
        db.execute(text("DELETE FROM ml_embeddings WHERE key=:key"), [{"key": key} for key in keys])
    db.execute(text("UPDATE ml_budget SET staging_cursor=:cursor WHERE id=1"), {"cursor": page[-1][0] if page else ""})
    return bool(page)
