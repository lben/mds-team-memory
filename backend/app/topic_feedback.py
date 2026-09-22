"""Explicit human topical outcomes, with model-free exact-version eligibility."""
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import and_, exists, or_, select, text
from sqlalchemy.orm import aliased

from .models import Account, Concept, ConceptTerm, KnowledgeItem, Profile, TopicConfirmation, utcnow
from .ml.sources import digest

CONTRACT_VERSION = "explicit-topic-feedback-v1"


class TopicChoice(BaseModel):
    concept_id: str
    identity_revision: int = Field(ge=1)


class TopicSelection(BaseModel):
    expected_context: str = Field(min_length=64, max_length=64)
    topics: list[TopicChoice] = Field(max_length=20)


class TopicFeedbackIn(TopicSelection):
    kind: Literal["helped", "accepted"]


def begin_write(db):
    """Acquire SQLite's write lock before re-reading any caller-visible state.

    Route dependencies have only read identity (or committed profile creation).
    Do not call after staging application writes.
    """
    db.rollback()
    db.execute(text("BEGIN IMMEDIATE"))


def current_predicate(db, confirmation=TopicConfirmation):
    """SQL read guard usable against TopicConfirmation or an ORM alias.

    Indexed EXISTS checks bind each recorded claim to current authoritative
    rows. This never calls effective.expertise or loads an inference runtime.
    """
    from .ml import effective

    c = confirmation
    active_concepts = effective.concepts(db).with_entities(Concept.id).statement.cte()
    actor, beneficiary, asker = aliased(Profile), aliased(Profile), aliased(Profile)
    item, question = aliased(KnowledgeItem), aliased(KnowledgeItem)
    concept = aliased(Concept)
    actor_valid = exists(select(actor.id).where(
        actor.id == c.actor_profile_id, actor.account_id == c.actor_account_id,
        actor.account_binding_revision == c.actor_binding_revision).correlate(c))
    beneficiary_valid = exists(select(beneficiary.id).where(
        beneficiary.id == c.beneficiary_profile_id, beneficiary.account_id == c.beneficiary_account_id,
        beneficiary.account_binding_revision == c.beneficiary_binding_revision).correlate(c))
    asker_valid = exists(select(asker.id).where(
        asker.id == c.asker_profile_id, asker.account_id == c.actor_account_id,
        asker.account_binding_revision == c.asker_binding_revision).correlate(c))
    question_valid = exists(select(question.id).where(
        question.id == c.question_id, question.kind == "question", question.visibility == "team",
        question.evidence_revision == c.question_evidence_revision,
        or_(c.kind != "accepted", and_(
            question.accepted_answer_id == c.item_id, question.question_status == "resolved",
            question.acceptance_revision == c.acceptance_revision,
            question.author_profile_id == c.asker_profile_id, asker_valid))).correlate(c))
    item_valid = exists(select(item.id).where(
        item.id == c.item_id, item.evidence_revision == c.item_evidence_revision,
        item.author_profile_id == c.beneficiary_profile_id, item.visibility == "team",
        or_(and_(item.kind == "note", c.question_id.is_(None), c.kind == "helped"),
            and_(item.kind == "answer", item.parent_id == c.question_id, question_valid))).correlate(c))
    concept_valid = exists(select(concept.id).where(
        concept.id == c.concept_id, concept.credit_identity_revision == c.concept_identity_revision,
        exists(select(ConceptTerm.id).where(ConceptTerm.concept_id == concept.id, ConceptTerm.is_canonical.is_(True))),
        concept.id.in_(select(active_concepts.c.id))).correlate(c))
    return and_(c.state == "current", c.contract_version == CONTRACT_VERSION,
                c.actor_account_id != c.beneficiary_account_id,
                exists(select(Account.id).where(Account.id == c.actor_account_id).correlate(c)),
                exists(select(Account.id).where(Account.id == c.beneficiary_account_id).correlate(c)),
                actor_valid, beneficiary_valid, item_valid, concept_valid)


def eligible_query(db, beneficiary_profile_id=None):
    query = db.query(TopicConfirmation).filter(current_predicate(db))
    if beneficiary_profile_id is not None:
        query = query.filter(TopicConfirmation.beneficiary_profile_id == beneficiary_profile_id)
    return query


def eligible_confirmations(db, beneficiary_profile_id=None):
    return eligible_query(db, beneficiary_profile_id).all()


def confirmation_current(db, confirmation):
    return eligible_query(db).filter(TopicConfirmation.id == confirmation.id).first() is not None


def _context(db, item, profile, account):
    # Triggers advance versions inside the write transaction. Refresh avoids
    # reading SQLAlchemy's pre-trigger object state with expire_on_commit=False.
    db.refresh(item)
    db.refresh(profile)
    beneficiary = db.get(Profile, item.author_profile_id)
    if beneficiary:
        db.refresh(beneficiary)
    question = db.get(KnowledgeItem, item.parent_id) if item.kind == "answer" and item.parent_id else None
    if question:
        db.refresh(question)
    asker = db.get(Profile, question.author_profile_id) if question else None
    if asker:
        db.refresh(asker)
    account_id = account.id if account and db.get(Account, account.id) else None
    existing = []
    if account_id:
        owned = db.query(TopicConfirmation).filter(
            TopicConfirmation.item_id == item.id, TopicConfirmation.actor_account_id == account_id)
        # Two kinds, at most twenty current choices each. Keep a bounded recent
        # withdrawal readback rather than returning a user's entire edit history.
        existing = owned.filter(TopicConfirmation.state == "current").order_by(TopicConfirmation.id).all()
        existing += owned.filter(TopicConfirmation.state == "revoked").order_by(
            TopicConfirmation.state_changed_at.desc(), TopicConfirmation.created_at.desc(), TopicConfirmation.id).limit(40).all()
    token = digest([CONTRACT_VERSION, item.id, item.evidence_revision,
                    [question.id, question.evidence_revision, question.acceptance_revision, question.accepted_answer_id,
                     question.question_status] if question else None,
                    [account_id, profile.id, profile.account_id, profile.account_binding_revision],
                    [beneficiary.id, beneficiary.account_id, beneficiary.account_binding_revision] if beneficiary else None,
                    [asker.id, asker.account_id, asker.account_binding_revision] if asker else None,
                    sorted((c.id, c.state) for c in existing)])
    return {"item": item, "question": question, "beneficiary": beneficiary, "asker": asker,
            "account_id": account_id, "profile": profile, "existing": existing, "token": token}


def _independent(db, context):
    item, beneficiary = context["item"], context["beneficiary"]
    question = context["question"]
    return bool(context["account_id"] and context["profile"].account_id == context["account_id"]
                and beneficiary and beneficiary.account_id and db.get(Account, beneficiary.account_id)
                and context["account_id"] != beneficiary.account_id
                and item.visibility == "team" and item.kind in {"note", "answer"}
                and (item.kind != "answer" or (question and question.kind == "question" and question.visibility == "team")))


def _asker(context):
    return bool(context["asker"] and context["asker"].account_id == context["account_id"])


def feedback_dict(db, item, profile, account, query=""):
    from .concepts import source_concepts
    from .ml import effective

    context = _context(db, item, profile, account)
    question = context["question"]
    # Do not leak a private question through its answer's feedback dialog.
    visible_question = question if question and question.visibility == "team" else None
    item_topics = {c.id for c in source_concepts(db, "item", item.id, item.body)} if item.visibility == "team" else set()
    question_topics = {c.id for c in source_concepts(db, "item", question.id, question.body)} if visible_question else set()
    helpful_topics = item_topics | question_topics
    accepted_topics = question_topics
    feedback = {"helped": [], "accepted": []}
    seen = set()
    current_ids = {row.id for row in eligible_query(db).filter(
        TopicConfirmation.id.in_([c.id for c in context["existing"]] or [""])).all()}
    for row in context["existing"]:
        key = row.kind, row.concept_id
        if key in seen:
            continue
        seen.add(key)
        concept = db.get(Concept, row.concept_id)
        feedback[row.kind].append({"concept_id": row.concept_id, "name": concept.name if concept else "Unavailable topic",
                                   "state": row.state if row.state == "revoked" else "current" if row.id in current_ids else "stale"})
    selected = {c.concept_id for c in context["existing"] if c.state == "current"}
    choices = effective.concepts(db).join(ConceptTerm, and_(ConceptTerm.concept_id == Concept.id, ConceptTerm.is_canonical.is_(True)))
    if query.strip():
        escaped = query.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        choices = choices.filter(ConceptTerm.display.ilike("%" + escaped + "%", escape="\\"))
    else:
        choices = choices.filter(Concept.id.in_(helpful_topics | accepted_topics | selected or {""}))
    concepts = choices.order_by(ConceptTerm.display, Concept.id).limit(30).all()
    # Existing choices are never lost merely because suggestions/search change.
    missing = selected - {c.id for c in concepts}
    if missing:
        concepts += effective.concepts(db).filter(Concept.id.in_(missing)).all()
    independent = _independent(db, context)
    return {
        "context_token": context["token"], "item_id": item.id,
        "item_revision": item.evidence_revision,
        "question_revision": visible_question.evidence_revision if visible_question else None,
        "acceptance_revision": visible_question.acceptance_revision if visible_question else None,
        "question_id": visible_question.id if visible_question else None,
        "item": {"body": item.body, "author": context["beneficiary"].label if context["beneficiary"] else "Unknown"},
        "question": {"id": visible_question.id, "body": visible_question.body,
                     "author": context["asker"].label if context["asker"] else "Unknown"} if visible_question else None,
        "topics": [{"concept_id": c.id, "name": c.name, "identity_revision": c.credit_identity_revision,
                    "suggested_for": (["helped"] if c.id in helpful_topics else []) + (["accepted"] if c.id in accepted_topics else [])}
                   for c in concepts],
        "feedback": feedback, "signed_in": bool(context["account_id"]),
        "can_confirm_helped": independent,
        "can_confirm_accepted": bool(independent and _asker(context) and question
                                     and question.accepted_answer_id in (None, item.id)),
    }


def validate_selection(db, item, profile, account, kind, selection, *, accepting=False):
    from .ml import effective

    context = _context(db, item, profile, account)
    if not context["account_id"]:
        raise HTTPException(401, "Sign in to confirm which topics helped")
    if selection.topics and not _independent(db, context):
        raise HTTPException(403, "Topic credit needs a different signed-in contributor and current shared content")
    if kind == "accepted" and selection.topics:
        question = context["question"]
        if not question or not _asker(context):
            raise HTTPException(403, "Only the signed-in asker can confirm resolved topics")
        if not accepting and (question.accepted_answer_id != item.id or question.question_status != "resolved"):
            raise HTTPException(409, "This answer is no longer accepted; refresh before confirming topics")
    wanted = {choice.concept_id: choice.identity_revision for choice in selection.topics}
    if len(wanted) != len(selection.topics):
        raise HTTPException(422, "Choose each topic only once")
    concepts = effective.concepts(db).filter(Concept.id.in_(wanted or {""})).all()
    if {c.id: c.credit_identity_revision for c in concepts if c.canonical} != wanted:
        raise HTTPException(409, "A selected topic changed or is unavailable; refresh your choices")
    if context["token"] != selection.expected_context:
        # A retry of a successful request is harmless only while every chosen
        # assertion remains current and was created using that request token.
        active = [c for c in context["existing"] if c.kind == kind and c.state == "current"]
        if (wanted and {c.concept_id: c.concept_identity_revision for c in active} == wanted
                and all(c.context_token == selection.expected_context and confirmation_current(db, c) for c in active)):
            context["retry"] = True
        else:
            raise HTTPException(409, "The content or your feedback changed; refresh before confirming topics")
    return context


def save_selection(db, item, profile, account, kind, selection, *, validated=None):
    """Stage the desired topical set without committing; caller owns transaction.

    For first acceptance validate against pre-acceptance context, then save
    after flushing the pointer so the rows capture its new generation.
    """
    context = validated or validate_selection(db, item, profile, account, kind, selection)
    if context.get("retry"):
        return
    current_context = _context(db, item, profile, account)
    wanted = {choice.concept_id: choice.identity_revision for choice in selection.topics}
    old = [c for c in current_context["existing"] if c.kind == kind and c.state == "current"]
    unchanged = ({c.concept_id: c.concept_identity_revision for c in old} == wanted
                 and all(confirmation_current(db, c) for c in old))
    # A changed set gets one coherent request token, including retained topics.
    # This makes a lost-response retry idempotent without mutating assertions.
    keep = set(wanted) if unchanged else set()
    for row in old:
        if row.concept_id not in keep:
            row.state = "superseded" if row.concept_id in wanted else "revoked"
            row.state_changed_at = utcnow()
    db.flush()
    beneficiary, question, asker = (current_context[key] for key in ("beneficiary", "question", "asker"))
    for cid, revision in wanted.items():
        if cid in keep:
            continue
        # Supersede any earlier revoked history for this topic so readback has
        # one unambiguous current/latest state without rewriting the assertion.
        for previous in current_context["existing"]:
            if previous.kind == kind and previous.concept_id == cid and previous.state == "revoked":
                previous.state = "superseded"
                previous.state_changed_at = utcnow()
        db.add(TopicConfirmation(
            kind=kind, actor_account_id=account.id, actor_profile_id=profile.id,
            actor_binding_revision=profile.account_binding_revision,
            beneficiary_account_id=beneficiary.account_id, beneficiary_profile_id=beneficiary.id,
            beneficiary_binding_revision=beneficiary.account_binding_revision,
            item_id=item.id, item_evidence_revision=item.evidence_revision,
            question_id=question.id if question else None,
            question_evidence_revision=question.evidence_revision if question else None,
            acceptance_revision=question.acceptance_revision if kind == "accepted" else None,
            asker_profile_id=asker.id if kind == "accepted" else None,
            asker_binding_revision=asker.account_binding_revision if kind == "accepted" else None,
            concept_id=cid, concept_identity_revision=revision, contract_version=CONTRACT_VERSION,
            state="current", context_token=selection.expected_context,
        ))
    db.flush()
