import re

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .impact import notify
from .models import Concept, ConceptAlias, ExpertiseMapping, ItemConcept, KnowledgeItem


def alias_map(db: Session) -> dict[str, Concept]:
    """normalized alias -> Concept (includes each concept's own name)."""
    result: dict[str, Concept] = {}
    for concept in db.query(Concept).all():
        result[concept.name.lower()] = concept
        for a in concept.aliases:
            result[a.alias.lower()] = concept
    return result


def alias_groups(db: Session) -> dict[str, list[str]]:
    """normalized alias -> all alias spellings of its concept (for query expansion)."""
    groups: dict[str, list[str]] = {}
    for concept in db.query(Concept).all():
        variants = [concept.name] + [a.alias for a in concept.aliases]
        for v in variants:
            groups[v.lower()] = variants
    return groups


def match_concepts(db: Session, text: str) -> list[Concept]:
    """Deterministic word-boundary alias matching."""
    low = text.lower()
    matched: dict[str, Concept] = {}
    for alias, concept in alias_map(db).items():
        if concept.id in matched:
            continue
        if re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", low):
            matched[concept.id] = concept
    return sorted(matched.values(), key=lambda c: c.name)


def tag_subject(db: Session, subject_kind: str, subject_id: str, text: str) -> list[Concept]:
    concepts = match_concepts(db, text)
    for concept in concepts:
        try:
            with db.begin_nested():
                db.add(
                    ItemConcept(
                        subject_kind=subject_kind, subject_id=subject_id, concept_id=concept.id
                    )
                )
        except IntegrityError:
            pass
    return concepts


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
