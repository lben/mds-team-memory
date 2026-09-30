"""Isolated localhost acceptance exercise; never targets configured UAT/PROD."""
import http.cookiejar,json,sys,urllib.request,urllib.parse,time,shlex
from pathlib import Path
sys.path.insert(0,str(Path.cwd()/'tools'))
import update,update_session
update_session.getpass.getpass=lambda _: 'isolated-update-test'
target,known_hosts=update.configured_target('uat',Path('build/bge-update/test.toml'))
ssh=update_session.Session(target,known_hosts)
base='http://127.0.0.1:28094/api/'
trace=[]
def browser():return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
def call(client,method,path,payload=None,form=False):
 data=None if payload is None else (urllib.parse.urlencode(payload).encode() if form else json.dumps(payload).encode())
 req=urllib.request.Request(base+path,data=data,method=method,headers={} if form else {'Content-Type':'application/json'})
 with client.open(req,timeout=30) as r:
  trace.append({'method':method,'path':path,'status':r.status});return json.load(r)
def query(sql):
 code='import sqlite3,json; c=sqlite3.connect("file:/home/mds/mds-uat/data/mds.sqlite3?mode=ro",uri=True); print(json.dumps(c.execute('+repr(sql)+').fetchall()))'
 cmd='set -a; source /home/mds/mds-uat/app.env; "$PYTHON_VERSION" -c '+shlex.quote(code)
 result=ssh.run('bash -c '+shlex.quote(cmd),capture=True)
 if result.returncode:raise RuntimeError(result.stderr)
 return json.loads(result.stdout)
def drained():
 deadline=time.monotonic()+600
 while time.monotonic()<deadline:
  rows=query('SELECT count(*) FROM ml_jobs')
  if rows[0][0]==0 and query('SELECT backfill_kind FROM ml_state WHERE id=1')[0][0] is None:return
  errors=query('SELECT error FROM ml_jobs WHERE error IS NOT NULL')
  if errors:raise RuntimeError(errors)
  time.sleep(2)
 raise TimeoutError('Real daemon queue did not drain')
admin=browser();call(admin,'POST','auth/login',{'username':'bge-admin','password':'isolated-admin-test'})
preserved=json.loads(Path('build/bge-update/preserved-data.json').read_text())
assert call(admin,'GET','items/'+preserved['post']['item']['id'])['body']==preserved['post']['item']['body']
assert preserved['concept'] in call(admin,'GET','admin/concepts')
drained();print('Old account, content and manual concept preserved; upgraded queue drained',flush=True)
a,b=browser(),browser()
c=browser()
text1='CVM (Cryogenic Valve Map) shows the isolation valves between the separator and the cold return.'
text2='The Cryogenic Valve Map (CVM) identifies isolation valves on the cold return circuit.'
ids=[call(a,'POST','capture',{'body':text1},True)['item']['id'],call(b,'POST','capture',{'body':text2},True)['item']['id']]
ids.append(call(c,'POST','capture',{'body':'The Cryogenic Valve Map records valve identifiers, isolation settings and inspection results.'},True)['item']['id'])
drained()
sources=query("SELECT id,json_extract(result,'$.eligibility'),json_extract(result,'$.corroborated_definitions'),(SELECT count(*) FROM ml_embeddings c WHERE c.source_id=ml_sources.id) FROM ml_sources WHERE id IN ('"+"','".join(ids)+"')")
assert len(sources)==3
for row in sources:
 judgment=json.loads(row[1]);assert judgment['version'].startswith('bge:') and judgment['margins']
 assert all(-1<=value<=1 for value in judgment['margins'].values())
 assert row[3]>0
assert any(json.loads(row[2]) for row in sources)
search=call(a,'GET','search?q='+urllib.parse.quote('Cryogenic Valve Map'));print('Actual BGE sources',json.dumps(sources),flush=True)
assert any(c['name']=='Cryogenic Valve Map' for c in search['concepts']),search
assert any(c['name']=='Cryogenic Valve Map' for c in call(a,'GET','search?q=CVM')['concepts'])
item=call(a,'GET','items/'+ids[0]);assert any(c['name']=='Cryogenic Valve Map' for c in item['concepts'])
# Actual edit then deletion must retire both the text and derived vectors.
call(a,'PUT','items/'+ids[0],{'body':'The maintenance log has been corrected.'});drained()
call(a,'DELETE','items/'+ids[0]);call(b,'DELETE','items/'+ids[1]);call(c,'DELETE','items/'+ids[2]);drained()
assert not any(c['name']=='Cryogenic Valve Map' for c in call(admin,'GET','search?q=CVM')['concepts'])
assert query("SELECT count(*) FROM ml_embeddings WHERE source_id IN ('"+"','".join(ids)+"')")[0][0]==0
assert preserved['concept'] in call(admin,'GET','admin/concepts')
result={'status':'PASS','trace':trace,'sources':sources,'search':search,'preserved':{'account':'bge-admin','post_id':preserved['post']['item']['id'],'concept_id':preserved['concept']['id']},'queue':query('SELECT count(*) FROM ml_jobs'),'integrity':query('PRAGMA quick_check'),'foreign_keys':query('PRAGMA foreign_key_check')}
assert result['integrity']==[['ok']] and result['foreign_keys']==[]
Path('build/bge-update/live-e2e.json').write_text(json.dumps(result,indent=2)+'\n')
ssh.close();print('LIVE_E2E_PASS',flush=True)
