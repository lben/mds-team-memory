# Independent review, 14 September 2026

Verdict: the branch needed fixes. Its engineering foundation is useful, but the
global automatic-maintenance objective is not yet achieved. In particular,
published expertise can be attributed to an unrelated topic, and relationship
grounding can mistake quoted or rejected claims for assertions. Neither the
existing development results nor successful engineering checks certify the
unchanged release-quality targets.

At the user's explicit request, three independent AI reviewers inspected
correctness, operations, and quality/product behavior from fresh task contexts.
They worked like separate code reviewers, with concrete counterexamples and
retained evidence; these were not reviews performed by human evaluators.

## Reproduced defects repaired in this review

| Finding | Repair | Verification |
| --- | --- | --- |
| A pinned inverse alias loses relation-only evidence on replay. | Resolve the pinned inverse route when the entity pass omitted the endpoint. | Public graph replay, witness removal, pin/release and source-edit regressions, with and without entity spans. |
| Higher-scoring conditional evidence erases an independent native contradiction. | Retain the strongest supported evidence per complete identity-dependency set. | Expiring one route does not erase another route or a native assertion; public support counts still count each source once. |
| Old expertise remains public across policy, grammar or schema changes. | Bind derived profiles to their projection and pipeline generation; migration 0013 invalidates old projections on upgrade and downgrade. | Original four reviewer reproductions pass; tests also bypass the new read guard to model an old rollback reader. Pins survive. |
| Rolling back an incomplete embedding rebuild strands its staging reservation. | Reconcile the selected generation at startup and reclaim abandoned generations in bounded transactions before a replacement rebuild. | Rollback, same-generation resume, tight-quota replacement and startup ordering tests. |
| A live but hung inference child can hold the queue indefinitely. | Finite monotonic load/job deadlines supervise complete IPC, including partially received messages, and kill the child process group before retry. | Linux tests cover hanging load, inference and partial IPC, continued heartbeats, descendant termination and successful retry. |
| Open findings evidence stays stale when the knowledge revision changes. | Refresh the selected finding and discard superseded, closed and unmounted requests. | Production frontend build and seven compiled-component refresh/race checks. |

Verification on the reviewed repairs: 17 focused correctness tests passed in the
supported offline Linux runtime; the operations reviewer ran 49 worker/embedding
tests successfully in a separate offline Linux container. The frontend production
build passed. The previous committed source had 462 passing backend tests;
that historical total must not be presented as a full-suite result for later
source changes. Native macOS API probes establish isolated mechanics only.

Detailed reproductions, isolated patches, logs and source hashes are retained in
`data/ml-runs/astra-sep14/independent-review/` (ignored local evidence):
`correctness/REVIEW.md`, `operations/review.md`, `quality/REVIEW.md`, and
`root-focused-linux.log`.

## Work still open

- Expertise needs durable topic-specific contribution attribution. The current
  implementation can give credit for PostgreSQL when useful contributions are
  actually about slides, a bicycle and a fern, even with empty ML concept output
  and explicit skill disclaimers. It then suggests that person for a PostgreSQL
  question and sends a routing notification. A generic entity span is insufficient.
- Relationship support needs predicate argument and assertion scope checks for
  both positive claims and negative vetoes. Supplied model-shaped proposals
  demonstrate publication of rejected/quoted claims and cross-clause mistakes;
  their frequency with the real extractor has not been measured.
- The quality runner retains downstream observations but previously did not
  grade source tags, actual search hits, suggested experts, question matches or
  routing notifications. A separate predeclared product-effects gate is being
  added; correlated effects must not increase independent quality sample counts.
- The final candidate still needs all four unchanged frozen quality gates and
  final-source lifecycle/resource verification. Existing exposed development
  corpora cannot become unseen evidence by renaming or rehashing them.

Each candidate experiment has a frozen plan and stop condition. A failed screen
is retained as a failure, not repaired by changing its gold labels or lowering
acceptance targets. Current model trials do not establish release readiness.

## Follow-up implementation and verification

The typed parser guard is integrated for learned positive proposals and
entity-grounded negative vetoes. Negative vetoes carry zero confidence and
cannot originate a positive scored relation. Full-source quotation/heading
scope applies across model windows. The recorded parser corpus currently gives
79/80 supported gold assertions and zero unsupported edges across 2,100 checked
proposals; 224 focused runtime/scope tests pass. Migration 0014 and current
source/record markers cover replay and rollback. Real-model release quality
remains unmeasured for this candidate.

Six additional reviewer probes reproduced global aliases from hypothetical or
quoted definitions. Runtime and application boundaries now filter both role and
lexical definitions using full-source scope. Current generation guards preserve
manual pins and withhold obsolete automatic aliases before replay. A subsequent
public transition test found a dependent tag surviving withdrawal of its alias;
that published-term dependency repair remains in progress, including graph and
expertise propagation.

The first full integration run passed 503 tests with 21 failures and two errors.
The graph/alias subset reproduced seven failures caused by incomplete synthetic
source coverage; after fixing the fixture it passes all 15 tests. The current
Linux worker/embedding subset passes 51 tests. These focused results do not
replace a new full integration run after all repairs.

The separate effects gate now checks actual public behavior and retained raw
responses. An independent root review found that missing question-list results
could certify deletion without a confirming 404 detail response; the two failing
counterexamples and one valid-deletion control now pass. Uncertain relationship
records remain available for inspection but do not count as supporting or
opposing assertions. The frontend production build passes.

## September 22 repair and review status

The attribution finding above is addressed by deliberate, version-bound human
canonical-topic feedback. Generic votes and broad acceptance never become topic
credit. Automatic expertise requires two actor accounts, three original
contribution/problem groups and one accepted-topic confirmation. These are
account identities, not verified distinct humans. Existing factual/model quality
requirements remain open for independent evaluation.

Independent backend and product reviews exercised the API, database and compiled
browser interface. They found and repaired nested-query correlation, canonical
identity restoration, evidence-generation mismatch, FTS revision-trigger behavior
and excessive item-update query work. Explicit confirmations become stale after
source/question edits, acceptance changes, account reassignment or canonical
identity changes, and require a new assertion to revive. Ten compiled-browser
interaction checks and the production frontend build pass.

Published-term dependencies now preserve separate evidence alternatives and
check current ownership, source hashes and generation contracts on public reads.
An additional ingestion-order reproduction exposed a circular concept/alias
prerequisite. Exact scored identity certificates now supply their own mapping;
both ingestion orders, withdrawal and restoration pass. The 42-test runtime and
published-term sequence passes after removing obsolete synthetic test sources
through the normal deletion path.

The controller independently checks the target release against the shared
schema before stopping processes or changing links. An incompatible rollback
preserves both processes and human data. A database guard also prevents a stale
worker from certifying its legacy generic-vote projection. All 27 supported-Linux
controller/deployment checks pass; actual downgrade refusal preserves human
confirmation rows and schema. A separate causal queue probe confirms that
explicit off-tag topic credit recovers when its canonical concept regains support.
No production deployment was performed.

The second full Linux integration run reported 736 passes, one failure and 17
fixture errors. Each remaining cause was identified in the isolated test database
or test setup: two parser-unavailable fixtures retained obsolete coverage, and one
rollback fixture bypassed the new projection contract. Corrected focused checks
pass; a final complete run is in progress. Original failing logs are retained.

Two fresh authors supplied 300 scenarios. Independent label reviewers identified
semantic, exhaustiveness and narrative-independence problems before inference;
separate reviewed copies are being corrected while original seals remain intact.
The implementation candidate is frozen and its owner has not seen the new case
contents or labels. This remains an AI-authored synthetic engineering evaluation,
not a human study or a claim about workplace accuracy. No quality pass is claimed.

Final checkpoint result: all **754 backend tests pass** in supported offline
Linux (502.80 seconds), with no skips. The application and tests match the
frozen candidate. Original failing integration runs remain retained. Fresh
quality/effects evaluation is still pending independent corpus readiness.
