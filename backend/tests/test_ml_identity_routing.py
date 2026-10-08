"""Public identity lifecycle with fixed retained entity and exact role records."""
import copy
import json
from pathlib import Path
import uuid

import pytest

from ml_synthetic_records import current_synthetic_result

from test_ml_alias_conflicts import automated

CASES = json.loads((Path(__file__).parent / 'fixtures/identity_routing_recorded.json').read_text())['cases']


@pytest.fixture(autouse=True)
def embedding_generation_isolation(app_modules):
    # This recorded suite has zero embedding chunks. Its real model revision
    # must not leave a third-generation rebuild in the shared API test database.
    from sqlalchemy import text
    from app.db import SessionLocal

    columns = ('active_generation', 'staging_generation', 'staging_cursor',
               'staging_reserved_bytes', 'staging_bytes')
    with SessionLocal() as db:
        previous = dict(db.execute(text('SELECT ' + ','.join(columns) + ' FROM ml_budget WHERE id=1')).mappings().one())
    yield
    with SessionLocal() as db:
        db.execute(text('UPDATE ml_budget SET ' + ','.join(key + '=:' + key for key in columns) + ' WHERE id=1'), previous)
        db.commit()


def capture(make_client, case):
    """Each actor posts from their own account, named after them."""
    clients, items = {}, []
    for post in case['posts']:
        if post['actor'] not in clients:
            clients[post['actor']] = make_client(account=False)
            response = clients[post['actor']].post('/api/auth/signup', json={
                'username': post['actor'] + uuid.uuid4().hex[:8], 'password': 'a-good-password'})
            assert response.status_code == 200, response.text
        client = clients[post['actor']]
        if post['kind'] == 'question':
            response = client.post('/api/questions', json={'body': post['body']})
            item = response.json()['id']
        elif post['kind'] == 'answer':
            parent = post['parent']
            response = client.post(f'/api/questions/{items[parent]}/answers', json={'body': post['body']})
            item = response.json()['id']
            if post.get('accepted'):
                accepted = clients[case['posts'][parent]['actor']].post(
                    f'/api/questions/{items[parent]}/accept', json={'answer_id': item})
                assert accepted.status_code == 200, accepted.text
        else:
            response = client.post('/api/capture', data={'body': post['body']})
            item = response.json()['item']['id']
        assert response.status_code == 200, response.text
        items.append(item)
    return clients, items


def apply(item, record, *, cached=False, empty=False, margins=None):
    from sqlalchemy import text
    from app.db import SessionLocal
    from app.ml import adapter, policy, runtime, syntax
    from app.ml.sources import snapshot

    with SessionLocal() as db:
        source = snapshot(db, 'item', item)
        models = {role: {'revision': revision} for role, revision in zip(
            ('extractor', 'embeddings', 'syntax'), record['application_model_version'].split(':')[:3])}
        version = runtime.inference_version(models)
        db.execute(text('UPDATE ml_state SET pipeline_version=:version WHERE id=1'),
                   {'version': version + ':' + policy.VERSION})
        metadata = (version, record['original_metadata']['embedding_version'], 1024)
        result = copy.deepcopy(record['result']) if source else None
        if result:
            # Mechanical identity replay retains these conflict observations.
            # Current parser behavior is exercised in test_ml_alias_grammar.
            result['conflict_coverage_revision'] = syntax.CONFLICT_REVISION
            # These retained definitions do not use the corrected stands-for
            # branch. Replay their unchanged scores and spans in this generation.
            for definition in result.get('corroborated_definitions', []):
                assert definition['syntax_rule_revision'] == 'r5-copular-development:7fde15e1b9c5f8f6cb627618dfd2849ba0d3b76d2892897199b5f1c56c4eb827'
                assert 'stands_for' not in definition['syntax_rules']
                definition['syntax_rule_revision'] = syntax.REVISION
        if cached and source:
            result, metadata = adapter.cached_result(db, source, version)
        if empty:
            result.update(concepts=[], relations=[], corroborated_definitions=[], conflict_definitions=[])
        if source and not cached:
            # Synthetic application replay of retained geometry/scores, not a
            # new parser/model observation or an upgrade of a stored cache.
            result = current_synthetic_result(result, source.text)
            if margins is not None:
                result['eligibility']['margins'] = margins
        adapter.apply_source(db, 'item', item, source, result, *metadata)
        db.commit()


def replay(items, records):
    for item, record in zip(items, records):
        apply(item, record, cached=True)


def names(client, spelling):
    response = client.get('/api/search', params={'q': spelling})
    assert response.status_code == 200, response.text
    return {concept['name']: concept['id'] for concept in response.json()['concepts']}


@pytest.mark.parametrize('witness_last', [False, True])
def test_exact_role_routes_reserved_entity_and_keeps_dependency_after_term_replay(make_client, admin_client, witness_last):
    from app.ml.sources import finding_key

    saved = CASES[0]
    case, records = saved['case'], saved['records']
    clients, items = capture(make_client, case)
    client, owner = clients['alice'], clients['ben']
    order = [0, 4, 2, 3, 1] if witness_last else list(range(5))
    key = finding_key('concept', 'threaded index')
    try:
        for index in order:
            apply(items[index], records[index])
        replay(items, records)
        replay(items, records)
        canonical = names(client, 'TIndex')['Threaded Index']
        assert 'TIndex' not in names(client, 'TIndex')
        detail = admin_client.get(f'/api/ml/findings/{key}').json()
        routed = next(e for e in detail['evidence'] if e['source_id'] == items[4])
        assert routed['raw_score'] == 0.9985187649726868
        assert (routed['start'], routed['end'], routed['quote']) == (71, 77, 'TIndex')
        certificate = routed['identity_routes'][0]
        assert not routed.get('term_routes')
        assert certificate['witness_source_id'] == items[1]
        assert certificate['concept_key'] == key
        assert certificate['method_fingerprint']
        assert canonical in {c['id'] for c in client.get(f'/api/items/{items[4]}').json()['concepts']}
        # An alias-term replay must keep the same exact role witness.
        apply(items[4], records[4], cached=True)
        repeated = admin_client.get(f'/api/ml/findings/{key}').json()
        assert next(e for e in repeated['evidence'] if e['source_id'] == items[4])['identity_routes'] == [certificate]
        assert owner.put(f'/api/items/{items[1]}', json={'body': 'The card archive is retired.'}).status_code == 200
        assert 'Threaded Index' not in names(client, 'Threaded Index')
        assert canonical not in {c['id'] for c in client.get(f'/api/items/{items[4]}').json()['concepts']}
        apply(items[1], records[1], empty=True)
        replay(items, records)
        assert 'Threaded Index' not in names(client, 'TIndex')
        weak = admin_client.get(f'/api/ml/findings/{key}').json()['evidence']
        assert max(e['raw_score'] for e in weak) == 0.8546918034553528
        assert not any(e.get('identity_routes') for e in weak)
        assert owner.put(f'/api/items/{items[1]}', json={'body': records[1]['body']}).status_code == 200
        apply(items[1], records[1])
        replay(items, records)
        assert names(client, 'TIndex')['Threaded Index'] == canonical
        assert admin_client.put(f'/api/ml/findings/{key}/decision', json={'mode': 'suppressed'}).status_code == 200
        replay(items, records)
        assert 'Threaded Index' not in names(client, 'TIndex')
        assert admin_client.put(f'/api/ml/findings/{key}/decision', json={'mode': 'automatic'}).status_code == 200
        replay(items, records)
        assert names(client, 'TIndex')['Threaded Index'] == canonical
        assert clients['cam'].delete(f'/api/items/{items[4]}').status_code == 200
        apply(items[4], records[4])
        replay(items, records)
        assert 'Threaded Index' not in names(client, 'TIndex')
        # Both definition sources are gone; the durable alias reservation still
        # keeps cached standalone observations on their conditional identity.
        assert owner.delete(f'/api/items/{items[1]}').status_code == 200
        apply(items[1], records[1])
        replay(items, records)
        assert admin_client.get(f"/api/ml/findings/{finding_key('concept', 'tindex')}").status_code == 404
    finally:
        for item, post, record in reversed(list(zip(items, case['posts'], records))):
            clients[post['actor']].delete(f'/api/items/{item}')
            apply(item, record)


def test_short_form_judged_alone_does_not_hold_back_its_concept_and_alias(make_client):
    saved = CASES[0]
    case, records = saved['case'], saved['records']
    clients, items = capture(make_client, case)
    client = clients['alice']
    # Only the last post supports the concept: its strongest span is the short
    # form, which the eligibility check rejects alone, beside the accepted name.
    judged = {items[4]: {'tindex': 0.2, 'threaded index': 0.99}}
    try:
        for item, record in zip(items, records):
            apply(item, record, margins=judged.get(item, {}))
        replay(items, records)
        assert set(names(client, 'TIndex')) == {'Threaded Index'}
    finally:
        for item, post, record in reversed(list(zip(items, case['posts'], records))):
            clients[post['actor']].delete(f'/api/items/{item}')
            apply(item, record)


@pytest.mark.parametrize('saved', CASES[1:], ids=lambda saved: saved['case']['id'])
def test_unqualified_role_and_distinct_descriptor_do_not_route_entity_scores(make_client, admin_client, saved):
    from app.ml.sources import finding_key

    case, records = saved['case'], saved['records']
    clients, items = capture(make_client, case)
    name, alias = ('comet profile extractor', 'CPE') if case['id'] == 'qa_b_005' else ('Rolled Rim', 'RRim')
    try:
        for item, record in zip(items, records):
            apply(item, record)
        replay(items, records)
        replay(items, records)
        detail = admin_client.get(f"/api/ml/findings/{finding_key('concept', name.casefold())}").json()
        assert not any(e.get('identity_routes') for e in detail['evidence'])
        assert name not in names(next(iter(clients.values())), alias)
    finally:
        for item, post, record in reversed(list(zip(items, case['posts'], records))):
            clients[post['actor']].delete(f'/api/items/{item}')
            apply(item, record)


@pytest.mark.parametrize('processed', [True, False], ids=['replayed', 'before_source_replay'])
@pytest.mark.parametrize('coverage', ['old_structure', 'old_revision'])
def test_explicit_topic_credit_is_independent_of_alias_coverage_with_live_manual_concept(make_client, admin_client, tmp_path, processed, coverage):
    """Mechanical empty-extraction control; recorded entity/role data stay fixed."""
    from app.ml.sources import finding_key
    from test_ml_automation import _profile_work, _confirm_topic

    saved = CASES[0]
    case, records = saved['case'], saved['records']
    clients, items = capture(make_client, case)
    expert, asker, reader = clients['cam'], clients['alice'], clients['ben']
    if not processed:
        expert = make_client(account=False)
        response = expert.post('/api/auth/signup', json={
            'username': 'dana' + uuid.uuid4().hex[:8], 'password': 'a-good-password'})
        assert response.status_code == 200, response.text
    profile_data = expert.get('/api/profile').json()
    profile = profile_data['id']
    extra = []
    key = finding_key('concept', 'threaded index')
    try:
        for item, record in zip(items, records):
            apply(item, record)
        replay(items, records)
        replay(items, records)
        canonical = names(asker, 'TIndex')['Threaded Index']
        assert admin_client.put(f'/api/ml/findings/{key}/decision', json={'mode': 'pinned'}).status_code == 200
        question = asker.post('/api/questions', json={'body': 'Can I recover the drawer label?'}).json()['id']
        extra.append((asker, question))
        answer = expert.post(f'/api/questions/{question}/answers', json={'body': 'TIndex provides the current drawer label.'}).json()['id']
        extra.append((expert, answer))
        assert asker.post(f'/api/questions/{question}/accept', json={'answer_id': answer}).status_code == 200
        for body in ('TIndex recovered the requested drawer label.', 'TIndex preserves the drawer order.'):
            item = expert.post('/api/capture', data={'body': body}).json()['item']['id']
            extra.append((expert, item))
            assert reader.post(f'/api/items/{item}/helped').status_code == 200
        if processed:
            for _, item in extra:
                apply(item, records[0], empty=True)
        _profile_work(profile)
        assert not any('Threaded Index' in entry['areas'] for entry in asker.get('/api/expertise').json()
                       if entry['label'] == profile_data['label'])
        _confirm_topic(asker, answer, 'accepted', 'Threaded Index')
        for _, credited_item in extra[2:]:
            _confirm_topic(reader, credited_item, 'helped', 'Threaded Index')
        expertise_key = finding_key('expertise', profile, canonical)
        assert admin_client.post('/api/admin/expertise', json={
            'profile_id': profile, 'concept_id': canonical}).status_code == 200
        assert admin_client.put(f'/api/ml/findings/{expertise_key}/decision', json={'mode': 'automatic'}).status_code == 200
        if not processed:
            from sqlalchemy import text
            from app.db import SessionLocal
            with SessionLocal() as db:
                assert db.execute(text("""SELECT count(*) FROM ml_evidence WHERE author_id=:author
                    AND json_type(features,'$.identity_routes')='array'"""), {'author': profile}).scalar_one() == 0
        areas = lambda: next((entry['areas'] for entry in asker.get('/api/expertise').json()
                              if entry['label'] == profile_data['label']), [])
        assert 'Threaded Index' in areas()
        # Real Supervisor startup commits a new generation. Observe the public
        # boundary before its first inference or queued replay can repair it.
        from app.db import SessionLocal
        from app.ml import effective, runtime
        from app.ml.worker import Supervisor
        import threading

        parts = records[0]['application_model_version'].split(':')
        models = {role: {'revision': revision} for role, revision in zip(
            ('extractor', 'embeddings', 'syntax'), parts[:3])}
        original_revision = models['extractor']['revision']
        class StopBeforeReplay(Exception):
            pass

        class BeforeReplay:
            process = None
            def check_memory(self):
                raise StopBeforeReplay()
            def close(self):
                pass

        def transition(revision):
            models['extractor']['revision'] = revision
            (tmp_path / 'models.json').write_text(json.dumps({'models': models}))
            with SessionLocal() as db:
                database = Path(db.get_bind().url.database)
            worker = Supervisor(database, tmp_path, threading.Event())
            worker.inference.close()
            worker.inference = BeforeReplay()
            try:
                with pytest.raises(StopBeforeReplay):
                    worker.run('drain')
            finally:
                worker.close()

        transition(original_revision + '-generation-control')
        assert names(asker, 'Threaded Index')['Threaded Index'] == canonical
        with SessionLocal() as db:
            assert 'tindex' not in {term.term for term in effective.terms(db)}
        assert 'Threaded Index' not in areas()
        _profile_work(profile)
        assert 'Threaded Index' in areas()
        assert admin_client.put(f'/api/ml/findings/{expertise_key}/decision', json={'mode': 'pinned'}).status_code == 200
        assert 'Threaded Index' in areas()
        assert admin_client.put(f'/api/ml/findings/{expertise_key}/decision', json={'mode': 'automatic'}).status_code == 200
        assert 'Threaded Index' in areas()
        transition(original_revision)
        _profile_work(profile)
        assert 'Threaded Index' in areas()
        # Explicit canonical-topic choices do not depend on an alias mention
        # route, including contributions with no extraction records yet.
        from app.ml.models import Source
        with SessionLocal() as db:
            source = db.get(Source, ('item', items[0]))
            original_result = source.result
            data = json.loads(source.result)
            if coverage == 'old_structure':
                data['definitions_indexed'] = 1
            else:
                data['conflict_coverage_revision'] = 'previous-conflict-rules'
            source.result = json.dumps(data)
            db.commit()
        assert names(asker, 'Threaded Index')['Threaded Index'] == canonical
        # Older broad invalidation triggers may hold the projection until its
        # queued refresh; the human topic credit survives that recomputation.
        _profile_work(profile)
        assert 'Threaded Index' in areas()
        with SessionLocal() as db:
            db.get(Source, ('item', items[0])).result = original_result
            db.commit()
        _profile_work(profile)
        assert 'Threaded Index' in areas()
        # Manual concept authority remains, but does not pin its automatic alias.
        assert reader.put(f'/api/items/{items[1]}', json={'body': 'The card archive is retired.'}).status_code == 200
        assert names(asker, 'Threaded Index')['Threaded Index'] == canonical
        # Older broad invalidation triggers may hold the projection until its
        # queued refresh; the human topic credit survives that recomputation.
        _profile_work(profile)
        assert 'Threaded Index' in areas()
        assert reader.put(f'/api/items/{items[1]}', json={'body': records[1]['body']}).status_code == 200
        apply(items[1], records[1])
        replay(items, records)
        for _, item in extra:
            apply(item, records[0], cached=processed, empty=not processed)
        _profile_work(profile)
        assert 'Threaded Index' in areas()
        alias_key = finding_key('alias', 'tindex', canonical)
        assert admin_client.put(f'/api/ml/findings/{alias_key}/decision', json={'mode': 'suppressed'}).status_code == 200
        assert names(asker, 'Threaded Index')['Threaded Index'] == canonical
        # Older broad invalidation triggers may hold the projection until its
        # queued refresh; the human topic credit survives that recomputation.
        _profile_work(profile)
        assert 'Threaded Index' in areas()
        assert admin_client.put(f'/api/ml/findings/{alias_key}/decision', json={'mode': 'automatic'}).status_code == 200
        assert admin_client.put(f'/api/ml/findings/{key}/decision', json={'mode': 'automatic'}).status_code == 200
    finally:
        for owner, item in reversed(extra):
            owner.delete(f'/api/items/{item}')
            apply(item, records[0])
        for item, post, record in reversed(list(zip(items, case['posts'], records))):
            clients[post['actor']].delete(f'/api/items/{item}')
            apply(item, record)
