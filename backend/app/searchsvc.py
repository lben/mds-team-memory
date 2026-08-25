import html
from datetime import timedelta

from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

from .concepts import alias_groups
from .knowledge import item_dict
from .models import DocumentPassage, KnowledgeItem, Profile, Scratchpad, utcnow
from .text import build_fts_match, find_matches

SEARCHABLE_KINDS = ("note", "question", "answer", "excerpt")


def _safe_snippet(raw: str) -> str:
    """Escape user content, then restore only our highlight markers as <mark> tags."""
    return (
        html.escape(raw)
        .replace("\x01", "<mark>")
        .replace("\x02", "</mark>")
    )


def _item_hits(db: Session, match: str, profile: Profile) -> list[dict]:
    rows = db.execute(
        sql_text(
            "SELECT ki.id, snippet(items_fts, 0, char(1), char(2), ' … ', 28), bm25(items_fts) "
            "FROM items_fts JOIN knowledge_items ki ON ki.rowid = items_fts.rowid "
            "WHERE items_fts MATCH :m AND ki.visibility = 'team' "
            "AND ki.kind IN ('note','question','answer','excerpt') "
            "ORDER BY bm25(items_fts) LIMIT 50"
        ),
        {"m": match},
    ).fetchall()
    fresh_cutoff = utcnow() - timedelta(days=30)
    hits = []
    for item_id, snip, rank in rows:
        item = db.get(KnowledgeItem, item_id)
        d = item_dict(db, item, profile)
        d["type"] = "item"
        d["snippet"] = _safe_snippet(snip)
        # Rank mainly by textual relevance, then useful signals and freshness.
        d["score"] = (
            -rank
            + 0.4 * d["helped"]
            + 0.25 * (d["contributors"] - 1)
            + (0.5 if item.updated_at >= fresh_cutoff else 0.0)
        )
        hits.append(d)
    # Collapse corroboration groups to their best-scoring representative.
    best: dict[str, dict] = {}
    for h in sorted(hits, key=lambda x: -x["score"]):
        key = h["group_id"] or h["id"]
        if key not in best:
            best[key] = h
    return sorted(best.values(), key=lambda x: -x["score"])


def _passage_hits(db: Session, match: str) -> list[dict]:
    rows = db.execute(
        sql_text(
            "SELECT p.id, snippet(passages_fts, 0, char(1), char(2), ' … ', 28), bm25(passages_fts) "
            "FROM passages_fts JOIN document_passages p ON p.rowid = passages_fts.rowid "
            "WHERE passages_fts MATCH :m ORDER BY bm25(passages_fts) LIMIT 20"
        ),
        {"m": match},
    ).fetchall()
    hits = []
    for passage_id, snip, rank in rows:
        passage = db.get(DocumentPassage, passage_id)
        doc = passage.document
        hits.append(
            {
                "type": "passage",
                "id": passage.id,
                "document_id": doc.id,
                "filename": doc.filename,
                "locator": passage.locator,
                "uploader": doc.uploader.label,
                "uploaded_at": doc.uploaded_at.isoformat() + "Z",
                "snippet": _safe_snippet(snip),
                "score": -rank,
            }
        )
    return hits


def _scratchpad_hits(db: Session, profile: Profile, query: str) -> list[dict]:
    hits = []
    for pad in db.query(Scratchpad).filter(Scratchpad.profile_id == profile.id).all():
        for m in find_matches(pad.content, query, max_hits=5):
            hits.append(
                {
                    "type": "scratchpad",
                    "scratchpad_id": pad.id,
                    "line": m["line"],
                    "snippet": m["text"],
                }
            )
    return hits


def search_all(db: Session, profile: Profile, query: str) -> dict:
    groups = alias_groups(db)
    match = build_fts_match(query, groups)
    items, passages = [], []
    if match:
        items = _item_hits(db, match, profile)
        passages = _passage_hits(db, match)
        if not items and not passages:
            # Google-like recall: fall back to any-term matching, still ranked by bm25.
            relaxed = build_fts_match(query, groups, operator="OR")
            items = _item_hits(db, relaxed, profile)
            passages = _passage_hits(db, relaxed)
    return {
        "query": query,
        "items": items,
        "documents": passages,
        "scratchpad": _scratchpad_hits(db, profile, query),
    }
