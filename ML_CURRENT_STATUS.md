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

## Current measurement

A frozen source snapshot is running the existing 24-case cross-domain
development baseline with actual models, the production queue, and public APIs.
It runs offline in Linux, within four CPUs and five GiB, using a private database.
This is diagnostic development evidence, not a new release-quality evaluation.

Evidence is retained under `data/ml-runs/astra-sep14/` (excluded from Git).
The baseline container is `mds-astra-sep14-baseline`; export its
`/tmp/astra-baseline` directory before removing it.

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
