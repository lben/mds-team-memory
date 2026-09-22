from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..auth import get_account, get_profile
from ..concepts import match_concepts
from ..db import get_db
from ..impact import notify, record_event
from ..knowledge import delete_item, dependents_by_others, item_dict, process_after_save
from ..models import Account, ExpertiseMapping, ItemConcept, KnowledgeItem, Notification, Profile
from ..ml import effective
from ..concepts import source_concepts
from .. import topic_feedback
from ..topic_feedback import TopicSelection

router = APIRouter(prefix="/api/questions", tags=["questions"])


class QuestionIn(BaseModel):
    body: str = Field(min_length=1, max_length=20000)


class AnswerIn(BaseModel):
    body: str = Field(min_length=1, max_length=20000)


class AcceptIn(BaseModel):
    answer_id: str
    topic_feedback: TopicSelection | None = None
    expected_acceptance_revision: int | None = Field(default=None, ge=0)


class AcceptanceRevisionIn(BaseModel):
    expected_acceptance_revision: int = Field(ge=0)


def _same_person(db: Session, author_id: str, profile: Profile) -> bool:
    author = db.get(Profile, author_id)
    return author_id == profile.id or bool(author and author.account_id and author.account_id == profile.account_id)


def _question(db: Session, question_id: str) -> KnowledgeItem:
    q = db.get(KnowledgeItem, question_id)
    if not q or q.kind != "question" or q.visibility != "team":
        raise HTTPException(404, "Question not found")
    return q


def _my_concept_ids(db: Session, profile_id: str) -> list[str]:
    profile = db.get(Profile, profile_id)
    query = effective.expertise(db).with_entities(ExpertiseMapping.concept_id)
    query = query.filter(Profile.account_id == profile.account_id) if profile and profile.account_id else query.filter(
        ExpertiseMapping.profile_id == profile_id)
    return [
        cid
        for (cid,) in query
    ]


@router.get("")
def list_questions(profile: Profile = Depends(get_profile), db: Session = Depends(get_db)):
    questions = (
        db.query(KnowledgeItem)
        .filter(KnowledgeItem.kind == "question", KnowledgeItem.visibility == "team")
        .order_by(KnowledgeItem.created_at.desc())
        .limit(200)
        .all()
    )
    answer_counts = dict(
        db.query(KnowledgeItem.parent_id, func.count())
        .filter(KnowledgeItem.kind == "answer", KnowledgeItem.visibility == "team")
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
        d["matches_me"] = q.id in matching_mine and not _same_person(db, q.author_profile_id, profile)
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
        .filter(KnowledgeItem.kind == "answer", KnowledgeItem.parent_id == question.id, KnowledgeItem.visibility == "team")
        .order_by(KnowledgeItem.created_at)
        .all()
    )
    concepts = source_concepts(db, "item", question.id, question.body)
    experts = (
        effective.expertise(db)
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
    asker = db.get(Profile, question.author_profile_id)
    by_account = {}
    for expert in sorted(experts, key=lambda e: e.profile_id):
        if expert.profile.account_id and (not asker or expert.profile.account_id != asker.account_id):
            by_account.setdefault(expert.profile.account_id, expert.profile.label)
    data["suggested_experts"] = sorted(by_account.values())
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
    has answered, so a teammate's contribution is never destroyed with it.

    The removal itself goes through `knowledge.delete_item`, the one place that
    knows every table pointing at a contribution. This used to delete the row
    directly after clearing only its notifications, so a question carrying a
    correction, a revision or an impact event failed its foreign keys and the
    asker got a 500.
    """
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
    attached = dependents_by_others(db, question)
    if attached:
        raise HTTPException(
            400,
            f"A teammate has added {attached} correction to this question, "
            "so deleting it would destroy their work too",
        )
    delete_item(db, question)
    return {"deleted": True}


@router.post("/{question_id}/accept")
def accept_answer(
    question_id: str,
    payload: AcceptIn,
    profile: Profile = Depends(get_profile),
    account: Account | None = Depends(get_account),
    db: Session = Depends(get_db),
):
    topic_feedback.begin_write(db)
    question = _question(db, question_id)
    if not _same_person(db, question.author_profile_id, profile):
        raise HTTPException(403, "Only the asker can accept an answer")
    answer = db.get(KnowledgeItem, payload.answer_id)
    if not answer or answer.kind != "answer" or answer.parent_id != question.id or answer.visibility != "team":
        raise HTTPException(404, "Answer not found for this question")
    if (payload.expected_acceptance_revision is not None
            and payload.expected_acceptance_revision != question.acceptance_revision):
        raise HTTPException(409, "Acceptance changed; refresh before replacing it")
    if (question.accepted_answer_id and question.accepted_answer_id != answer.id
            and payload.expected_acceptance_revision is None):
        raise HTTPException(400, "An answer is already accepted")
    validated = None
    if payload.topic_feedback is not None:
        validated = topic_feedback.validate_selection(
            db, answer, profile, account, "accepted", payload.topic_feedback, accepting=True)
    question.accepted_answer_id = answer.id
    question.question_status = "resolved"
    db.flush()
    if payload.topic_feedback is not None:
        topic_feedback.save_selection(
            db, answer, profile, account, "accepted", payload.topic_feedback, validated=validated)
    created = False
    if not _same_person(db, answer.author_profile_id, profile):
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
    db.refresh(question)
    return {"accepted": True, "impact_created": created, "acceptance_revision": question.acceptance_revision,
            "topic_feedback": topic_feedback.feedback_dict(db, answer, profile, account) if payload.topic_feedback is not None else None}


@router.delete("/{question_id}/accept")
def unaccept_answer(
    question_id: str, payload: AcceptanceRevisionIn, profile: Profile = Depends(get_profile),
    db: Session = Depends(get_db),
):
    topic_feedback.begin_write(db)
    question = _question(db, question_id)
    if not _same_person(db, question.author_profile_id, profile):
        raise HTTPException(403, "Only the asker can withdraw acceptance")
    if question.acceptance_revision != payload.expected_acceptance_revision:
        raise HTTPException(409, "Acceptance changed; refresh before withdrawing it")
    question.accepted_answer_id = None
    question.question_status = "answered" if db.query(KnowledgeItem.id).filter(
        KnowledgeItem.parent_id == question.id, KnowledgeItem.kind == "answer").first() else "open"
    db.commit()
    db.refresh(question)
    return {"accepted": False, "acceptance_revision": question.acceptance_revision}
