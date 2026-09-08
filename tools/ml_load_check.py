#!/usr/bin/env python3
r"""Run the fixed 50,000-item, 50-client paired capacity check on Linux.

The worker starts cold for the second phase. A failed capacity verdict is a
result, not permission to change the workload. Every run creates a new directory
containing its private SQLite database, backend snapshot, logs, pins and report.
The application database configured in the calling shell is never used.

Example (use the deployment's prepared Python environments and local models):
  python tools/ml_load_check.py --web-python /opt/mds/web/bin/python \
      --worker-python /opt/mds/ml/bin/python --models /opt/mds/models

The driver re-executes with --worker-python, which needs psutil and the existing
backend dependencies. No packages or models are downloaded. Resource limits come
from the host/service configuration and the production worker; this command does
not change host or cgroup settings. Run it in the intended deployment envelope.
Synthetic content does not establish model quality or full-backlog drain time.
"""
import argparse
import concurrent.futures
import datetime
import hashlib
import json
import math
import os
import platform
import signal
import socket
from pathlib import Path
import random
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request


def digest_tree(directory):
    hashes = {str(p.relative_to(directory)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted(directory.rglob('*')) if p.is_file()}
    return hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest(), hashes


def lock_accounting(worker_log, web_log):
    prefix = 'ML SQLite retries final: '
    final = [line[len(prefix):] for line in worker_log.splitlines() if line.startswith(prefix)]
    counters = None
    operations = {'acquire', 'renew_worker', 'renew_claim', 'bootstrap', 'checkpoint',
                  'claim', 'backfill', 'housekeeping', 'apply', 'retry', 'release'}
    if len(final) == 1:
        try:
            parsed = json.loads(final[0])
            if (isinstance(parsed, dict) and parsed.keys() <= operations
                    and all(type(value) is int and value >= 0 for value in parsed.values())):
                counters = parsed
        except json.JSONDecodeError:
            pass
    def is_lock(line):
        return any(message in line.lower() for message in (
            'database is locked', 'database table is locked', 'database schema is locked', 'database is busy'))
    worker_lines = [line for line in worker_log.splitlines() if is_lock(line)]
    handled = sum(line.startswith('ML job retained: ') for line in worker_lines)
    return {
        'worker_lock_retry_accounting_complete': counters is not None,
        'worker_handled_contention_retries_by_operation': counters,
        'worker_handled_contention_retries': sum(counters.values()) if counters is not None else None,
        'worker_handled_lock_log_mentions': handled,
        'worker_unhandled_lock_mentions': len(worker_lines) - handled,
        'web_unhandled_lock_mentions': sum(is_lock(line) for line in web_log.splitlines()),
        'lock_retry_measurement': 'Final cumulative worker counters include blocked-WAL checkpoint backoff. '
                                  'Log mentions are diagnostics, not retry totals.',
    }


def environment(python):
    probe = """import importlib.metadata as m, json, platform, sqlite3, sys
names = ('fastapi','uvicorn','sqlalchemy','alembic','psutil','torch','gliner2','sentence-transformers','transformers')
versions = {}
for name in names:
    try: versions[name] = m.version(name)
    except m.PackageNotFoundError: versions[name] = None
print(json.dumps(dict(python=sys.version, executable=sys.executable, sqlite=sqlite3.sqlite_version,
                     platform=platform.platform(), packages=versions)))
"""
    return json.loads(subprocess.check_output([str(python), '-I', '-c', probe], text=True, timeout=30))


def cgroup_files():
    files = {}
    for line in Path('/proc/self/cgroup').read_text().splitlines():
        _, controllers, relative = line.split(':', 2)
        if not controllers:
            base = Path('/sys/fs/cgroup') / relative.lstrip('/')
            files.update({name: base / filename for name, filename in (
                ('cpu_quota', 'cpu.max'), ('memory_max', 'memory.max'),
                ('swap_max', 'memory.swap.max'), ('cgroup_memory_bytes', 'memory.current'))})
        elif 'memory' in controllers.split(','):
            base = Path('/sys/fs/cgroup/memory') / relative.lstrip('/')
            files.update(memory_max=base / 'memory.limit_in_bytes',
                         memory_and_swap_max=base / 'memory.memsw.limit_in_bytes',
                         cgroup_memory_bytes=base / 'memory.usage_in_bytes')
        elif 'cpu' in controllers.split(','):
            base = Path('/sys/fs/cgroup/cpu') / relative.lstrip('/')
            files.update(cpu_quota_us=base / 'cpu.cfs_quota_us', cpu_period_us=base / 'cpu.cfs_period_us')
    return {name: path for name, path in files.items() if path.is_file()}


def run(args):
    import psutil
    out = Path(tempfile.mkdtemp(prefix='mds-ml-load-check-', dir=args.output_parent))
    print('ARTIFACT_DIRECTORY=' + str(out), flush=True)
    root = out / 'snapshot'
    shutil.copytree(args.source_root / 'backend', root / 'backend',
                    ignore=shutil.ignore_patterns('__pycache__', '.pytest_cache', '.venv', '*.pyc', 'data'))
    tree_hash, files = digest_tree(root)
    manifest = (args.models / 'models.json').read_bytes()
    (out / 'models.json').write_bytes(manifest)
    git = (subprocess.run(['git', '-C', str(args.source_root), 'rev-parse', 'HEAD'],
                          capture_output=True, text=True, check=False) if shutil.which('git') else None)
    metadata = {
        'snapshot_utc': datetime.datetime.now(datetime.UTC).isoformat(),
        'backend_sha256': tree_hash, 'file_sha256': files,
        'driver_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'source_git_head': git.stdout.strip() if git is not None and git.returncode == 0 else None,
        'source_root': str(args.source_root), 'models': str(args.models),
        'models_manifest_sha256': hashlib.sha256(manifest).hexdigest(),
        'model_pins': json.loads(manifest),
        'web_environment': environment(args.web_python),
        'worker_environment': environment(args.worker_python),
        'phase_seconds': args.seconds,
        'limitations': ['Synthetic content does not establish model quality.',
                        'A single paired cold-start run does not establish full-backlog drain time.'],
    }
    calibration = args.models / 'calibration.json'
    if calibration.is_file():
        metadata['calibration_sha256'] = hashlib.sha256(calibration.read_bytes()).hexdigest()
    (out / 'snapshot.json').write_text(json.dumps(metadata, indent=2))
    dbpath = out / 'app.sqlite3'
    os.environ.update(MDS_DATA_DIR=str(out), MDS_DATABASE_URL='sqlite:///' + str(dbpath),
                      PYTHONPATH=str(root / 'backend'), HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
                      HF_HUB_DISABLE_TELEMETRY='1', OMP_NUM_THREADS='4', MKL_NUM_THREADS='4',
                      OPENBLAS_NUM_THREADS='4', TOKENIZERS_PARALLELISM='false', CUDA_VISIBLE_DEVICES='')
    sys.path.insert(0, str(root / 'backend'))
    from app.text import normalized_hash
    started = time.monotonic()
    subprocess.run([str(args.web_python), '-m', 'alembic', '-c', str(root / 'backend/alembic.ini'),
                    'upgrade', 'head'], cwd=root / 'backend', check=True)
    print('Migrated database', flush=True)
    names = ['PostgreSQL', 'Kafka', 'Redis', 'Python', 'Kubernetes', 'Docker', 'Prometheus',
             'Grafana', 'SQLite', 'Linux', 'Airflow', 'Spark', 'Parquet', 'FastAPI', 'Nginx',
             'Terraform', 'Ansible', 'Elasticsearch', 'Jenkins', 'Git']
    templates = [
        '{a} uses {b} to store audit events. The team reviewed incident {x} on service {y}. The recorded duration was {n} seconds and the batch contained {m} records.',
        'A maintenance check for {a} found a connection issue with {b}. Operator {x} restarted job {y} after {n} attempts. The next run processed {m} records successfully.',
        'The team compared {a} with {b} for project {x}. Ticket {y} documents a memory limit of {n} MiB and a queue length of {m}. A later review will verify the results.',
        'Project {x} depends on {a}. Its {b} configuration uses deployment {y}. The observed retry interval was {n} seconds, with {m} pending requests at the end of the review.',
        '{a} produces events for {b}. The migration for workspace {x} started in partition {y}, copied {m} records, and completed its first validation after {n} seconds.'
    ]
    def body_for(index, phase):
        rng = random.Random(f'{phase}:{index}')
        a, b = rng.sample(names, 2)
        values = dict(a=a, b=b, x=f'{rng.getrandbits(40):010x}', y=f'{rng.getrandbits(40):010x}',
                      n=rng.randrange(11, 999), m=rng.randrange(1000, 99999))
        body = rng.choice(templates).format(**values)
        # Materially different identifiers and numbers avoid near-copy grouping.
        body += f' Run reference {phase}-{index:06d}-{rng.getrandbits(64):016x}.'
        if phase == 'seed' and index % 100 == 99:
            body += (' The operations review records deployment checks, recovery steps, timeout budgets, '
                     'storage usage, backup status, and remaining observations for the next shift.' * 45)
        elif phase == 'seed' and index % 10 == 9:
            body += (' The review covers capacity, ownership, recovery, deployment, and the next maintenance window.' * 9)
        return body, a, b
    tokens = [f'load-browser-{i:02d}' for i in range(50)]
    profiles = [f'{i + 1:032x}' for i in range(50)]
    concept_ids = {name: f'{1000 + i:032x}' for i, name in enumerate(names)}
    now = datetime.datetime(2026, 9, 8)
    with sqlite3.connect(dbpath) as con:
        con.execute('PRAGMA foreign_keys=ON')
        con.execute('PRAGMA journal_mode=WAL')
        for i, token in enumerate(tokens):
            con.execute('INSERT INTO profiles(id,token_hash,display_name,claim_locked,created_at) VALUES(?,?,?,?,?)',
                        (profiles[i], hashlib.sha256(token.encode()).hexdigest(), f'Load profile {i + 1}', 0, now))
        for i, name in enumerate(names):
            con.execute('INSERT INTO concepts(id) VALUES(?)', (concept_ids[name],))
            con.execute('INSERT INTO concept_terms(id,concept_id,term,display,is_canonical) VALUES(?,?,?,?,1)',
                        (f'{2000+i:032x}', concept_ids[name], name.lower(), name))
        body_lengths = []
        for i in range(50000):
            body, a, b = body_for(i, 'seed')
            body_lengths.append(len(body))
            item_id = f'{100000+i:032x}'
            slot = i % 20
            kind = 'question' if slot in (15, 17, 19) else 'answer' if slot in (16, 18) else 'note'
            parent = f'{100000+i-1:032x}' if kind == 'answer' else None
            date = now - datetime.timedelta(minutes=(50000-i)*15)
            con.execute('''INSERT INTO knowledge_items(id,kind,body,visibility,author_profile_id,parent_id,
                normalized_hash,question_status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)''',
                (item_id, kind, body, 'team', profiles[i % 50], parent, normalized_hash(body),
                 'open' if kind == 'question' else None, date, date))
            for j, name in enumerate((a, b)):
                con.execute('INSERT INTO item_concepts(id,item_id,concept_id) VALUES(?,?,?)',
                            (f'{1000000+2*i+j:032x}', item_id, concept_ids[name]))
            if i % 1000 == 999:
                con.commit()
        con.commit()
        seeded_jobs = con.execute('SELECT count(*) FROM ml_jobs').fetchone()[0]
        seed_kind_counts = dict(con.execute('SELECT kind,count(*) FROM knowledge_items GROUP BY kind'))
        migration = con.execute('SELECT version_num FROM alembic_version').fetchone()[0]
    groups = cgroup_files()
    report = {'seed': {'items': 50000, 'profiles': 50, 'canonical_concepts': len(names),
                      'jobs': seeded_jobs, 'kinds': seed_kind_counts, 'characters': sum(body_lengths),
                      'max_characters': max(body_lengths), 'seconds': time.monotonic()-started},
              'runtime': {'uid': os.getuid(), 'python': sys.version, 'sqlite': sqlite3.sqlite_version,
                          'migration': migration, 'machine': os.uname().machine,
                          'platform': platform.platform(), 'cpu_affinity': sorted(os.sched_getaffinity(0)),
                          'host_memory_bytes': psutil.virtual_memory().total,
                          'cgroup': Path('/proc/self/cgroup').read_text().strip(),
                          'cgroup_limits': {name: path.read_text().strip() for name, path in groups.items()
                                           if name != 'cgroup_memory_bytes'}},
              'snapshot': metadata,
              'phases': {}, 'samples': [], 'failures': []}
    from app.db import SessionLocal
    from app.ml.adapter import bootstrap
    with SessionLocal() as db:
        bootstrap(db)
        db.commit()
    report['comparison_configuration'] = 'Automatic maintenance initialized in both phases; only inference worker changes.'
    print(json.dumps({'seed': report['seed'], 'runtime': report['runtime']}), flush=True)
    web_log = (out/'web.log').open('w')
    worker_log = (out/'worker.log').open('w')
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(('127.0.0.1', 0))
    listener.listen(2048)
    base_url = 'http://127.0.0.1:' + str(listener.getsockname()[1])
    # Keep this socket open until shutdown: a failed child cannot free its port
    # for another app while this driver still has requests in flight.
    report['base_url'] = base_url
    web = subprocess.Popen([str(args.web_python), '-m', 'uvicorn', 'app.main:app',
                            '--fd', str(listener.fileno()), '--no-access-log'],
                           pass_fds=(listener.fileno(),), cwd=root / 'backend', stdout=web_log, stderr=web_log)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    worker = None
    monitoring = threading.Event()
    attempts = []
    phase_name = 'startup'
    def snapshot():
        sample = {'elapsed': time.monotonic()-started, 'phase': phase_name}
        for name, proc in [('web', web), ('worker', worker)]:
            rss, cpu = 0, 0
            if proc and proc.poll() is None:
                try:
                    parent = psutil.Process(proc.pid)
                    for p in [parent, *parent.children(recursive=True)]:
                        try:
                            rss += p.memory_info().rss
                            t = p.cpu_times()
                            cpu += t.user+t.system
                        except psutil.NoSuchProcess:
                            pass
                except psutil.NoSuchProcess:
                    pass
            sample[name+'_tree_rss'] = rss
            sample[name+'_tree_cpu_seconds'] = cpu
        sample['database_bytes'] = dbpath.stat().st_size
        wal = Path(str(dbpath)+'-wal')
        sample['wal_bytes'] = wal.stat().st_size if wal.exists() else 0
        if 'cgroup_memory_bytes' in groups:
            sample['cgroup_memory_bytes'] = int(groups['cgroup_memory_bytes'].read_text())
        try:
            with sqlite3.connect(f'file:{dbpath}?mode=ro', uri=True, timeout=.25) as con:
                sample['jobs'], sample['attempts'], sample['error_jobs'], oldest = con.execute(
                    'SELECT count(*),coalesce(sum(attempts),0),coalesce(sum(error IS NOT NULL),0),min(created_at) FROM ml_jobs').fetchone()
                sample['oldest_queue_age_seconds'] = time.time()-oldest if oldest else 0
                sample['sources'] = con.execute('SELECT count(*) FROM ml_sources').fetchone()[0]
                sample['chunks'] = con.execute('SELECT count(*) FROM ml_embeddings').fetchone()[0]
                sample['revision'], sample['status'] = con.execute('SELECT revision,status FROM ml_state WHERE id=1').fetchone()
        except sqlite3.OperationalError as error:
            sample['monitor_error'] = str(error)
        report['samples'].append(sample)
    def monitor():
        while not monitoring.wait(.5):
            snapshot()
    def request(client, path, body=None):
        headers = {'Cookie': 'mds_profile='+tokens[client]}
        data = None
        if body is not None:
            data = urllib.parse.urlencode({'body': body}).encode()
            headers['Content-Type'] = 'application/x-www-form-urlencoded'
        req = urllib.request.Request(base_url+path, data=data, headers=headers)
        with opener.open(req, timeout=20) as response:
            return json.load(response)
    def run_phase(name):
        nonlocal phase_name
        phase_name = name
        barrier = threading.Barrier(51)
        stop_at = [0]
        records = []
        def client_run(client):
            local = []
            barrier.wait()
            i = 0
            while time.monotonic() < stop_at[0]:
                write = (client+i) % 5 == 0
                body = None
                if write:
                    body, _, _ = body_for(client*100000+i, name)
                    path = '/api/capture'
                else:
                    choice = (client+i) % 3
                    path = ['/api/feed', '/api/items/'+f'{100000+(client*977+i)%50000:032x}',
                            '/api/search?q='+urllib.parse.quote('PostgreSQL')][choice]
                rec = {'client': client, 'op': 'write' if write else 'read', 'path': path,
                       'started': time.monotonic(), 'ok': False}
                if write:
                    rec['body'] = body
                try:
                    result = request(client, path, body)
                    rec['ok'] = True
                    if write:
                        rec['id'] = result['item']['id']
                        rec['group_size'] = result['corroboration']['group_size']
                except Exception as error:
                    rec['error'] = f'{type(error).__name__}: {error}'
                    if isinstance(error, urllib.error.HTTPError):
                        rec['http_body'] = error.read().decode(errors='replace')[:2000]
                rec['seconds'] = time.monotonic()-rec.pop('started')
                local.append(rec)
                i += 1
            return local
        with concurrent.futures.ThreadPoolExecutor(max_workers=50) as pool:
            futures = [pool.submit(client_run, i) for i in range(50)]
            begin = time.monotonic()
            stop_at[0] = begin+args.seconds
            barrier.wait()
            for future in futures:
                records.extend(future.result())
        summary = {'seconds': time.monotonic()-begin, 'concurrent_clients': 50,
                   'mix': 'one capture per five operations; other requests rotate feed, item, search',
                   'requests': len(records), 'errors': [r for r in records if not r['ok']]}
        for op in ('read', 'write'):
            values = sorted(r['seconds'] for r in records if r['op']==op)
            summary[op] = {'count': len(values), 'successes': sum(r['ok'] for r in records if r['op']==op),
                           'p50_seconds': values[math.ceil(.5*len(values))-1] if values else None,
                           'p95_seconds': values[math.ceil(.95*len(values))-1] if values else None,
                           'max_seconds': max(values) if values else None}
        attempts.extend(r for r in records if r['op']=='write')
        report['phases'][name] = summary
        (out/(name+'-requests.json')).write_text(json.dumps(records, indent=2))
        print(json.dumps({'phase': name, 'summary': summary}), flush=True)
        snapshot()
    thread = threading.Thread(target=monitor, daemon=True)
    try:
        deadline = time.monotonic()+30
        while True:
            try:
                with opener.open(base_url+'/api/health', timeout=1) as response:
                    assert json.load(response)['ok']
                break
            except OSError:
                if web.poll() is not None or time.monotonic()>deadline:
                    raise RuntimeError('Web process did not become healthy')
                time.sleep(.1)
        snapshot()
        thread.start()
        run_phase('worker_off')
        worker = subprocess.Popen([str(args.worker_python), '-m', 'app.ml.worker', '--assets', str(args.models)],
                                  cwd=root/'backend', stdout=worker_log, stderr=worker_log)
        run_phase('worker_on')
        report['worker_exit_before_stop'] = worker.poll()
        phase_name = 'verification'
        with sqlite3.connect(dbpath) as con:
            successful = [r for r in attempts if r['ok']]
            missing, duplicates, failed_persisted = [], [], []
            for r in attempts:
                rows = con.execute('SELECT id,author_profile_id FROM knowledge_items WHERE body=?', (r['body'],)).fetchall()
                if r['ok'] and rows != [(r['id'], profiles[r['client']])]:
                    missing.append({'client':r['client'],'id':r['id'],'rows':rows})
                if len(rows)>1:
                    duplicates.append(r['body'])
                if not r['ok'] and rows:
                    failed_persisted.append({'error':r.get('error'),'rows':rows})
            report['integrity'] = {'successful_api_writes': len(successful), 'missing_or_wrong_writes': missing,
                                   'duplicate_writes': duplicates, 'failed_responses_with_persisted_writes': failed_persisted,
                                   'item_count':con.execute('SELECT count(*) FROM knowledge_items').fetchone()[0],
                                   'profile_count':con.execute('SELECT count(*) FROM profiles').fetchone()[0],
                                   'api_corroboration_groups':sum(r.get('group_size',1)>1 for r in successful),
                                   'foreign_key_errors':con.execute('PRAGMA foreign_key_check').fetchall(),
                                   'quick_check':con.execute('PRAGMA quick_check').fetchone()[0]}
            report['worker_jobs_with_errors'] = con.execute('SELECT source_kind,source_id,attempts,error FROM ml_jobs WHERE error IS NOT NULL LIMIT 20').fetchall()
            report['real_inference'] = {'sources':con.execute("SELECT count(*) FROM ml_sources WHERE model_version != '' AND result != '{}'").fetchone()[0],
                                       'embedding_chunks':con.execute('SELECT count(*) FROM ml_embeddings').fetchone()[0]}
        for r in successful[:10]:
            detail = request(r['client'], '/api/items/'+r['id'])
            assert detail['body'] == r['body'], 'Public item read did not preserve body'
        baseline = report['phases']['worker_off']['write']['p95_seconds']
        active = report['phases']['worker_on']['write']['p95_seconds']
        report['write_p95_gate_seconds'] = max(1.0, 1.25*baseline) if baseline is not None else None
        if baseline is None or active is None or active > report['write_p95_gate_seconds']:
            report['failures'].append('Write p95 exceeds paired acceptance gate or has no samples')
        if any(p['errors'] for p in report['phases'].values()):
            report['failures'].append('Public API requests failed')
        if missing or duplicates or failed_persisted:
            report['failures'].append('Write integrity or response-commit consistency failed')
        if report['integrity']['foreign_key_errors'] or report['integrity']['quick_check']!='ok':
            report['failures'].append('SQLite integrity failed')
        if report['real_inference']['sources']==0 or report['real_inference']['embedding_chunks']==0:
            report['failures'].append('No completed real inference during worker-on window')
        if worker.poll() is not None:
            report['failures'].append('Worker stopped before load phase completed')
    except Exception as error:
        report['failures'].append(f'Driver failure: {type(error).__name__}: {error}')
        import traceback
        traceback.print_exc()
    finally:
        monitoring.set()
        if thread.is_alive():
            thread.join(timeout=3)
        for proc in (worker, web):
            if proc and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
        listener.close()
        worker_log.close()
        web_log.close()
        report.update(lock_accounting((out/'worker.log').read_text(), (out/'web.log').read_text()))
        if not report['worker_lock_retry_accounting_complete']:
            report['failures'].append('Final worker SQLite retry accounting is missing, malformed, or ambiguous')
        if report['worker_unhandled_lock_mentions']:
            report['failures'].append('Unhandled database lock errors in worker log')
        if report['web_unhandled_lock_mentions']:
            report['failures'].append('Unhandled database lock errors in web log')
        report['resources'] = {key: max((s[key] for s in report['samples'] if key in s),default=None)
                               for key in ('worker_tree_rss','web_tree_rss','wal_bytes','database_bytes','cgroup_memory_bytes')}
        report['elapsed_seconds'] = time.monotonic()-started
        report['verdict'] = 'FAIL' if report['failures'] else 'PASS'
        (out/'report.json').write_text(json.dumps(report, indent=2))
        print(json.dumps({key: report[key] for key in ('verdict', 'failures', 'resources',
                                                     'worker_handled_contention_retries_by_operation')}, indent=2), flush=True)
        print('ARTIFACT_DIRECTORY=' + str(out), flush=True)
    return 1 if report['failures'] else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--web-python', type=Path, required=True, help='Python executable with the deployed web dependencies')
    parser.add_argument('--worker-python', type=Path, required=True, help='Python executable with the deployed ML dependencies and psutil')
    parser.add_argument('--models', type=Path, required=True, help='Prepared local model directory containing models.json')
    parser.add_argument('--source-root', type=Path, default=Path(__file__).resolve().parents[1], help='Repository root to snapshot (default: this checkout)')
    parser.add_argument('--output-parent', type=Path, default=Path(tempfile.gettempdir()), help='Existing parent for a new, private run directory (default: system temp)')
    parser.add_argument('--seconds', type=int, default=60, help='Seconds per phase (default: 60; preserve this for the fixed capacity check)')
    args = parser.parse_args(argv)
    if sys.platform != 'linux':
        parser.error('Run the capacity check on Linux; --help is available on other systems')
    for name in ('web_python', 'worker_python'):
        # Preserve venv executable symlinks; resolving them changes the Python environment.
        path = getattr(args, name).expanduser().absolute()
        if not path.is_file() or not os.access(path, os.X_OK):
            parser.error(f'--{name.replace("_", "-")} must be an executable file')
        setattr(args, name, path)
    args.models = args.models.expanduser().resolve()
    args.source_root = args.source_root.expanduser().resolve()
    args.output_parent = args.output_parent.expanduser().resolve()
    if not (args.models / 'models.json').is_file():
        parser.error('--models must contain a prepared models.json manifest')
    if not (args.source_root / 'backend/alembic.ini').is_file():
        parser.error('--source-root must contain backend/alembic.ini')
    if not args.output_parent.is_dir() or args.seconds <= 0:
        parser.error('--output-parent must exist and --seconds must be positive')
    if Path(sys.executable).absolute() != args.worker_python:
        os.execv(str(args.worker_python), [str(args.worker_python), str(Path(__file__).absolute()), *(sys.argv[1:] if argv is None else argv)])
    def stop(*_):
        raise KeyboardInterrupt
    previous = signal.signal(signal.SIGTERM, stop)
    try:
        return run(args)
    except KeyboardInterrupt:
        print('Load check interrupted', file=sys.stderr)
        return 130
    except Exception as error:
        print(f'Load check failed: {type(error).__name__}: {error}', file=sys.stderr)
        return 1
    finally:
        signal.signal(signal.SIGTERM, previous)


if __name__ == '__main__':
    raise SystemExit(main())
