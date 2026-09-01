from collections import Counter, defaultdict

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..auth import require_admin
from ..db import get_db
from ..models import (
    CORROBORATES_ID,
    RELATED_TO_ID,
    Account,
    Concept,
    Document,
    DocumentPassage,
    ItemConcept,
    KnowledgeItem,
    PassageConcept,
    Relationship,
    RelationshipType,
)
from ..relationships import (
    VISIBLE_STATES,
    evidence_detail,
    evidence_text,
    find_link,
    link_dict,
    recount,
    set_state,
    type_usage,
)

router = APIRouter(prefix="/api/graph", tags=["graph"])

MAX_NEIGHBORS = 12


class LinkIn(BaseModel):
    src_id: str
    dst_id: str
    type_id: str
    note: str = Field(min_length=1, max_length=500)


class LinkPatch(BaseModel):
    state: str | None = Field(default=None, pattern="^(suggested|confirmed|rejected)$")
    type_id: str | None = None
    note: str | None = Field(default=None, max_length=500)


def _team_subject_concepts(db: Session) -> list[tuple[str, str, str]]:
    """(subject_kind, subject_id, concept_id) restricted to team-visible content.

    Private items are excluded so they can never appear in another profile's
    graph or counts.
    """
    item_rows = (
        db.query(ItemConcept.item_id, ItemConcept.concept_id)
        .join(KnowledgeItem, KnowledgeItem.id == ItemConcept.item_id)
        .filter(KnowledgeItem.visibility == "team")
        .all()
    )
    passage_rows = db.query(PassageConcept.passage_id, PassageConcept.concept_id).all()
    return [("item", s, c) for s, c in item_rows] + [("passage", s, c) for s, c in passage_rows]


def _visible_concept_links(db: Session) -> list[Relationship]:
    return (
        db.query(Relationship)
        .filter(
            Relationship.src_kind == "concept",
            Relationship.dst_kind == "concept",
            Relationship.state.in_(VISIBLE_STATES),
        )
        .all()
    )


def _selectable_type(db: Session, type_id: str) -> RelationshipType:
    """A label an admin may put on a concept link. 'corroborates' is generated
    automatically between contributions and is never chosen by hand."""
    rtype = db.get(RelationshipType, type_id)
    if not rtype:
        raise HTTPException(404, "Relationship type not found")
    if rtype.id == CORROBORATES_ID:
        raise HTTPException(400, "'corroborates' is generated automatically between contributions")
    return rtype


def _get_link(db: Session, link_id: str) -> Relationship:
    link = db.get(Relationship, link_id)
    if not link or link.src_kind != "concept" or link.dst_kind != "concept":
        raise HTTPException(404, "Link not found")
    return link


@router.get("/concepts")
def list_concepts(db: Session = Depends(get_db)):
    rows = _team_subject_concepts(db)
    counts = Counter(c for _, _, c in rows)
    concepts = sorted(db.query(Concept).all(), key=lambda c: c.name.lower())
    return [{"id": c.id, "name": c.name, "mentions": counts.get(c.id, 0)} for c in concepts]


@router.get("/local")
def local_graph(concept_id: str, db: Session = Depends(get_db)):
    center = db.get(Concept, concept_id)
    if not center:
        raise HTTPException(404, "Concept not found")
    center_node = {"id": f"c:{center.id}", "type": "concept", "label": center.name, "center": True}
    nodes: list[dict] = [center_node]
    edges: list[dict] = []

    def add(node: dict, edge_label: str, style: str, evidence: str, link_id: str | None = None) -> None:
        if len(nodes) - 1 >= MAX_NEIGHBORS or any(n["id"] == node["id"] for n in nodes):
            return
        nodes.append(node)
        edges.append(
            {
                "source": center_node["id"],
                "target": node["id"],
                "label": edge_label,
                "style": style,
                "evidence": evidence,
                "link_id": link_id,
            }
        )

    # 1. Concept-to-concept links: dashed while suggested, solid once confirmed.
    for link in _visible_concept_links(db):
        if center.id not in (link.src_id, link.dst_id):
            continue
        other_id = link.dst_id if link.src_id == center.id else link.src_id
        other = db.get(Concept, other_id)
        if not other:
            continue
        add(
            {"id": f"c:{other.id}", "type": "concept", "label": other.name},
            link.relationship_type.name,
            "dashed" if link.state == "suggested" else "solid",
            evidence_text(link),
            link.id,
        )

    concept_subjects = [(k, s) for k, s, c in _team_subject_concepts(db) if c == center.id]
    item_ids = [s for k, s in concept_subjects if k == "item"]
    passage_ids = [s for k, s in concept_subjects if k == "passage"]

    # 2. Documents whose passages mention the concept (structural, solid).
    if passage_ids:
        doc_locators: dict[str, list[str]] = defaultdict(list)
        for p in db.query(DocumentPassage).filter(DocumentPassage.id.in_(passage_ids)).all():
            doc_locators[p.document_id].append(p.locator)
        for doc_id in sorted(doc_locators)[:3]:
            doc = db.get(Document, doc_id)
            add(
                {"id": f"d:{doc.id}", "type": "document", "label": doc.filename},
                "documented in",
                "solid",
                f"Matched at {', '.join(doc_locators[doc_id][:3])}.",
            )

    # 3. Questions and team items mentioning the concept (structural, solid).
    if item_ids:
        items = (
            db.query(KnowledgeItem)
            .filter(KnowledgeItem.id.in_(item_ids), KnowledgeItem.visibility == "team")
            .all()
        )
        for q in sorted(
            [i for i in items if i.kind == "question"], key=lambda x: x.created_at, reverse=True
        )[:3]:
            add(
                {
                    "id": f"i:{q.id}",
                    "type": "question",
                    "label": (q.body[:60] + "…") if len(q.body) > 60 else q.body,
                    "sublabel": q.question_status,
                },
                "asked about",
                "solid",
                "The question text mentions this concept.",
            )
        seen_groups: set[str] = set()
        for i in sorted(
            [i for i in items if i.kind in ("note", "excerpt", "answer")],
            key=lambda x: x.created_at,
            reverse=True,
        ):
            group_key = i.group_id or i.id
            if group_key in seen_groups:
                continue
            seen_groups.add(group_key)
            add(
                {
                    "id": f"i:{i.id}",
                    "type": "item",
                    "label": (i.body[:60] + "…") if len(i.body) > 60 else i.body,
                },
                "mentioned in",
                "solid",
                "The contribution text mentions this concept.",
            )

    return {"nodes": nodes, "edges": edges}


@router.get("/global")
def global_graph(db: Session = Depends(get_db)):
    rows = _team_subject_concepts(db)
    counts = Counter(c for _, _, c in rows)
    links = _visible_concept_links(db)
    # Every concept an admin defined is shown, including ones nothing mentions
    # yet, so the count in the header matches what is on screen.
    concepts = {c.id: c for c in db.query(Concept).all()}

    neighbors: dict[str, set[str]] = defaultdict(set)
    for link in links:
        if link.src_id in concepts and link.dst_id in concepts:
            neighbors[link.src_id].add(link.dst_id)
            neighbors[link.dst_id].add(link.src_id)

    clusters = []
    unvisited = set(concepts)
    while unvisited:
        component, stack = [], [min(unvisited)]
        while stack:
            node = stack.pop()
            if node not in unvisited:
                continue
            unvisited.discard(node)
            component.append(node)
            stack.extend(neighbors[node] - set(component))
        members = sorted(component, key=lambda cid: (-counts[cid], concepts[cid].name))
        # Naming a cluster after one member reads as that concept rather than a
        # group, so say how many others it holds.
        lead = concepts[members[0]].name
        label = lead if len(members) == 1 else f"{lead} + {len(members) - 1} more"
        clusters.append(
            {
                "id": members[0],
                "label": label,
                "concepts": [
                    {"id": cid, "name": concepts[cid].name, "size": counts[cid]} for cid in members
                ],
            }
        )
    clusters.sort(key=lambda cl: -sum(c["size"] for c in cl["concepts"]))
    edges = [
        {
            "source": link.src_id,
            "target": link.dst_id,
            "count": link.occurrence_count,
            "link_id": link.id,
            "state": link.state,
            "label": link.relationship_type.name,
        }
        for link in links
        if link.src_id in concepts and link.dst_id in concepts
    ]
    return {"clusters": clusters, "edges": edges}


@router.get("/links/{link_id}/evidence")
def link_evidence(link_id: str, db: Session = Depends(get_db)):
    """Drill-down into the contributions behind a link. Readable by everyone;
    only ever returns team-visible content."""
    return evidence_detail(db, _get_link(db, link_id))


@router.get("/links", dependencies=[Depends(require_admin)])
def list_links(
    concept_id: str | None = None,
    state: str | None = Query(default=None, pattern="^(suggested|confirmed|rejected)$"),
    db: Session = Depends(get_db),
):
    query = db.query(Relationship).filter(
        Relationship.src_kind == "concept", Relationship.dst_kind == "concept"
    )
    if concept_id:
        query = query.filter(
            (Relationship.src_id == concept_id) | (Relationship.dst_id == concept_id)
        )
    if state:
        query = query.filter(Relationship.state == state)
    links = query.all()
    for link in links:
        recount(db, link)
    db.commit()
    links.sort(key=lambda link: (-link.occurrence_count, link.id))
    return [link_dict(db, link) for link in links]


@router.post("/links")
def create_link(
    payload: LinkIn, admin: Account = Depends(require_admin), db: Session = Depends(get_db)
):
    if payload.src_id == payload.dst_id:
        raise HTTPException(400, "A link needs two different concepts")
    for concept_id in (payload.src_id, payload.dst_id):
        if not db.get(Concept, concept_id):
            raise HTTPException(404, "Concept not found")
    _selectable_type(db, payload.type_id)
    if find_link(db, payload.src_id, payload.dst_id):
        raise HTTPException(400, "These concepts are already linked — edit the existing link")

    link = Relationship(
        src_kind="concept",
        src_id=payload.src_id,
        dst_kind="concept",
        dst_id=payload.dst_id,
        relationship_type_id=payload.type_id,
        state="confirmed",
        # A concept link's evidence is derived at read time from its live
        # occurrence count and this note; storing the note twice would be a
        # second copy to keep in step.
        reviewed_by=admin.username,
        review_note=payload.note.strip(),
    )
    db.add(link)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(400, "These concepts are already linked")
    recount(db, link)
    db.commit()
    return link_dict(db, link)


@router.patch("/links/{link_id}")
def update_link(
    link_id: str,
    payload: LinkPatch,
    admin: Account = Depends(require_admin),
    db: Session = Depends(get_db),
):
    link = _get_link(db, link_id)
    if payload.type_id:
        _selectable_type(db, payload.type_id)
        link.relationship_type_id = payload.type_id
    if payload.state:
        set_state(db, link, payload.state, admin.username, payload.note)
    else:
        if payload.note is not None:
            link.review_note = payload.note.strip() or None
        db.commit()
    db.refresh(link)
    return link_dict(db, link)


@router.delete("/links/{link_id}", dependencies=[Depends(require_admin)])
def delete_link(link_id: str, db: Session = Depends(get_db)):
    """Permanent removal. Rejecting is usually better: it keeps the link
    inspectable and lets the count carry on rising."""
    link = _get_link(db, link_id)
    db.delete(link)
    db.commit()
    return {"deleted": True}


@router.get("/relationship-types")
def list_relationship_types(db: Session = Depends(get_db)):
    types = db.query(RelationshipType).order_by(RelationshipType.name).all()
    return [
        {
            "id": t.id,
            "name": t.name,
            "is_builtin": t.is_builtin,
            "usage": type_usage(db, t.id),
            "is_default": t.id == RELATED_TO_ID,
            # 'corroborates' is generated by duplicate grouping between items and
            # is not a label an admin can put on a concept link.
            "selectable": t.id != CORROBORATES_ID,
        }
        for t in types
    ]
