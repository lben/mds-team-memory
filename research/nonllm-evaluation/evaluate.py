"""Separate development screens; identical final-state evidence and domain folds."""
import collections
import importlib.util
import json
import math
from pathlib import Path

OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[1]
EXP=ROOT/'data/ml-runs/with-opus55/expanded-concepts'
spec=importlib.util.spec_from_file_location('original_judge',EXP/'judge_expanded.py')
judge=importlib.util.module_from_spec(spec)
spec.loader.exec_module(judge)
BASE_PUBLISHED=judge.published


def screen(name, scores_path=None, force_two=False, fixed_grid=None):
    judge.published=BASE_PUBLISHED
    if force_two:
        def two(case, config):
            result=set()
            for concept, rows in case['evidence'].items():
                groups,_=judge.independent_support(rows)
                if groups >= 2 and max(r['raw_score'] for r in rows) >= config[2]:
                    result.add(concept)
            return result
        judge.published=two
    if scores_path:
        scores=[v['margin'] for row in map(json.loads,Path(scores_path).read_text().splitlines())
                for v in row['scores'].values()]
        scores.sort()
        judge.TAUS=fixed_grid or [judge.NO_FILTER]+[scores[min(len(scores)-1,int(len(scores)*q/10))] for q in range(1,10)]
    else:
        judge.TAUS=[judge.NO_FILTER]
    cases=judge.load(scores_path)
    folds=[]
    for domain in sorted({c['domain'] for c in cases}):
        train=[c for c in cases if c['domain'] != domain]
        test=[c for c in cases if c['domain'] == domain]
        config,fitted=judge.choose(train)
        folds.append({'domain':domain,'config':list(config),'training':fitted,
                      'held_out':judge.metrics(test,config)})
    result=judge.pooled([f['held_out'] for f in folds])
    gates={'precision_98':(result['complete_output_precision'] or 0)>=0.98,
           'selected_precision_98':(result['selected_decision_precision'] or 0)>=0.98,
           'recall_50':result['selected_recall']>=0.5}
    report={'strategy':name,'fixture_sha256':judge.sha(judge.FIXTURE),'scores_sha256':judge.sha(scores_path) if scores_path else None,
            'folds':folds,'pooled_held_out':result,'gates':gates,
            'status':'SHORTLIST_ONLY' if all(gates.values()) else 'FAIL_SCREEN',
            'release_quality_pass':False,'scope':'concept-only proxy, spent development corpus; no application integration'}
    (OUT/f'{name}-decision.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k != 'folds'},indent=2))
    return report


def main():
    result={}
    # Controls use the same proxy, not the non-comparable full-app batch-5 report.
    judge.published=BASE_PUBLISHED
    cases=judge.load(None)
    result['original_evidence_control']={'pooled_held_out':judge.pooled([judge.metrics(cases,judge.CONTROL)]),
                                         'scope':'fixed original control, no classifier'}
    result['qwen_control']=screen('qwen_control',str(EXP/'score-run-1/scores.jsonl'),fixed_grid=[judge.NO_FILTER,-4,-2,0,2,4,6,8])
    result['confidence_only']=screen('confidence_only')
    result['two_independent_sources']=screen('two_independent_sources',force_two=True)
    result['structural']=screen('structural',str(OUT/'structural-scores.jsonl'),fixed_grid=[judge.NO_FILTER,-2,-1,0,1,2])
    for fmt in ('plain','described'):
        result[f'decide_{fmt}']=screen(f'decide_{fmt}',str(ROOT/f'data/ml-runs/with-opus55/decide-screen/scores-{fmt}/scores.jsonl'))
    for method in ('bge','nli'):
        path=OUT/f'{method}-scores.jsonl'
        if (OUT/f'{method}-execution.json').exists():
            result[method]=screen(method,str(path))
    (OUT/'comparison.json').write_text(json.dumps(result,indent=2)+'\n')


if __name__ == '__main__':
    main()
