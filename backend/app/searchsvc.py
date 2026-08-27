import html
import math
import re
from datetime import timedelta

from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

from .concepts import term_groups
from .knowledge import item_dict
from .models import DocumentPassage, KnowledgeItem, Profile, Scratchpad, utcnow
from .text import build_fts_match, content_terms, find_matches

SEARCHABLE_KINDS = ("note", "question", "answer", "excerpt")


def _safe_snippet(raw: str) -> str:
    """Escape user content, then restore only our highlight markers as <mark> tags."""
    return html.escape(raw).replace("\x01", "<mark>").replace("\x02", "</mark>")


def _coverage(text: str, terms: list[str], aliases: dict[str, list[str]]) -> float:
    """Fraction of the query's meaningful words this text actually contains.

    Ranking by coverage first is what keeps a result that matches one word of a
    long question below one that matches all of them.
    """
    if not terms:
        return 1.0
    low = text.lower()
    hits = 0
    for term in terms:
        variants = aliases.get(term, [term])
        if any(re.search(rf"(?<!\w){re.escape(v.lower())}", low) for v in variants):
            hits += 1
    return hits / len(terms)


def _signals(entry: dict, updated_at) -> float:
    """Useful signals and freshness, log-damped so they refine relevance
    rather than override it (PRD 2: rank mainly by textual relevance)."""
    fresh = updated_at >= utcnow() - timedelta(days=30)
    return (
        0.6 * math.log1p(entry["helped"])
        + 0.4 * math.log1p(max(0, entry["contributors"] - 1))
        + (0.3 if fresh else 0.0)
    )


def _item_hits(db: Session, match: str, profile: Profile, terms: list[str], aliases: dict) -> list[dict]:
    rows = db.execute(
        sql_text(
            "SELECT ki.id, snippet(items_fts, 0, char(1), char(2), ' … ', 28), bm25(items_fts) "
            "FROM items_fts JOIN knowledge_items ki ON ki.rowid = items_fts.rowid "
            "WHERE items_fts MATCH :m AND ki.visibility = 'team' "
            "AND ki.kind IN ('note','question','answer','excerpt') "
            "ORDER BY bm25(items_fts) LIMIT 80"
        ),
        {"m": match},
    ).fetchall()
    hits = []
    for item_id, snip, rank in rows:
        item = db.get(KnowledgeItem, item_id)
        d = item_dict(db, item, profile)
        d["type"] = "item"
        d["snippet"] = _safe_snippet(snip)
        d["coverage"] = _coverage(f"{item.title or ''} {item.body}", terms, aliases)
        d["score"] = -rank + _signals(d, item.updated_at)
        hits.append(d)
    return hits


def _passage_hits(db: Session, match: str, terms: list[str], aliases: dict) -> list[dict]:
    rows = db.execute(
        sql_text(
            "SELECT p.id, snippet(passages_fts, 0, char(1), char(2), ' … ', 28), bm25(passages_fts) "
            "FROM passages_fts JOIN document_passages p ON p.rowid = passages_fts.rowid "
            "WHERE passages_fts MATCH :m ORDER BY bm25(passages_fts) LIMIT 40"
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
                "coverage": _coverage(passage.text, terms, aliases),
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


def _rank(hits: list[dict], group: bool) -> list[dict]:
    """Best result per id (or per corroboration group), ordered by how much of
    the query it covers, then by relevance and useful signals."""
    best: dict[str, dict] = {}
    for hit in sorted(hits, key=lambda x: (-x["coverage"], -x["score"])):
        key = (hit.get("group_id") or hit["id"]) if group else hit["id"]
        best.setdefault(key, hit)
    return sorted(best.values(), key=lambda x: (-x["coverage"], -x["score"]))


def search_all(db: Session, profile: Profile, query: str) -> dict:
    groups = term_groups(db)
    terms = content_terms(query)
    items: list[dict] = []
    passages: list[dict] = []

    # The strict pass guarantees entries matching every meaningful word are
    # retrieved even in a large corpus; the broad pass adds partial matches,
    # which coverage ranking then keeps below the full ones.
    expressions = [build_fts_match(query, groups)]
    if len(terms) > 1:
        expressions.append(build_fts_match(query, groups, operator="OR"))
    for expression in expressions:
        if not expression:
            continue
        items += _item_hits(db, expression, profile, terms, groups)
        passages += _passage_hits(db, expression, terms, groups)

    return {
        "query": query,
        "terms": terms,
        "items": _rank(items, group=True),
        "documents": _rank(passages, group=False),
        "scratchpad": _scratchpad_hits(db, profile, query),
    }
