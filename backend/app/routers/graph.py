from collections import Counter, defaultdict
from itertools import combinations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import config
from ..auth import get_profile
from ..db import get_db
from ..models import (
    Concept,
    Document,
    DocumentPassage,
    ExpertiseMapping,
    ItemConcept,
    KnowledgeItem,
    Profile,
)
router = APIRouter(prefix="/api/graph", tags=["graph"])

MAX_NEIGHBORS = 12


def _team_subject_concepts(db: Session) -> list[tuple[str, str, str]]:
    """(subject_kind, subject_id, concept_id) restricted to team-visible content.

    Private items and scratchpads are excluded so they can never appear in
    another profile's graph or counts.
    """
    item_rows = (
        db.query(ItemConcept.subject_id, ItemConcept.concept_id)
        .join(
            KnowledgeItem,
            (ItemConcept.subject_kind == "item") & (KnowledgeItem.id == ItemConcept.subject_id),
        )
        .filter(KnowledgeItem.visibility == "team")
        .all()
    )
    passage_rows = (
        db.query(ItemConcept.subject_id, ItemConcept.concept_id)
        .filter(ItemConcept.subject_kind == "passage")
        .all()
    )
    return [("item", s, c) for s, c in item_rows] + [("passage", s, c) for s, c in passage_rows]


def _cooccurrence(rows: list[tuple[str, str, str]]) -> Counter:
    by_subject: dict[tuple[str, str], set[str]] = defaultdict(set)
    for kind, subject_id, concept_id in rows:
        by_subject[(kind, subject_id)].add(concept_id)
    pairs: Counter = Counter()
    for concept_ids in by_subject.values():
        for a, b in combinations(sorted(concept_ids), 2):
            pairs[(a, b)] += 1
    return pairs


@router.get("/concepts")
def list_concepts(db: Session = Depends(get_db)):
    rows = _team_subject_concepts(db)
    counts = Counter(c for _, _, c in rows)
    concepts = db.query(Concept).order_by(Concept.name).all()
    return [{"id": c.id, "name": c.name, "mentions": counts.get(c.id, 0)} for c in concepts]


@router.get("/local")
def local_graph(
    concept_id: str,
    profile: Profile = Depends(get_profile),
    db: Session = Depends(get_db),
):
    center = db.get(Concept, concept_id)
    if not center:
        raise HTTPException(404, "Concept not found")
    rows = _team_subject_concepts(db)
    pairs = _cooccurrence(rows)
    center_node = {"id": f"c:{center.id}", "type": "concept", "label": center.name, "center": True}
    nodes: list[dict] = [center_node]
    edges: list[dict] = []

    def add(node: dict, edge_label: str, style: str, evidence: str) -> None:
        if len(nodes) - 1 >= MAX_NEIGHBORS:
            return
        if any(n["id"] == node["id"] for n in nodes):
            return
        nodes.append(node)
        edges.append(
            {
                "source": center_node["id"],
                "target": node["id"],
                "label": edge_label,
                "style": style,
                "evidence": evidence,
            }
        )

    # 1. Related concepts through supported co-occurrence (inferred, dashed).
    related = [
        (pair[0] if pair[1] == center.id else pair[1], count)
        for pair, count in pairs.items()
        if center.id in pair and count >= config.COOCCURRENCE_MIN
    ]
    for other_id, count in sorted(related, key=lambda r: (-r[1], r[0]))[:5]:
        other = db.get(Concept, other_id)
        add(
            {"id": f"c:{other.id}", "type": "concept", "label": other.name},
            "related to",
            "dashed",
            f"Mentioned together in {count} team entries.",
        )

    concept_subjects = [(k, s) for k, s, c in rows if c == center.id]
    item_ids = [s for k, s in concept_subjects if k == "item"]
    passage_ids = [s for k, s in concept_subjects if k == "passage"]

    # 2. Documents whose passages mention the concept (confirmed, solid).
    if passage_ids:
        doc_locators: dict[str, list[str]] = defaultdict(list)
        for p in db.query(DocumentPassage).filter(DocumentPassage.id.in_(passage_ids)).all():
            doc_locators[p.document_id].append(p.locator)
        for doc_id in sorted(doc_locators)[:3]:
            doc = db.get(Document, doc_id)
            locators = ", ".join(doc_locators[doc_id][:3])
            add(
                {"id": f"d:{doc.id}", "type": "document", "label": doc.filename},
                "documented in",
                "solid",
                f"Matched at {locators}.",
            )

    # 3. Questions and team items mentioning the concept (confirmed, solid).
    if item_ids:
        items = (
            db.query(KnowledgeItem)
            .filter(KnowledgeItem.id.in_(item_ids), KnowledgeItem.visibility == "team")
            .all()
        )
        questions = [i for i in items if i.kind == "question"]
        contents = [i for i in items if i.kind in ("note", "excerpt", "answer")]
        for q in sorted(questions, key=lambda x: x.created_at, reverse=True)[:3]:
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
        for i in sorted(contents, key=lambda x: x.created_at, reverse=True):
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

    # 4. Mapped experts (confirmed, solid).
    for m in (
        db.query(ExpertiseMapping).filter(ExpertiseMapping.concept_id == center.id).all()[:2]
    ):
        add(
            {"id": f"p:{m.profile_id}", "type": "profile", "label": m.profile.label},
            "expert",
            "solid",
            "Mapped by an admin as an expertise area.",
        )

    return {"nodes": nodes, "edges": edges}


@router.get("/global")
def global_graph(db: Session = Depends(get_db)):
    rows = _team_subject_concepts(db)
    counts = Counter(c for _, _, c in rows)
    pairs = _cooccurrence(rows)
    strong = {pair: n for pair, n in pairs.items() if n >= config.COOCCURRENCE_MIN}

    concepts = {c.id: c for c in db.query(Concept).all() if counts.get(c.id, 0) > 0}
    # Connected components over strong co-occurrence edges.
    neighbors: dict[str, set[str]] = defaultdict(set)
    for a, b in strong:
        if a in concepts and b in concepts:
            neighbors[a].add(b)
            neighbors[b].add(a)
    clusters = []
    unvisited = set(concepts)
    while unvisited:
        start = min(unvisited)
        component, stack = [], [start]
        while stack:
            node = stack.pop()
            if node not in unvisited:
                continue
            unvisited.discard(node)
            component.append(node)
            stack.extend(neighbors[node] - set(component))
        members = sorted(component, key=lambda cid: (-counts[cid], concepts[cid].name))
        clusters.append(
            {
                "id": members[0],
                "label": concepts[members[0]].name,
                "concepts": [
                    {"id": cid, "name": concepts[cid].name, "size": counts[cid]} for cid in members
                ],
            }
        )
    clusters.sort(key=lambda cl: -sum(c["size"] for c in cl["concepts"]))
    edges = [
        {"source": a, "target": b, "count": n}
        for (a, b), n in sorted(strong.items())
        if a in concepts and b in concepts
    ]
    return {"clusters": clusters, "edges": edges}
