"""Independent generation/rollback regressions with retained projections."""
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

import pytest

from test_ml_alias_conflicts import automated
from test_ml_identity_routing import CASES, apply, capture, embedding_generation_isolation, names, replay
from test_ml_automation import _profile_work


def test_grammar_only_transition_recomputes_conditional_expertise_without_source_replay(
        make_client, admin_client, monkeypatch):
    from app.ml import syntax
    from app.ml.sources import digest, finding_key

    original = CASES[0]
    # Preserve offsets and scores while isolating the retained fixture's names.
    encoded = json.dumps(original).replace('Threaded Index', 'Buffered Index').replace('TIndex', 'BIndex')
    encoded = encoded.replace('threaded index', 'buffered index').replace('tindex', 'bindex')
    for record in original['records']:
        body = record['body'].replace('Threaded Index', 'Buffered Index').replace('TIndex', 'BIndex')
        encoded = encoded.replace(digest(record['body']), digest(body))
    saved = json.loads(encoded)
    case, records = saved['case'], saved['records']
    clients, items = capture(make_client, case)
    expert, asker, reader = clients['cam'], clients['alice'], clients['ben']
    suffix = uuid.uuid4().hex[:8]
    for actor, client in clients.items():
        assert client.post('/api/auth/signup', json={
            'username': actor + suffix, 'password': 'a-private-test-password'}).status_code == 200
    profile = expert.get('/api/profile').json()
    extra = []
    concept_key = finding_key('concept', 'buffered index')
    try:
        for item, record in zip(items, records):
            apply(item, record)
        replay(items, records)
        replay(items, records)
        cid = names(asker, 'BIndex')['Buffered Index']
        assert admin_client.put(f'/api/ml/findings/{concept_key}/decision', json={'mode': 'pinned'}).status_code == 200
        question = asker.post('/api/questions', json={'body': 'Can I recover the drawer label?'}).json()['id']
        extra.append((asker, question))
        answer = expert.post(f'/api/questions/{question}/answers', json={
            'body': 'BIndex provides the current drawer label.'}).json()['id']
        extra.append((expert, answer))
        assert asker.post(f'/api/questions/{question}/accept', json={'answer_id': answer}).status_code == 200
        for body in ('BIndex recovered the requested drawer label.', 'BIndex preserves the drawer order.'):
            item = expert.post('/api/capture', data={'body': body}).json()['item']['id']
            extra.append((expert, item))
            assert reader.post(f'/api/items/{item}/helped').status_code == 200
        _profile_work(profile['id'])
        expertise_key = finding_key('expertise', profile['id'], cid)

        def areas():
            return next((entry['areas'] for entry in asker.get('/api/expertise').json()
                         if entry['label'] == profile['label']), [])

        assert 'Buffered Index' in areas()
        with monkeypatch.context() as grammar:
            grammar.setattr(syntax, 'CONFLICT_REVISION', syntax.CONFLICT_REVISION + '-transition')
            assert not names(asker, 'BIndex')
            assert 'Buffered Index' not in areas()
            # Only recompute the profile: unchanged old source caches cannot
            # authorize a fresh expertise projection under the new grammar.
            _profile_work(profile['id'])
            assert 'Buffered Index' not in areas()
            assert admin_client.put(f'/api/ml/findings/{expertise_key}/decision', json={'mode': 'pinned'}).status_code == 200
            assert 'Buffered Index' in areas()
            assert admin_client.put(f'/api/ml/findings/{expertise_key}/decision', json={'mode': 'automatic'}).status_code == 200
            assert 'Buffered Index' not in areas()
        # Reverting the grammar makes the retained source evidence usable, but
        # the just-computed profile still requires its own generation replay.
        assert 'Buffered Index' not in areas()
        _profile_work(profile['id'])
        assert 'Buffered Index' in areas()
    finally:
        for owner, item in reversed(extra):
            owner.delete(f'/api/items/{item}')
            apply(item, records[0])
        admin_client.put(f'/api/ml/findings/{concept_key}/decision', json={'mode': 'automatic'})
        for item, post, record in reversed(list(zip(items, case['posts'], records))):
            clients[post['actor']].delete(f'/api/items/{item}')
            apply(item, record)


def _migrate(database, direction, target):
    backend = Path(__file__).resolve().parents[1]
    environment = {**os.environ, 'MDS_DATA_DIR': str(database.parent),
                   'MDS_DATABASE_URL': f'sqlite:///{database}'}
    result = subprocess.run([sys.executable, '-m', 'alembic', '-c', str(backend / 'alembic.ini'),
                             direction, target], env=environment, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize('direction,target', [('upgrade', 'head'), ('downgrade', '0012'), ('downgrade', '0010')])
def test_projection_migration_protects_reader_without_generation_guard(
        app_modules, tmp_path, monkeypatch, direction, target):
    from sqlalchemy import create_engine, text, true
    from sqlalchemy.orm import Session
    from app.ml import policy, projection
    from app.ml.models import Evidence, Finding, Override, Source
    from app.models import Account, Concept, ConceptTerm, ExpertiseMapping, Profile, utcnow
    from app.routers.expertise import who_knows_what

    database = tmp_path / 'projection-migration.sqlite3'
    _migrate(database, 'upgrade', '0012' if direction == 'upgrade' else 'head')
    engine = create_engine(f'sqlite:///{database}')
    # Model the old reader explicitly: schema rollback must remain safe when
    # its application does not execute projection.current at all.
    monkeypatch.setattr(projection, 'current', lambda: true())
    try:
        with Session(engine) as db:
            db.add(Concept(id='topic'))
            db.flush()
            db.add(ConceptTerm(concept_id='topic', term='retained topic', display='Retained Topic', is_canonical=True))
            for mode in ('automatic', 'pinned'):
                db.add(Account(id=mode, username=mode, password_hash='unused'))
                db.flush()
                db.add(Profile(id=mode, account_id=mode, display_name=mode))
                db.flush()
                mapping = ExpertiseMapping(profile_id=mode, concept_id='topic')
                db.add(mapping)
                db.flush()
                payload = json.dumps({'profile_id': mode, 'concept_id': 'topic'})
                db.add(Finding(key=mode, kind='expertise', payload=payload, state='active', score=0,
                               policy_version=policy.VERSION, canonical_id=mapping.id,
                               created_at=utcnow(), updated_at=utcnow()))
                db.flush()
                db.add(Source(kind='profile', id=mode, content_hash=mode, valid=True,
                              model_version=policy.VERSION, result='{}', updated_at=utcnow()))
                db.add(Evidence(key=mode, finding_key=mode, source_kind='profile', source_id=mode,
                                source_hash=mode, group_key=mode, author_id=mode, start=0, end=0,
                                raw_score=0, polarity='positive', features='{}', model_version=policy.VERSION))
                if mode == 'pinned':
                    db.add(Override(key=mode, kind='expertise', mode='pinned', payload=payload,
                                    username='reviewer', updated_at=utcnow()))
            db.execute(text("UPDATE ml_state SET backfill_kind=NULL,backfill_cursor='' WHERE id=1"))
            before_generation = db.execute(text('SELECT backfill_generation FROM ml_state WHERE id=1')).scalar_one()
            db.commit()
            assert {entry['label'] for entry in who_knows_what(db)} == {'automatic', 'pinned'}
        _migrate(database, direction, target)
        with Session(engine) as db:
            assert {entry['label'] for entry in who_knows_what(db)} == {'pinned'}
            assert db.query(ExpertiseMapping).count() == 2
            assert db.query(Source).filter_by(kind='profile', valid=True).count() == 0
            state = db.execute(text('SELECT backfill_kind,backfill_generation FROM ml_state WHERE id=1')).one()
            assert state.backfill_kind == 'item' and state.backfill_generation > before_generation
            assert db.execute(text('PRAGMA foreign_key_check')).all() == []
    finally:
        engine.dispose()
