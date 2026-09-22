"""Alias generation transitions through public readers; no model inference."""
import json
import uuid

import pytest

from test_ml_automation import automation, _apply, _apply_current_definition, _capture, _tags
from test_ml_relationship_generation import _migrate


@pytest.fixture
def current_alias(make_client, admin_client):
    from app.ml.sources import finding_key

    suffix = uuid.uuid4().hex[:8]
    name, alias = 'Archive Gauge ' + suffix, 'AG' + suffix
    cid = admin_client.post('/api/admin/concepts', json={'name': name}).json()['id']
    owner, reader = make_client(), make_client()
    definition = _capture(owner, f'{alias} denotes {name} in our instrument register.')
    dependent = _capture(reader, f'{alias} supplied the calibration history for the northern station.')

    def renew():
        _apply_current_definition(definition, name, alias)
        _apply(dependent, [alias])
        # Finishing the last stale source removes the global coverage barrier;
        # replay the retained definition and dependent vocabulary afterwards.
        _apply_current_definition(definition, name, alias, cached=True)
        _apply(dependent, cached=True)

    def public():
        return {row['name']: row['id'] for row in reader.get('/api/search', params={'q': alias}).json()['concepts']}

    renew()
    assert public().get(name) == cid
    assert _tags(reader, dependent).get(name) == cid
    yield {'name': name, 'alias': alias, 'cid': cid, 'definition': definition, 'dependent': dependent,
           'reader': reader, 'owner': owner, 'renew': renew, 'public': public,
           'key': finding_key('alias', alias.lower(), cid),
           'definition_key': finding_key('alias_definition', alias.lower(), name.lower())}
    for client, item in ((reader, dependent), (owner, definition)):
        client.delete(f'/api/items/{item}')
        _apply(item)


@pytest.mark.parametrize('change', ['missing_scope', 'old_scope', 'source', 'evidence', 'pipeline', 'coherent_retired'])
def test_alias_reads_reject_incomplete_or_retired_generation_before_worker(current_alias, admin_client, change):
    from sqlalchemy import text
    from app.db import SessionLocal
    from app.ml import adapter, effective, policy
    from app.ml.models import Evidence, Source
    from app.ml.sources import snapshot

    item = current_alias['definition']
    with SessionLocal() as db:
        stored = db.get(Source, ('item', item))
        current_version = stored.model_version
        if change in {'missing_scope', 'old_scope'}:
            result = json.loads(stored.result)
            if change == 'missing_scope':
                result.pop('alias_scope_contract')
            else:
                result['alias_scope_contract'] = 'retired-scope-contract'
            stored.result = json.dumps(result)
        elif change == 'pipeline':
            db.execute(text("UPDATE ml_state SET pipeline_version='retired-pipeline' WHERE id=1"))
        else:
            parts = current_version.split(':')
            parts[3] = 'retired-extraction-contract'
            retired = ':'.join(parts)
            if change in {'source', 'coherent_retired'}:
                stored.model_version = retired
            if change in {'evidence', 'coherent_retired'}:
                for row in db.query(Evidence).filter_by(source_kind='item', source_id=item):
                    row.model_version = retired
            if change == 'coherent_retired':
                db.execute(text('UPDATE ml_state SET pipeline_version=:version WHERE id=1'),
                           {'version': retired + ':' + policy.VERSION})
        db.commit()
        assert effective.evidence_rows(db, current_alias['key']) == []
        assert effective.evidence_rows(db, current_alias['definition_key']) == []
        if change in {'missing_scope', 'old_scope'}:
            assert adapter.cached_result(db, snapshot(db, 'item', item), current_version) is None
    assert current_alias['name'] not in current_alias['public']()
    assert admin_client.put(f"/api/ml/findings/{current_alias['key']}/decision", json={'mode': 'pinned'}).status_code == 200
    assert current_alias['public']().get(current_alias['name']) == current_alias['cid']
    assert admin_client.put(f"/api/ml/findings/{current_alias['key']}/decision", json={'mode': 'automatic'}).status_code == 200
    assert current_alias['name'] not in current_alias['public']()
    current_alias['renew']()
    assert current_alias['public']().get(current_alias['name']) == current_alias['cid']


@pytest.mark.parametrize('change', ['extraction', 'scope'])
def test_same_policy_alias_contract_change_withdraws_alias_and_dependent_tags_before_replay(
        current_alias, admin_client, monkeypatch, change):
    from app.db import SessionLocal
    from app.ml import adapter, identity, policy, runtime
    from app.ml.models import Source
    from app.ml.sources import finding_key, snapshot

    before = identity.scope_contract()
    policy_before = policy.VERSION
    with SessionLocal() as db:
        version = db.get(Source, ('item', current_alias['definition'])).model_version
    with monkeypatch.context() as generation:
        if change == 'extraction':
            generation.setattr(runtime, 'EXTRACTION_VERSION', runtime.EXTRACTION_VERSION + '-next')
        else:
            generation.setattr(identity, 'scope_contract', lambda: before + '-next')
        assert policy.VERSION == policy_before and identity.scope_contract() != before
        assert current_alias['name'] not in current_alias['public']()
        mention_key = finding_key('mention', 'item', current_alias['dependent'], current_alias['cid'])
        assert current_alias['cid'] not in _tags(current_alias['reader'], current_alias['dependent']).values(), {
            'alias': admin_client.get(f"/api/ml/findings/{current_alias['key']}").json(),
            'mention': admin_client.get(f'/api/ml/findings/{mention_key}').json(),
        }
        with SessionLocal() as db:
            assert adapter.cached_result(db, snapshot(db, 'item', current_alias['definition']), version) is None
        assert admin_client.put(f"/api/ml/findings/{current_alias['key']}/decision", json={'mode': 'pinned'}).status_code == 200
        assert current_alias['public']().get(current_alias['name']) == current_alias['cid']
        assert admin_client.put(f"/api/ml/findings/{current_alias['key']}/decision", json={'mode': 'automatic'}).status_code == 200
        current_alias['renew']()
        assert current_alias['public']().get(current_alias['name']) == current_alias['cid']
        assert _tags(current_alias['reader'], current_alias['dependent']).get(current_alias['name']) == current_alias['cid']
    # Rolling back code must not relabel newly stored records as old observations.
    assert current_alias['name'] not in current_alias['public']()
    current_alias['renew']()
    assert current_alias['public']().get(current_alias['name']) == current_alias['cid']


@pytest.mark.parametrize('direction,target', [('upgrade', '0014'), ('downgrade', '0013'), ('downgrade', '0010')])
def test_0014_alias_invalidation_protects_legacy_public_search_and_manual_pins(
        app_modules, tmp_path, direction, target):
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session
    import ml_legacy_readers as legacy

    database = tmp_path / 'alias-migration.sqlite3'
    _migrate(database, 'upgrade', '0013' if direction == 'upgrade' else '0014')
    engine = create_engine(f'sqlite:///{database}')
    try:
        with Session(engine) as db:
            legacy.insert(db, 'profiles', id='reader', display_name='Reader')
            for mode in ('automatic', 'pinned'):
                cid, spelling = mode + '-concept', mode + 'Alias'
                legacy.insert(db, 'concepts', id=cid)
                legacy.insert(db, 'concept_terms', concept_id=cid, term=cid, display=cid, is_canonical=1)
                term = legacy.insert(db, 'concept_terms', concept_id=cid, term=spelling.lower(), display=spelling, is_canonical=0)
                payload = {'alias': spelling, 'alias_key': spelling.lower(), 'concept_id': cid}
                legacy.finding(db, mode, 'alias', term, payload, score=.99)
                body = f'Hypothesis:\n{spelling} denotes {cid}.'
                legacy.insert(db, 'knowledge_items', id=mode, body=body, author_profile_id='reader')
                legacy.evidence(db, mode, mode, 'item', mode, 'reader', body=body, score=.99)
                if mode == 'pinned':
                    legacy.pin(db, mode, 'alias', payload)
            db.commit()
            for mode in ('automatic', 'pinned'):
                assert legacy.search(db, mode + 'Alias') == [{'id': mode + '-concept', 'name': mode + '-concept'}]
            db.execute(text("UPDATE ml_state SET backfill_kind=NULL,backfill_cursor='' WHERE id=1"))
            before = db.execute(text('SELECT backfill_generation FROM ml_state WHERE id=1')).scalar_one()
            db.commit()
        _migrate(database, direction, target)
        with Session(engine) as db:
            assert legacy.search(db, 'automaticAlias') == []
            assert legacy.search(db, 'pinnedAlias') == [{'id': 'pinned-concept', 'name': 'pinned-concept'}]
            assert db.execute(text('SELECT count(*) FROM concept_terms')).scalar_one() == 4
            assert db.execute(text('SELECT count(*) FROM ml_sources WHERE valid=1')).scalar_one() == 0
            state = db.execute(text('SELECT backfill_kind,backfill_generation FROM ml_state WHERE id=1')).one()
            assert state.backfill_kind == 'item' and state.backfill_generation > before
            assert db.execute(text('PRAGMA foreign_key_check')).all() == []
    finally:
        engine.dispose()
