# Automatic maintenance: current status

Branch: `astra-again-sep14`. Started 14 September 2026 from DeepSeek's `72e387e`.
The full objective is unfinished. On September 14 the user authorized independent
agents to review the work. Three reviewers have been dispatched for correctness,
model quality/evaluation, and operational readiness. Fresh evaluation authoring
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
- The current offline Linux regression suite passes: 462 tests in 196 seconds.
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

## Remaining work

1. Obtain an independent review of this verified continuation and prepare the
   new blind corpus under `ML_EVALUATION_PROTOCOL.md`. Agent delegation is now
   authorized; independent reviews are in progress.
2. Improve all four finding categories without altering the release targets or
   reusing spent holdouts as unseen evidence.
3. Freeze the selected implementation and establish quality using fresh,
   independently labeled evaluation data with complete-output auditing.
4. After selecting a quality-qualified implementation, verify any affected
   regression, live application, lifecycle, offline, and capacity behavior on
   that final source. The checks above establish the current branch's
   engineering behavior; they cannot substitute for the unmet quality gate.

Acceptance targets remain: 98% precision for concepts and relationships, 99% for
aliases, 95% for expertise; at least 50% recall; and per-category minima of 300
independent decisions, 150 positives, 100 hard negatives, and 100 applied
predictions. Incomplete measurements are not acceptance.
