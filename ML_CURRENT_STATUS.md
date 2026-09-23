# Automatic maintenance: current status

Branch: `astra-again-sep14`. Started 14 September 2026 from DeepSeek's `72e387e`.
The full objective is unfinished. On September 14 the user authorized independent
agents to review the work. Three independent reviews identified concrete
correctness, quality/evaluation and operational defects. Their findings and the
verified repair scope are in `ML_INDEPENDENT_REVIEW_SEP14.md`. Fresh evaluation authoring
will use separate agents who have not reviewed implementation or model outputs.
The earlier rejected model experiments remain closed. Historical requirements and results are in
`ML_IMPLEMENTATION_PLAN.md`; branch ancestry is in `BRANCH_EVOLUTION.txt`.

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
The actual full evaluation is now running with four CPUs, 5 GiB, no extra swap
and no network. `quality-execution-r2.json` records the exact image, command,
source and corpus hashes. Results remain pending; no quality pass is claimed.

An independent audit of the first completed case confirmed its concept, alias
and expertise successes and exact grading, but found a separate public search
defect: the unquoted full concept name omitted earlier acronym-only posts that
had current lexical tags. Search now recognizes the longest active vocabulary
phrase before splitting words and uses the same phrase groups for relevance.
Quoted phrases and prefixes retain their meaning; removed aliases cannot revive
identity through stale stored tags. Four new API regressions and 49 existing
workflow tests pass; independent expanded boundary/ranking checks pass all 13
targeted tests. The running evaluator uses its unchanged read-only snapshot.

The run completed 13 cases, then encountered repeated cached replay in case 14
after one post and one model call. A consistent private database snapshot and
raw output are retained in `fresh-evaluation-sep22/runtime-diagnosis/` for a
bounded independent reproduction. This is an unresolved runtime issue, not a
quality success; the existing per-case deadline and full-run reporting remain
in force. The first 13 cases alone are not a release measurement.

## Remaining work

1. Complete the frozen candidate's actual run against the approved corpus,
   retaining complete
   outputs, public effects and demonstrated lifecycle behavior. Evaluate the
   unchanged targets, including sample sizes and applied-prediction minima.
2. Resolve and verify the reproduced runtime issue; complete appropriate final
   source regression and capacity checks after implementation changes.
3. If quality requires implementation changes, preserve these results and obtain
   appropriate fresh validation for
   the next candidate; exposed cases cannot become unseen evidence again.

Acceptance targets remain: 98% precision for concepts and relationships, 99% for
aliases, 95% for expertise; at least 50% recall; and per-category minima of 300
independent decisions, 150 positives, 100 hard negatives, and 100 applied
predictions. Incomplete measurements are not acceptance.
