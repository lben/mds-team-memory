from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..auth import get_profile
from ..concepts import match_concepts
from ..db import get_db
from ..impact import notify, record_event
from ..knowledge import item_dict, process_after_save
from ..models import ExpertiseMapping, ItemConcept, KnowledgeItem, Profile

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


@router.get("")
def list_questions(
    mine_expertise: bool = False,
    profile: Profile = Depends(get_profile),
    db: Session = Depends(get_db),
):
    query = db.query(KnowledgeItem).filter(KnowledgeItem.kind == "question")
    if mine_expertise:
        my_concepts = db.query(ExpertiseMapping.concept_id).filter(
            ExpertiseMapping.profile_id == profile.id
        )
        matching = db.query(ItemConcept.subject_id).filter(
            ItemConcept.subject_kind == "item", ItemConcept.concept_id.in_(my_concepts)
        )
        query = query.filter(KnowledgeItem.id.in_(matching))
    questions = query.order_by(KnowledgeItem.created_at.desc()).limit(200).all()
    answer_counts = dict(
        db.query(KnowledgeItem.parent_id, func.count())
        .filter(KnowledgeItem.kind == "answer")
        .group_by(KnowledgeItem.parent_id)
        .all()
    )
    result = []
    for q in questions:
        d = item_dict(db, q, profile)
        d["answer_count"] = answer_counts.get(q.id, 0)
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
        .filter(ExpertiseMapping.concept_id.in_([c.id for c in concepts] or [""]))
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
