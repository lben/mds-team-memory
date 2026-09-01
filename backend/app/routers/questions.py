from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..auth import get_profile
from ..concepts import match_concepts
from ..db import get_db
from ..impact import notify, record_event
from ..knowledge import item_dict, process_after_save
from ..models import ExpertiseMapping, ItemConcept, KnowledgeItem, Notification, Profile

router = APIRouter(prefix="/api/questions", tags=["questions"])


class QuestionIn(BaseModel):
    body: str = Field(min_length=1, max_length=20000)


class AnswerIn(BaseModel):
    body: str = Field(min_length=1, max_length=20000)


class AcceptIn(BaseModel):
    answer_id: str


def _question(db: Session, question_id: str) -> KnowledgeItem:
    q = db.get(KnowledgeItem, question_id)
    if not q or q.kind != "question":
        raise HTTPException(404, "Question not found")
    return q


def _my_concept_ids(db: Session, profile_id: str) -> list[str]:
    return [
        cid
        for (cid,) in db.query(ExpertiseMapping.concept_id).filter(
            ExpertiseMapping.profile_id == profile_id
        )
    ]


@router.get("")
def list_questions(profile: Profile = Depends(get_profile), db: Session = Depends(get_db)):
    questions = (
        db.query(KnowledgeItem)
        .filter(KnowledgeItem.kind == "question")
        .order_by(KnowledgeItem.created_at.desc())
        .limit(200)
        .all()
    )
    answer_counts = dict(
        db.query(KnowledgeItem.parent_id, func.count())
        .filter(KnowledgeItem.kind == "answer")
        .group_by(KnowledgeItem.parent_id)
        .all()
    )
    my_concepts = _my_concept_ids(db, profile.id)
    matching_mine: set[str] = set()
    if my_concepts:
        matching_mine = {
            iid
            for (iid,) in db.query(ItemConcept.item_id).filter(
                ItemConcept.concept_id.in_(my_concepts)
            )
        }
    result = []
    for q in questions:
        d = item_dict(db, q, profile)
        d["answer_count"] = answer_counts.get(q.id, 0)
        # You are never routed your own question: the person who asked is not
        # one of the people who should answer.
        d["matches_me"] = q.id in matching_mine and q.author_profile_id != profile.id
        result.append(d)
    return result


@router.post("")
def create_question(
    payload: QuestionIn, profile: Profile = Depends(get_profile), db: Session = Depends(get_db)
):
    question = KnowledgeItem(
        kind="question",
        body=payload.body.strip(),
        visibility="team",
        author_profile_id=profile.id,
        question_status="open",
    )
    db.add(question)
    db.commit()
    process_after_save(db, question)
    return item_dict(db, question, profile)


@router.get("/{question_id}")
def question_detail(
    question_id: str, profile: Profile = Depends(get_profile), db: Session = Depends(get_db)
):
    question = _question(db, question_id)
    answers = (
        db.query(KnowledgeItem)
        .filter(KnowledgeItem.kind == "answer", KnowledgeItem.parent_id == question.id)
        .order_by(KnowledgeItem.created_at)
        .all()
    )
    concepts = match_concepts(db, question.body)
    experts = (
        db.query(ExpertiseMapping)
        .filter(
            ExpertiseMapping.concept_id.in_([c.id for c in concepts] or [""]),
            # Same rule as `matches_me`: never suggest the asker to themselves.
            ExpertiseMapping.profile_id != question.author_profile_id,
        )
        .all()
    )
    data = item_dict(db, question, profile)
    data["answers"] = [item_dict(db, a, profile) for a in answers]
    data["concepts"] = [{"id": c.id, "name": c.name} for c in concepts]
    data["suggested_experts"] = sorted({e.profile.label for e in experts})
    return data


@router.post("/{question_id}/answers")
def add_answer(
    question_id: str,
    payload: AnswerIn,
    profile: Profile = Depends(get_profile),
    db: Session = Depends(get_db),
):
    question = _question(db, question_id)
    answer = KnowledgeItem(
        kind="answer",
        body=payload.body.strip(),
        visibility="team",
        author_profile_id=profile.id,
        parent_id=question.id,
    )
    db.add(answer)
    if question.question_status == "open":
        question.question_status = "answered"
    db.commit()
    process_after_save(db, answer)
    if question.author_profile_id != profile.id:
        notify(
            db, question.author_profile_id, "answer", "Your question received a new answer.", question.id
        )
        db.commit()
    return item_dict(db, answer, profile)


@router.delete("/{question_id}")
def delete_question(
    question_id: str, profile: Profile = Depends(get_profile), db: Session = Depends(get_db)
):
    """The asker can delete a question posted by mistake — but only while nobody
    has answered, so a teammate's contribution is never destroyed with it."""
    question = _question(db, question_id)
    if question.author_profile_id != profile.id:
        raise HTTPException(403, "Only the asker can delete their question")
    answers = (
        db.query(KnowledgeItem)
        .filter(KnowledgeItem.kind == "answer", KnowledgeItem.parent_id == question.id)
        .count()
    )
    if answers:
        raise HTTPException(400, "This question already has answers and cannot be deleted")
    db.query(Notification).filter(Notification.item_id == question.id).delete()
    db.delete(question)
    db.commit()
    return {"deleted": True}


@router.post("/{question_id}/accept")
def accept_answer(
    question_id: str,
    payload: AcceptIn,
    profile: Profile = Depends(get_profile),
    db: Session = Depends(get_db),
):
    question = _question(db, question_id)
    if question.author_profile_id != profile.id:
        raise HTTPException(403, "Only the asker can accept an answer")
    answer = db.get(KnowledgeItem, payload.answer_id)
    if not answer or answer.kind != "answer" or answer.parent_id != question.id:
        raise HTTPException(404, "Answer not found for this question")
    if question.accepted_answer_id and question.accepted_answer_id != answer.id:
        raise HTTPException(400, "An answer is already accepted")
    question.accepted_answer_id = answer.id
    question.question_status = "resolved"
    db.commit()
    created = False
    if answer.author_profile_id != profile.id:
        created = record_event(
            db,
            "answer_accepted",
            answer.author_profile_id,
            f"accepted:{answer.id}",
            profile.id,
            answer.id,
        )
        if created:
            notify(
                db, answer.author_profile_id, "accepted", "Your answer was accepted.", question.id
            )
    db.commit()
    return {"accepted": True, "impact_created": created}
