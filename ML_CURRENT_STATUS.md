# Automatic maintenance: current status

Branch: `astra-again-sep14`. Started 14 September 2026 from DeepSeek's `72e387e`.
The full objective remains active. Historical requirements and results are in
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
- The full offline Linux regression suite passes: 456 tests in 194 seconds.

## Current measurement

A frozen source snapshot completed the existing 24-case cross-domain
development baseline with actual models, the production queue, and public APIs.
It ran offline in Linux, within four CPUs and five GiB, using a private database.
This is diagnostic development evidence, not a new release-quality evaluation.

Evidence is retained under `data/ml-runs/astra-sep14/` (excluded from Git).
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

The untried GLiNER2.5 multilingual checkpoint is being screened under the same
resource and evidence constraints. Its immutable model revision and a frozen
early-stop gate are recorded in `multi-checkpoint-trial/`. It remains an
experiment, not the selected model. The screen stops at the first completed
case that fails a negative or loses a previously satisfied positive; a failed
screen cannot be repaired by threshold tuning or selective output union.

Two environment failures were preserved before the current launch: the older
unit-test image lacked spaCy; the complete inference image then competed with
the earlier demo's resident worker and suffered a host-memory OOM kill. The
demo's ML worker was gracefully stopped while its web process stayed running.
Restore that worker after measurements using the recorded command in
`data/ml-runs/astra-sep14/demo-worker-restart.json`. The correct inference image
is `mds-ml-e2e:ubi8.10`; its dependency versions match the pinned requirements.
The evaluator now checks dependencies before starting and stops if inference
cannot start, instead of repeating that failure across the corpus.

## Remaining work

1. Attribute development failures to extraction, identity resolution, publication,
   or source support; select a bounded improvement experiment from that evidence.
2. Improve all four finding categories without altering the release targets or
   reusing spent holdouts as unseen evidence.
3. Freeze the selected implementation and establish quality using fresh,
   independently labeled evaluation data with complete-output auditing.
4. Complete the regression suite, live application and lifecycle checks, offline
   bundle verification, and resource/concurrent-load verification on that source.

Acceptance targets remain: 98% precision for concepts and relationships, 99% for
aliases, 95% for expertise; at least 50% recall; and per-category minima of 300
independent decisions, 150 positives, 100 hard negatives, and 100 applied
predictions. Incomplete measurements are not acceptance.
