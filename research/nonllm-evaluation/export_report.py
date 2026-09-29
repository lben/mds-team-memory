"""Export isolated research results; no application or deployment writes."""
from pathlib import Path
import hashlib
import html
import json
import math
import statistics

OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[1]


def strict(value):
    if isinstance(value,float) and not math.isfinite(value):
        return '-infinity' if value<0 else 'infinity'
    if isinstance(value,dict):
        return {k:strict(v) for k,v in value.items()}
    if isinstance(value,list):
        return [strict(v) for v in value]
    return value


def main():
    comparison=json.loads((OUT/'comparison.json').read_text())
    definitions={
      'original_evidence_control':('Original evidence rules without Qwen','Fixed historical control: extractor confidence and independent evidence.','Retained extractor observations; new replay'),
      'qwen_control':('Qwen 4B control','Fixed Yes/No eligibility margin. Same twelve-domain proxy used for every row.','Retained Qwen scores; exactly reproduced original replay'),
      'confidence_only':('Stricter confidence rules','Select one/two-source confidence cutoffs without a semantic verifier.','New isolated rule screen'),
      'two_independent_sources':('Require two independent sources','Require provenance-independent evidence, then select confidence cutoff.','New isolated rule screen'),
      'structural':('Structural rules','Fixed predicate, relation, incidental-preposition and measurement patterns. No learned weights.','New scoring and isolated replay'),
      'decide_plain':('GLiNER2.5-Decide — plain question','340M non-generative decision encoder answering one typed question per candidate.','Retained inference scores; exactly reproduced original replay'),
      'decide_described':('GLiNER2.5-Decide — described labels','Same decision encoder with descriptions for its two answer labels.','Retained inference scores; exactly reproduced original replay'),
      'bge':('BGE relevance','Reuse the existing BGE-large checkpoint: cosine similarity between candidate and full source.','New inference and isolated replay'),
      'nli':('MiniLM entailment classifier','82.1M pretrained encoder scores whether a fixed substantive-subject hypothesis follows from the post.','New inference, isolated replay and offline Red Hat compatibility probe'),
      'hybrid':('BGE + structural veto','BGE relevance with a mandatory veto for negative structural scores.','Supplemental follow-up registered after the initial BGE result')}
    report={'date':'2026-09-29','scope':'Separate research experiments; no replacement implemented',
      'conclusion':'No tested non-LLM strategy meets both current concept-screen precision and recall minima. BGE is the most promising coverage/cost tradeoff, but misses precision.',
      'corpus':{'cases':300,'domains':12,'source_versions':1499,'candidate_occurrences':3387,
                'selected_positive_decisions':150,'max_source_characters':418,'max_source_whitespace_tokens':67,
                'status':'previously spent synthetic development corpus; not fresh release validation'},
      'selection':'Leave-one-domain-out. Select evidence/score cutoffs only on the other eleven domains, using original 98.5% training precision objective. Unlabeled deciles for new continuous signals. No weights fitted.',
      'requirements':{'complete_output_precision_min':.98,'selected_decision_precision_min':.98,'selected_recall_min':.5},
      'metric_definitions':{'precision':'Correct concepts divided by all concepts the proxy publishes, including outputs beyond selected labels.',
                            'recall':'Selected valid concepts published divided by the 150 selected positives.'},
      'production':{'branch':'withgpt6.1solhigh','commit':'e51318b','changed':False,'pushed':False,'qwen_still_deployed_by_update':True},
      'limits':['This proxy omits the application identity/alias paths and all alias/relationship/expertise effects.',
                'The corpus has informed past development. Domain folds are exploratory evidence, not untouched evaluation.',
                'The hybrid was added after observing the BGE aggregate result; it is a separate development follow-up.',
                'These short posts do not establish long-document quality or production server throughput.',
                'Point-estimate differences are not established improvements in deployment; precision intervals overlap.',
                'Native macOS CPU timing is indicative. Historical Qwen/Decide runs are not a controlled paired speed benchmark.',
                'The new NLI Red Hat run is a compatibility probe, not a full accuracy/capacity test.'],
      'strategies':{},'resources':{},'integrity':json.loads((OUT/'integrity-check.json').read_text()),
      'nli_compatibility':json.loads((OUT/'nli-compatibility.json').read_text()),
      'plan_sha256':hashlib.sha256((OUT/'plan.json').read_bytes()).hexdigest()}
    rows=[]
    for key,value in comparison.items():
        label,definition,provenance=definitions[key]
        report['strategies'][key]={'label':label,'definition':definition,'execution':provenance,**value}
        m=value['pooled_held_out']
        verdict='CONTROL' if key in ('qwen_control','original_evidence_control') else 'FAIL — precision' if m['complete_output_precision']<.98 else 'FAIL — recall'
        rows.append(f'<tr><td><b>{html.escape(label)}</b><small>{html.escape(definition)}</small></td><td>{m["correct"]}/{m["published"]}<br><b>{100*m["complete_output_precision"]:.2f}%</b></td><td>{m["selected_hits"]}/150<br><b>{100*m["selected_recall"]:.2f}%</b></td><td>{html.escape(verdict)}</td><td>{html.escape(provenance)}</td></tr>')
    for name in ('structural','bge','nli'):
        report['resources'][name]=json.loads((OUT/f'{name}-execution.json').read_text())
    for name,path in [('qwen',ROOT/'data/ml-runs/with-opus55/expanded-concepts/score-run-1/scores.jsonl'),
                      ('decide_plain',ROOT/'data/ml-runs/with-opus55/decide-screen/scores-plain/scores.jsonl')]:
        ts=[json.loads(line)['seconds'] for line in path.read_text().splitlines()]
        report['resources'][name]={'historical_retained_run':True,'source_median_seconds':statistics.median(ts),
                                   'summed_source_scoring_seconds':sum(ts),'threads':4,'platform':'darwin'}
    (OUT/'report.json').write_text(json.dumps(strict(report),indent=2,allow_nan=False)+'\n')
    costs=[]
    for name in ('qwen','decide_plain','bge','nli','structural'):
        r=report['resources'][name]
        rss=f'{r["peak_process_rss_bytes"]/1024**3:.3f} GiB' if 'peak_process_rss_bytes' in r else 'Not compared here'
        costs.append(f'<tr><td>{html.escape(name)}</td><td>{r["source_median_seconds"]:.6f} s</td><td>{rss}</td><td>{"Historical retained run" if r.get("historical_retained_run") else "New native CPU run"}</td></tr>')
    limits=''.join(f'<li>{html.escape(s)}</li>' for s in report['limits'])
    page='''<!doctype html><html lang="en"><meta charset="utf-8"><title>Non-LLM replacement evaluation</title>
<style>body{font:16px/1.55 system-ui,sans-serif;color:#172637;background:#f6f8fb;margin:0}main{max-width:1180px;margin:40px auto;padding:30px;background:white;border-radius:12px}h1{font-size:30px;margin:0}h2{margin-top:32px;font-size:21px}p{max-width:980px}table{border-collapse:collapse;width:100%;font-size:14px}th{text-align:left;background:#edf2f8}th,td{padding:12px;border-bottom:1px solid #dce3ec;vertical-align:top}small{display:block;color:#516175;margin-top:5px}.note{padding:16px;background:#eef3ff;border-left:4px solid #4263bd}a{color:#2458a0}.muted{color:#516175}</style><main>
<h1>Non-LLM replacement evaluation</h1><p class="muted">29 September 2026 · isolated experiments · withgpt6.1solhigh @ e51318b</p>
<p class="note"><b>No tested replacement meets both current quality minima.</b> BGE is the strongest coverage/cost tradeoff, but remains below 98% precision. Qwen, application code and deployment assets remain unchanged. Nothing was pushed.</p>
<h2>Comparable quality results</h2><p>300 previously spent synthetic cases across twelve domains; 1,499 source versions and 3,387 candidate occurrences. Every row uses the same final-state concept publication proxy. Threshold selection uses the other eleven domains; no neural weights were fitted. Required precision ≥98%, selected recall ≥50%. These are separate from the previously reported full-application batch-5 results.</p>
<table><thead><tr><th>Strategy</th><th>Complete-output precision</th><th>Selected recall</th><th>Result</th><th>Test provenance</th></tr></thead><tbody>'''+''.join(rows)+'''</tbody></table>
<p><b>Precision</b> counts every published concept, including unselected outputs. <b>Recall</b> measures recovery of the 150 selected valid concepts. Reproduced Qwen and both Decide controls match their original pooled metrics exactly. The structural strategy reaches high precision by withholding most valid concepts. BGE recovers more selected positives, but also publishes more unsupported names.</p>
<h2>CPU cost observations</h2><p>Median scoring time per source, excluding initial loading and common extraction work. The native CPU runs use four PyTorch/model threads. Memory is total standalone scorer-process peak RSS, not additional memory in a deployed worker.</p>
<table><thead><tr><th>Scorer</th><th>Median/source</th><th>Peak RSS</th><th>Timing provenance</th></tr></thead><tbody>'''+''.join(costs)+'''</tbody></table>
<p>BGE reuses the existing model checkpoint and would need no additional weights. The new MiniLM checkpoint is 328,532,073 bytes and has 82,120,707 parameters. Its offline UBI 8.10 compatibility probe passed under four CPUs and a 2 GiB container limit; native/Linux margins agreed within 0.000006. This does not turn its failed accuracy screen into a pass.</p>
<h2>Interpretation and limits</h2><ul>'''+limits+'''</ul>
<p>The pretrained entailment model evaluates sentence-pair entailment, contradiction and neutrality; this is not the same task as determining a reusable, substantive knowledge concept. It uses a non-generative encoder. Model background: <a href="https://huggingface.co/cross-encoder/nli-MiniLM2-L6-H768">MiniLM NLI card</a>, <a href="https://github.com/huggingface/sentence-transformers/blob/main/docs/cross_encoder/pretrained_models.md">Sentence Transformers NLI documentation</a>. BGE relevance uses <a href="https://sbert.net/docs/sentence_transformer/usage/semantic_textual_similarity.html">cosine similarity</a>.</p>
<p><b>Recommendation:</b> retain Qwen for the prepared deployment. If eliminating the LLM becomes the priority, BGE is the best candidate for further isolated work, followed by full-application shadow evaluation and fresh validation. Do not lower the quality target to make a replacement pass.</p>
<h2>Reproducible evidence</h2><p><a href="report.json">Full machine-readable report</a> · <a href="plan.json">Frozen initial plan</a> · <a href="hybrid-plan.json">Supplemental hybrid plan</a> · <a href="integrity-check.json">Integrity check</a> · <a href="nli-compatibility.json">NLI Red Hat compatibility</a></p>
<p class="muted">Scoring scripts, per-source scores, per-domain decisions, model pin/checksums and console logs are retained beside this report. No UAT/PROD data or credentials were used.</p></main></html>'''
    (OUT/'report.html').write_text(page)
    print('Exported',OUT/'report.html')


if __name__=='__main__':
    main()
