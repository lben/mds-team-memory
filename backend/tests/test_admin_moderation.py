"""Administrators remove any contribution; withdrawn automatic concepts leave the admin lists."""

import json
import uuid


def test_admin_deletes_any_contribution_with_what_is_attached(make_client, admin_client):
    asker, answerer, stranger = make_client(), make_client(), make_client()
    question = asker.post("/api/questions", json={"body": f"Who runs the {uuid.uuid4().hex[:6]} export?"}).json()["id"]
    first = answerer.post(f"/api/questions/{question}/answers", json={"body": "The platform team."}).json()["id"]
    second = answerer.post(f"/api/questions/{question}/answers", json={"body": "Ask the data desk."}).json()["id"]
    assert asker.post(f"/api/questions/{question}/accept", json={"answer_id": first}).status_code == 200

    # Nobody else may, and the asker may not once it has answers.
    assert stranger.delete(f"/api/items/{first}").status_code == 403
    assert asker.delete(f"/api/questions/{question}").status_code == 400

    # Deleting the accepted answer leaves the question answered, not pointing at nothing.
    assert admin_client.delete(f"/api/items/{first}").status_code == 200
    detail = asker.get(f"/api/questions/{question}").json()
    assert detail["accepted_answer_id"] is None and detail["question_status"] == "answered"
    assert [answer["id"] for answer in detail["answers"]] == [second]
    third = answerer.post(f"/api/questions/{question}/answers", json={"body": "Or the night shift."}).json()["id"]
    assert admin_client.delete(f"/api/items/{third}").status_code == 200
    assert asker.get(f"/api/questions/{question}").json()["question_status"] == "answered"

    # Deleting the question takes its remaining answers with it.
    assert admin_client.delete(f"/api/items/{question}").status_code == 200
    assert asker.get(f"/api/questions/{question}").status_code == 404
    assert asker.get(f"/api/items/{second}").status_code == 404

    post = answerer.post("/api/capture", data={"body": "A note someone else wrote."}).json()["item"]["id"]
    assert admin_client.delete(f"/api/items/{post}").status_code == 200
    assert answerer.get(f"/api/items/{post}").status_code == 404


def test_withdrawn_automatic_concepts_leave_the_admin_lists(admin_client):
    from app.db import SessionLocal
    from app.models import Concept, ConceptTerm, RELATED_TO_ID, Relationship, utcnow
    from app.ml.models import Finding
    from app.ml.policy import VERSION
    from app.ml.sources import finding_key

    kept = admin_client.post("/api/admin/concepts", json={"name": f"Kept{uuid.uuid4().hex[:6]}"}).json()
    name = f"Withdrawn{uuid.uuid4().hex[:6]}"
    with SessionLocal() as db:  # what automation leaves after the posts behind a concept are deleted
        concept = Concept()
        db.add(concept)
        db.flush()
        db.add(ConceptTerm(concept_id=concept.id, term=name.lower(), display=name, is_canonical=True))
        db.add(Finding(key=finding_key("concept", name), kind="concept", payload=json.dumps({"name": name}),
                       state="withdrawn", score=0.0, calibrated=False, policy_version=VERSION,
                       canonical_id=concept.id, created_at=utcnow(), updated_at=utcnow()))
        db.add(Relationship(src_kind="concept", src_id=kept["id"], dst_kind="concept", dst_id=concept.id,
                            relationship_type_id=RELATED_TO_ID, state="rejected"))
        db.commit()
        withdrawn = concept.id
    names = {row["name"] for row in admin_client.get("/api/admin/concepts").json()}
    assert kept["name"] in names and name not in names
    links = admin_client.get("/api/graph/links", params={"concept_id": kept["id"]}).json()
    assert not any(withdrawn in (link["src_id"], link["dst_id"]) for link in links)


def test_a_question_whose_only_answer_is_deleted_is_open_again(make_client, admin_client):
    asker, answerer = make_client(), make_client()
    question = asker.post("/api/questions", json={"body": f"Where is the {uuid.uuid4().hex[:6]} runbook?"}).json()["id"]
    answer = answerer.post(f"/api/questions/{question}/answers", json={"body": "In the shared drive."}).json()["id"]
    assert asker.get(f"/api/questions/{question}").json()["question_status"] == "answered"
    assert admin_client.delete(f"/api/items/{answer}").status_code == 200
    assert asker.get(f"/api/questions/{question}").json()["question_status"] == "open"
