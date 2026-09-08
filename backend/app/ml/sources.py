"""Committed, team-visible snapshots used outside the write transaction."""

from dataclasses import dataclass
import hashlib
import json
import re


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode()).hexdigest()


def finding_key(kind, *identity):
    return digest([kind, *identity])


@dataclass(frozen=True)
class Snapshot:
    kind: str
    id: str
    text: str
    content_hash: str
    group_key: str
    text_hash: str
    author_id: str | None
    assertion_allowed: bool
    locator: str
    origin: str


def snapshot(db, kind, source_id):
    from ..models import DocumentPassage, KnowledgeItem

    if kind == "item":
        item = db.get(KnowledgeItem, source_id)
        if item is None or item.visibility != "team":
            return None
        semantic = {name: getattr(item, name) for name in (
            "kind", "body", "visibility", "author_profile_id", "parent_id", "source_document_id",
            "source_passage_id", "group_id", "accepted_answer_id", "question_status", "correction_state",
        )}
        text = item.body
        group = "document:" + item.source_document_id if item.source_document_id else "item:" + (item.group_id or item.id)
        if item.source_passage_id:
            passage = db.get(DocumentPassage, item.source_passage_id)
            if passage:
                group = "document:" + passage.document_id
        author = item.author_profile_id
        allowed = item.kind != "question" and (item.kind != "correction" or item.correction_state == "adopted")
        locator, origin = "", item.kind
    elif kind == "passage":
        passage = db.get(DocumentPassage, source_id)
        if passage is None:
            return None
        semantic = {name: getattr(passage, name) for name in ("text", "document_id", "ord", "locator")}
        text, group = passage.text, "document:" + passage.document_id
        author, allowed, locator, origin = None, True, passage.locator, "document"
    else:
        raise ValueError("Only team items and document passages have text snapshots")
    text_hash = digest(re.sub(r"\s+", " ", text.casefold()).strip())
    return Snapshot(kind, source_id, text, digest(semantic), group, text_hash, author, allowed, locator, origin)
