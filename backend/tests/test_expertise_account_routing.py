"""Routing follows account identity even when legacy data has several profiles."""
import uuid


def test_routing_deduplicates_accounts_and_excludes_all_asker_profiles(make_client, admin_client):
    from app.db import SessionLocal
    from app.models import ExpertiseMapping, Notification, Profile
    from app.ml.adapter import fix_expertise
    from app.concepts import route_question
    from app.models import Concept, KnowledgeItem

    suffix = uuid.uuid4().hex[:8]
    asker, expert = make_client(), make_client()
    for label, client in (("asker", asker), ("expert", expert)):
        assert client.post("/api/auth/signup", json={"username": label + suffix,
                           "password": "a-private-test-password"}).status_code == 200
    asker_id = asker.get("/api/profile").json()["id"]
    expert_id = expert.get("/api/profile").json()["id"]
    topic = "Routing Accounts " + suffix
    response = admin_client.post("/api/admin/concepts", json={"name": topic, "aliases": []})
    assert response.status_code == 200, response.text
    cid = response.json()["id"]
    with SessionLocal() as db:
        asker_account = db.get(Profile, asker_id).account_id
        expert_account = db.get(Profile, expert_id).account_id
        duplicates = [Profile(account_id=asker_account, display_name="Other asker profile"),
                      Profile(account_id=expert_account, display_name="Other expert profile")]
        db.add_all(duplicates)
        db.flush()
        for pid in (expert_id, *(p.id for p in duplicates)):
            mapping = ExpertiseMapping(profile_id=pid, concept_id=cid)
            db.add(mapping)
            db.flush()
            fix_expertise(db, mapping, "pinned")
        db.commit()
    question = asker.post("/api/questions", json={"body": f"How do I configure {topic}?"}).json()["id"]
    with SessionLocal() as db:
        # Simulate queued delivery and multiple topic/profile matches replaying.
        for _ in range(2):
            route_question(db, db.get(KnowledgeItem, question), [db.get(Concept, cid)])
        db.commit()
        notifications = db.query(Notification).filter_by(kind="expertise_match", item_id=question).all()
        assert [row.profile_id for row in notifications] == [expert_id]
    assert any(row["item_id"] == question for row in expert.get("/api/notifications").json()["notifications"])
    assert not any(row["item_id"] == question and row["kind"] == "expertise_match"
                   for row in asker.get("/api/notifications").json()["notifications"])
