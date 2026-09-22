"""Relationship syntax generations with explicit new synthetic records, no models."""
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

import pytest

from test_ml_automation import automation


MODELS = {role: {'revision': 'fixture-' + role} for role in ('extractor', 'embeddings', 'syntax')}


@pytest.fixture
def current_relationship(make_client, admin_client):
    from sqlalchemy import text
    from app.db import SessionLocal
    from app.ml import adapter, policy, relation_syntax, runtime, syntax
    from app.ml.sources import snapshot

    suffix = uuid.uuid4().hex[:8]
    names = ['Generation Pump ' + suffix, 'Generation Battery ' + suffix]
    cids = [admin_client.post('/api/admin/concepts', json={'name': name}).json()['id'] for name in names]
    owners = [make_client(), make_client()]
    bodies = [f'During the staged load test, {names[0]} uses {names[1]}; its current trace is attached for analysis.',
              f'Our portable installation differs from the lab layout. {names[0]} uses {names[1]} to run the field logger.']
    items = [owner.post('/api/capture', data={'body': body}).json()['item']['id']
             for owner, body in zip(owners, bodies)]

    def metadata():
        return runtime.inference_version(MODELS), MODELS['embeddings']['revision'], 1024

    def apply_new(index):
        # Explicitly authored current records at the inference boundary. No
        # stored or golden model observation is recertified by this helper.
        with SessionLocal() as db:
            source = snapshot(db, 'item', items[index])
            version = metadata()
            db.execute(text('UPDATE ml_state SET pipeline_version=:version WHERE id=1'),
                       {'version': version[0] + ':' + policy.VERSION})
            spans = [{'name': name, 'start': source.text.index(name),
                      'end': source.text.index(name) + len(name), 'score': .91,
                      'label': 'relation endpoint'} for name in names]
            result = {'concepts': [], 'chunks': [], 'corroborated_definitions': [], 'conflict_definitions': [],
                      'conflict_coverage_revision': syntax.CONFLICT_REVISION,
                      'relation_guard_revision': relation_syntax.REVISION,
                      'relations': [{'head': spans[0], 'tail': spans[1], 'predicate': 'uses',
                                     'start': 0, 'end': len(source.text), 'score': .91,
                                     'polarity': 'positive', 'literal_support': True,
                                     'relation_guard_revision': relation_syntax.REVISION}]}
            adapter.apply_source(db, 'item', source.id, source, result, *version)
            db.commit()

    def edge():
        return next((edge for edge in admin_client.get('/api/graph/global').json()['edges']
                     if {edge['source'], edge['target']} == set(cids)), None)

    for index in range(2):
        apply_new(index)
    assert edge()['label'] == 'uses' and edge()['state'] == 'active'
    yield {'items': items, 'apply_new': apply_new, 'metadata': metadata, 'edge': edge}
    for owner, item in zip(owners, items):
        owner.delete(f'/api/items/{item}')
        with SessionLocal() as db:
            adapter.apply_source(db, 'item', item, None, None, *metadata())
            db.commit()


def _no_automatic_assertion(edge):
    return edge is None or edge['origin'] == 'manual' or edge['label'] != 'uses'


@pytest.mark.parametrize('missing', ['source', 'record', 'old_source', 'old_record'])
def test_obsolete_markers_withdraw_before_worker_and_miss_same_version_cache(current_relationship, missing):
    from app.db import SessionLocal
    from app.ml import adapter
    from app.ml.models import Evidence, Finding, Source
    from app.ml.sources import snapshot

    saved_version = current_relationship['metadata']()[0]
    with SessionLocal() as db:
        for item in current_relationship['items']:
            stored = db.get(Source, ('item', item))
            result = json.loads(stored.result)
            if missing in {'source', 'old_source'}:
                if missing == 'source':
                    result.pop('relation_guard_revision', None)
                else:
                    result['relation_guard_revision'] = 'retired-guard'
            else:
                for record in result['relations']:
                    if missing == 'record':
                        record.pop('relation_guard_revision', None)
                    else:
                        record['relation_guard_revision'] = 'retired-guard'
                for evidence in db.query(Evidence).join(Finding, Finding.key == Evidence.finding_key).filter(
                        Evidence.source_id == item, Finding.kind == 'relationship'):
                    features = json.loads(evidence.features)
                    if missing == 'record':
                        features.pop('relation_guard_revision', None)
                    else:
                        features['relation_guard_revision'] = 'retired-guard'
                    evidence.features = json.dumps(features)
            stored.result = json.dumps(result)
        db.commit()
    assert _no_automatic_assertion(current_relationship['edge']())
    with SessionLocal() as db:
        for item in current_relationship['items']:
            assert db.get(Source, ('item', item)).model_version == saved_version
            assert adapter.cached_result(db, snapshot(db, 'item', item), saved_version) is None
    for index in range(2):
        current_relationship['apply_new'](index)
    assert current_relationship['edge']()['state'] == 'active'
    with SessionLocal() as db:
        item = current_relationship['items'][0]
        result, version = adapter.cached_result(db, snapshot(db, 'item', item), saved_version)
        adapter.apply_source(db, 'item', item, snapshot(db, 'item', item), result, *version)
        db.commit()
    assert current_relationship['edge']()['state'] == 'active'


@pytest.mark.parametrize('mismatch', ['source', 'evidence', 'pipeline', 'retired_coherent'])
def test_relationship_reads_require_coherent_model_generation(current_relationship, mismatch):
    from sqlalchemy import text
    from app.db import SessionLocal
    from app.ml.models import Evidence, Finding, Source

    with SessionLocal() as db:
        if mismatch == 'pipeline':
            db.execute(text("UPDATE ml_state SET pipeline_version='other-generation' WHERE id=1"))
        else:
            retired = current_relationship['metadata']()[0].split(':')
            retired[3] = 'retired-extraction-contract'
            retired = ':'.join(retired)
            if mismatch == 'retired_coherent':
                from app.ml import policy

                db.execute(text('UPDATE ml_state SET pipeline_version=:version WHERE id=1'),
                           {'version': retired + ':' + policy.VERSION})
            for item in current_relationship['items']:
                if mismatch in {'source', 'retired_coherent'}:
                    db.get(Source, ('item', item)).model_version = retired if mismatch == 'retired_coherent' else 'other-generation'
                if mismatch in {'evidence', 'retired_coherent'}:
                    for evidence in db.query(Evidence).join(Finding, Finding.key == Evidence.finding_key).filter(
                            Evidence.source_id == item, Finding.kind == 'relationship'):
                        evidence.model_version = retired if mismatch == 'retired_coherent' else 'other-generation'
        db.commit()
    assert _no_automatic_assertion(current_relationship['edge']())


def test_same_policy_relation_rule_change_hides_before_worker_preserves_pin_and_regenerates(
        current_relationship, admin_client, monkeypatch):
    from app.db import SessionLocal
    from app.ml import adapter, policy, relation_syntax, runtime
    from app.ml.sources import snapshot

    original = current_relationship['edge']()
    version = current_relationship['metadata']()[0]
    policy_version = policy.VERSION
    with monkeypatch.context() as rules:
        rules.setattr(relation_syntax, 'REVISION', relation_syntax.REVISION + '-next')
        assert policy.VERSION == policy_version
        assert runtime.inference_version(MODELS) != version
        assert _no_automatic_assertion(current_relationship['edge']())
        with SessionLocal() as db:
            for item in current_relationship['items']:
                assert adapter.cached_result(db, snapshot(db, 'item', item), version) is None
        key = original['finding_key']
        assert admin_client.put(f'/api/ml/findings/{key}/decision', json={'mode': 'pinned'}).status_code == 200
        pinned = current_relationship['edge']()
        assert pinned['state'] == 'active' and pinned['origin'] == 'manual' and pinned['label'] == 'uses'
        assert admin_client.put(f'/api/ml/findings/{key}/decision', json={'mode': 'automatic'}).status_code == 200
        assert _no_automatic_assertion(current_relationship['edge']())
        for index in range(2):
            current_relationship['apply_new'](index)
        renewed = current_relationship['edge']()
        assert renewed['link_id'] == original['link_id'] and renewed['state'] == 'active'
    assert _no_automatic_assertion(current_relationship['edge']())
    for index in range(2):
        current_relationship['apply_new'](index)
    assert current_relationship['edge']()['state'] == 'active'


def _migrate(database, direction, target):
    backend = Path(__file__).resolve().parents[1]
    environment = {**os.environ, 'MDS_DATA_DIR': str(database.parent),
                   'MDS_DATABASE_URL': f'sqlite:///{database}'}
    result = subprocess.run([sys.executable, '-m', 'alembic', '-c', str(backend / 'alembic.ini'),
                             direction, target], env=environment, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize('direction,target', [('upgrade', '0014'), ('downgrade', '0013'), ('downgrade', '0010')])
def test_migration_withdraws_old_typed_evidence_for_legacy_readers_and_keeps_pins(
        app_modules, tmp_path, direction, target):
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session
    from app.ml.sources import finding_key
    import ml_legacy_readers as legacy

    database = tmp_path / 'relation-migration.sqlite3'
    _migrate(database, 'upgrade', '0013' if direction == 'upgrade' else '0014')
    engine = create_engine(f'sqlite:///{database}')
    try:
        with Session(engine) as db:
            for owner in ('author0', 'author1', 'automatic', 'pinned'):
                legacy.insert(db, 'accounts', id=owner, username=owner)
                legacy.insert(db, 'profiles', id=owner, account_id=owner, display_name=owner)
            for mode in ('automatic', 'pinned'):
                left, right = mode + '-left', mode + '-right'
                for cid in (left, right):
                    legacy.insert(db, 'concepts', id=cid)
                    legacy.insert(db, 'concept_terms', concept_id=cid, term=cid, display=cid, is_canonical=1)
                legacy.insert(db, 'relationships', id=mode, src_kind='concept', src_id=left, dst_kind='concept', dst_id=right,
                              relationship_type_id='00000000000000000000000000000001')
                payload = {'src_id': left, 'dst_id': right, 'predicate': 'uses'}
                legacy.finding(db, mode, 'relationship', mode, payload, score=.95)
                for index in range(2):
                    item = f'{mode}-{index}'
                    body = f'{left} uses {right} at station {index}.'
                    legacy.insert(db, 'knowledge_items', id=item, body=body, author_profile_id=f'author{index}')
                    legacy.evidence(db, item, mode, 'item', item, f'author{index}', body=body, score=.95,
                                    features={'literal_support': True, 'assertion_allowed': True})
                mapping = legacy.insert(db, 'expertise_mappings', profile_id=mode, concept_id=left)
                expertise_key = mode + '-expertise'
                expertise_payload = {'profile_id': mode, 'concept_id': left}
                legacy.finding(db, expertise_key, 'expertise', mapping, expertise_payload)
                legacy.evidence(db, expertise_key, expertise_key, 'profile', mode, mode)
                if mode == 'pinned':
                    legacy.pin(db, finding_key('relationship_pair', *sorted((left, right))), 'relationship_pair', payload)
                    legacy.pin(db, expertise_key, 'expertise', expertise_payload)
            db.commit()
            for mode in ('automatic', 'pinned'):
                assert legacy.graph(db, mode)['state'] == 'active'
            assert legacy.expertise(db) == {'automatic', 'pinned'}
            db.execute(text("UPDATE ml_state SET backfill_kind=NULL,backfill_cursor='' WHERE id=1"))
            before = db.execute(text('SELECT backfill_generation FROM ml_state WHERE id=1')).scalar_one()
            db.commit()
        _migrate(database, direction, target)
        with Session(engine) as db:
            assert legacy.graph(db, 'automatic') is None
            manual = legacy.graph(db, 'pinned')
            assert manual['state'] == 'active' and manual['origin'] == 'manual'
            assert legacy.expertise(db) == {'pinned'}
            assert db.execute(text('SELECT count(*) FROM knowledge_items')).scalar_one() == 4
            assert db.execute(text('SELECT count(*) FROM expertise_mappings')).scalar_one() == 2
            assert db.execute(text("SELECT count(*) FROM ml_sources WHERE kind IN ('item','profile') AND valid=1")).scalar_one() == 0
            state = db.execute(text('SELECT backfill_kind,backfill_generation FROM ml_state WHERE id=1')).one()
            assert state.backfill_kind == 'item' and state.backfill_generation > before
            assert db.execute(text('PRAGMA foreign_key_check')).all() == []
    finally:
        engine.dispose()
