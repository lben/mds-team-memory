"""Isolated scoring experiments. Does not import or modify deployment/application state."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import resource
import time

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
EXP = ROOT / 'data/ml-runs/with-opus55/expanded-concepts'
NLI_REVISION = '72873be33d4058aac21d3c9e86036a8901636537'
NLI_HYPOTHESIS = 'The post discusses {name} as a specific subject of knowledge, explaining its role, behavior or properties.'
MEASUREMENT = re.compile(r'^[-+]?\d+(?:[.,]\d+)?\s*(?:%|ms|s|sec|seconds?|minutes?|hours?|mm|cm|m|km|kg|g|mb|gb|gib|bytes?)?$', re.I)
INCIDENTAL = re.compile(r'\b(?:during|near|beside|at|inside|outside|before|after)\s+(?:the\s+)?$', re.I)
PREDICATE = re.compile(r'^\s*(?:[,()]\s*)?(?:is|are|was|were|uses?|requires?|produces?|replaces?|contains?|retains?|routes?|measures?|stores?|controls?|connects?|depends?|operates?|supports?|compares?|failed|succeeds?|means|refers?)\b', re.I)
RELATION = re.compile(r'\b(?:uses?|requires?|produces?|replaces?|contains?|retains?|routes?|measures?|stores?|controls?|connects?|depends?\s+on|part\s+of)\b', re.I)


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def inputs():
    sources = {s['source_key']: s for s in json.loads((EXP/'sources.json').read_text())['sources']}
    rows = []
    for line in (EXP/'extract-control/raw-sources.jsonl').read_text().splitlines():
        row = json.loads(line)
        source = sources[row['source_key']]
        assert source['body_sha256'] == row['body_sha256'] == hashlib.sha256(source['body'].encode()).hexdigest()
        names = {}
        for p in row['proposals']:
            if p['specific_name']:
                names.setdefault(' '.join(p['name'].casefold().split()), p['name'])
        rows.append((row['source_key'], source['body'], list(names.values())))
    return rows


def structural(body, name):
    if MEASUREMENT.fullmatch(name.strip()):
        return -2.0
    mentions = list(re.finditer(re.escape(name), body, re.I))
    scores = []
    for match in mentions:
        left, right = body[max(0, match.start()-90):match.start()], body[match.end():match.end()+100]
        if PREDICATE.search(right):
            scores.append(2.0)
        elif RELATION.search(left[-65:]) and not INCIDENTAL.search(left):
            scores.append(1.0)
        elif INCIDENTAL.search(left):
            scores.append(-1.0)
        else:
            scores.append(0.0)
    return max(scores, default=-2.0)


def main(method):
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', TOKENIZERS_PARALLELISM='false',
                      HF_HUB_DISABLE_TELEMETRY='1', OMP_NUM_THREADS='4', MKL_NUM_THREADS='4')
    rows = inputs()
    model = tokenizer = None
    loaded = time.perf_counter()
    if method != 'structural':
        import torch
        torch.set_num_threads(4)
        torch.set_num_interop_threads(1)
        if method == 'bge':
            from sentence_transformers import SentenceTransformer
            model = SentenceTransformer(str(ROOT/'data/ml-runs/with-opus55/assets-v9/embeddings'),
                                        local_files_only=True, trust_remote_code=False, device='cpu')
            model.max_seq_length = 512
        elif method == 'nli':
            from transformers import AutoModelForSequenceClassification, AutoTokenizer
            path = OUT/'nli-model'
            tokenizer = AutoTokenizer.from_pretrained(str(path), local_files_only=True, trust_remote_code=False)
            model = AutoModelForSequenceClassification.from_pretrained(str(path), local_files_only=True,
                                                                        trust_remote_code=False).eval()
            labels = {int(k): v.casefold() for k,v in model.config.id2label.items()}
            assert set(labels.values()) == {'contradiction','entailment','neutral'}, labels
            entailment = next(k for k,v in labels.items() if v == 'entailment')
            negative = [k for k,v in labels.items() if v != 'entailment']
        else:
            raise ValueError(method)
    load_seconds = time.perf_counter()-loaded
    began = time.perf_counter()
    source_times = []
    with (OUT/f'{method}-scores.jsonl').open('w') as f:
        for index, (key, body, names) in enumerate(rows):
            start = time.perf_counter()
            scores = {}
            if names and method == 'structural':
                scores = {name: {'margin': structural(body,name)} for name in names}
            elif names and method == 'bge':
                vectors = model.encode([body, *names], normalize_embeddings=True, batch_size=16,
                                       show_progress_bar=False, convert_to_numpy=True)
                scores = {name: {'margin': float(vectors[0] @ vectors[i+1])} for i,name in enumerate(names)}
            elif names and method == 'nli':
                hypotheses = [NLI_HYPOTHESIS.format(name=name) for name in names]
                with torch.inference_mode():
                    encoded = tokenizer([body]*len(names), hypotheses, padding=True,
                                        truncation='only_first', max_length=512, return_tensors='pt')
                    logits = model(**encoded).logits
                    margins = logits[:,entailment] - torch.logsumexp(logits[:,negative], dim=1)
                scores = {name: {'margin': float(margins[i]), 'p_yes': float(torch.sigmoid(margins[i]))}
                          for i,name in enumerate(names)}
            assert all(math.isfinite(v['margin']) for v in scores.values())
            elapsed = time.perf_counter()-start
            source_times.append(elapsed)
            f.write(json.dumps({'source_key':key, 'scores':scores, 'seconds':elapsed})+'\n')
            f.flush()
            if (index+1)%100 == 0:
                print(json.dumps({'method':method,'sources':index+1,'total':len(rows),
                                  'seconds':round(time.perf_counter()-began,1)}),flush=True)
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    import sys
    if sys.platform != 'darwin':
        rss *= 1024
    receipt = {'method':method,'sources':len(rows),'candidates':sum(len(n) for _,_,n in rows),
               'load_seconds':load_seconds,'scoring_seconds':time.perf_counter()-began,
               'source_median_seconds':sorted(source_times)[len(source_times)//2],
               'peak_process_rss_bytes':rss,'threads':4,'device':'cpu','platform':sys.platform,
               'script_sha256':sha(__file__),'inputs_sha256':sha(EXP/'sources.json'),
               'proposals_sha256':sha(EXP/'extract-control/raw-sources.jsonl'),
               'scores_sha256':sha(OUT/f'{method}-scores.jsonl')}
    if method == 'nli':
        receipt.update(model='cross-encoder/nli-MiniLM2-L6-H768', revision=NLI_REVISION,
                       parameters=sum(p.numel() for p in model.parameters()),
                       model_sha256=sha(OUT/'nli-model/pytorch_model.bin'))
    (OUT/f'{method}-execution.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps(receipt,indent=2),flush=True)


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('method',choices=['structural','bge','nli'])
    main(parser.parse_args().method)
