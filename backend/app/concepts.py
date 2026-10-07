"""Concept vocabulary and the tagging derived from it.

The vocabulary is one table (`concept_terms`). Tags in `item_concepts` and
`passage_concepts` are derived from it and are always recomputed rather than
patched, so they cannot drift when a term is renamed or removed.
"""

import re

from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

from .impact import notify
from .text import stem
from .ml import effective
from .models import (
    Account,
    Concept,
    ConceptTerm,
    DocumentPassage,
    ExpertiseMapping,
    ItemConcept,
    KnowledgeItem,
    Notification,
    PassageConcept,
    Profile,
)


def normalize_term(text: str) -> str:
    return " ".join(text.lower().split())


def vocabulary(db: Session) -> list[tuple[str, str]]:
    """(term, concept_id) for every searchable word, longest first.

    Longest first so 'data governance' wins over 'data' when both are defined.
    """
    rows = effective.terms(db).with_entities(ConceptTerm.term, ConceptTerm.concept_id).all()
    return sorted(rows, key=lambda r: (-len(r[0]), r[0]))


def term_groups(db: Session) -> dict[str, list[str]]:
    """term -> every spelling of its concept, for search query expansion."""
    by_concept: dict[str, list[str]] = {}
    terms = effective.terms(db).all()
    for t in terms:
        by_concept.setdefault(t.concept_id, []).append(t.display)
    groups: dict[str, list[str]] = {}
    for t in terms:
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
    concepts = effective.concepts(db).filter(Concept.id.in_(ids or [""])).all()
    return sorted(concepts, key=lambda c: c.name.lower())


# Searches forgive how a name is written. Shorter names ("AR") only match
# exactly; a misspelling must keep the first letter and be long enough that
# one wrong letter still identifies the name.
LOOSE_MIN = 4


def _compact(text: str) -> str:
    return re.sub(r"[\W_]+", "", text.lower())


def _typos_allowed(length: int) -> int:
    return 2 if length >= 12 else 1 if length >= 7 else 0


def _close(a: str, b: str, limit: int) -> bool:
    """Levenshtein distance of at most `limit`, with the first letter kept."""
    if a[0] != b[0] or abs(len(a) - len(b)) > limit:
        return False
    previous = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        current = [i]
        for j, y in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (x != y)))
        if min(current) > limit:
            return False
        previous = current
    return previous[-1] <= limit


def _team_word(db: Session, word: str) -> bool:
    """Whether team posts or documents use the word: then it is a word, not a misspelt name."""
    match = {"word": f'"{word}"'}
    return bool(db.execute(sql_text("""SELECT 1 FROM items_fts JOIN knowledge_items k ON k.rowid = items_fts.rowid
      WHERE items_fts MATCH :word AND k.visibility = 'team' LIMIT 1"""), match).first()
                or db.execute(sql_text("SELECT 1 FROM passages_fts WHERE passages_fts MATCH :word LIMIT 1"), match).first())


def search_concepts(db: Session, query: str) -> list[Concept]:
    """Concepts a search names, however it spaces, punctuates or slightly misspells them."""
    ids = match_concept_ids(db, query)
    names: dict[str, set[str]] = {}
    for term, concept_id in vocabulary(db):
        if len(key := _compact(term)) >= LOOSE_MIN:
            names.setdefault(key, set()).add(concept_id)
    words = re.findall(r"[^\W_]+", query.lower())
    for start in range(len(words)):
        for end in range(start + 1, min(len(words), start + 4) + 1):
            window = "".join(words[start:end])
            if len(window) < LOOSE_MIN:
                continue
            owners = names.get(window)
            limit = _typos_allowed(len(window))
            if owners is None and limit and not (end - start == 1 and _team_word(db, window)):
                owners = set().union(*(o for key, o in names.items() if _close(key, window, limit)))
            if owners and len(owners) == 1:  # a spelling two concepts could mean matches neither
                ids |= owners
    concepts = effective.concepts(db).filter(Concept.id.in_(ids or [""])).all()
    return sorted(concepts, key=lambda c: c.name.lower())


def source_concepts(db: Session, kind: str, source_id: str, text: str) -> list[Concept]:
    ids = effective.source_tags(db, kind, source_id, match_concept_ids(db, text))
    return sorted(effective.concepts(db).filter(Concept.id.in_(ids)).all(), key=lambda c: c.name.lower())


def _sync(db: Session, existing: dict[str, object], wanted: set[str], make) -> None:
    for concept_id in wanted - set(existing):
        db.add(make(concept_id))
    for concept_id in set(existing) - wanted:
        db.delete(existing[concept_id])


def retag_item(db: Session, item: KnowledgeItem, vocab=None, stems=None) -> set[str]:
    """Recompute an item's tags from scratch, adding and removing as needed."""
    wanted = match_concept_ids(db, item.body, vocab, stems)
    wanted = effective.source_tags(db, "item", item.id, wanted)
    existing = {
        row.concept_id: row
        for row in db.query(ItemConcept).filter(ItemConcept.item_id == item.id).all()
    }
    _sync(db, existing, wanted, lambda cid: ItemConcept(item_id=item.id, concept_id=cid))
    return wanted


def retag_passage(db: Session, passage: DocumentPassage, vocab=None, stems=None) -> set[str]:
    wanted = match_concept_ids(db, passage.text, vocab, stems)
    wanted = effective.source_tags(db, "passage", passage.id, wanted)
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
    if db.execute(sql_text("SELECT automation_enabled FROM ml_state WHERE id=1")).scalar():
        from .ml.queue import request_backfill
        request_backfill(db)
        db.commit()
        return
    vocab, stems = vocabulary(db), stem_index(db)
    for item in db.query(KnowledgeItem).all():
        retag_item(db, item, vocab, stems)
    for passage in db.query(DocumentPassage).all():
        retag_passage(db, passage, vocab, stems)
    db.commit()


def route_question(db: Session, question: KnowledgeItem, concepts: list[Concept]) -> None:
    """Notify each matching account once, using its normal sign-in profile."""
    if (not concepts or question.kind != "question" or question.visibility != "team"
            or question.accepted_answer_id or question.question_status == "resolved"):
        return
    from .auth import account_profile

    asker = db.get(Profile, question.author_profile_id)
    mappings = (
        effective.expertise(db)
        .filter(ExpertiseMapping.concept_id.in_([c.id for c in concepts]))
        .all()
    )
    delivered_accounts = {account_id for (account_id,) in db.query(Profile.account_id)
                          .join(Notification, Notification.profile_id == Profile.id)
                          .filter(Notification.kind == "expertise_match", Notification.item_id == question.id,
                                  Profile.account_id.isnot(None)).all()}
    for m in sorted(mappings, key=lambda mapping: (mapping.profile_id, mapping.concept_id)):
        account_id = m.profile.account_id
        if (not account_id or account_id in delivered_accounts
                or m.profile_id == question.author_profile_id
                or (asker and asker.account_id == account_id)):
            continue
        account = db.get(Account, account_id)
        if account is None:
            continue
        recipient = account_profile(db, account)
        notify(
            db,
            recipient.id,
            "expertise_match",
            f"A new question matches your expertise area '{m.concept.name}'.",
            question.id,
            dedup_key=f"route-account:{question.id}:{account_id}",
        )
        delivered_accounts.add(account_id)
