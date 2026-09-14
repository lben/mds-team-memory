# Next independent evaluation

This protocol defines fresh evidence for the global objective. It does not
certify the current branch. The earlier expanded and acronym holdouts are
spent; their results remain development evidence.

## Authoring and separation

Prepare 300 new, independent scenarios, using the version 2 fixture contract
accepted by `tools/ml_quality_check.py`. Two authors each prepare 150 cases;
each contributes 75 positive and 75 negative preselected decisions **in each
of the four categories**. Together they must supply at least 100 substantive
hard negatives per category. An independent reviewer audits every label
before inference. Agent delegation requires the user's authorization under
the current session rules; no authors have been dispatched yet.

Authors may read the schema and product semantics, but not model outputs,
failed holdout cases, confidence values, or implementation-specific extraction
rules. The implementation owner receives only schema validation, counts,
domain coverage, and hashes until a candidate is frozen. Files remain sealed
from implementation work. The reviewer can inspect the complete new corpus.

Use at least twelve domains, including astronomy, software, engineering,
science, and ordinary non-workplace subjects. Different names inserted into
the same story template do not create independent scenarios. Reviewers must
check story structure, evidence dependencies, and semantics as well as text
overlap within the corpus and against prior evaluation sets.

## Required labels and behavior

Each case identifies its source authors, kinds, visibility, chronology,
outcomes, and expected findings. Names and relationship directions must be
supported by the actual source text. Gold labels describe source support,
not external truth or what a model is likely to extract.

Label every acceptable automatic output, including alternate forms and
additional supported findings, before inference. An allowed concept boundary
does not automatically establish an alias between two spellings. A reviewer
must explain exclusions for generic descriptions, ambiguous identities,
negation, hypotheticals, questions, quotations, unrelated facts, copied
sources, private content, and inadequate expertise outcomes.

Positive expertise requires the application's independent outcomes; posting
volume or routing decisions alone cannot supply it. Positive relationships
must have independent supporting sources. Alias cases must cover chronology,
local expansions, non-initial aliases, conflicting meanings, established
identities, and significant letters, digits, spaces, and symbols.

Include separate source edit/delete and chronological cold-start/backfill
scenarios. Include admin pin/suppress/restore, restart, generation transition,
and rollback cases in the engineering verification. An initially unpublished
target cannot count as a successful withdrawal demonstration.

## Freeze and execute once

Before inspecting any new model result, retain the reviewed corpus and SHA256,
source commit and file hashes, exact model manifests, dependency versions,
publication policy, resource limits, and evaluation command. Run the actual
queue and public application interfaces in offline Linux with a private
database. Preserve all predictions, errors, final databases, public effects,
and resource measurements. Do not modify expected answers after seeing output.

An independently identified labeling error must be documented transparently.
Do not silently relabel an output as correct. Any changed corpus needs a new
prospective evaluation plan; the exposed cases are no longer unseen.

## Acceptance

| Category | Required measured precision |
| --- | --- |
| Concepts | 98% |
| Aliases | 99% |
| Typed relationships | 98% |
| Expertise | 95% |

Every category also requires at least 50% recall, 300 independent decisions,
150 positives, 100 hard negatives, and 100 automatically applied predictions.
The 100-prediction minimum is independent of the recall floor. Report the
selected-decision counts, Wilson 95% intervals, abstention, and a separate
complete-output precision audit. Correlated additional outputs do not enlarge
the independent sample. Failure or insufficient evidence remains visible.

Quality does not replace lifecycle or capacity verification. The final
candidate must also pass the existing 50,000-item/50-client concurrent-load
gate, actual offline bundle verification, resource controls, migrations,
rollback, admin controls, and live application behavior. Emulated Linux
measurements cannot establish performance on the deployment server.
