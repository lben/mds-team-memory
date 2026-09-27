# Automatic maintenance: current status

Branch: `with-opus55`, created 27 September 2026 from `astra-again-sep14` at
`551e52a`. The sections below that branch point describe `astra-again-sep14`.

## Current position — September 27

Batch 2 recorded a development GO for the concept-eligibility filter; nothing
is integrated into the application yet. Batch 3 (integration with staged model
loading, then an application-level measurement on the expanded corpus) is
proposed but not started. Alternative open-source structured-extraction
approaches are under consideration first. Release quality remains open for
every category.

## Expanded-corpus concept-eligibility screen — September 27 (`with-opus55`, batch 2)

Batch 1's small screen was too coarse to choose a rule (each error moved
precision by three points), and its generated reasons were defective. Batch 2
re-ran the check on the spent 300-case expanded corpus (`ml_heldout.json`,
1,499 source versions, 12 domains), which is development evidence only.

- **Extractor evidence:** the production GLiNER2.5 base entity pass (same
  schema, windows and grounding as `runtime.py`) in the pinned offline image,
  four CPUs, 5 GiB: all 1,499 sources, 3,060 s, 2.28 GB cgroup peak, no OOM.
- **Eligibility signal:** the same Qwen3-4B-Instruct model and definition text,
  but no generation: one fixed Yes/No question per candidate and the logit
  margin of "Yes" over "No", reusing the post prefix in the KV cache. A
  synthetic-post check confirmed rewind/fresh equality and order and thread
  invariance. 3,387 candidates; every top token was Yes or No; median 1.8 s per
  source on four development-machine CPU threads.
- **Frozen before labeled results** (`plan.json`): the rule family (count only
  evidence with margin ≥ τ, then publish at single-source score ≥ s1 or two
  independent groups at ≥ s2; the production rule is one member), the grid,
  leave-one-domain-out selection at ≥ 98.5% training precision, and the gates.

| Rule (proxy judge, final state after edits/deletes) | Precision, all published names | Selected decisions | Selected recall |
| --- | ---: | ---: | ---: |
| Production rule | 579/605 (95.70%) | 102/102 | 102/150 (68%) |
| Pooled held-out domains, eligibility-filtered | 498/507 (98.22%, Wilson 96.66–99.06%) | 99/99 | 99/150 (66%) |
| All-data selection τ=4, s1=0.995, s2=0.9 (optimistic) | 529/537 (98.51%) | 106/106 | 106/150 (70.7%) |

Disposition: GO_TO_INTEGRATION_AND_FRESH_VALIDATION under the frozen gates.
Every fold selected s1 = 0.995; s2 ranged 0.8–0.94 and τ 2–8. This is not a
release result: the judge is a proxy without the application's alias and
definition identity paths, the corpus is spent, and the Wilson lower bound is
below 98%. The integrated application must be measured on this corpus, then
on fresh independent cases.

One independent read-only reviewer returned PASS: an independent recomputation
reproduced every count, and label isolation, final-state handling, fold
separation and the hash freeze hold (plan SHA256 `2fd8eaa6…4f35c`, decision
`09b9853e…e2ad0`). Its caveats bound the result:

- The margin is thin: 11 unsupported names instead of 9 would fail. In 6 of 12
  folds no configuration reached 98.5% training precision, and the
  pre-registered highest-precision fallback applied.
- The corpus was spent and earlier all-corpus score diagnostics informed the
  grid, so 98.22% is an optimistic development estimate.
- The proxy omits identity paths. The full application previously recorded
  147/150 selected and 500/528 published concepts on this corpus, so proxy
  absolute numbers do not transfer.
- The gain comes from the eligibility filter: the same procedure without it
  gives 96.44% (reviewer's own check, not pre-registered).

Resources: the classifier alone peaked at 2.95 GB in a 5 GiB Linux cgroup
(2,048-token context), so it cannot be resident with the three current models;
the worker needs staged loading. A portable Linux build without the host's
SIMD extensions ran about 28 times slower than the native build, so the
deployment wheel must target the server's instruction set and be timed there.

Evidence: `data/ml-runs/with-opus55/expanded-concepts/` (`plan.json`,
`extract-control/`, `score-run-1/`, `control-only.json`, `decision.json`,
`mechanics_check.py`) and `data/ml-runs/with-opus55/llm-linux/`.

## LLM concept-eligibility screen — September 27 (`with-opus55`, batch 1)

Working rules for this branch: the acceptance targets stay fixed; evaluation
uses AI-authored cases only; permissively licensed CPU models may be added with
pinned hashes; work proceeds in bounded batches with at most one independent
reviewer per material change.

The concept definition is semantic ("a substantive subject, not a mere
occurrence"), which an entity extractor cannot judge. One untried direction was
screened: a local instruction model checking the current extractor's proposals.
Model: Qwen3-4B-Instruct-2507 (Apache-2.0), Q4_K_M GGUF from
`unsloth/Qwen3-4B-Instruct-2507-GGUF@a06e946`, SHA256 `3605803b…c67e597`, run
with llama-cpp-python 0.3.35 (MIT) on four CPU threads, greedy decoding and
JSON-schema-constrained output. The prompt paraphrases the annotation
protocol's concept definition. Prompt, variants and gates were hashed in
`plan.json` before inference. Same 24-case development set and judge as the
earlier screens; the control judgment reproduced exactly.

| Variant | Supported | Unsupported | Precision | Selected hits | Gate |
| --- | ---: | ---: | ---: | ---: | --- |
| Control (current rules) | 33 | 3 | 91.67% | 30/38 | — |
| V1: control rules + LLM filter | 26 | 0 | 100% | 25/38 | FAIL (needs ≥28) |
| V2: publish when both models agree | 52 | 4 | 92.86% | 34/38 | FAIL (needs 98%) |

Disposition: REJECTED AS CONFIGURED. Production models, thresholds and
application source are unchanged. The filter removed every unsupported name,
and agreement found far more correct concepts, but neither rule met both gates.

The configuration itself was defective: 171 of 179 free-text reasons hit the
200-character schema cap before the accept field, 21 drifted into Chinese
tokens, and one call produced invalid output (fail-closed, costing one selected
hit). Decisions were inconsistent in both directions (for example rejecting
"Arduino" as the board using PWM, accepting "equipment"). Generating the reasons
dominated cost: median 11.5 s per post (about 180 output tokens). Peak RSS on
the development machine was 5.5 GB with a 4,096-token context; Linux
cgroup memory and x86 throughput were not measured.

Context for the next decision: on the spent 300-case expanded corpus
(`ml_heldout.json`, now development data), the earlier score-rule union reached
337/345 correct concepts (97.68%) with 126/150 selected positives. At that scale
one error moves precision by about 0.3 points rather than 3, so it is the
appropriate development set for any further concept rule. Its per-post
extractor outputs were not retained and would need regeneration.

Evidence: `data/ml-runs/with-opus55/llm-concept-screen/` (`plan.json`,
`run-1/`, `judge-repair.json`, `receipt.json`). The judge's baseline equality
check needed the same JSON canonicalization as the earlier filter screen; the
failed attempt is retained and no gate changed. Global quality remains open.

## Earlier branch history (`astra-again-sep14`)

Started 14 September 2026 from DeepSeek's `72e387e`.
The full objective is unfinished. On September 14 the user authorized independent
agents to review the work. Three independent reviews identified concrete
correctness, quality/evaluation and operational defects. Their findings and the
verified repair scope are in `ML_INDEPENDENT_REVIEW_SEP14.md`. Fresh evaluation authoring
will use separate agents who have not reviewed implementation or model outputs.
The earlier rejected model experiments remain closed. Historical requirements and results are in
`ML_IMPLEMENTATION_PLAN.md`; branch ancestry is in `BRANCH_EVOLUTION.txt`.

## Implementation checkpoint — September 27

**Core workflows implemented; model quality and release acceptance pending.**
The application includes automatic concept, alias, relationship and expertise
pipelines; graph/search/tag/routing updates; persistent administrator overrides;
bounded CPU workers; offline model installation; and migration, replay and
rollback handling. Current application, frontend and tool source is unchanged
from the packaged checkpoint with 772 passing Linux tests, zero failures and
zero skips. This is engineering verification, not a model-quality certificate.

Selected models remain GLiNER2.5 base, BGE-large-en-v1.5 and spaCy
en_core_web_trf. The implementation combines learned extraction and embeddings
with grammatical parsing, regex, conservative spelling/whole-word matching,
stemming, fuzzy near-duplicate matching and explicit evidence/publication rules.
It does not use a formal fuzzy inference engine, and similarity alone cannot
establish an alias or a typed factual relationship.

| Category | Required precision / recall | Evidence available | Acceptance |
| --- | --- | --- | --- |
| Concepts | 98% / 50% | Current 24-case development screen: 91.67% precision, 30/38 selected recall (78.95%) | Precision fails; development evidence only |
| Aliases | 99% / 50% | Earlier completed 300-case independent evaluation: 92.45% precision, 32.67% recall; newer interrupted run recovered 12/13 selected aliases without an incorrect alias output | Earlier evaluation fails; newer partial evidence is insufficient |
| Typed relationships | 98% / 50% | Newer interrupted run recovered 0/13 selected relationships and published none; precision undefined | Coverage fails; incomplete evaluation |
| Expertise | 95% / 50% | Newer interrupted run recovered 13/13 selected mappings without an incorrect expertise output | Only 13 positive cases, no negative controls; insufficient evidence |

These are different datasets and source snapshots, not one current-release
benchmark. Each category additionally requires 300 independent decisions, 150
positive examples, 100 hard negatives and 100 automatically applied predictions,
with complete-output auditing and the required public effects/lifecycle checks.
No category has a current release-quality pass. Earlier capacity and offline
checks remain evidence for their recorded snapshots; final release verification
and deployment-server UAT remain outstanding.

The September 27 short-label, calibrated-score and contextual-filter experiments
are rejected as tested. None changed selected production models or publication
settings. Their results were committed in `b95f37d`, `19556ad` and `2178143`;
the detailed local evidence remains under the ignored `data/ml-runs/` directory.
Do not repeat these variants or treat their best isolated precision as approval.

Remaining work is to qualify a configuration against the agreed accuracy and
coverage targets, complete consistent independent evaluation, and verify the
final release's actual-model behavior and operational limits. Further code
changes may be required for quality, but no tested replacement is qualified.

The objective remains reliable automatic knowledge maintenance with pretrained
models running locally on CPU: maintain concepts, aliases, relationships and
expertise and their graph/search/tag/routing effects; apply high-confidence
findings automatically and hold uncertainty for evidence; preserve admin edits
and opt-outs; support offline deployment and shared-server limits; and pass
quality and lifecycle verification. Acronym testing is one milestone within
that objective. The objective is not yet achieved.

## Fixed contextual concept filter — September 27

The user authorized one 20-minute test of a contextual filter on the current
GLiNER2.5 extractor. One native span-attribute schema was frozen before
inference: `knowledge_role` with `substantive knowledge topic` and
`incidental background context`. The native two-label softmax decision had to
select the substantive label with confidence strictly above 0.5. No label,
confidence rule, name blacklist, or expected answer changed after observation.

All original control proposals, extraction scores, and provenance were fixed.
The native attributed extraction was matched by source, exact character span,
and entity type. Every matching attribute had to pass; absent or conflicting
matches were held. New spans were retained as raw evidence but could not enter
publication. Original publication rules then evaluated the surviving evidence.

| Measurement | Original extractor | Contextual filter |
| --- | ---: | ---: |
| Supported eligible subjects | 33 | 11 |
| Unsupported eligible names | 3 | 0 |
| Observed subject precision | 91.67% | 100% |
| Selected targets recovered | 30/38 (78.95%) | 11/38 (28.95%) |

Disposition: REJECTED_FIXED_CONTEXT_FILTER. It removes all three false positives
but loses 19 of the 30 previously recovered selected targets and 22 correct
subjects overall. This fails the predeclared requirement to retain all 30
selected hits. The 100% precision is an observation on only eleven outputs,
not release-quality certification. There is no reason to expose fresh release
data or integrate this failed configuration.

Of 185 original proposals, 34 survive, 123 fail the attribute gate, and 28 lack
an exact match in the attributed extraction. Those last losses are caused by
the changed joint extraction query, not an observed negative attribute. All
three original false-positive phrases did have exact matches and were labeled
incidental context. The native interface therefore supplied a useful signal for
those errors but the complete fixed filter suppressed too much valid knowledge.
This result does not establish that every contextual classifier would fail.

The existing offline Linux image ran one zero-forward preparation and one
60-call inference pass covering all 60 public source versions; two private
records remained excluded. Full native inputs were verified without truncation
(maximum 127 tokens). Preparation took 47.50 seconds, inference/retention 94.43
seconds; peak inference cgroup memory was 2,389,159,936 bytes under the same
four-CPU/five-GiB/no-extra-swap limits. No new model was downloaded or loaded
alongside the current extractor. No application source or production setting
changed, and the existing engineering baseline was not redundantly rerun.

One independent reviewer verified the filtering, preserved scores/provenance,
baseline equality, and rejection. A tuple/list representation check initially
stopped grading; its sole repair canonicalizes the in-memory baseline through
JSON before equality. The original frozen judge and partial output remain
retained, and inference was not repeated. No scoring or acceptance rule changed.

Evidence under `data/ml-runs/astra-sep14/classic-concept-trial/`:
`context-filter-schema.json`, `context-filter-prepare-*`,
`context-filter-infer-*`, `context-filter-judgment/decision.json`,
`context-filter-judgment/filter-decisions.json`,
`context-filter-false-positive-diagnostic.json`, and
`context-filter-checkpoint-receipt.json`. The prospective corpus remains sealed;
the global objective remains unfinished.

## Confidence and evidence rule validation — September 27

At the user's request, the classic GLiNER short-label results underwent one
retrospective calibration/validation analysis. No model inference or download
was needed. The rule family remained: publish above a single-source score
threshold, or above a lower threshold with at least two independent provenance
groups. Names, grounding, ambiguity handling, current-public source selection,
subject deduplication, and truth annotations were unchanged.

For each of the nine annotated domains, fit both thresholds using only the other
domains, then evaluate the omitted domain. Every case and all its source
versions stay in one fold. Training maximizes supported subjects at a minimum
98% training precision, with frozen tie-breaks. Threshold candidates come only
from training scores. All 24 cases were evaluated exactly once, covering the
same 38 selected targets. This is decision-rule tuning, not a calibrated
probability model. The distinction and need to separate fitting/evaluation are
consistent with the [scikit-learn calibration documentation](https://scikit-learn.org/1.8/modules/calibration.html).

| Measurement | Supported subjects | Unsupported names | Precision | Selected recall |
| --- | ---: | ---: | ---: | ---: |
| Original publication rules | 4 | 0 | 100% | 4/38 (10.53%) |
| Fit and score on all development cases, optimistic only | 25 | 0 | 100% | 21/38 (55.26%) |
| Pooled omitted-domain evaluation | 23 | 1 | 95.83% | 20/38 (52.63%) |

The omitted-domain result FAILS the required 98% precision. It also loses 15
correct control subjects, although it recovers three selected control misses.
The all-data thresholds (0.909037 single-source, 0.800734 corroborated) are
training artifacts, not approved settings. Their apparently perfect fit must
not be substituted for the failed validation. The one held-out false positive
was the shortened name `Mars` in the exploration case, unsupported by the
unchanged concept truth; no post-result label amendment or name blacklist was
made. Correct extraction of an entity span does not by itself establish the
application's substantive, reusable-concept requirement.

The evidence-group checks confirm that repeated authors or copied text count
only once, while different authors and distinct texts can provide independent
groups. This validates those mechanics, not a guarantee that corroboration
makes a candidate publishable. Descriptive score bins likewise remain small,
correlated observations, not calibrated probabilities.

Disposition: DO NOT integrate these tuned rules. No production threshold or
model changed. This already-exposed development set is not prospective release
evidence: 24 cases cannot satisfy the 300-decision/100-applied-prediction minima,
and domains may share entity vocabulary. There is no basis here to certify
automatic publication or close the global objective. The next model decision
needs evidence of semantic concept eligibility, not another arbitrary threshold
adjustment on this set.

One independent reviewer recomputed the training-only threshold optima and
held-out counts, verified the hashes and fold separation, and found no actionable
defect. The original baseline was reproduced exactly before fitting. An initial
tuple-versus-JSON-list assertion mismatch was repaired before any fitting ran;
the original script and freeze are retained. Evidence under
`data/ml-runs/astra-sep14/classic-concept-trial/` includes
`policy-validation-plan.json`, `validate_short_label_policy.py`,
`policy-validation-cases.json`, `policy-validation-result.json`, and
`policy-validation-receipt.json`. The prospective release corpus was not opened.

## Fixed short-label concept schema — September 27

The user authorized one bounded test of a model-appropriate schema using the
already staged classic GLiNER medium checkpoint. One 14-label taxonomy was
frozen before inference: person, organization, location, event, project,
product, software, technology, scientific concept, method, process, material,
organism, and physical object. The checkpoint, source text, truth annotations,
native proposal cutoff (0.5), publication rules, and acceptance gates stayed
fixed. The original control results were reused and hash-verified.

The schema change restored extraction: 158 native proposals instead of zero.
A descriptive audit of current public source versions found matching,
unambiguous, source-supported proposals for 34/38 selected targets. Under the
unchanged publication rules, however, only 4/38 targets qualified (10.53% recall,
versus the control's 30/38). All four eligible subjects were supported; observed
precision is 100% on just four outputs, not evidence of release-level precision.
The candidate lost 29 of the control's 33 correct subjects and recovered no
control miss. It fails the frozen replacement screen and is CLOSED in this
configuration. This result is not a rejection of every possible use of the model.

Thirty extracted targets were held; four had no qualifying raw proposal. The
maximum native score was 0.968593; only six proposals reached 0.94 and none
reached the single-source publication threshold of 0.985. The immediate
integration obstacle is therefore the interaction of model scores with the
publication policy, alongside remaining extraction gaps. These scores have not
been calibrated as probabilities of correct publication. Lowering thresholds
against these observed cases would not establish safe automation. A future
integration decision would need separately validated confidence/evidence
calibration, rather than another label sweep or model download. No such trial
or production change was made in this batch.

Token-only preparation passed with zero model forward calls. The single
candidate inference pass completed 60 public source versions in 60.80 seconds,
with two private records explicitly skipped, no truncation, and 2,255,720,448
bytes peak cgroup memory. It used the existing offline Linux image, four CPUs,
five GiB, and no additional swap. One initial sandbox Docker-access failure
occurred before any container/model execution; the identical frozen preparation
then ran with Docker access. One independent reviewer found no concrete defect,
confirmed the reused control judgment was identical, and agreed with rejection.

Evidence is in `data/ml-runs/astra-sep14/classic-concept-trial/`:
`short-label-schema.json`, the `short-label-prepare-*` / `short-label-infer-*`
artifacts, `short-label-judgment/bounded-screen-decision.json`,
`short-label-proposal-diagnostic.json`, and `short-label-checkpoint-receipt.json`.
The prospective release corpus was not accessed. The existing 772-test Linux
engineering result remains the baseline; no application source changed or
redundant full test run occurred. The global quality objective is unfinished.

## Publisher-example sanity check — September 26

At the user's request, the same staged classic GLiNER checkpoint ran exactly
two calls on its pinned publisher README example. With the documented short
labels it returned 11 grounded entities; with the unchanged application
entity-description mapping it returned zero. Both calls used native threshold
0.5, identical source text and verified untruncated inputs. The offline run
completed in 29.29 seconds. Evidence: `classic-concept-trial/publisher-sanity-results/`.

This establishes working entity extraction on the publisher's task and narrows
the earlier failure to the tested application configuration. Because the label
categories and wording both differ, it does not isolate description length as
the sole cause. It is not a concept-quality pass or authorization to integrate
the model. No development/heldout case, threshold or production model changed.
Any future candidate must use a model-appropriate schema and demonstrate useful
concept extraction before larger evaluation or additional model downloads.

Adding knowledge does not retrain the current extractor or feed the whole
knowledge base into each extraction window. Independent source evidence and
established vocabulary can improve corroboration and identity resolution after
extraction; duplicated text/authors do not create independent support. More data
alone does not resolve the demonstrated schema mismatch or guarantee precision.

## Bounded feasibility checkpoint — September 26

The user resumed one 30-minute checkpoint after the process audit below. The
single classic GLiNER candidate is now CLOSED / REJECTED under the frozen
configuration. No production model or numeric publication rule changed.

Both extractors completed all 60 public source versions from the existing
24-case development fixture; two private records were explicitly skipped.
Separate preparation verified every complete native input without truncation.
Each inference pass made exactly 60 native entity calls in an offline four-CPU,
five-GiB container with no additional swap. The current extractor took 97.68
seconds including loading/retention; the candidate took 66.53 seconds. Both
finished without OOM or operational errors. Peak cgroup memory was 2.439 GB and
2.346 GB respectively. All frozen source/asset and retained artifact hashes pass.

In this entity-only eligibility screen, the current extractor recovered 30/38
selected positive concepts (78.95%). Its full output scored 33 supported subjects
and three unsupported names, giving 91.67% precision against the required 98%.
The unsupported names were contextual renovation/upgrade/audit phrases. Classic
GLiNER returned no native proposals at its frozen default 0.5 proposal cutoff
with the existing entity-description mapping: 0/38 recall, zero eligible
concepts. Its precision is undefined, not an estimated 0%; the machine judge's
numeric zero is a conservative failure sentinel. It loses all 33 correct control
subjects and recovers no missed target, so it cannot advance to integration.
This does not measure full-application accuracy or satisfy release sample minima.

One independent reviewer approved the exact pre-existing truth corrections and
identified three concrete harness/judge issues: ambiguity must consider all
current public meanings; malformed/partial native results must be retained;
and final evidence collection must obey deadline/reserve checks. Those issues
were repaired before inference; focused mechanical checks pass. Original
reviewed files, labels and preparations remain preserved. No prospective corpus
work, alternate-model trial or post-observation label/threshold change occurred.

Evidence is under `data/ml-runs/astra-sep14/classic-concept-trial/`:
`bounded-screen-decision.json`, `bounded-checkpoint-final-verification.json`,
the four `bounded-prepare-*` / `bounded-infer-*` result directories, and their
frozen inputs. The global objective remains unfinished. Stop at this result;
another implementation or research batch requires a new bounded decision.

## Historical execution pause — September 26 process audit

The user paused implementation to investigate excessive quota consumption. At
the end of that audit, the goal was paused and no subagents were running. The
user subsequently authorized the single bounded checkpoint recorded above.

The task history records two turns ending at the account usage limit. The latest
failed turn lasted 4.30 hours and contains 135 root shell calls, 11 distinct
subagents and two context compactions. These are activity counts, not an
itemized token bill. The goal counter stopped at 747,266 tokens on September 14;
it is not a complete measure of subsequent work or account-wide consumption.

Diagnosis: useful engineering fixes were followed by an expanding cycle of
dataset authoring, independent audits, annotation corrections, experiment
protocols and harness reviews. Individual experiments had limits; the overall
research effort did not. After engineering tests passed, there was no timely
go/no-go decision on the remaining model-quality feasibility. At audit time,
eight of the branch's 20 commits since DeepSeek's baseline were documentation-only; the latest four were
all documentation. Those facts do not make the repairs worthless, but show why
continued activity did not amount to completing the objective.

Preserve the verified checkpoint: 772 Linux tests pass. The full model-quality
gate has not passed. The complete prospective audit found 21 incorrect concept
polarities and 13 allowed-name issues; correcting the polarities leaves 84 valid
concept hard negatives against the existing minimum of 100. The newer classic
extractor passed strict offline loading in 26.65 seconds with all 224 parameter
tensors verified, no inference and a 2,529,943,552-byte cgroup peak. It has not
passed a concept-quality screen. Earlier dated pending states below are history.

On an explicitly resumed task, use one bounded completion decision at a time:
reuse completed reviews and passing checks; choose one existing candidate and
one fixed development screen before more release-corpus expansion; use at most
one independent reviewer for a concrete result; repair only identified defects;
do not recursively review reviews or reopen closed model experiments. Report a
failed feasibility result instead of automatically starting another model or
protocol cycle. Preserve acceptance targets and the unseen evaluation boundary.
Recommended first batch: a 30-minute feasibility checkpoint, with account usage
checked at its start and end. This is a work bound, not a promise of completion
or an enforceable per-task quota cap. No new test/model run occurred in this audit.

## Historical position — September 22, evening

The engineering checkpoint passes; the full objective does not yet pass.

- **Regression verification:** 772 tests pass, with no failures or skips, in one
  clean offline Linux run of the packaged application (655.34 seconds). All 220
  packaged files still match their pre-run hashes. Application source includes
  `a988634`; the isolated controller fixture includes `bcca9d6`. The two warnings
  are dependency deprecations. The full log, container state and source checks
  are in `fresh-evaluation-sep22/package-full-result.json` and its referenced files.
- **Quality:** the interrupted 13-case measurement remains development evidence,
  with zero selected relationships and 16/18 correct full-output concepts. It is
  not a release pass. Only three expected relationships had both endpoint
  concepts active, so adding relationship classification alone is insufficient.
- **Independent evaluation review:** the prospective replacement set is NOT
  APPROVED. An earlier source/selected-label review passed, but subsequent
  review found inconsistent concept specificity and alternate-boundary standards
  across the replacement and retained cases, followed by the fixed-label errors
  described below. No replacement inference ran.
  A separate uniform annotation method is independently approved in
  `ML_CONCEPT_ANNOTATION_PROTOCOL.md`. Two clean authors have completed all 300
  unobserved source reviews; their combined candidate preserves every other
  field and remains NOT APPROVED. Independent review has confirmed seven
  incorrect fixed concept labels in the first half alone. Correcting these
  would leave 98 concept hard negatives, below the unchanged 100 minimum;
  further author concerns await decisions. A separately approved prospective
  method permits one exact, independently ruled polarity-correction round while
  preserving source text, selected names, historical results and release minima.
  No corpus or inference approval follows from these method approvals.
- **Resource experiment:** the one fixed BGE-large INT8 versus FP32 screen ran
  after physical free space recovered. It is REJECTED: INT8 preserved 14/16
  retrieval rankings and lost two existing positive decisions at 0.75. Its
  785,338,368-byte inference RSS saving and 2,432,053,248-byte process peak met the
  memory gates, but cannot override failed accuracy preservation. Independent
  audit verified all 96 vectors, execution/source/asset hashes and exact frozen
  gates. The FP32 control exactly matches the prior baseline. This candidate is
  closed; the production model remains FP32. Evidence: `embedding-int8-trial/`.
- **Storage and staging:** the host initially recovered to approximately 2.1 GiB,
  enough for the existing-asset INT8 screen. It subsequently recovered to about
  21 GiB without further task cleanup. The pinned 780,735,696-byte classic GLiNER
  medium checkpoint and 4.14 MB of package/configuration/tokenizer inputs are now
  byte-verified for a separate development screen. Staging maintained more than
  20 GiB free. No inference or model activation followed. No unrelated Demucs
  image or other unrelated work was removed. Evidence: `classic-concept-trial/`.
- **Next technical assessment:** sequential model loading is explicitly allowed
  by the deployment plan and is being reviewed as a way to preserve FP32
  embedding behavior while making room for native concept extraction. Independent
  architecture review found it feasible: search consumes stored vectors and
  does not require BGE residency. Repeated model loading may still fail the
  unchanged capacity gate; the design is not implemented or measured. The single
  Qwen2.5-3B-Instruct quantized-model
  feasibility review found licensing, score-contract, offline dependency and
  capacity prerequisites unresolved; no screen or integration follows.
- **Prepared concept screen:** a source-only author is labeling all 60 original
  development posts and two edited source versions before one native entity
  comparison. A separate strict offline load-only preflight is being prepared.
  Neither activity uses the prospective corpus or approves execution. All
  original development labels/results and numeric publication rules are retained.

Selected production model assets and numeric evidence/acceptance rules remain
unchanged. Final-source actual-model compatibility and capacity measurements
will be required after any further model integration. Earlier dated sections
below retain the work history; their intermediate pending states are historical.

## Completed in this continuation

- Recorded the branch history in commit `b1c9e51`.
- Fixed immediate manual authority for existing inverse-alias routes. A new
  alias pin protects existing graph relationships before source replay; releasing
  it withdraws unsupported effects. Removed relationship evidence stays removed.
- Added migration `0011` to schedule bounded source replay in both directions.
  Downgrading through it to `0010` or `0009` does not leave incompatible identity
  certificates stranded. Previous migration files remain unchanged.
- Both independent review reproductions now pass. The focused Linux lifecycle
  suite passes 33 checks, including real migration and queue behavior.
- Restored the exact pinned syntax model and verified all three model assets.
  Prior extractor and embedding weights remain unchanged.
- Added explicit naming grammar with unchanged score and evidence requirements.
  Recorded model/parser observations recovered two additional development
  definitions; adversarial controls added no unsupported identities.
- Fixed definition coverage across rule revisions in publication, stored
  identity routes, cache reuse, replay completion, and expertise invalidation.
  The before-fix regressions failed through public APIs, including profiles
  computed before source replay. Migration `0012` adds the missing invalidation.
- A final grammar review narrowed `known as` so `known to` cannot propose an
  identity or false conflict. Its regression fails before the fix and passes
  afterward. All 119 retained trial observations remain exactly unchanged.
- The previous committed offline Linux regression suite passed: 462 tests in
  196 seconds at the policy-v6 checkpoint. Later source changes need their own
  full-suite result.
- The fixed 50,000-item/50-client capacity check passes. All 2,855 requests
  succeeded and all 565 writes were preserved. Five real sources/chunks
  completed during worker-on traffic. Write p95 was 4.316 seconds with ML
  versus 4.770 seconds without it (5.963-second limit). No unhandled locks,
  lost/duplicate writes, integrity errors, or worker job errors occurred.
  ML peak RSS was 3.80 GB; the combined container remained under five GiB.
  These are emulated Linux results, not deployment-server latency promises.
- Complete offline transfer and installation pass. The 2.99 GB model/wheel
  archive was split into 32 hash-verified parts, reassembled, and extracted.
  All 88 pinned packages installed in a fresh network-disabled Linux environment;
  dependency checks and the actual three-model inference smoke check passed.
  Managed transfer bytes peaked at 15.09 GB, below the 16 GiB ceiling, while
  preserving the filesystem reserve. Temporary transfer copies were removed
  after their verified replacements; manifests and proof remain retained.
- Updated the model compatibility checker to verify all asset hashes and run
  the actual parser-backed alias path. It now rejects incomplete two-model
  generations. Earlier installation instructions only exercised two models.
- Found and fixed source-level relationship evidence selection. When one source
  contains a literal assertion and another unsupported extraction of the same
  relationship, the unsupported result's higher score must not erase the real
  assertion. The same defect could erase a contradiction and leave a false
  solid graph edge. Four public API regressions reproduce both failures before
  the fix, in both extraction orders. Original scores remain unchanged.
- Advanced the application policy revision to v6 so the normal bounded worker
  backfill reselects evidence from complete current caches. The extraction
  fingerprint, model assets, publication thresholds, and inference cost are
  unchanged. Separate verification covers cache reuse without new inference.
  Commit: `8509bbd`. Current regression evidence is
  `relationship-evidence-full.log`; the four failing before-fix reproductions
  are retained in `relationship-evidence-before.log`.
- Saved the subsequent independent-review repairs in `5014d16`: pinned inverse
  relation-only replay; independent relationship provenance alternatives;
  expertise projection generation and migration 0013; interrupted embedding
  rebuild reconciliation; finite inference deadlines including partial IPC;
  and live evidence refresh with request-race handling. Focused Linux results:
  17 correctness tests and 49 worker/embedding tests pass. Frontend build and
  seven compiled-component refresh/race checks pass.
- Added a separate product-effects gate, currently under final integration
  review. It grades exact public tags/search hits/expert suggestions/question
  matches and routing history. A fresh question challenged after expertise
  withdrawal must remain unrouted, without deleting historical notifications.
  Its 22 focused Linux regressions pass; these add no independent quality N.

## Current work after independent review

Predicate-specific parser grounding is being integrated for positive relations
and negative vetoes, together with source-cache/read-generation invalidation and
migration 0014. Two bounded parser-only development screens found zero unsupported
proposals in 1,460 and 2,040 checked pairs respectively; they are not real extractor
quality runs. Independent review then reproduced a neither/nor coordination bug;
the repaired recorded corpus supports 79/80 gold assertions with zero unsupported
edges across 2,100 proposals. Runtime/source-scope and negative-veto checks pass
224 focused tests. These are recorded parser and synthetic application checks,
not measurements of real extractor precision or recall.

The first full Linux integration run after these changes passed 503 tests and
reported 21 failures plus two fixture errors. Incomplete graph-test source
records globally blocked alias coverage in the shared test database; the
identical graph/alias sequence now passes 15/15 after correcting those synthetic
records. Worker cache tests needed explicit current parser/coverage contracts;
all 51 Linux worker/embedding tests now pass. The intermediate fixture-import
failure and its correction are retained. A new full-suite result is still needed.

Independent review also reproduced six alias source-scope failures: quoted or
hypothetical definitions could publish through lexical and learned-role paths,
including a context marker outside the local model window. Both paths now check
full-source scope. A source contract and read guards withdraw obsolete automatic
aliases immediately while preserving pins. Follow-up transition tests found
that a dependent tag could survive because a previously published alias had
been treated as unconditional identity. Published-term dependencies now preserve independent evidence alternatives and
withdraw dependent tags and graph claims immediately. Indexed, correlated SQL
keeps a one-item check bounded with 2,000 unrelated managed mentions. Expertise
now uses the separate explicit-feedback contract described below.

Uncertain relationship evidence is now labeled as an uncertain extraction and
excluded from supporting/opposing assertion counts. The frontend build passes.
The product-effects grader also requires an actual 404 before an absent question
can count as a successful deletion; its two before-fix counterexamples now pass
with the valid-deletion control.

Expertise attribution is still unresolved. A frozen separate semantic-label trial
using the existing GLiNER checkpoint stopped at its first unsupported output,
control 7/32: it credited Ardour for a contribution explaining crossfades. Four
of the seven completed selected positives were found; none of the selected
negative controls had run. This cannot estimate full recall or precision. The
trial completed normally in 57.0 seconds with a 2,320,949,248-byte peak RSS, and
was not integrated. Its original schema, labels, raw output and failed decision
are retained in `expertise-attribution-trial/`. That configuration is closed.
A distinct proposition/argument attribution prototype then failed its single
frozen screen. It accepted "Chroma subsampling" at score 0.9982401133 from
"Chroma subsampling deserves everyone's admiration". It stopped at new control
7/8 before the original 32 controls or the missed-second-topic counterfactual
were semantically tested. All 40 parser outputs and seven semantic observations
remain retained. The container completed normally in 75.963 seconds under four
CPUs and three GiB; peak parser and classifier child RSS were 2.895 GB and
2.323 GB respectively. This configuration is also closed and unintegrated.
The failed evidence shows that grammatical position plus an extraction score
does not establish an informative contribution. The September 22 implementation replaces whole-item vote attribution with
explicit, authenticated canonical-topic feedback. Neither failed extractor
configuration was tuned or integrated.

## September 22 implementation and verification

The new optional topic picker records which topics a contribution helped with
or an accepted answer resolved. Choices start unchecked, support canonical
search, and can be edited or removed. Generic acceptance/helpfulness/endorsement
retains its impact meaning and supplies zero automatic expertise evidence.
Aggregation requires two actor accounts, three independent contribution/problem
groups and one accepted-topic confirmation. Distinct accounts are not verified
independent people, and the rule is not a calibrated expertise probability.

Migration 0015 introduces immutable confirmations and monotonic source, question,
acceptance, account-binding and canonical-identity revisions. Indexed inverse
invalidation hides withdrawn expertise at commit. Independent review reproduced
and repaired canonical identity revival and an uncorrelated SQL condition that
could borrow another question's acceptance. Recorded confirmation IDs, exact
revisions and source/evidence model generations are rechecked on reads. Human
canonical choices do not inherit unrelated alias lifetime. Manual expertise pins
are bound to the current account/profile binding. Generic endorsement reporting
no longer assigns every tagged topic to a contributor; routing deduplicates by
account and excludes all profiles belonging to the asker.

Fourteen focused backend checks and ten browser checks pass. The first root
integration run passed 23 cases; its remaining grammar-transition check stopped
at the 2 GiB filesystem reserve, then passed after hash-verified duplicate
installation inputs were removed. Twelve prospective feedback-harness checks
pass. Eight production-queue effect tests require the supported Linux runtime;
that full integrated suite is now running. These checks are engineering evidence,
not release-quality measurements.

An actual pinned-parser diagnostic retained 14 controls and 990 proposed
endpoint/predicate combinations. It found one unsupported outer assertion in an
ambiguous mixed conjunction. The guard now holds that entire noncontrasting
coordination. The unchanged recorded parses/gold and earlier failed output are
retained; the combined parser/scope replay passes 239 tests. No GLiNER accuracy
claim follows from manually supplied endpoint spans.

Two fresh corpus authors with no implementation-review history are preparing
sealed 150-case halves under the unchanged quality targets. Root receives only
counts, domains and hashes until source freeze. Labels will be independently
audited before one actual inference run. These are AI-authored scenarios, not
human workplace evaluations.

The host filesystem fell to roughly 234 MiB free. About 2.99 GB of task-owned
duplicate installed model/wheel inputs were hash-compared with retained originals
and removed; manifests, original outcomes, source snapshots and the sole retained
asset/wheel copies remain. Available space recovered to about 3 GiB. Docker was
restarted for isolated tests; the user's earlier demo container remains stopped.

## Current measurement

A frozen source snapshot completed the existing 24-case cross-domain
development baseline with actual models, the production queue, and public APIs.
It ran offline in Linux, within four CPUs and five GiB, using a private database.
This is diagnostic development evidence, not a new release-quality evaluation.

Evidence is retained under `data/ml-runs/astra-sep14/` (excluded from Git).
Capacity evidence is in `capacity/`; complete offline evidence and the prepared
generation are in `offline-verification/`. The compatibility smoke check
reports the same production inference fingerprint as the capacity check.
Those capacity and installation measurements were taken at `bedd280`, before
the policy-v6 evidence-selection change; the 462-test result includes that fix.
The baseline evidence is exported to `baseline-r3/ml-quality-4_nsurau/`.
All 60 inference calls completed in 282.7 seconds; sampled process-tree peak
RSS was 3.99 GB. Positive assertions passed: concepts 32/38, aliases 2/3,
relationships 6/9, expertise 1/2. All 48 negative assertions passed. These
assertion counts are not complete-output precision or release acceptance.
Missing entity confidence and missing independently supported relation
extractions account for several failures; publication correctly withholds them.

The full 48-case acronym development run then completed all 100 inference calls
in 425.1 seconds. It published 19/24 selected aliases (previously 17/24), with
zero false positives among the 24 selected negatives. All 22 published aliases
matched the frozen complete-output labels. Both actual edit/delete withdrawals
passed. Peak sampled process-tree RSS was 3.97 GB within four CPUs/five GiB.
Results: `grammar-development/ml-quality-0ujrnen5/`. This development sample
cannot establish release quality; its status is `INSUFFICIENT_EVIDENCE`.

The GLiNER2.5 multilingual checkpoint failed its frozen screening gate on the
first completed case: it lost the previously published Earth/Sun membership
relationships with the Solar System. Both inference calls completed. The
screen stopped as planned after 77.6 seconds; its interrupted aggregate report
shows ERROR, while `multi-checkpoint-trial/results/screen-stop.json` identifies
the deliberate STOP and preserves the complete disqualifying case. No further
cases were run and no full-corpus quality estimate is claimed. The existing
base checkpoint remains selected; no model or score threshold was changed.

A separate literal-relationship prototype also stopped at its frozen futility
gate. It intersected a scored natural record's head, literal predicate phrase,
and tail with exact typed parser arguments. The selected input was 71 exposed
development posts plus 26 controls, with a predeclared early stop. It completed
all 26 controls and the first three complete scenarios (15 posts). Only one
record qualified, duplicating an existing native control result; it added no
supported source facts. No schema rewording or lower threshold was attempted,
and the prototype was not integrated or replayed into the application.
The stopped run completed normally in 213 seconds, with 4.10 GB peak RSS and
no OOM under four CPUs/five GiB. Its frozen inputs, every raw prediction and
parse, and the rejection decision remain in `literal-relation-trial/`.

Two environment failures were preserved before the current launch: the older
unit-test image lacked spaCy; the complete inference image then competed with
the earlier demo's resident worker and suffered a host-memory OOM kill. The
demo's ML worker was gracefully stopped while its web process stayed running.
That worker is now restored and verified healthy, with the web process still
alive. Its read-only `/repo` mount follows the current checkout, so restoration
uses an archived `72e387e` source tree inside the container to retain the
original behavior without migrating the demo database. The exact restoration
is recorded in `demo-worker-restored.json`. The correct inference image
is `mds-ml-e2e:ubi8.10`; its dependency versions match the pinned requirements.
The evaluator now checks dependencies before starting and stops if inference
cannot start, instead of repeating that failure across the corpus.

## September 22 integration verification

The first integrated Linux run reported 642 passes, 68 failures and 17 fixture
errors. Its original log remains in `independent-review/integrated-linux-sep22-r1.log`.
The failures exposed both old test assumptions and real integration defects;
that run is not a release pass. Current worker tests now use schema 0015 while
historical migration round trips explicitly stop at 0014. Shared alias fixtures
process their deletion jobs before the next case; the previously failing
60-test sequence now passes. Recorded model outputs and gold labels are unchanged.

A separately reproduced ingestion-order bug attached both a scored definition
certificate and a dependent published-alias mapping to the same observation.
The alias then depended on the concept whose score depended on the alias.
The adapter now retains the exact definition certificate as its authority;
both ingestion orders, witness withdrawal, replay and restoration pass.

The focused supported-Linux worker/storage/queue run passed 64 of 65 tests; the
remaining profile-growth fixture still used generic feedback. After adding an
explicit topical action, its storage refusal/retry/retraction check passes in
the separate 39-pass public-effects/dependency run. That run's four remaining
lifecycle failures identified conservative legacy profile invalidation before
refresh; checks now verify that explicit canonical credit survives the queued
recomputation. All eight ingestion-order and explicit-credit lifecycle checks now pass in
supported Linux (`credit-lifecycle-linux-sep22-r5.log`). The new complete
backend run is in progress; final integrated verification remains pending.

Nineteen native projection/feedback checks pass, including three tests proving
that legacy profile INSERT, UPSERT and direct valid-flag writes cannot bypass
the schema 0015 feedback contract. A separate actual downgrade attempt is refused
and preserves the complete human-confirmation rows and database schema. All 27 focused controller/deployment checks pass independently in supported
Linux, including actual test-process stop/start on a compatible rollback; no real
server was stopped or deployed. Fresh corpus authors remain isolated from code
and inference outputs until implementation freeze and independent label review.

The second full supported-Linux run finished with 736 passes, one failure and 17
fixture errors (`integrated-linux-sep22-r2.log`). A read-only inspection of that
isolated test database identified exactly two retained parser-unavailable source
records from the spelling-equivalent-endpoint test. Processing their deletions
at cleanup fixes the 42-test runtime/term sequence. The one failed rollback test
had inserted a profile Source without the new feedback contract; it now starts
from a real `apply_profile` result and its transaction rollback passes. These
are fixture corrections, without weakening source validity or read guards.
The final coherent Linux suite is running.

Both fresh authors completed 150 cases, but independent semantic review found
ambiguous/extraneous labels, repeated narrative patterns and overuse of privacy-only
negatives. Original sealed bytes/hashes are retained. Separate reviewed copies
are being revised before any inference; counts alone do not establish readiness.
The implementation owner remains blind to case contents and labels.

## Verified implementation checkpoint — September 22

All 754 backend tests pass in the supported, network-disabled Linux runtime
(502.80 seconds, two dependency deprecation warnings). No tests were skipped.
The final log is `independent-review/integrated-linux-sep22-r3.log`. The frozen
candidate's application, test, frontend and tool files remained unchanged during
this run. The production frontend build and 10 compiled-browser topic-feedback
interaction checks also pass. Independently tested controller rollback checks
are included in the full backend suite.

The implementation snapshot is in `fresh-evaluation-sep22/candidate-source`, with
202 file hashes in `candidate-freeze.json`; it was frozen before implementation
owner access to the new evaluation cases or labels. All 131 retained model,
wheel and lock files match the prior offline transfer bundle byte-for-byte
(2,993,060,653 bytes). The fresh quality evaluation is described below.
These engineering passes do not establish quality.

Commit `c9c4c54` saves this implementation checkpoint. The frozen source also
passed the actual three-model offline compatibility check in a four-CPU,
5 GiB Linux container with no network or extra swap. Peak process RSS was
4,054,110,208 bytes; the container exited successfully without an OOM event.
The complete result and raw log are retained in
`fresh-evaluation-sep22/offline-model-smoke-result.json` and
`fresh-evaluation-sep22/offline-model-smoke.log`. This is compatibility evidence,
not an accuracy measurement.

The same frozen source passed the fixed 50,000-item/50-client capacity check in
246.11 seconds. All 1,328 API requests succeeded and all 268 writes were preserved,
with no integrity errors or unhandled lock failures. Write p95 was 8.455 seconds
with the loaded worker idle and 9.173 seconds during inference, below the fixed
10.568-second limit. Seven real inference chunks completed during traffic.
Peak combined cgroup memory was 5,301,137,408 of 5,368,709,120 bytes (98.74%);
memory headroom remains tight. Source hashes match the frozen candidate.
`fresh-evaluation-sep22/capacity/` retains the report, logs, request traces and
private test database; `capacity-summary.json` records the cross-check. This
emulated Linux run does not establish deployment-server latency or full-backlog
drain time and does not add independent quality decisions.

## Fresh independent evaluation — September 22

Two independent authors and two label reviewers completed and approved 300
scenarios across 17 domains. All 1,200 selected labels and allowed outputs were
reviewed before inference, including independence checks against 672 prior
inputs. Each category has 150 positive and 150 negative decisions; substantive
hard-negative counts are 105 concepts, 144 aliases, 136 relationships and 133
expertise decisions. The frozen plan also contains 57 public-effects assertions
and two fresh-target routing challenges. These are authored engineering
scenarios, not representative workplace data or a human accuracy evaluation.

The approved corpus is `fresh-evaluation-sep22/sealed/reviewed-corpus.json`, SHA256
`7d7de42d726d7b3ac1e2bb00a43f49d845f0c11c2921a88b87ad21110371f447`.
`review-summary.json` records counts and all original/revision/audit hashes.
The implementation owner did not inspect the case contents or labels before
freezing the implementation.

The first launch stopped before inference: report construction indexed the
optional descriptive `purpose` field. Commit `3c6722f` changes only that access
to an optional lookup; application behavior, scoring, effect checks and all
corpus bytes are unchanged. The original failed launch log/metadata are retained.
The replacement snapshot is `candidate-source-r2`, with freeze SHA256
`16ce87d21f6e5576654eb5c35a02a11b38121464213c642fe347e21c9608e5d3`.
The actual evaluation ran with four CPUs, 5 GiB, no extra swap and no network.
`quality-execution-r2.json` records the exact image, command, source and corpus
hashes. It was interrupted for an independently reproduced runtime loop, as
described below; it is not a quality pass.

An independent audit of the first completed case confirmed its concept, alias
and expertise successes and exact grading, but found a separate public search
defect: the unquoted full concept name omitted earlier acronym-only posts that
had current lexical tags. Search now recognizes the longest active vocabulary
phrase before splitting words and uses the same phrase groups for relevance.
Quoted phrases and prefixes retain their meaning; removed aliases cannot revive
identity through stale stored tags. Four new API regressions and 49 existing
workflow tests pass; independent expanded boundary/ranking checks pass all 13
targeted tests. Commit `b4cb651` saves this search repair; the interrupted
evaluator used its unchanged read-only snapshot.

The run completed 13 cases, then repeatedly replayed the first post of case 14
without another model call. An independent bounded reproduction found that
source-local acronym evidence depended on its own published alias. Each cached
replay temporarily held and reactivated the concept, scheduling another complete
vocabulary backfill even though the final projection was unchanged. Four replay
cycles reproduced four new backfill generations. Removing only that redundant
self-dependency eliminated all four cycles while preserving external alias
withdrawal dependencies.

The run was gracefully interrupted after 1,206.5 seconds. All 67 actual inference
calls had completed; the report records ERROR/KeyboardInterrupt, not PASS.
`fresh-evaluation-sep22/interrupted-quality-run/` retains all partial reports,
raw predictions, private databases and diagnostics. `quality-interruption.json`
records the exposed case IDs and hashes. The original sealed corpus is unchanged.
The first 13 completed cases and the partially observed 14th are now development
evidence and cannot be called unseen again.

The partial run found all 13 selected concepts, 12 of 13 selected aliases, none
of the 13 selected relationships and all 13 selected expertise mappings. The
complete-output concept audit found 16 correct of 18 published concepts. These
small, positive-only partial results do not satisfy any release sample gate or
establish precision on hard negatives. Missing relationship coverage remains a
substantive quality concern; no score or acceptance threshold has been lowered.

The minimal adapter repair gives a unique current asserted local definition its
own identity authority. It keeps external alias dependencies, source validity,
manual suppressions and the exception for an already distinct canonical concept.
Five permanent recorded-score regressions verify settled cached replay,
withdrawal after definition edits/deletion, admin alias suppression and a real
v7-to-v8 worker upgrade. The replay regression fails against the frozen
implementation and passes with the repair. All 93 focused Linux replay, identity
and search checks pass. All six Linux upgrade/suppression checks also pass,
including the existing policy-reselection regression; none were skipped. The
policy revision advances to v8 so quiet existing databases replay their complete
current caches once. Model versions, raw predictions and numeric decision rules
are unchanged. The new upgrade test forbids inference and confirms that a second
worker restart schedules no work.

## Subsequent verification and bounded model screening

Commit `a988634` saves the local-definition replay repair and policy-v8 upgrade.
The complete source-only Linux run passed 764 tests and reported eight setup
failures in 750.19 seconds. Seven controller checks launched their dummy worker
from the backend directory and therefore imported the real application; the
controller's compatibility refusal itself succeeded. Independent reproduction
and a fixture working-directory repair (`bcca9d6`) pass all 27 controller checks.
The eighth failure was the SPA route because the Git-only source archive omitted
ignored frontend build assets. A separate packaged snapshot includes the already
verified compiled frontend. Its 28 controller/SPA checks pass. The complete
failed run and focused follow-up are retained as `replay-fixed-full-linux.log`
and `package-focused-linux.log`; this is not described as one clean full-suite
invocation. The application code did not change for these setup corrections.

The independent 14-case replacement draft preserves all 286 unexposed case
objects byte-for-byte, the 150/150 balance in each category, hard negatives,
eight cold starts, 57 public-effects assertions and two routing challenges.
Separate semantic review is in progress before any candidate inference. The
original interrupted corpus remains unchanged and retained.

The subsequent bounded independent review rejected the exact R3 replacement
hashes. It found incidental descriptive concept labels and inconsistent handling
of grammatical and secondary-name variants relative to the retained set. Counts
alone were not used as a rejection rule. No approved copy was created, no
historical exposed labels were changed, and there was no new inference.

One separate BGE-small embedding candidate was rejected by its predeclared
screen. In the same supported Linux image, BGE-large preserved all 16 existing
retrieval comparisons; BGE-small preserved 15, and lost one existing positive
at the unchanged 0.75 weak-association cutoff. Peak process RSS fell from
1,755,430,912 to 626,438,144 bytes, but the quality-preservation gates failed.
No selected model or cutoff changed. All 96 vectors, scores, logs, hashes,
resource measurements and the failed decision remain in `embedding-small-trial/`.
Its unselected 133 MB weight file was retired only after hash verification;
exact public pins and preparation instructions remain. This candidate is closed.

An exposed-case diagnostic also shows that only three of the 13 missing
relationships have both endpoint concepts active. A relation-only NLI addition
cannot solve that publication-coverage ceiling under the current concept rules.
The separate NLI shadow runner/controls are prepared but blocked from execution;
there has been no NLI model download, inference or production integration.
Research is now considering a distinct integer representation of the selected
BGE-large checkpoint for resource headroom and a separate concept-evidence
strategy. Any new trial requires its own frozen, rejectable development screen;
the full quality/effects and final application resource gates remain unchanged.

That declared BGE-large INT8 screen has now completed and is rejected. The two
processes used the same selected checkpoint, frozen development probes, pinned
Linux image and four-CPU/five-GiB limits with no network or extra swap. All 48
vectors per role were retained. FP32 preserved all 16 rankings; INT8 reversed
rankings 07/14 and lost the existing 0.75 positive cutoff on 08/13. Neither run
had an execution error or resource-limit failure. Observed inference RSS fell
from 1,753,980,928 to 968,642,560 bytes; candidate startup/conversion peak was
2,432,053,248 bytes. Both memory conditions passed, both quality-preservation
conditions failed. There will be no per-channel, engine, cutoff or layer variant
of this rejected configuration. Independent integrity review confirms rejection
and bitwise equality of the new FP32 vectors with the previous FP32 baseline.
The exact decision and independent audit are `embedding-int8-trial/results-v1/`
and `embedding-int8-trial/results-v1-independent-audit.json`.

## Remaining work

1. Preserve the physical disk reserve before any further managed allocation.
   The BGE-small and BGE-large INT8 experiments are both closed as rejected.
2. Resolve the demonstrated concept and relationship coverage shortfall through
   a qualified development experiment. No alternative extractor is qualified
   yet; an embedding memory pass alone cannot establish extraction quality.
3. Establish consistent prospective concept annotations for an independently
   reviewed evaluation set. Preserve exposed historical labels and failed
   results; source/label changes must precede inference and remain model-blind.
4. Obtain independent quality and public-effects validation for the resulting
   frozen candidate, followed by appropriate final-source actual-model
   compatibility and capacity checks. The current complete regression suite
   passes; repeat it only when further changes or new concerns justify it.

Acceptance targets remain: 98% precision for concepts and relationships, 99% for
aliases, 95% for expertise; at least 50% recall; and per-category minima of 300
independent decisions, 150 positives, 100 hard negatives, and 100 applied
predictions. Incomplete measurements are not acceptance.
