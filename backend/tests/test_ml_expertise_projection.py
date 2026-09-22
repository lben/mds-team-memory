"""Current-generation expertise through public readers; no model-quality claim."""
import json
import uuid

import pytest

from test_ml_automation import automation, _apply, _capture, _confirm_topic, _profile_work, _tags


@pytest.mark.parametrize("change", ["missing", "policy", "syntax", "schema", "pipeline"])
def test_expertise_projection_withholds_changed_generation_until_recomputed(
        make_client, admin_client, monkeypatch, change):
    from sqlalchemy import text
    from app.db import SessionLocal
    from app.ml import policy, runtime, syntax
    from app.ml.models import Source
    from app.ml.sources import finding_key

    suffix = uuid.uuid4().hex[:8]
    expert, asker, peer = make_client(), make_client(), make_client()
    for actor, client in (("expert", expert), ("asker", asker), ("peer", peer)):
        assert client.post('/api/auth/signup', json={
            'username': actor + suffix, 'password': 'a-private-test-password'}).status_code == 200
    profile = expert.get('/api/profile').json()
    topic = 'Projection Recorder ' + suffix
    question = asker.post('/api/questions', json={'body': f'How do I repair {topic}?'}).json()['id']
    answer = expert.post(f'/api/questions/{question}/answers', json={
        'body': f'I repaired {topic} by replacing its failed relay.'}).json()['id']
    _apply(answer, [topic])
    cid = _tags(expert, answer)[topic]
    assert asker.post(f'/api/questions/{question}/accept', json={'answer_id': answer}).status_code == 200
    _confirm_topic(asker, answer, 'accepted', topic)
    items = [answer]
    for body in (f'I restored the saved clock configuration on {topic}.',
                 f'I calibrated the output voltage of {topic} against a reference meter.'):
        item = _capture(expert, body)
        items.append(item)
        _apply(item, [topic])
        assert peer.post(f'/api/items/{item}/helped').status_code == 200
        _confirm_topic(peer, item, 'helped', topic)
    _profile_work(profile['id'])
    key = finding_key('expertise', profile['id'], cid)

    def areas():
        return next((entry['areas'] for entry in asker.get('/api/expertise').json()
                     if entry['label'] == profile['label']), [])

    assert topic in areas()
    with SessionLocal() as db:
        pipeline_before = db.execute(text('SELECT pipeline_version FROM ml_state WHERE id=1')).scalar_one()
    try:
        with monkeypatch.context() as changed:
            if change == 'missing':
                with SessionLocal() as db:
                    db.get(Source, ('profile', profile['id'])).result = '{}'
                    db.commit()
            elif change == 'policy':
                changed.setattr(policy, 'VERSION', policy.VERSION + '-next')
            elif change == 'syntax':
                changed.setattr(syntax, 'REVISION', syntax.REVISION + '-next')
            elif change == 'schema':
                changed.setitem(runtime.ENTITIES, 'named entity', 'A changed extraction contract.')
            else:
                with SessionLocal() as db:
                    db.execute(text("UPDATE ml_state SET pipeline_version='next-checkpoint-generation' WHERE id=1"))
                    db.commit()
            assert topic not in areas(), 'An old projection must not stay public before worker startup'
            assert admin_client.put(f'/api/ml/findings/{key}/decision', json={'mode': 'pinned'}).status_code == 200
            assert topic in areas(), 'Generation invalidation must not override a manual pin'
            assert admin_client.put(f'/api/ml/findings/{key}/decision', json={'mode': 'automatic'}).status_code == 200
            _profile_work(profile['id'])
            assert topic in areas()
            with SessionLocal() as db:
                assert json.loads(db.get(Source, ('profile', profile['id'])).result)['projection_contract']
    finally:
        with SessionLocal() as db:
            db.execute(text('UPDATE ml_state SET pipeline_version=:version WHERE id=1'), {'version': pipeline_before})
            db.commit()
        for item in items:
            expert.delete(f'/api/items/{item}')
        asker.delete(f'/api/questions/{question}')
