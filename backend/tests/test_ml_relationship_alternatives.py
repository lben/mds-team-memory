"""Public relationship provenance regressions; fixed records, no inference."""
import copy
import json

import pytest

from test_ml_alias_conflicts import automated
from test_ml_identity_routing import capture, embedding_generation_isolation
from test_ml_inverse_alias import CASES, apply, replay, terms


@pytest.fixture
def inverse_pair(make_client, admin_client):
    from app.ml.sources import digest, finding_key

    sources = []
    pinned = []
    for case_id, old_short, old_full, short, full in (
        ('acronym_dev_002', 'PGM', 'Packet Gap Monitor', 'SGM', 'Socket Gap Monitor'),
        ('acronym_dev_021', 'SSR', 'Source Sync Receipt', 'RSR', 'Record Sync Receipt'),
    ):
        original = next(saved for saved in CASES if saved['case']['id'] == case_id)
        # Equal-length substitutions isolate identities from other tests that
        # intentionally retain competing canonical records. Scores/spans stay
        # fixed; these are synthetic regression names, not new observations.
        encoded = json.dumps(original).replace(old_full, full).replace(old_short, short)
        encoded = encoded.replace(old_full.lower(), full.lower()).replace(old_short.lower(), short.lower())
        for record in original['records']:
            body = record['body'].replace(old_full, full).replace(old_short, short)
            encoded = encoded.replace(digest(record['body']), digest(body))
        saved = json.loads(encoded)
        case, records = saved['case'], saved['records']
        clients, items = capture(make_client, case)
        for item, record in zip(items, records):
            apply(item, record)
        replay(items, records)
        replay(items, records)
        cid = terms()[short.lower()][0]
        assert terms()[full.lower()] == (cid, False)
        key = finding_key('concept', short.lower())
        assert admin_client.put(f'/api/ml/findings/{key}/decision', json={'mode': 'pinned'}).status_code == 200
        pinned.append(key)
        witness = next(i for i, record in enumerate(records) if record['result']['corroborated_definitions'])
        sources.append({'id': cid, 'short': short, 'full': full, 'records': records,
                        'items': items, 'case': case, 'clients': clients, 'witness': witness})
    yield sources
    for key in pinned:
        admin_client.put(f'/api/ml/findings/{key}/decision', json={'mode': 'automatic'})
    for source in reversed(sources):
        for item, post, record in reversed(list(zip(source['items'], source['case']['posts'], source['records']))):
            source['clients'][post['actor']].delete(f'/api/items/{item}')
            apply(item, record)


@pytest.fixture
def relationship_source(make_client, inverse_pair):
    from app.db import SessionLocal
    from app.ml import adapter, runtime, syntax
    from app.ml.sources import snapshot

    record = inverse_pair[0]['records'][0]
    parts = record['original_metadata']['model_version'].split(':')
    models = {role: {'revision': revision} for role, revision in zip(('extractor', 'embeddings', 'syntax'), parts[:3])}
    metadata = runtime.inference_version(models), record['original_metadata']['embedding_version'], 1024
    created = []

    def update(item, result):
        with SessionLocal() as db:
            source = snapshot(db, 'item', item)
            adapter.apply_source(db, 'item', item, source, copy.deepcopy(result) if source else None, *metadata)
            db.commit()

    def create(sentences, polarity='positive'):
        # Each tuple supplies one literal sentence, its head/tail spellings and
        # the score observed at the expensive-inference boundary.
        body = ' '.join(sentence for sentence, _, _, _ in sentences)
        owner = make_client()
        item = owner.post('/api/capture', data={'body': body}).json()['item']['id']
        created.append((owner, item))
        relations, offset = [], 0
        for sentence, head, tail, score in sentences:
            endpoints = [{'name': name, 'start': offset + sentence.index(name),
                          'end': offset + sentence.index(name) + len(name),
                          'score': score, 'label': 'relation endpoint'} for name in (head, tail)]
            relations.append({'head': endpoints[0], 'tail': endpoints[1], 'predicate': 'uses',
                              'start': offset, 'end': offset + len(sentence), 'score': score,
                              'polarity': polarity, 'literal_support': True})
            offset += len(sentence) + 1
        result = {'concepts': [], 'relations': relations, 'chunks': [], 'corroborated_definitions': [],
                  'conflict_definitions': [], 'conflict_coverage_revision': syntax.CONFLICT_REVISION}
        update(item, result)
        return item, lambda: update(item, result)

    yield create
    for owner, item in reversed(created):
        owner.delete(f'/api/items/{item}')
        update(item, None)


def _edge(client, pair):
    edges = [edge for edge in client.get('/api/graph/global').json()['edges']
             if {edge['source'], edge['target']} == {source['id'] for source in pair}]
    assert len(edges) == 1, edges
    return edges[0]


def _remove_witness(source):
    index = source['witness']
    owner = source['clients'][source['case']['posts'][index]['actor']]
    assert owner.put(f"/api/items/{source['items'][index]}", json={
        'body': 'The obsolete definition was removed.'}).status_code == 200


def test_native_contradiction_remains_after_alias_witness_edit(
        admin_client, inverse_pair, relationship_source):
    from app.db import SessionLocal
    from app.ml.models import Evidence, Finding
    from app.ml.sources import finding_key

    for time in ('morning', 'evening'):
        relationship_source([(f'SGM uses RSR during {time} checks.', 'SGM', 'RSR', .9)])
    negative, replay_negative = relationship_source([
        ('SGM does not use RSR.', 'SGM', 'RSR', .76),
        ('Socket Gap Monitor does not use RSR.', 'Socket Gap Monitor', 'RSR', .99),
    ], 'negative')
    for replayed in (False, True):
        if replayed:
            replay_negative()
        before = _edge(admin_client, inverse_pair)
        assert before['state'] == 'held' and before['conflicts']
        with SessionLocal() as db:
            rows = db.query(Evidence).join(Finding, Finding.key == Evidence.finding_key).filter(
                Evidence.source_id == negative, Finding.kind == 'relationship').all()
            assert len(rows) == 2
            native = next(row for row in rows if not json.loads(row.features).get('identity_routes'))
            assert native.raw_score == .76
            assert native.key == finding_key('evidence', native.finding_key, 'item', negative, 'negative')
    _remove_witness(inverse_pair[0])
    after = _edge(admin_client, inverse_pair)
    assert after['state'] == 'held' and after['conflicts']
    detail = admin_client.get(f"/api/graph/links/{after['link_id']}/evidence").json()
    claim = next(claim for claim in detail['claims'] if claim['predicate'] == 'uses')
    remaining = [source['quote'] for source in claim['sources'] if source['source_id'] == negative]
    assert remaining == ['SGM does not use RSR.']


def test_conditional_alternatives_withdraw_independently_without_inflating_support(
        admin_client, inverse_pair, relationship_source):
    item, replay_item = relationship_source([
        ('Socket Gap Monitor uses RSR.', 'Socket Gap Monitor', 'RSR', .76),
        ('SGM uses Record Sync Receipt.', 'SGM', 'Record Sync Receipt', .99),
        # Same dependency set keeps its strongest literal record.
        ('SGM uses Record Sync Receipt during validation.', 'SGM', 'Record Sync Receipt', .80),
    ])
    for replayed in (False, True):
        if replayed:
            replay_item()
        edge = _edge(admin_client, inverse_pair)
        assert edge['state'] == 'held' and edge['support_count'] == 1
        detail = admin_client.get(f"/api/graph/links/{edge['link_id']}/evidence").json()
        claim = next(claim for claim in detail['claims'] if claim['predicate'] == 'uses')
        assert claim['support_count'] == 1
        assert sorted(source['quote'] for source in claim['sources'] if source['source_id'] == item) == [
            'SGM uses Record Sync Receipt.', 'Socket Gap Monitor uses RSR.']
    relationship_source([('SGM uses RSR during an independent audit.', 'SGM', 'RSR', .9)])
    edge = _edge(admin_client, inverse_pair)
    assert edge['state'] == 'active' and edge['support_count'] == 2
    # Removing the stronger alternative's tail route leaves the other source
    # contribution intact through its separately witnessed head route.
    _remove_witness(inverse_pair[1])
    edge = _edge(admin_client, inverse_pair)
    assert edge['state'] == 'active' and edge['support_count'] == 2
    detail = admin_client.get(f"/api/graph/links/{edge['link_id']}/evidence").json()
    claim = next(claim for claim in detail['claims'] if claim['predicate'] == 'uses')
    assert [source['quote'] for source in claim['sources'] if source['source_id'] == item] == [
        'Socket Gap Monitor uses RSR.']
    _remove_witness(inverse_pair[0])
    edge = _edge(admin_client, inverse_pair)
    assert edge['state'] == 'held' and edge['support_count'] == 1
