"""Concept vocabulary and the tagging derived from it.

The vocabulary is one table (`concept_terms`). Tags in `item_concepts` and
`passage_concepts` are derived from it and are always recomputed rather than
patched, so they cannot drift when a term is renamed or removed.
"""

import re

from sqlalchemy.orm import Session

from .impact import notify
from .text import stem
from .models import (
    Concept,
    ConceptTerm,
    DocumentPassage,
    ExpertiseMapping,
    ItemConcept,
    KnowledgeItem,
    PassageConcept,
)


def normalize_term(text: str) -> str:
    return " ".join(text.lower().split())


def vocabulary(db: Session) -> list[tuple[str, str]]:
    """(term, concept_id) for every searchable word, longest first.

    Longest first so 'data governance' wins over 'data' when both are defined.
    """
    rows = db.query(ConceptTerm.term, ConceptTerm.concept_id).all()
    return sorted(rows, key=lambda r: (-len(r[0]), r[0]))


def term_groups(db: Session) -> dict[str, list[str]]:
    """term -> every spelling of its concept, for search query expansion."""
    by_concept: dict[str, list[str]] = {}
    for t in db.query(ConceptTerm).all():
        by_concept.setdefault(t.concept_id, []).append(t.display)
    groups: dict[str, list[str]] = {}
    for t in db.query(ConceptTerm).all():
        groups[t.term] = by_concept[t.concept_id]
    return groups


def stem_index(db: Session) -> dict[str, str]:
    """stem -> concept_id, for single-word terms only.

    A stem claimed by two different concepts is dropped rather than resolved
    arbitrarily: those words still match exactly, they just stop matching
    loosely. Same rule as the vocabulary itself — one word, one concept.
    """
    owners: dict[str, set[str]] = {}
    for term, concept_id in vocabulary(db):
        if " " in term:
            continue  # multi-word terms only ever match exactly
        owners.setdefault(stem(term), set()).add(concept_id)
    return {key: next(iter(ids)) for key, ids in owners.items() if len(ids) == 1}


def mentions(text: str, term: str) -> bool:
    """The one rule for 'does this text mention this term': whole words only."""
    return re.search(rf"(?<!\w){re.escape(term)}(?!\w)", text.lower()) is not None


def match_concept_ids(db: Session, text: str, vocab=None, stems=None) -> set[str]:
    low = text.lower()
    found = {cid for term, cid in (vocab or vocabulary(db)) if mentions(low, term)}
    # Then loosely: "emulate" and "emulators" should reach the Emulation concept.
    index = stem_index(db) if stems is None else stems
    for word in re.findall(r"\w+", low):
        owner = index.get(stem(word))
        if owner:
            found.add(owner)
    return found


def match_concepts(db: Session, text: str) -> list[Concept]:
    ids = match_concept_ids(db, text)
    concepts = db.query(Concept).filter(Concept.id.in_(ids or [""])).all()
    return sorted(concepts, key=lambda c: c.name.lower())


def _sync(db: Session, existing: dict[str, object], wanted: set[str], make) -> None:
    for concept_id in wanted - set(existing):
        db.add(make(concept_id))
    for concept_id in set(existing) - wanted:
        db.delete(existing[concept_id])


def retag_item(db: Session, item: KnowledgeItem, vocab=None, stems=None) -> set[str]:
    """Recompute an item's tags from scratch, adding and removing as needed."""
    wanted = match_concept_ids(db, item.body, vocab, stems)
    existing = {
        row.concept_id: row
        for row in db.query(ItemConcept).filter(ItemConcept.item_id == item.id).all()
    }
    _sync(db, existing, wanted, lambda cid: ItemConcept(item_id=item.id, concept_id=cid))
    return wanted


def retag_passage(db: Session, passage: DocumentPassage, vocab=None, stems=None) -> set[str]:
    wanted = match_concept_ids(db, passage.text, vocab, stems)
    existing = {
        row.concept_id: row
        for row in db.query(PassageConcept).filter(PassageConcept.passage_id == passage.id).all()
    }
    _sync(db, existing, wanted, lambda cid: PassageConcept(passage_id=passage.id, concept_id=cid))
    return wanted


def retag_everything(db: Session) -> None:
    """Rebuild every tag after the vocabulary changes.

    Covers passages as well as items, so a concept created after a document was
    uploaded still finds it.
    """
    vocab, stems = vocabulary(db), stem_index(db)
    for item in db.query(KnowledgeItem).all():
        retag_item(db, item, vocab, stems)
    for passage in db.query(DocumentPassage).all():
        retag_passage(db, passage, vocab, stems)
    db.commit()


def route_question(db: Session, question: KnowledgeItem, concepts: list[Concept]) -> None:
    """Notify profiles whose expertise areas match the question's concepts."""
    if not concepts:
        return
    mappings = (
        db.query(ExpertiseMapping)
        .filter(ExpertiseMapping.concept_id.in_([c.id for c in concepts]))
        .all()
    )
    for m in mappings:
        if m.profile_id == question.author_profile_id:
            continue
        notify(
            db,
            m.profile_id,
            "expertise_match",
            f"A new question matches your expertise area '{m.concept.name}'.",
            question.id,
            dedup_key=f"route:{question.id}:{m.profile_id}",
        )
