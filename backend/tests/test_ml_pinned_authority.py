"""Immediate public effects of manual alias authority; no quality claim."""
import copy
import json

from test_ml_alias_conflicts import automated
from test_ml_identity_routing import embedding_generation_isolation, capture
from test_ml_inverse_alias import CASES, apply, replay, terms, cleanup


def test_pin_preserves_already_routed_graph_before_replay(make_client, admin_client):
    from app.db import SessionLocal
    from app.ml import adapter, runtime, syntax
    from app.ml.sources import digest, finding_key, snapshot

    # Isolate this mechanical authority check from the separate test that
    # deliberately gives PGM two permanent identities. Substitutions preserve
    # span lengths; these fixture scores are not new inference observations.
    encoded = json.dumps(CASES[0]).replace('Packet Gap Monitor', 'Packet Lag Monitor').replace('PGM', 'PLM')
    encoded = encoded.replace('packet gap monitor', 'packet lag monitor').replace('pgm', 'plm')
    for record in CASES[0]['records']:
        changed = record['body'].replace('Packet Gap Monitor', 'Packet Lag Monitor').replace('PGM', 'PLM')
        encoded = encoded.replace(digest(record['body']),
                                  digest(changed))
    saved = json.loads(encoded)
    case, records = saved['case'], saved['records']
    clients, items = capture(make_client, case)
    extra, alias_key = [], None
    try:
        for item, record in zip(items, records):
            apply(item, record)
        replay(items, records)
        replay(items, records)
        short = terms()['plm'][0]
        alias_key = finding_key('alias', 'packet lag monitor', short)
        assert 'packet lag monitor' in terms(), {
            'full': admin_client.get(f'/api/ml/findings/{finding_key("concept", "packet lag monitor")}').json(),
            'alias': admin_client.get(f'/api/ml/findings/{alias_key}').json(),
        }
        assert terms()['packet lag monitor'] == (short, False)
        assert admin_client.put(f'/api/ml/findings/{finding_key("concept", "plm")}/decision', json={'mode':'pinned'}).status_code == 200
        tail = admin_client.post('/api/admin/concepts', json={'name':'Telemetry Store'}).json()['id']
        parts = records[0]['original_metadata']['model_version'].split(':')
        models = {role:{'revision':rev} for role,rev in zip(('extractor','embeddings','syntax'),parts[:3])}
        version = runtime.inference_version(models)
        relation_items = []
        for ending in ('during nightly validation.', 'during the independent daytime audit.'):
            client = make_client()
            body = 'Packet Lag Monitor uses Telemetry Store ' + ending
            item = client.post('/api/capture', data={'body':body}).json()['item']['id']
            extra.append((client,item))
            spans = [{'name':name, 'start':body.index(name), 'end':body.index(name)+len(name), 'score':0.95, 'label':'relation endpoint'} for name in ('Packet Lag Monitor','Telemetry Store')]
            result = {'concepts':spans, 'relations':[{'head':spans[0], 'tail':spans[1], 'predicate':'uses', 'start':0, 'end':len(body), 'score':0.95, 'polarity':'positive', 'literal_support':True}], 'chunks':[], 'corroborated_definitions':[], 'conflict_definitions':[], 'conflict_coverage_revision':syntax.CONFLICT_REVISION}
            relation_items.append((item,result))
        def apply_relations():
            for item,result in relation_items:
                with SessionLocal() as db:
                    source = snapshot(db,'item',item)
                    adapter.apply_source(db,'item',item,source,copy.deepcopy(result),version,records[0]['original_metadata']['embedding_version'],1024)
                    db.commit()
        def graph():
            return [e for e in admin_client.get('/api/graph/global').json()['edges'] if {e['source'],e['target']} == {short,tail}]
        apply_relations()
        before = graph()
        assert len(before)==1 and before[0]['state']=='active', before
        assert admin_client.put(f'/api/ml/findings/{alias_key}/decision', json={'mode':'pinned'}).status_code == 200
        assert len(graph()) == 1
        owner = clients[case['posts'][1]['actor']]
        assert owner.put(f'/api/items/{items[1]}',json={'body':'The obsolete definition was removed.'}).status_code == 200
        assert terms()['packet lag monitor'] == (short,False)
        after = graph()
        apply(items[1],records[1],empty=True)
        apply_relations()
        restored = graph()
        assert len(after)==1 and after[0]['state']=='active', 'Pinned alias kept its term, but its existing graph relationship disappeared until replay'
        assert restored[0]['link_id'] == before[0]['link_id']
        assert admin_client.put(f'/api/ml/findings/{alias_key}/decision', json={'mode':'automatic'}).status_code == 200
        assert graph() == [], 'Releasing the pin must withdraw the now-unsupported identity immediately'
        apply_relations()
        assert graph() == []
        assert admin_client.put(f'/api/ml/findings/{alias_key}/decision', json={'mode':'pinned'}).status_code == 200
        apply_relations()
        assert graph()[0]['state'] == 'active'
        source_owner, source_id = extra[0]
        assert source_owner.put(f'/api/items/{source_id}', json={'body':'This relationship was removed.'}).status_code == 200
        remaining = graph()
        assert not remaining or remaining[0]['state'] != 'active', 'An alias pin must not preserve deleted relationship evidence'
    finally:
        for client,item in extra:
            client.delete(f'/api/items/{item}')
        if alias_key:
            admin_client.put(f'/api/ml/findings/{alias_key}/decision', json={'mode':'automatic'})
        admin_client.put(f'/api/ml/findings/{finding_key("concept", "plm")}/decision', json={'mode':'automatic'})
        cleanup(clients, items, case, records)
