"""Independent public feedback/projection regressions; no model inference."""
import uuid
import pytest

from test_ml_automation import _capture, _confirm_topic, _profile_work


def _signup(make_client):
    client = make_client(account=False)
    assert client.post('/api/auth/signup', json={'username': 'review' + uuid.uuid4().hex[:10], 'password': 'a-good-password'}).status_code == 200
    return client, client.get('/api/profile').json()['id']


@pytest.mark.parametrize('result', ['{}', '{"projection_contract":"legacy-generic-votes"}', 'invalid-json'])
def test_legacy_worker_cannot_certify_profile_source(app_modules, result):
    from sqlalchemy import text
    from app.db import SessionLocal
    from app.topic_feedback import CONTRACT_VERSION
    import json

    source_id = uuid.uuid4().hex
    statement = text("""INSERT INTO ml_sources(kind,id,content_hash,valid,model_version,result,updated_at)
      VALUES('profile',:id,'hash',1,'policy',:result,datetime('now'))
      ON CONFLICT(kind,id) DO UPDATE SET valid=1,result=excluded.result""")
    with SessionLocal() as db:
        read = lambda: db.execute(text("SELECT valid FROM ml_sources WHERE kind='profile' AND id=:id"), {'id': source_id}).scalar_one()
        db.execute(statement, {'id': source_id, 'result': result})
        assert read() == 0
        db.execute(statement, {'id': source_id, 'result': json.dumps({'topic_feedback_contract': CONTRACT_VERSION})})
        assert read() == 1
        db.execute(statement, {'id': source_id, 'result': result})
        assert read() == 0
        db.execute(text("UPDATE ml_sources SET valid=1 WHERE kind='profile' AND id=:id"), {'id': source_id})
        assert read() == 0
        db.rollback()


@pytest.fixture
def topic_claim(make_client, admin_client):
    from app.db import SessionLocal
    from app.models import Account, Concept, KnowledgeItem, Profile
    from app.topic_feedback import TopicFeedbackIn, begin_write, feedback_dict, save_selection

    owner, beneficiary_id = _signup(make_client)
    reader, actor_id = _signup(make_client)
    concept = admin_client.post('/api/admin/concepts', json={'name': 'Clock recovery ' + uuid.uuid4().hex[:8]}).json()['id']
    item_id = _capture(owner, 'This contribution explains why framing markers must be restored before reacquiring the clock.')
    with SessionLocal() as db:
        item = db.get(KnowledgeItem, item_id)
        profile = db.get(Profile, actor_id)
        account = db.get(Account, profile.account_id)
        before = feedback_dict(db, item, profile, account)
        request = TopicFeedbackIn(kind='helped', expected_context=before['context_token'], topics=[
            {'concept_id': concept, 'identity_revision': db.get(Concept, concept).credit_identity_revision}])
        begin_write(db)
        save_selection(db, item, profile, account, request.kind, request)
        db.commit()
    yield {'item': item_id, 'concept': concept, 'reader': reader, 'actor': actor_id, 'owner': owner,
           'beneficiary': beneficiary_id, 'request': request}


def test_direct_claim_is_current_and_same_request_retry_does_not_duplicate(topic_claim):
    from app.db import SessionLocal
    from app.models import Account, KnowledgeItem, Profile, TopicConfirmation
    from app.topic_feedback import begin_write, eligible_confirmations, save_selection

    claim = topic_claim
    with SessionLocal() as db:
        before = eligible_confirmations(db, claim['beneficiary'])
        assert len(before) == 1
        item, actor = db.get(KnowledgeItem, claim['item']), db.get(Profile, claim['actor'])
        account = db.get(Account, actor.account_id)
        begin_write(db)
        save_selection(db, item, actor, account, 'helped', claim['request'])
        db.commit()
        assert db.query(TopicConfirmation).filter_by(item_id=claim['item']).count() == 1
        assert len(eligible_confirmations(db, claim['beneficiary'])) == 1


def test_automatic_downgrade_preserves_human_confirmations(topic_claim, tmp_path):
    import os
    from pathlib import Path
    import sqlite3
    import subprocess
    import sys
    from app.db import SessionLocal

    target = tmp_path / 'rollback.sqlite3'
    with SessionLocal() as db:
        origin = db.get_bind().url.database
    with sqlite3.connect(origin) as source, sqlite3.connect(target) as copy:
        source.backup(copy)
    def state():
        with sqlite3.connect(target) as db:
            db.row_factory = sqlite3.Row
            return ([dict(row) for row in db.execute('SELECT * FROM topic_confirmations ORDER BY id')],
                    db.execute('SELECT * FROM alembic_version').fetchall(),
                    db.execute('SELECT type,name,sql FROM sqlite_master ORDER BY type,name').fetchall())
    before = state()
    assert any(row['item_id'] == topic_claim['item'] for row in before[0])
    backend = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, '-m', 'alembic', '-c', str(backend / 'alembic.ini'),
                             'downgrade', '0014'], capture_output=True, text=True,
                            env={**os.environ, 'MDS_DATA_DIR': str(tmp_path), 'MDS_DATABASE_URL': f'sqlite:///{target}'})
    assert result.returncode != 0 and 'Automatic downgrade is disabled' in result.stderr
    assert state() == before


def test_content_edit_and_restore_does_not_revive_original_confirmation(topic_claim):
    from app.db import SessionLocal
    from app.models import KnowledgeItem
    from app.topic_feedback import eligible_confirmations

    claim = topic_claim
    with SessionLocal() as db:
        item = db.get(KnowledgeItem, claim['item'])
        original = item.body
        item.body = 'Changed authoritative content.'
        db.commit()
        assert not eligible_confirmations(db, claim['beneficiary'])
        item.body = original
        db.commit()
        assert not eligible_confirmations(db, claim['beneficiary'])


def test_profile_detach_and_same_account_restore_does_not_revive_confirmation(topic_claim):
    from app.db import SessionLocal
    from app.models import Profile
    from app.topic_feedback import eligible_confirmations

    claim = topic_claim
    with SessionLocal() as db:
        actor = db.get(Profile, claim['actor'])
        account = actor.account_id
        actor.account_id = None
        db.commit()
        assert not eligible_confirmations(db, claim['beneficiary'])
        actor.account_id = account
        db.commit()
        assert not eligible_confirmations(db, claim['beneficiary'])


def test_stale_topic_acceptance_rejects_pointer_event_and_confirmation_atomically(make_client, admin_client):
    from app.db import SessionLocal
    from app.models import ImpactEvent, KnowledgeItem, TopicConfirmation

    expert, _ = _signup(make_client)
    asker, _ = _signup(make_client)
    name = 'Frame synchronization ' + uuid.uuid4().hex[:8]
    cid = admin_client.post('/api/admin/concepts', json={'name': name}).json()['id']
    question = asker.post('/api/questions', json={'body': f'How do I restore {name}?'}).json()['id']
    answer = expert.post(f'/api/questions/{question}/answers', json={'body': 'Restore the framing markers before reacquiring the bit clock.'}).json()['id']
    context = asker.get(f'/api/items/{answer}/topic-feedback').json()
    topic = next(row for row in context['topics'] if row['concept_id'] == cid)
    assert expert.put(f'/api/items/{answer}', json={'body': 'The answer changed after the dialog opened.'}).status_code == 200
    rejected = asker.post(f'/api/questions/{question}/accept', json={'answer_id': answer, 'topic_feedback': {
        'expected_context': context['context_token'], 'topics': [{'concept_id': cid, 'identity_revision': topic['identity_revision']}]}})
    assert rejected.status_code == 409, rejected.text
    with SessionLocal() as db:
        assert db.get(KnowledgeItem, question).accepted_answer_id is None
        assert db.query(ImpactEvent).filter_by(item_id=answer, event_type='answer_accepted').count() == 0
        assert db.query(TopicConfirmation).filter_by(item_id=answer).count() == 0


def test_unaccept_and_reaccept_same_answer_does_not_revive_topic_confirmation(make_client, admin_client):
    from app.db import SessionLocal
    from app.topic_feedback import eligible_confirmations

    expert, beneficiary = _signup(make_client)
    asker, _ = _signup(make_client)
    name = 'Phase recovery ' + uuid.uuid4().hex[:8]
    cid = admin_client.post('/api/admin/concepts', json={'name': name}).json()['id']
    question = asker.post('/api/questions', json={'body': f'How do I restore {name}?'}).json()['id']
    answer = expert.post(f'/api/questions/{question}/answers', json={'body': 'Restart the tracking loop with a wide capture bandwidth.'}).json()['id']
    context = asker.get(f'/api/items/{answer}/topic-feedback').json()
    topic = next(row for row in context['topics'] if row['concept_id'] == cid)
    accepted = asker.post(f'/api/questions/{question}/accept', json={'answer_id': answer, 'topic_feedback': {
        'expected_context': context['context_token'], 'topics': [{'concept_id': cid, 'identity_revision': topic['identity_revision']}]}})
    assert accepted.status_code == 200, accepted.text
    with SessionLocal() as db:
        assert len(eligible_confirmations(db, beneficiary)) == 1
    removed = asker.request('DELETE', f'/api/questions/{question}/accept', json={'expected_acceptance_revision': accepted.json()['acceptance_revision']})
    assert removed.status_code == 200, removed.text
    assert asker.post(f'/api/questions/{question}/accept', json={'answer_id': answer}).status_code == 200
    with SessionLocal() as db:
        assert eligible_confirmations(db, beneficiary) == []


def test_stale_dialog_cannot_restore_revoked_topic(topic_claim):
    from app.db import SessionLocal
    from app.models import TopicConfirmation

    claim = topic_claim
    reader = claim['reader']
    context = reader.get(f'/api/items/{claim["item"]}/topic-feedback').json()
    revoked = reader.put(f'/api/items/{claim["item"]}/topic-feedback', json={
        'kind': 'helped', 'expected_context': context['context_token'], 'topics': []})
    assert revoked.status_code == 200, revoked.text
    rejected = reader.put(f'/api/items/{claim["item"]}/topic-feedback', json=claim['request'].model_dump())
    assert rejected.status_code == 409, rejected.text
    with SessionLocal() as db:
        rows = db.query(TopicConfirmation).filter_by(item_id=claim['item']).all()
        assert len(rows) == 1 and rows[0].state == 'revoked'


def test_other_valid_confirmation_cannot_authorize_changed_answer_question(make_client, admin_client):
    from app.db import SessionLocal
    from app.topic_feedback import eligible_confirmations

    expert, beneficiary = _signup(make_client)
    asker, _ = _signup(make_client)
    name = 'Carrier recovery ' + uuid.uuid4().hex[:8]
    cid = admin_client.post('/api/admin/concepts', json={'name': name}).json()['id']
    accepted_records = []
    for which in ('receiver', 'transmitter'):
        question = asker.post('/api/questions', json={'body': f'How does {name} work in the {which}?'}).json()['id']
        answer = expert.post(f'/api/questions/{question}/answers', json={'body': f'Use a tracking loop around the {which} timing reference.'}).json()['id']
        context = asker.get(f'/api/items/{answer}/topic-feedback').json()
        topic = next(row for row in context['topics'] if row['concept_id'] == cid)
        accepted = asker.post(f'/api/questions/{question}/accept', json={'answer_id': answer, 'topic_feedback': {
            'expected_context': context['context_token'], 'topics': [{'concept_id': cid, 'identity_revision': topic['identity_revision']}]}})
        assert accepted.status_code == 200, accepted.text
        accepted_records.append((question, answer, accepted.json()['acceptance_revision']))
    with SessionLocal() as db:
        assert {row.item_id for row in eligible_confirmations(db, beneficiary)} == {answer for _, answer, _ in accepted_records}
    question, answer, revision = accepted_records[1]
    removed = asker.request('DELETE', f'/api/questions/{question}/accept', json={'expected_acceptance_revision': revision})
    assert removed.status_code == 200, removed.text
    with SessionLocal() as db:
        assert {row.item_id for row in eligible_confirmations(db, beneficiary)} == {accepted_records[0][1]}


@pytest.fixture
def explicit_expert(make_client, admin_client):
    author, profile = _signup(make_client)
    asker, _ = _signup(make_client)
    peer, peer_profile = _signup(make_client)
    name = 'Clock tracking ' + uuid.uuid4().hex[:8]
    cid = admin_client.post('/api/admin/concepts', json={'name': name}).json()['id']
    for body in (f'Our {name} experiment isolated drift after the recovered reference oscillator warmed up.',
                 f'For {name} calibration, measure the phase detector output while injecting a controlled timing offset.'):
        item = _capture(author, body)
        _confirm_topic(peer, item, 'helped', name)
    question = asker.post('/api/questions', json={'body': f'How can I restart {name} after a loss of lock?'}).json()['id']
    answer = author.post(f'/api/questions/{question}/answers', json={'body': 'Widen the capture range, restart acquisition and then restore narrow tracking.'}).json()['id']
    assert asker.post(f'/api/questions/{question}/accept', json={'answer_id': answer}).status_code == 200
    _confirm_topic(asker, answer, 'accepted', name)
    _profile_work(profile)
    label = author.get('/api/profile').json()['label']
    assert next(row['areas'] for row in peer.get('/api/expertise').json() if row['label'] == label) == [name]
    return {'profile': profile, 'label': label, 'topic': cid, 'name': name, 'reader': peer, 'peer_profile': peer_profile}


def test_profile_evidence_retired_model_cannot_remain_public(explicit_expert):
    from app.db import SessionLocal
    from app.ml.models import Evidence

    expert = explicit_expert
    with SessionLocal() as db:
        evidence = db.query(Evidence).filter_by(source_kind='profile', source_id=expert['profile']).one()
        evidence.model_version = 'retired-projection-policy'
        db.commit()
    assert not any(row['label'] == expert['label'] for row in expert['reader'].get('/api/expertise').json())


def test_one_expert_lookup_work_does_not_scale_with_unrelated_confirmations(explicit_expert, make_client):
    from app.db import SessionLocal
    from app.ml import effective
    from app.models import Concept, ExpertiseMapping, KnowledgeItem, Profile, TopicConfirmation
    from app.topic_feedback import CONTRACT_VERSION

    expert = explicit_expert
    _, unrelated_profile = _signup(make_client)
    with SessionLocal() as db:
        raw = db.connection().connection.driver_connection
        def measured():
            ticks = []
            raw.set_progress_handler(lambda: (ticks.append(1), 0)[1], 100)
            try:
                found = effective.expertise(db).filter(ExpertiseMapping.profile_id == expert['profile']).all()
                assert [row.concept_id for row in found] == [expert['topic']]
            finally:
                raw.set_progress_handler(None, 0)
            return len(ticks)
        small = measured()
        actor, beneficiary = db.get(Profile, expert['peer_profile']), db.get(Profile, unrelated_profile)
        identity_revision = db.get(Concept, expert['topic']).credit_identity_revision
        for index in range(2000):
            db.add(KnowledgeItem(id='unrelated-confirmed-item-' + str(index), kind='note',
                    body=f'Unrelated independent record {index}.', visibility='team', author_profile_id=beneficiary.id))
        db.flush()
        for index in range(2000):
            db.add(TopicConfirmation(id='unrelated-confirmation-' + str(index), kind='helped',
                    actor_account_id=actor.account_id, actor_profile_id=actor.id,
                    actor_binding_revision=actor.account_binding_revision,
                    beneficiary_account_id=beneficiary.account_id, beneficiary_profile_id=beneficiary.id,
                    beneficiary_binding_revision=beneficiary.account_binding_revision,
                    item_id='unrelated-confirmed-item-' + str(index), item_evidence_revision=1,
                    concept_id=expert['topic'], concept_identity_revision=identity_revision,
                    contract_version=CONTRACT_VERSION, state='current', context_token='0' * 64))
        db.flush()
        large = measured()
        assert large <= small + 20, (small, large)
        db.rollback()


def _trigger_database(tmp_path):
    import sqlite3
    from test_ml_relationship_generation import _migrate

    database = tmp_path / 'topic-trigger.sqlite3'
    _migrate(database, 'upgrade', 'head')
    db = sqlite3.connect(database)
    db.execute('PRAGMA foreign_keys=ON')
    db.execute("INSERT INTO profiles(id,claim_locked,created_at) VALUES ('owner',0,datetime('now'))")
    return db


def _insert_trigger_items(db, ids):
    db.executemany('''INSERT INTO knowledge_items(id,kind,body,visibility,author_profile_id,created_at,updated_at)
      VALUES (?,'note','Independent originaltokenx','team','owner',datetime('now'),datetime('now'))''', [(id,) for id in ids])
    db.commit()


def test_semantic_revision_updates_preserve_fts_content_and_integrity(tmp_path):
    # Exercise committed low-level writes as well as the real migration. An
    # AFTER UPDATE trigger that rewrites only a revision must not run the FTS
    # body replacement again, before the outer body update reaches the index.
    db = _trigger_database(tmp_path)
    try:
        _insert_trigger_items(db, ['target'])
        rowid = db.execute("SELECT rowid FROM knowledge_items WHERE id='target'").fetchone()[0]
        assert db.execute("SELECT rowid FROM items_fts WHERE items_fts MATCH 'originaltokenx'").fetchall() == [(rowid,)]
        db.execute("UPDATE knowledge_items SET body='Revised replacementtokenz' WHERE id='target'")
        db.commit()
        assert db.execute("SELECT evidence_revision FROM knowledge_items WHERE id='target'").fetchone()[0] == 2
        # A second semantic change has no effect on the indexed body.
        db.execute("UPDATE knowledge_items SET source_item_id='new-origin' WHERE id='target'")
        db.commit()
        assert db.execute("SELECT evidence_revision FROM knowledge_items WHERE id='target'").fetchone()[0] == 3
        assert db.execute("SELECT rowid FROM items_fts WHERE items_fts MATCH 'originaltokenx'").fetchall() == []
        assert db.execute("SELECT rowid FROM items_fts WHERE items_fts MATCH 'replacementtokenz'").fetchall() == [(rowid,)]
        db.execute("INSERT INTO items_fts(items_fts,rank) VALUES ('integrity-check',1)")
        assert db.execute('PRAGMA integrity_check').fetchall() == [('ok',)]
    finally:
        db.close()


def test_one_item_edit_work_is_bounded_without_topic_confirmations(tmp_path):
    db = _trigger_database(tmp_path)
    try:
        _insert_trigger_items(db, ['target'])
        def edit(body):
            ticks = []
            db.set_progress_handler(lambda: (ticks.append(1), 0)[1], 100)
            try:
                db.execute("UPDATE knowledge_items SET body=? WHERE id='target'", (body,))
                db.commit()
            finally:
                db.set_progress_handler(None, 0)
            return len(ticks)
        small = edit('First unique editedtokena')
        _insert_trigger_items(db, [f'unrelated-{index}' for index in range(2000)])
        assert db.execute('SELECT count(*) FROM topic_confirmations').fetchone()[0] == 0
        large = edit('Second unique editedtokenb')
        assert large <= small + 20, (small, large)
    finally:
        db.close()
