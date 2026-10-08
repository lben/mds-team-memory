"""Explicit topic assertions: real routes, committed SQLite versions, no inference."""
import uuid

from sqlalchemy import text


def _user(make_client):
    client = make_client(account=False)
    response = client.post("/api/auth/signup", json={"username": "topic" + uuid.uuid4().hex[:10], "password": "strong-password"})
    assert response.status_code == 200, response.text
    return client


def _setup(make_client, admin_client):
    asker, author, reader = [_user(make_client) for _ in range(3)]
    name = "Slides " + uuid.uuid4().hex[:8]
    result = admin_client.post("/api/admin/concepts", json={"name": name, "aliases": []})
    assert result.status_code == 200, result.text
    cid = result.json()["id"]
    question = asker.post("/api/questions", json={"body": f"How do I export {name}?"})
    assert question.status_code == 200, question.text
    qid = question.json()["id"]
    answer = author.post(f"/api/questions/{qid}/answers", json={"body": "Use the second export option and preserve the images."})
    assert answer.status_code == 200, answer.text
    return asker, author, reader, qid, answer.json()["id"], cid, name


def _selection(client, item, cid):
    response = client.get(f"/api/items/{item}/topic-feedback")
    assert response.status_code == 200, response.text
    context = response.json()
    topic = next(c for c in context["topics"] if c["concept_id"] == cid)
    return {"expected_context": context["context_token"], "topics": [
        {"concept_id": cid, "identity_revision": topic["identity_revision"]}]}


def _eligible(profile=None):
    from app.db import SessionLocal
    from app.topic_feedback import eligible_confirmations
    with SessionLocal() as db:
        return eligible_confirmations(db, profile)


def test_generic_actions_never_create_topic_credit(make_client, admin_client):
    from app.db import SessionLocal
    from app.models import TopicConfirmation
    asker, author, reader, qid, aid, cid, _ = _setup(make_client, admin_client)
    assert asker.post(f"/api/questions/{qid}/accept", json={"answer_id": aid}).status_code == 200
    assert reader.post(f"/api/items/{aid}/helped").status_code == 200
    assert reader.post(f"/api/items/{aid}/endorse").status_code == 200
    with SessionLocal() as db:
        assert db.query(TopicConfirmation).filter_by(item_id=aid).count() == 0
    assert author.get("/api/profile").json()["totals"]["score"] == 6
    assert reader.get(f"/api/items/{aid}/topic-feedback").json()["feedback"] == {"helped": [], "accepted": []}


def test_atomic_accept_exact_topic_readback_and_retry(make_client, admin_client):
    from app.db import SessionLocal
    from app.models import TopicConfirmation
    asker, author, reader, qid, aid, cid, _ = _setup(make_client, admin_client)
    selection = _selection(asker, aid, cid)
    request = {"answer_id": aid, "topic_feedback": selection}
    response = asker.post(f"/api/questions/{qid}/accept", json=request)
    assert response.status_code == 200, response.text
    assert response.json()["topic_feedback"]["feedback"]["accepted"] == [{"concept_id": cid, "name": response.json()["topic_feedback"]["topics"][0]["name"], "state": "current"}]
    retry = asker.post(f"/api/questions/{qid}/accept", json=request)
    assert retry.status_code == 200, retry.text
    assert retry.json()["impact_created"] is False
    with SessionLocal() as db:
        rows = db.query(TopicConfirmation).filter_by(item_id=aid).all()
        assert len(rows) == 1
        assert rows[0].question_id == qid
        assert rows[0].concept_id == cid
        assert rows[0].acceptance_revision == response.json()["acceptance_revision"]
    assert len(_eligible(author.get("/api/profile").json()["id"])) == 1


def test_stale_question_acceptance_rolls_back_all_changes(make_client, admin_client):
    from app.db import SessionLocal
    from app.models import ImpactEvent, KnowledgeItem, TopicConfirmation
    asker, _, _, qid, aid, cid, _ = _setup(make_client, admin_client)
    selection = _selection(asker, aid, cid)
    assert asker.put(f"/api/items/{qid}", json={"body": "A different question now."}).status_code == 200
    response = asker.post(f"/api/questions/{qid}/accept", json={"answer_id": aid, "topic_feedback": selection})
    assert response.status_code == 409, response.text
    with SessionLocal() as db:
        assert db.get(KnowledgeItem, qid).accepted_answer_id is None
        assert db.query(TopicConfirmation).filter_by(item_id=aid).count() == 0
        assert db.query(ImpactEvent).filter_by(item_id=aid, event_type="answer_accepted").count() == 0


def test_question_edit_invalidates_answer_beneficiary_before_replay(make_client, admin_client):
    from app.db import SessionLocal
    asker, author, _, qid, aid, cid, _ = _setup(make_client, admin_client)
    profile_id = author.get("/api/profile").json()["id"]
    selection = _selection(asker, aid, cid)
    assert asker.post(f"/api/questions/{qid}/accept", json={"answer_id": aid, "topic_feedback": selection}).status_code == 200
    with SessionLocal() as db:
        db.execute(text("""INSERT INTO ml_sources(kind,id,content_hash,valid,model_version,result,updated_at)
          VALUES('profile',:id,'projection',1,'test','{}',datetime('now'))
          ON CONFLICT(kind,id) DO UPDATE SET valid=1"""), {"id": profile_id})
        db.execute(text("DELETE FROM ml_jobs WHERE source_kind='profile' AND source_id=:id"), {"id": profile_id})
        db.commit()
    assert asker.put(f"/api/items/{qid}", json={"body": "Rewritten to ask a different thing."}).status_code == 200
    with SessionLocal() as db:
        assert db.execute(text("SELECT valid FROM ml_sources WHERE kind='profile' AND id=:id"), {"id": profile_id}).scalar() == 0
        assert db.execute(text("SELECT 1 FROM ml_jobs WHERE source_kind='profile' AND source_id=:id"), {"id": profile_id}).scalar() == 1
    assert _eligible(profile_id) == []
    assert asker.get(f"/api/items/{aid}/topic-feedback").json()["feedback"]["accepted"][0]["state"] == "stale"


def test_helpfulness_withdraw_reconfirm_and_old_request_cannot_resurrect(make_client, admin_client):
    from app.db import SessionLocal
    from app.models import TopicConfirmation
    _, author, reader, _, aid, cid, _ = _setup(make_client, admin_client)
    selection = _selection(reader, aid, cid)
    response = reader.put(f"/api/items/{aid}/topic-feedback", json={"kind": "helped", **selection})
    assert response.status_code == 200, response.text
    revoke = {"kind": "helped", "expected_context": response.json()["context_token"], "topics": []}
    response = reader.put(f"/api/items/{aid}/topic-feedback", json=revoke)
    assert response.status_code == 200, response.text
    assert response.json()["feedback"]["helped"][0]["state"] == "revoked"
    assert reader.put(f"/api/items/{aid}/topic-feedback", json={"kind": "helped", **selection}).status_code == 409
    fresh = _selection(reader, aid, cid)
    assert reader.put(f"/api/items/{aid}/topic-feedback", json={"kind": "helped", **fresh}).status_code == 200
    with SessionLocal() as db:
        rows = db.query(TopicConfirmation).filter_by(item_id=aid).all()
        assert len(rows) == 2
        assert {c.state for c in rows} == {"current", "superseded"}
    assert len(_eligible(author.get("/api/profile").json()["id"])) == 1


def test_anonymous_and_same_account_profiles_cannot_confirm(make_client, admin_client):
    from app.db import SessionLocal
    from app.models import Profile
    _, author, reader, _, aid, cid, _ = _setup(make_client, admin_client)
    anonymous = make_client(account=False)
    selection = _selection(anonymous, aid, cid)
    assert anonymous.put(f"/api/items/{aid}/topic-feedback", json={"kind": "helped", **selection}).status_code == 401
    selection = _selection(author, aid, cid)
    assert author.put(f"/api/items/{aid}/topic-feedback", json={"kind": "helped", **selection}).status_code == 403
    # Distinct profile IDs do not make self-feedback independent.
    author_id = author.get("/api/profile").json()["id"]
    with SessionLocal() as db:
        original = db.get(Profile, author_id)
        alternate = Profile(account_id=original.account_id, claim_locked=True, display_name="Other profile, same account")
        db.add(alternate)
        db.flush()
        db.execute(text("UPDATE knowledge_items SET author_profile_id=:profile WHERE id=:item"), {"profile": alternate.id, "item": aid})
        db.commit()
    selection = _selection(author, aid, cid)
    assert author.put(f"/api/items/{aid}/topic-feedback", json={"kind": "helped", **selection}).status_code == 403


def test_profile_rebind_and_content_revert_never_revive(make_client, admin_client):
    from app.db import SessionLocal
    from app.models import Profile
    _, author, reader, _, aid, cid, _ = _setup(make_client, admin_client)
    selection = _selection(reader, aid, cid)
    assert reader.put(f"/api/items/{aid}/topic-feedback", json={"kind": "helped", **selection}).status_code == 200
    profile_id = author.get("/api/profile").json()["id"]
    reader_id = reader.get("/api/profile").json()["id"]
    with SessionLocal() as db:
        account = db.get(Profile, reader_id).account_id
        db.execute(text("UPDATE profiles SET account_id=NULL WHERE id=:id"), {"id": reader_id})
        db.execute(text("UPDATE profiles SET account_id=:account WHERE id=:id"), {"id": reader_id, "account": account})
        db.commit()
    assert _eligible(profile_id) == []
    fresh = _selection(reader, aid, cid)
    assert reader.put(f"/api/items/{aid}/topic-feedback", json={"kind": "helped", **fresh}).status_code == 200
    with SessionLocal() as db:
        original = db.execute(text("SELECT body FROM knowledge_items WHERE id=:id"), {"id": aid}).scalar()
        db.execute(text("UPDATE knowledge_items SET body='Changed' WHERE id=:id"), {"id": aid})
        db.execute(text("UPDATE knowledge_items SET body=:body WHERE id=:id"), {"id": aid, "body": original})
        db.commit()
    assert _eligible(profile_id) == []


def test_current_query_correlates_each_questions_revision(make_client, admin_client):
    # A second valid Q/A must never satisfy the first confirmation's stale Q.
    first = _setup(make_client, admin_client)
    second = _setup(make_client, admin_client)
    for asker, _, _, qid, aid, cid, _ in (first, second):
        assert asker.post(f"/api/questions/{qid}/accept", json={"answer_id": aid, "topic_feedback": _selection(asker, aid, cid)}).status_code == 200
    assert first[0].put(f"/api/items/{first[3]}", json={"body": "Different context"}).status_code == 200
    ids = {c.item_id for c in _eligible()}
    assert first[4] not in ids
    assert second[4] in ids


def test_acceptance_replace_and_delete_clear_exact_credit(make_client, admin_client):
    asker, author, _, qid, aid, cid, _ = _setup(make_client, admin_client)
    response = asker.post(f"/api/questions/{qid}/accept", json={"answer_id": aid, "topic_feedback": _selection(asker, aid, cid)})
    assert response.status_code == 200, response.text
    revision = response.json()["acceptance_revision"]
    other = author.post(f"/api/questions/{qid}/answers", json={"body": "A different answer with another approach."}).json()["id"]
    response = asker.post(f"/api/questions/{qid}/accept", json={"answer_id": other, "expected_acceptance_revision": revision})
    assert response.status_code == 200, response.text
    assert _eligible(author.get("/api/profile").json()["id"]) == []
    assert author.delete(f"/api/items/{other}").status_code == 200
    detail = asker.get(f"/api/questions/{qid}").json()
    assert detail["accepted_answer_id"] is None
    assert detail["question_status"] == "answered"


def test_canonical_delete_recreate_cannot_restore_credit(make_client, admin_client):
    from app.db import SessionLocal
    from app.models import Concept
    _, author, reader, _, aid, cid, _ = _setup(make_client, admin_client)
    assert reader.put(f"/api/items/{aid}/topic-feedback", json={"kind": "helped", **_selection(reader, aid, cid)}).status_code == 200
    with SessionLocal() as db:
        before = db.get(Concept, cid).credit_identity_revision
        canonical = db.execute(text("SELECT term,display FROM concept_terms WHERE concept_id=:id AND is_canonical=1"), {"id": cid}).one()
        db.execute(text("DELETE FROM concept_terms WHERE concept_id=:id AND is_canonical=1"), {"id": cid})
        db.execute(text("INSERT INTO concept_terms(id,concept_id,term,display,is_canonical) VALUES(:term_id,:id,:term,:display,1)"),
                   {"term_id": uuid.uuid4().hex, "id": cid, "term": canonical.term, "display": canonical.display})
        db.commit()
        db.expire_all()
        assert db.get(Concept, cid).credit_identity_revision > before
    assert _eligible(author.get("/api/profile").json()["id"]) == []
    assert reader.get(f"/api/items/{aid}/topic-feedback").json()["feedback"]["helped"][0]["state"] == "stale"


def test_unaccept_stale_confirmation_can_be_explicitly_removed(make_client, admin_client):
    asker, _, _, qid, aid, cid, _ = _setup(make_client, admin_client)
    accepted = asker.post(f"/api/questions/{qid}/accept", json={"answer_id": aid, "topic_feedback": _selection(asker, aid, cid)})
    assert accepted.status_code == 200, accepted.text
    response = asker.request("DELETE", f"/api/questions/{qid}/accept", json={"expected_acceptance_revision": accepted.json()["acceptance_revision"]})
    assert response.status_code == 200, response.text
    context = asker.get(f"/api/items/{aid}/topic-feedback").json()
    assert context["feedback"]["accepted"][0]["state"] == "stale"
    response = asker.put(f"/api/items/{aid}/topic-feedback", json={"kind": "accepted", "expected_context": context["context_token"], "topics": []})
    assert response.status_code == 200, response.text
    assert response.json()["feedback"]["accepted"][0]["state"] == "revoked"


def test_private_context_and_account_delete_immediately_remove_credit(make_client, admin_client):
    from app.db import SessionLocal
    from app.models import Profile, TopicConfirmation
    asker, author, reader, qid, aid, cid, _ = _setup(make_client, admin_client)
    assert reader.put(f"/api/items/{aid}/topic-feedback", json={"kind": "helped", **_selection(reader, aid, cid)}).status_code == 200
    profile_id = author.get("/api/profile").json()["id"]
    with SessionLocal() as db:
        db.execute(text("UPDATE knowledge_items SET visibility='private' WHERE id=:id"), {"id": qid})
        db.commit()
    assert _eligible(profile_id) == []
    context = reader.get(f"/api/items/{aid}/topic-feedback").json()
    assert context["question"] is None and context["question_id"] is None
    assert not context["can_confirm_helped"]
    reader_id = reader.get("/api/profile").json()["id"]
    with SessionLocal() as db:
        reader_account = db.get(Profile, reader_id).account_id
        db.execute(text("DELETE FROM accounts WHERE id=:id"), {"id": reader_account})
        db.commit()
        assert db.query(TopicConfirmation).filter_by(item_id=aid).count() == 0


def test_source_and_credit_invalidation_roll_back_together(make_client, admin_client):
    from app.db import SessionLocal
    from app.ml import adapter
    asker, author, _, qid, aid, cid, _ = _setup(make_client, admin_client)
    assert asker.post(f"/api/questions/{qid}/accept", json={"answer_id": aid, "topic_feedback": _selection(asker, aid, cid)}).status_code == 200
    profile_id = author.get("/api/profile").json()["id"]
    with SessionLocal() as db:
        adapter.apply_profile(db, profile_id)
        db.commit()
        assert db.execute(text("SELECT valid FROM ml_sources WHERE kind='profile' AND id=:id"), {"id": profile_id}).scalar() == 1
        db.execute(text("UPDATE knowledge_items SET body='Uncommitted edit' WHERE id=:id"), {"id": qid})
        assert db.execute(text("SELECT valid FROM ml_sources WHERE kind='profile' AND id=:id"), {"id": profile_id}).scalar() == 0
        db.rollback()
        assert db.execute(text("SELECT valid FROM ml_sources WHERE kind='profile' AND id=:id"), {"id": profile_id}).scalar() == 1
    assert len(_eligible(profile_id)) == 1
