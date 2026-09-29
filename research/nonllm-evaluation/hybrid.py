"""Separately registered, development-only follow-up after the initial BGE screen."""
import hashlib
import json
from pathlib import Path
import evaluate

OUT=Path(__file__).resolve().parent


def main():
    plan_path=OUT/'hybrid-plan.json'
    assert plan_path.exists(), 'Freeze the supplemental plan before running.'
    plan=json.loads(plan_path.read_text())
    assert plan['script_sha256']==hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    structural={r['source_key']:r['scores'] for r in map(json.loads,(OUT/'structural-scores.jsonl').read_text().splitlines())}
    originals=list(map(json.loads,(OUT/'bge-scores.jsonl').read_text().splitlines()))
    raw=sorted(v['margin'] for r in originals for v in r['scores'].values())
    grid=[0.0]+[max(0.0,raw[int(len(raw)*q/10)]) for q in range(1,10)]
    excluded=0
    with (OUT/'hybrid-scores.jsonl').open('w') as f:
        for row in originals:
            scores={}
            for name, value in row['scores'].items():
                reject=structural[row['source_key']][name]['margin']<0
                excluded+=int(reject)
                scores[name]={'margin':-1.0 if reject else value['margin']}
            f.write(json.dumps({'source_key':row['source_key'],'scores':scores})+'\n')
    result=evaluate.screen('hybrid',str(OUT/'hybrid-scores.jsonl'),fixed_grid=grid)
    result.update(followup_after_bge_results=True,excluded_candidate_occurrences=excluded,
                  supplemental_plan_sha256=hashlib.sha256(plan_path.read_bytes()).hexdigest())
    (OUT/'hybrid-decision.json').write_text(json.dumps(result,indent=2)+'\n')


if __name__=='__main__':
    main()
