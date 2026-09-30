"""Observed-development application replay; external inference is retained data."""
import copy
import hashlib
import json
from pathlib import Path
import re
from types import SimpleNamespace

import pytest

from ml_synthetic_records import judged, keep_single_source_publication
from test_ml_alias_conflicts import automated, capture_post

FIXTURE = json.loads((Path(__file__).parent / 'fixtures/copular_alias_recorded.json').read_text())
CASES = FIXTURE['cases']
MODELS = {'extractor': {'revision': '72ac19b486cd4557424c8d61114e7530c243e9b0'},
          'embeddings': {'revision': 'fixture-embedding'},
          'syntax': {'revision': '272a31e9d8530d1e075351d30a462d7e80e31da23574f1b274e200f3fff35bf5'}}
OLD_REVISION = 'r4:536d6a7e86fad2dad4c3ce7945fd03a0a5041ce1e6e2e614dfdd521cdae4fbc1'


def capture_recorded(client, post, items):
    if post['kind'] == 'answer':
        response = client.post(f"/api/questions/{items[post['parent']]}/answers", json={'body': post['body']})
        assert response.status_code == 200, response.text
        return response.json()['id']
    return capture_post(client, post)


def analyze(post, monkeypatch, control=None):
    from app.ml import runtime, syntax
    record = post['recorded']
    assert record['matches_retained_result']
    assert post['body'] == record['text']
    assert hashlib.sha256(post['body'].encode()).hexdigest() == record['text_sha256']
    stages = {r['stage']: copy.deepcopy(r) for r in record['stages']}
    assert all(r['body'] == post['body'] for r in stages.values())
    if control == 'no_roles':
        stages['alias_roles']['raw'] = {'alias_definition': []}
    elif control == 'low_role':
        for pair in stages['alias_roles']['raw'].get('alias_definition', []):
            pair['short_name']['confidence'] = 0.0
    elif control == 'wrong_role_span':
        for pair in stages['alias_roles']['raw'].get('alias_definition', []):
            pair['short_name']['start'] += 1
    model = object.__new__(runtime.LocalModels)
    # Parser/extraction mechanics are independent of BGE accuracy.
    model.judge_eligibility = lambda *args: {"version": "synthetic", "margins": {}}
    model.entity_schema, model.relation_schema, model.alias_schema = 'generic_entities', 'relations', 'alias_roles'
    model.manifest = {'models': MODELS}
    model.tokenizer = lambda body, **kwargs: {'offset_mapping': [m.span() for m in re.finditer(r'\S+', body)]}
    model.extractor = SimpleNamespace(extract=lambda body, schema, **kwargs: stages[schema]['raw'])
    model.syntax = lambda body: stages['syntax']
    monkeypatch.setattr(syntax, 'candidates', lambda parsed, **kwargs: syntax.propose(parsed, **kwargs))
    # Reapply today's unscored rules to the retained parser observation. This
    # does not relabel the original model output as new quality evidence.
    monkeypatch.setattr(runtime.relation_syntax, 'serialize', lambda parsed: parsed)
    vector = SimpleNamespace(astype=lambda dtype: SimpleNamespace(tobytes=lambda: b'\0' * 4096))
    model.embedding = SimpleNamespace(encode=lambda body, **kwargs: vector)
    result = model.analyze(post['body'])
    assert result['concepts'] == record['result']['concepts']
    fields = ('head', 'tail', 'predicate', 'score')
    assert [{key: relation[key] for key in fields} for relation in result['relations']] == [
        {key: relation[key] for key in fields} for relation in record['result']['relations']]
    # Embedding inference is outside this replay. Exercise the real empty derived store.
    result['chunks'] = []
    if control == 'no_genuine_concepts':
        result['concepts'], result['relations'] = [], []
    return judged(keep_single_source_publication(result), post['body'])


def apply(item_id, result=None, *, version=None):
    from sqlalchemy import text
    from app.db import SessionLocal
    from app.ml import adapter, policy, runtime
    from app.ml.sources import snapshot
    version = version or runtime.inference_version(MODELS)
    with SessionLocal() as db:
        source = snapshot(db, 'item', item_id)
        if source is not None and result is None:
            cached = adapter.cached_result(db, source, version)
            assert cached is not None
            result, _ = cached
        db.execute(text('UPDATE ml_state SET pipeline_version=:v WHERE id=1'), {'v': version + ':' + policy.VERSION})
        adapter.apply_source(db, 'item', item_id, source, result, version, MODELS['embeddings']['revision'], 1024)
        db.commit()


def search(client, alias):
    response = client.get('/api/search', params={'q': alias})
    assert response.status_code == 200
    return [c['name'] for c in response.json()['concepts']]


@pytest.mark.parametrize('case', CASES, ids=lambda c: c['id'])
def test_recorded_explicit_copular_name_public_cache_and_withdrawal(make_client, monkeypatch, case):
    pair = case['expect']['aliases'][0]
    clients, items, defining = [], [], []
    protected_ids = set()
    try:
        for post in case['posts']:
            client = make_client(); item = capture_recorded(client, post, items)
            clients.append(client); items.append(item)
        for i, post in enumerate(case['posts']):
            result = analyze(post, monkeypatch)
            assert post['recorded']['result']['corroborated_definitions'] == []
            if any(d['full_name']['text'] == pair['canonical'] and d['short_name']['text'] == pair['alias']
                   for d in result['corroborated_definitions']):
                defining.append(i)
                protected_ids.update(c['id'] for c in clients[0].get('/api/search', params={'q': pair['alias']}).json()['concepts'] if c['name'] == pair['alias'])
            apply(items[i], result)
        assert defining
        if protected_ids:
            assert pair['canonical'] not in search(clients[0], pair['alias'])
            assert protected_ids <= {c['id'] for c in clients[0].get('/api/search', params={'q': pair['alias']}).json()['concepts']}
        else:
            assert pair['canonical'] in search(clients[0], pair['alias'])
        before_cache = search(clients[0], pair['alias'])
        for item in items:
            apply(item)
        assert search(clients[0], pair['alias']) == before_cache
        for i in defining:
            assert clients[i].delete(f'/api/items/{items[i]}').status_code == 200
            apply(items[i])
        assert pair['canonical'] not in search(clients[0], pair['alias'])
    finally:
        for client, item in zip(clients, items):
            client.delete(f'/api/items/{item}')
            apply(item)


@pytest.mark.parametrize('control', ['no_roles', 'low_role', 'wrong_role_span', 'no_genuine_concepts', 'question_source'])
def test_new_grammar_cannot_replace_role_score_canonical_or_assertion_gate(make_client, monkeypatch, control):
    from app.db import SessionLocal
    from app.ml.models import Evidence, Finding
    case = CASES[0]
    pair = case['expect']['aliases'][0]
    clients, items = [], []
    try:
        for post in case['posts']:
            client = make_client()
            capture = {**post, 'kind': 'question'} if control == 'question_source' else post
            item = capture_recorded(client, capture, items)
            clients.append(client); items.append(item)
        for item, post in zip(items, case['posts']):
            result = analyze(post, monkeypatch, control)
            if control in ('no_roles', 'wrong_role_span'):
                assert result['corroborated_definitions'] == []
            apply(item, result)
        assert pair['canonical'] not in search(clients[0], pair['alias'])
        if control == 'no_genuine_concepts':
            with SessionLocal() as db:
                assert db.query(Evidence).join(Finding, Finding.key == Evidence.finding_key).filter(Evidence.source_id.in_(items), Finding.kind.in_(['concept', 'mention'])).count() == 0
    finally:
        for client, item in zip(clients, items):
            client.delete(f'/api/items/{item}')
            apply(item)


def test_old_syntax_generation_cache_reprocesses_recorded_outputs(make_client, monkeypatch):
    from app.db import SessionLocal
    from app.ml import adapter, runtime, syntax
    from app.ml.sources import snapshot
    case = CASES[0]
    pair = case['expect']['aliases'][0]
    current = runtime.inference_version(MODELS)
    with monkeypatch.context() as change:
        change.setattr(syntax, 'REVISION', OLD_REVISION)
        old = runtime.inference_version(MODELS)
    assert current != old
    clients, items = [], []
    try:
        for post in case['posts']:
            client = make_client(); item = capture_recorded(client, post, items)
            clients.append(client); items.append(item)
        for item, post in zip(items, case['posts']):
            result = copy.deepcopy(post['recorded']['result']); result['chunks'] = []
            apply(item, result, version=old)
            with SessionLocal() as db:
                assert adapter.cached_result(db, snapshot(db, 'item', item), current) is None
        assert pair['canonical'] not in search(clients[0], pair['alias'])
        for item, post in zip(items, case['posts']):
            apply(item, analyze(post, monkeypatch))
        assert pair['canonical'] in search(clients[0], pair['alias'])
        for item in items:
            apply(item)
        assert pair['canonical'] in search(clients[0], pair['alias'])
    finally:
        for client, item in zip(clients, items):
            client.delete(f'/api/items/{item}')
            apply(item)
