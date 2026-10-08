"""Public published-term provenance mechanics; supplied records, no inference."""
import json
import uuid

import pytest

from ml_synthetic_records import current_synthetic_metadata, current_synthetic_result, select_synthetic_pipeline
from test_ml_alias_generation import current_alias
from test_ml_automation import automation, _apply, _apply_current_definition, _capture, _confirm_topic, _edge, _profile_work, _tags


def _retire(item):
    from app.db import SessionLocal
    from app.ml.models import Source

    with SessionLocal() as db:
        source = db.get(Source, ('item', item))
        result = json.loads(source.result)
        result.pop('alias_scope_contract')
        source.result = json.dumps(result)
        db.commit()


def _decision(admin, key, mode):
    response = admin.put(f'/api/ml/findings/{key}/decision', json={'mode': mode})
    assert response.status_code == 200, response.text


def _records(item, entities=(), relations=()):
    from app.db import SessionLocal
    from app.ml import adapter, syntax
    from app.ml.sources import snapshot

    with SessionLocal() as db:
        source = snapshot(db, 'item', item)
        metadata = current_synthetic_metadata()
        select_synthetic_pipeline(db, metadata)
        spans = [{'name': name, 'start': source.text.index(name),
                  'end': source.text.index(name) + len(name), 'score': score, 'label': 'technology'}
                 for name, score in entities]
        records = []
        for sentence, head, tail, score, polarity in relations:
            offset = source.text.index(sentence)
            endpoints = [{'name': name, 'start': offset + sentence.index(name),
                          'end': offset + sentence.index(name) + len(name), 'score': score,
                          'label': 'relation endpoint'} for name in (head, tail)]
            records.append({'head': endpoints[0], 'tail': endpoints[1], 'predicate': 'uses',
                            'start': offset, 'end': offset + len(sentence), 'score': score,
                            'polarity': polarity, 'literal_support': True})
        result = current_synthetic_result({'concepts': spans, 'relations': records, 'chunks': [],
                    'corroborated_definitions': [], 'conflict_definitions': [],
                    'conflict_coverage_revision': syntax.CONFLICT_REVISION}, source.text)
        adapter.apply_source(db, 'item', item, source, result, *metadata)
        db.commit()


@pytest.mark.parametrize('entities', [True, False])
def test_published_alias_controls_existing_graph_and_tags_before_replay(current_alias, make_client, admin_client, entities):
    a = current_alias
    tail_name = 'Receiver ' + uuid.uuid4().hex[:8]
    tail = admin_client.post('/api/admin/concepts', json={'name': tail_name}).json()['id']
    created = []
    try:
        for when in ('morning', 'evening'):
            owner = make_client()
            sentence = f'{a["alias"]} uses {tail_name} during {when} checks.'
            item = _capture(owner, sentence)
            created.append((owner, item))
            _records(item, [(a['alias'], .995), (tail_name, .995)] if entities else (),
                     [(sentence, a['alias'], tail_name, .90, 'positive')])
        before = _edge(admin_client, a['cid'], tail)
        assert before['state'] == 'active' and before['support_count'] == 2
        _retire(a['definition'])
        assert a['name'] not in a['public']()
        assert a['cid'] not in _tags(a['reader'], a['dependent']).values()
        assert _edge(admin_client, a['cid'], tail) is None
        _decision(admin_client, a['key'], 'pinned')
        assert a['public']().get(a['name']) == a['cid']
        assert _tags(a['reader'], a['dependent']).get(a['name']) == a['cid']
        assert _edge(admin_client, a['cid'], tail)['link_id'] == before['link_id']
        # Replay while pinned must retain a dependency; release cannot convert
        # that managed spelling into unconditional entity or relation evidence.
        _apply(a['dependent'], cached=True)
        for _, item in created:
            _apply(item, cached=True)
        _decision(admin_client, a['key'], 'automatic')
        assert a['cid'] not in _tags(a['reader'], a['dependent']).values()
        assert _edge(admin_client, a['cid'], tail) is None
        _decision(admin_client, a['key'], 'pinned')
        owner, item = created[0]
        assert owner.put(f'/api/items/{item}', json={'body': 'The link assertion was removed.'}).status_code == 200
        remaining = _edge(admin_client, a['cid'], tail)
        assert remaining is None or remaining['state'] != 'active'
        _decision(admin_client, a['key'], 'suppressed')
        assert _edge(admin_client, a['cid'], tail) is None
        assert a['cid'] not in _tags(a['reader'], a['dependent']).values()
    finally:
        _decision(admin_client, a['key'], 'automatic')
        for owner, item in created:
            owner.delete(f'/api/items/{item}')
            _apply(item)


@pytest.mark.parametrize('score,active', [(0.995, True), (0.95, False)])
def test_native_mention_keeps_original_score_and_policy_when_alias_alternative_expires(
        current_alias, make_client, score, active):
    from app.db import SessionLocal
    from app.ml import effective, policy
    from app.ml.models import Finding
    from app.ml.sources import finding_key

    a = current_alias
    owner = make_client()
    item = _capture(owner, f'{a["name"]} supplies the clock signal; {a["alias"]} supplied the audit log.')
    key = finding_key('mention', 'item', item, a['cid'])
    try:
        _records(item, [(a['name'], score), (a['alias'], .999)])
        for replay in (False, True):
            if replay:
                _apply(item, cached=True)
            with SessionLocal() as db:
                rows = effective.evidence_rows(db, key)
                assert sorted(row['raw_score'] for row in rows) == [score, .999]
                assert policy.independent_support(rows)[0] == 1
        _retire(a['definition'])
        with SessionLocal() as db:
            rows = effective.evidence_rows(db, key)
            assert [row['raw_score'] for row in rows] == [score]
            assert bool(db.query(Finding.key).filter(Finding.key == key, effective.enabled(db, 'mention')).first()) == active
        # Canonical literal vocabulary remains usable independently.
        assert _tags(owner, item).get(a['name']) == a['cid']
    finally:
        owner.delete(f'/api/items/{item}')
        _apply(item)


def test_native_relationship_veto_survives_stronger_published_alias_alternative(current_alias, make_client, admin_client):
    a = current_alias
    tail_name = 'Veto receiver ' + uuid.uuid4().hex[:8]
    tail = admin_client.post('/api/admin/concepts', json={'name': tail_name}).json()['id']
    created = []
    try:
        for time in ('morning', 'evening'):
            owner = make_client()
            sentence = f'{a["name"]} uses {tail_name} during {time} checks.'
            item = _capture(owner, sentence)
            created.append((owner, item))
            _records(item, relations=[(sentence, a['name'], tail_name, .9, 'positive')])
        owner = make_client()
        native = f'{a["name"]} does not use {tail_name}.'
        routed = f'{a["alias"]} does not use {tail_name}.'
        item = _capture(owner, native + ' ' + routed)
        created.append((owner, item))
        _records(item, relations=[(native, a['name'], tail_name, .76, 'negative'),
                                 (routed, a['alias'], tail_name, .99, 'negative')])
        _apply(item, cached=True)
        before = _edge(admin_client, a['cid'], tail)
        assert before['conflicts']
        _retire(a['definition'])
        edge = _edge(admin_client, a['cid'], tail)
        assert edge['state'] == 'held' and edge['conflicts'] and edge['support_count'] == before['support_count']
        detail = admin_client.get(f'/api/graph/links/{edge["link_id"]}/evidence').json()
        claim = next(claim for claim in detail['claims'] if claim['predicate'] == 'uses')
        assert [row['quote'] for row in claim['sources'] if row['source_id'] == item] == [native]
    finally:
        for owner, item in created:
            owner.delete(f'/api/items/{item}')
            _apply(item)


@pytest.fixture
def alias_expert(current_alias, make_client):
    a = current_alias
    expert, asker, reader = [make_client(account=False) for _ in range(3)]
    definition_owner = make_client()
    suffix = uuid.uuid4().hex[:8]
    username = 'termexpert' + suffix
    for client, name in ((expert, username), (asker, 'asker' + suffix), (reader, 'reader' + suffix)):
        assert client.post('/api/auth/signup', json={'username': name, 'password': 'a-good-password'}).status_code == 200
    second = 'Secondary' + a['alias']
    definition = _capture(definition_owner, f'{second} denotes {a["name"]} in the alternative register.')
    _apply_current_definition(definition, a['name'], second)
    profile = expert.get('/api/profile').json()['id']
    note = _capture(expert, f'{a["alias"]} and {second} need a fresh checksum before settlement replay.')
    last = _capture(expert, f'Inspect the {a["alias"]} and {second} manifests before exporting historical receipts.')
    question = asker.post('/api/questions', json={'body': 'Which checkpoint is recoverable?'}).json()['id']
    answer = expert.post(f'/api/questions/{question}/answers', json={
        'body': f'Use the {a["alias"]} or {second} checkpoint whose receipt count matches the journal.'}).json()['id']
    for item in (note, last, answer):
        _apply(item, [a['alias'], second])
    assert reader.post(f'/api/items/{note}/helped').status_code == 200
    assert reader.post(f'/api/items/{last}/endorse').status_code == 200
    assert asker.post(f'/api/questions/{question}/accept', json={'answer_id': answer}).status_code == 200
    _confirm_topic(asker, answer, 'accepted', a['name'])
    _confirm_topic(reader, note, 'helped', a['name'])
    _confirm_topic(reader, last, 'helped', a['name'])
    _profile_work(profile)

    def public():
        return next((row['areas'] for row in reader.get('/api/expertise').json() if row['label'] == username), [])

    assert public() == [a['name']]
    yield {'public': public, 'profile': profile, 'items': [note, last, answer], 'second_definition': definition,
           'expert': expert, 'question': question, 'asker': asker, 'second': second}
    # Delete the owning question first so its accepted answer can be removed.
    asker.delete(f'/api/items/{question}')
    for item in (answer, note, last):
        expert.delete(f'/api/items/{item}')
        _apply(item)
    definition_owner.delete(f'/api/items/{definition}')
    _apply(definition)


def test_explicit_canonical_topic_credit_survives_unrelated_alias_withdrawal(current_alias, alias_expert, admin_client):
    from app.db import SessionLocal
    from app.ml import effective
    from app.ml.sources import finding_key

    a, expert = current_alias, alias_expert
    key = finding_key('expertise', expert['profile'], a['cid'])
    with SessionLocal() as db:
        evidence = effective.evidence_rows(db, key)
        contributions = evidence[0]['topic_confirmations']
        assert {row['item_id'] for row in contributions} == set(expert['items'])
        assert all(row['context_token'] and row['concept_id'] == a['cid'] for row in contributions)
        before = {field: evidence[0][field] for field in ('actors', 'originals', 'accepted_answers', 'topic_confirmations')}
    _retire(a['definition'])
    assert expert['public']() == [a['name']]
    with SessionLocal() as db:
        row = effective.evidence_rows(db, key)[0]
        assert {field: row[field] for field in before} == before
    _retire(expert['second_definition'])
    assert expert['public']() == [a['name']]
    _decision(admin_client, a['key'], 'pinned')
    assert expert['public']() == [a['name']]
    _decision(admin_client, a['key'], 'automatic')
    assert expert['public']() == [a['name']]
    _apply_current_definition(expert['second_definition'], a['name'], expert['second'])
    a['renew']()
    _profile_work(expert['profile'])
    assert expert['public']() == [a['name']]


@pytest.mark.parametrize('mutation', ['revision', 'hash', 'visibility', 'author'])
def test_expertise_rejects_changed_credited_item_even_without_profile_invalidation(current_alias, alias_expert, mutation):
    from app.db import SessionLocal
    from app.models import KnowledgeItem

    expert = alias_expert
    with SessionLocal() as db:
        item = db.get(KnowledgeItem, expert['items'][0])
        if mutation == 'revision':
            item.evidence_revision += 1
        elif mutation == 'hash':
            item.normalized_hash = 'retired-item-content'
        elif mutation == 'visibility':
            item.visibility = 'private'
        else:
            item.author_profile_id = expert['asker'].get('/api/profile').json()['id']
        db.commit()
    assert expert['public']() == []


def test_one_item_dependency_query_work_does_not_grow_with_unrelated_history(current_alias, monkeypatch):
    from app.db import SessionLocal
    from app.ml import effective
    from app.ml.models import Evidence, Finding, Source
    from app.ml.sources import finding_key
    from app.models import utcnow

    a = current_alias
    with SessionLocal() as db:
        # Two thousand active managed-term mentions are deliberately plausible;
        # the old whole-kind Python scan evaluated every one for a single item.
        template_key = finding_key('mention', 'item', a['dependent'], a['cid'])
        template = db.query(Evidence).filter_by(finding_key=template_key).first()
        calls = []
        original = effective._policy_active
        monkeypatch.setattr(effective, '_policy_active', lambda kind, encoded: (calls.append(json.loads(encoded)), original(kind, encoded))[1])
        connection = db.connection()
        connection.info.pop('ml_policy_active_registered', None)
        effective._register_policy(db)
        raw = connection.connection.driver_connection

        def measured():
            calls.clear()
            ticks = []
            raw.set_progress_handler(lambda: (ticks.append(1), 0)[1], 100)
            try:
                assert effective.source_tags(db, 'item', a['dependent'], set()) == {a['cid']}
            finally:
                raw.set_progress_handler(None, 0)
            assert len(calls) == 1, len(calls)
            assert {row['group_key'] for row in calls[0]} == {template.group_key}
            return len(ticks)

        small = measured()
        unrelated_evidence = []
        for index in range(2000):
            key = 'unrelated-term-' + str(index)
            source_id = 'unrelated-source-' + str(index)
            db.add(Source(kind='item', id=source_id, content_hash=template.source_hash, valid=True,
                          model_version=template.model_version, result=db.get(Source, ('item', a['dependent'])).result,
                          updated_at=utcnow()))
            db.add(Finding(key=key, kind='mention', canonical_id=a['cid'], state='active', score=.995,
                           payload=json.dumps({'source_kind': 'item', 'source_id': source_id, 'concept_id': a['cid']}),
                           policy_version='unused', created_at=utcnow(), updated_at=utcnow()))
            unrelated_evidence.append(Evidence(key=key, finding_key=key, source_kind='item', source_id=source_id,
                            source_hash=template.source_hash, model_version=template.model_version,
                            group_key=key, author_id=template.author_id, start=0, end=2, raw_score=.995,
                            polarity='positive', features=template.features))
        db.flush()
        db.add_all(unrelated_evidence)
        db.flush()
        large = measured()
        assert large <= small + 20, (small, large)
        db.rollback()
        raw.create_function("ml_policy_active", 2, original)


@pytest.mark.parametrize('mutation', ['source_model', 'evidence_model', 'hash', 'coherent_retired'])
def test_pinned_alias_does_not_authorize_incoherent_dependent_observation(current_alias, admin_client, mutation):
    from sqlalchemy import text
    from app.db import SessionLocal
    from app.ml import effective, policy
    from app.ml.models import Evidence, Source
    from app.ml.sources import finding_key

    a = current_alias
    _decision(admin_client, a['key'], 'pinned')
    key = finding_key('mention', 'item', a['dependent'], a['cid'])
    try:
        with SessionLocal() as db:
            source = db.get(Source, ('item', a['dependent']))
            old_pipeline = db.execute(text('SELECT pipeline_version FROM ml_state WHERE id=1')).scalar_one()
            retired = source.model_version.split(':')
            retired[3] = 'retired-extraction-contract'
            retired = ':'.join(retired)
            if mutation in {'source_model', 'coherent_retired'}:
                source.model_version = retired
            if mutation in {'evidence_model', 'coherent_retired'}:
                for row in db.query(Evidence).filter_by(source_kind='item', source_id=a['dependent']):
                    row.model_version = retired
            if mutation == 'hash':
                source.content_hash = 'retired-content'
            if mutation == 'coherent_retired':
                db.execute(text('UPDATE ml_state SET pipeline_version=:version WHERE id=1'),
                           {'version': retired + ':' + policy.VERSION})
            db.commit()
            assert effective.evidence_rows(db, key) == []
        # The literal alias remains manually authorized, while its ML mention
        # cannot provide evidence for other derived findings from stale bytes.
        assert a['public']().get(a['name']) == a['cid']
    finally:
        with SessionLocal() as db:
            db.execute(text('UPDATE ml_state SET pipeline_version=:version WHERE id=1'), {'version': old_pipeline})
            db.commit()
        _decision(admin_client, a['key'], 'automatic')
        _apply(a['dependent'], [a['alias']])


@pytest.mark.parametrize('field,value', [('alias_key', 'mismatched-spelling'), ('concept_id', 'mismatched-concept')])
def test_pin_requires_exact_current_published_mapping(current_alias, admin_client, field, value):
    from app.db import SessionLocal
    from app.ml.models import Finding

    a = current_alias
    _decision(admin_client, a['key'], 'pinned')
    try:
        with SessionLocal() as db:
            finding = db.get(Finding, a['key'])
            original = finding.payload
            payload = json.loads(original)
            payload[field] = value
            finding.payload = json.dumps(payload)
            db.commit()
        assert a['name'] not in a['public']()
        assert a['cid'] not in _tags(a['reader'], a['dependent']).values()
    finally:
        with SessionLocal() as db:
            db.get(Finding, a['key']).payload = original
            db.commit()
        _decision(admin_client, a['key'], 'automatic')
