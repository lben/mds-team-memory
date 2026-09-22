"""Retained pre-0015 columns/read predicates for real rollback migration tests.

Current ORM models and public routes require the later additive schema. These
small readers intentionally know only the old source-validity/hash boundary;
they cannot be protected by current inference or topic-confirmation guards.
"""
import json
import uuid
from datetime import datetime

from sqlalchemy import text


def insert(db, table, **values):
    # Table/column names are constants supplied by migration test code.
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')
    defaults = {'id': uuid.uuid4().hex}
    if table in {'accounts', 'profiles', 'knowledge_items', 'relationships'}:
        defaults['created_at'] = now
    if table == 'accounts':
        defaults.update(is_admin=0, password_hash='unused')
    elif table == 'profiles':
        defaults['claim_locked'] = 0
    elif table == 'knowledge_items':
        defaults.update(kind='note', visibility='team', updated_at=now)
    elif table == 'relationships':
        defaults.update(state='confirmed', occurrence_count=0)
    values = {**defaults, **values}
    db.execute(text(f'INSERT INTO {table} ({",".join(values)}) VALUES ({",".join(":" + key for key in values)})'), values)
    return values['id']


def finding(db, key, kind, canonical_id, payload, score=0):
    db.execute(text('''INSERT INTO ml_findings
      (key,kind,canonical_id,payload,state,score,calibrated,policy_version,created_at,updated_at)
      VALUES (:key,:kind,:canonical,:payload,'active',:score,0,'retained-policy',datetime('now'),datetime('now'))'''),
      {'key': key, 'kind': kind, 'canonical': canonical_id, 'payload': json.dumps(payload), 'score': score})


def evidence(db, key, finding_key, kind, source_id, author, *, body='', features=None, score=0):
    from app.ml.sources import digest

    source_hash = digest([kind, source_id, body])
    db.execute(text('''INSERT INTO ml_sources(kind,id,content_hash,valid,model_version,result,updated_at)
      VALUES (:kind,:id,:hash,1,'retained-model','{}',datetime('now'))'''),
      {'kind': kind, 'id': source_id, 'hash': source_hash})
    db.execute(text('''INSERT INTO ml_evidence
      (key,finding_key,source_kind,source_id,source_hash,group_key,author_id,start,end,raw_score,polarity,features,model_version)
      VALUES (:key,:finding,:kind,:id,:hash,:id,:author,0,:end,:score,'positive',:features,'retained-model')'''),
      {'key': key, 'finding': finding_key, 'kind': kind, 'id': source_id, 'hash': source_hash,
       'author': author, 'end': len(body), 'score': score,
       'features': json.dumps({'text_hash': digest(body or source_id), **(features or {})})})


def pin(db, key, kind, payload):
    db.execute(text('''INSERT INTO ml_overrides(key,kind,mode,payload,username,updated_at)
      VALUES (:key,:kind,'pinned',:payload,'reviewer',datetime('now'))'''),
      {'key': key, 'kind': kind, 'payload': json.dumps(payload)})


SUPPORTED = '''EXISTS (SELECT 1 FROM ml_evidence e JOIN ml_sources s
  ON s.kind=e.source_kind AND s.id=e.source_id AND s.valid=1 AND s.content_hash=e.source_hash
  WHERE e.finding_key=f.key)'''
ENABLED = f"COALESCE(o.mode,'')!='suppressed' AND (o.mode='pinned' OR (f.state='active' AND {SUPPORTED}))"


def search(db, spelling):
    return [dict(row) for row in db.execute(text(f'''SELECT t.concept_id AS id,n.display AS name
      FROM concept_terms t JOIN concept_terms n ON n.concept_id=t.concept_id AND n.is_canonical=1
      JOIN ml_findings f ON f.kind='alias' AND f.canonical_id=t.id
      LEFT JOIN ml_overrides o ON o.key=f.key WHERE t.term=:term AND {ENABLED}'''),
      {'term': spelling.lower()}).mappings()]


def expertise(db):
    return {row[0] for row in db.execute(text(f'''SELECT p.display_name
      FROM expertise_mappings m JOIN profiles p ON p.id=m.profile_id JOIN accounts a ON a.id=p.account_id
      JOIN ml_findings f ON f.kind='expertise' AND f.canonical_id=m.id
      LEFT JOIN ml_overrides o ON o.key=f.key WHERE {ENABLED}'''))}


def graph(db, link_id):
    from app.ml import policy
    from app.ml.sources import finding_key

    link = db.execute(text('SELECT src_id,dst_id FROM relationships WHERE id=:id'), {'id': link_id}).one()
    pair_key = finding_key('relationship_pair', *sorted(link))
    fixed = db.execute(text('SELECT mode FROM ml_overrides WHERE key=:key'), {'key': pair_key}).scalar()
    if fixed == 'pinned':
        return {'state': 'active', 'origin': 'manual'}
    if fixed == 'suppressed':
        return None
    rows = db.execute(text('''SELECT e.* FROM ml_evidence e JOIN ml_findings f ON f.key=e.finding_key
      JOIN ml_sources s ON s.kind=e.source_kind AND s.id=e.source_id AND s.valid=1 AND s.content_hash=e.source_hash
      WHERE f.kind='relationship' AND f.canonical_id=:id'''), {'id': link_id}).mappings().all()
    if not rows:
        return None
    state, _ = policy.decide('relationship', [{**json.loads(row['features']), **row} for row in rows])
    return {'state': state, 'origin': 'automatic'}
