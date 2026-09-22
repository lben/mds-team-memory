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
before inference. The user authorized independent agents on September 14.
Implementation reviewers and corpus authors must be separate agents; authors
must not inherit implementation-review history. No new authors have been
dispatched before the September 22 feedback-contract revision below.

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

## Separate public effects gate

Version 2 also requires `product_effects_gate: PASS`. These are engineering
assertions inside the authored cases, never additional quality decisions or
independent samples. The four selected quality decisions and complete-output
audit use the initial observation, or `after_actions` when actions exist.
Routing challenge observations do not change those quality observations.
An effects failure prevents overall PASS even when numeric quality passes;
missing assertions, API evidence, or required coverage cannot certify PASS.

Each case may declare `effects`, mapping a phase to an array of exact checks.
Supported phases are `initial`, `before_replay`, `after_actions`, and
`after_challenge`. The middle phases require source edit/delete actions;
`after_challenge` requires the routing challenge below. All targets and
expectations must be frozen with the corpus before inference.

| `kind` | Target fields | `expected` |
| --- | --- | --- |
| `tags` | `post`: zero-based public post index | Exact concept-name list |
| `search_items` | `query`: exact public search text | Exact returned post-index list |
| `suggested_experts` | `post`: public question index | Exact case-actor list |
| `question_match` | `post`: public question index; `actor`: signed-in case actor | Boolean `matches_me` |
| `routing_notifications` | `actor`: signed-in case actor | Exact question-index list for `expertise_match` history |

No other fields are accepted. List order is irrelevant; duplicates, unexpected
names, unknown item IDs and extra notifications fail. Predicted aliases cannot
substitute for frozen concept names. The runner records full public item and
question details (including expected deletion 404s), search responses, expertise
directory, signed-in question lists, notification history, revisions, and API
request chronology for every observed phase. Missing response fields cannot
be interpreted as empty results. Summary grading recomputes checks from those
retained observations and the frozen assertions, rather than trusting stored
pass flags or rewritten expectations. The runner and both evaluation helper
sources are hashed in the report.

Across the corpus, tags, search items, suggested experts and question matches
each need at least one passing positive, passing negative and demonstrated
initial-to-later withdrawal. An initially absent effect cannot prove withdrawal.
Routing needs positive and negative checks plus a demonstrated *new-target*
suppression challenge. Historical notifications are events: losing expertise
does not require old notifications to disappear. Deleting a question may clean
its historical notifications; the report labels this `question_delete_cleanup`
and it does not count as suppression of future routing.

A case can declare one small routing challenge:

```json
{
  "routing_suppression": {
    "actor": "expert",
    "concept": "Ember Cache",
    "historical_question": 4,
    "challenge_question": 5,
    "body": "How does Ember Cache rebuild a receipt?"
  }
}
```

Both questions must already exist, belong to another actor, be distinct, and
remain untouched by ordinary case actions. The challenge body must change the
second question. Declare an identical exact `routing_notifications` expectation
for this actor in `initial`, `after_actions` and `after_challenge`; each must
include the historical question and exclude the challenge question. For
example, all three expectations can be `[4]`.

The runner creates the unrelated second question before mutation. It records
initial effects, applies the predeclared evidence edits/deletions, drains the
production worker, and observes withdrawal. Only then does it edit the second
question to the predeclared challenge body, drain again, and observe effects.
The gate requires initial expertise for the exact actor/concept and its absence
after mutation and after the challenge. The challenge question must be open and
unanswered throughout; initially and after mutation it must lack the challenged
topic, expert suggestion and signed-in match. After its edit it must carry the
exact topic tag while still lacking the withdrawn expert suggestion, match and
new notification. The original notification must remain. Thus an existing
`route:question:profile` deduplication key cannot explain a successful challenge.

These checks cover retained REST responses and synchronous production-queue
drains in fresh databases. They do not establish browser refresh behavior,
WebSocket delivery, arbitrary concurrent schedules, performance, real model
accuracy, or results beyond public endpoint response limits. Those need their
separate engineering and deployment evidence. Synthetic inference regressions
validate the harness and fault detection only; they are not held-out quality N.

## Prospective explicit topic feedback (September 22)

The new interaction requires a deliberate canonical-topic choice. Historical
`accepted: true`, `helped_by`, and endorsement actions remain broad impact only;
they supply no automatic topical expertise. Previously frozen corpora retain
their original bytes and interpretation. Their inputs must not be retrospectively
filled with inferred choices. No quality target or independent sample minimum
changes with this interaction.

New cases may declare `feedback_contract: "explicit_topics_v1"` and a chronological
`feedback` list. Each entry has exactly `after_post` (zero-based creation index),
`post` (the contribution), `actor`, `kind` (`helped` or `accepted`), and `topics`
(the exact, independently authored canonical names explicitly chosen). An
optional `expected_status` can declare an intentional HTTP 401/403/409 refusal;
otherwise success is required. Feedback runs after that source's normal worker
drain, then the worker drains again before the next declared event. Several
entries may share a timing index; their written order is authoritative.

Accepted-topic feedback requires a currently accepted answer and the authenticated
asker account. Helpful topic feedback requires an authenticated independent
account. A generic acceptance can precede a later new topical confirmation;
the later action must appear explicitly in the frozen chronology. The automatic
expertise policy requires at least two actor accounts, three independent
contribution/problem groups and one accepted-topic confirmation, for the same
contributor and exact canonical topic. Copies and answers to the same problem
do not multiply originality. Distinct accounts are not verified distinct humans.

The runner reads the public feedback context and searches current canonical
choices by the frozen names. It saves every lookup, selected ID/revision,
request, response and unavailable choice. It never chooses from `expect` or
`allowed`, seeds a missing concept, resolves an unchosen alias, or substitutes
another prediction. If any intended choice is missing or ambiguous, no partial
set is submitted; the missed coverage remains visible to quality grading.
Empty topic lists explicitly revoke that actor's feedback of that kind.

A lifecycle action may use `type: "topic_feedback"`, `post`, `actor`, `kind`,
`topics`, and optional `expected_status`. This permits a predeclared replacement,
revocation or explicit reconfirmation after an edit through the same public API.
The before-replay and after-actions effect phases apply to those actions too.
The selected quality decision and complete-output audit remain unchanged.

Labels must independently describe what the contribution demonstrates. The
choice itself is an input, not ground truth: a mistaken topical confirmation
can still produce a false positive. Include broad-only outcomes, misleading
choices, incidental/promotional topics, copied material, account reuse and
stale/revoked feedback. Neither an explicit click nor a passing mechanics test
establishes expertise precision. Authoring and review roles remain separated
from implementation and model outputs.
