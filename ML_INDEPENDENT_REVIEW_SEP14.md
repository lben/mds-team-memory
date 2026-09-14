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
