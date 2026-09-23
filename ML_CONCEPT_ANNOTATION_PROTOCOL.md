# Prospective uniform concept annotation protocol

Status: APPROVED_METHOD_ONLY. September 22, 2026.
Version 2 addresses the single grammatical-variant issue in the V1 review.
This document does not authorize inference or approve any corpus.
The method body was independently approved at SHA-256
`c3ab57fb897323a8fc5fb91929ee670a17274ce3fc360c37c126b45f130e8389`;
this published copy changes only status and adds this provenance note.
Approval record: `prospective-replacement/independent-uniform-method-approval-v2.json`
under the retained `data/ml-runs/astra-sep14/fresh-evaluation-sep22/` evidence.

## Purpose and unchanged requirements

The independent R3 review found inconsistent concept specificity and alternate
name coverage. R3 remains rejected. This prospective revision clarifies the
existing source-semantic concept standard before any replacement inference;
it does not use model outputs, scores, extraction rules or desired results.
All numeric acceptance targets, independent-decision counts, source eligibility,
alias/relationship/expertise semantics and public-effects requirements remain
unchanged. Existing exposed gold and all reported outcomes remain untouched.

## What constitutes an acceptable concept

Annotate a distinct, reusable subject of knowledge supported by the team-visible
source: a named entity, domain topic, method/process, tool/artifact, phenomenon,
or material/substance. A concept need not be a proper noun or familiar outside
its domain. Its specificity must be understandable from the text alone.

A source must treat it as a substantive subject: explain its behavior or role,
ask about it, compare it, give an outcome about it, or use it as a meaningful
participant in a domain claim. Mere occurrence in prose is insufficient.
Incidental circumstances, arbitrary descriptive fragments, generic words with
no identifiable domain sense, measurements, and formatting/configuration values
are not automatically independent concepts. A configuration technique or a
domain quantity can qualify when it is itself a substantive subject; its type
alone is not a blacklist. Record the specific textual reason for borderline
inclusion or exclusion. Apply the same distinction in every domain and case.

Concept presence and assertion truth are separate. A genuine question, denial,
quotation or hypothetical can still mention a substantive topic, while supplying
no positive factual relationship or expertise evidence. Private-only presence
does not authorize a public concept. Existing identity ambiguity requirements
still apply. Do not exclude a valid topic merely to make a selected negative
label work; flag an inconsistent selected label for separate rejection.

## Names and boundaries under exact-name grading

First enumerate semantic subjects, then their acceptable source-grounded names.
Do not start by listing every noun phrase or substring. Every allowed name must
denote a qualifying subject in its source context and retain the words necessary
to distinguish that subject. Case and ordinary whitespace differences follow
the existing grader's normalization; do not invent additional normalization.

Include ordinary identity-preserving singular/plural and possessive/unpossessed
forms of an attested eligible name, even if that grammatical form is not verbatim
in the source. List them explicitly; the grader itself remains unchanged. Do not
apply that expansion to fixed names, acronyms, number-sensitive terms, substances
or other expressions when the change alters identity or domain meaning. Document
such exceptions. Different hyphenation, spelling and other lexical forms remain
source-bound. A shorter boundary is acceptable only when it remains
a meaningful name for a qualifying subject; dropping an identity-defining word
is not automatically a valid alternate. A base term can be a separate concept
when the source substantively discusses that broader subject. Explain that
decision, rather than accepting it solely because it is a substring.

Include a paraphrase only when its words occur as a source span naming the same
qualifying subject. Apart from the grammatical forms explicitly permitted above, do not generate
unseen spellings or synonyms. Acronyms and
expanded names still need their separate identity/alias analysis. An allowed
concept name is not an assertion that two names are globally interchangeable.
There is no fixed minimum or maximum allowed-name list length.

## Prospective continuation scope

The baseline is the rejected R3 merged candidate, SHA-256
4039d7b750f813698568f0b1d960d40e6ee8be19053311b18c6ee29b5c8df677.
It contains 14 entirely new replacements and 286 still-unobserved original
cases. None of those 300 scenarios has been run in the replacement candidate.
The original interrupted corpus and its 14 exposed cases remain historical.

After independent method approval, a clean author audits all 300 scenarios
uniformly under this rubric, including cases needing no change. Source text,
authors, chronology, visibility, feedback, selected expect labels, other output
categories, actions, effects, grouping and case order must remain byte/semantic
identical as applicable. Only the concept allowed-output lists may be amended
in a newly sealed copy. This explicitly supersedes the prior requirement that
all 286 retained JSON objects remain completely identical; their source content
and all non-concept-list fields must still remain identical. Record every changed
list, source-based reasons and before/after hashes. Never edit an existing seal.

If the audit exposes a selected label, source, identity, other category or effect
that cannot satisfy this protocol, report it and withhold approval. Do not quietly
change that field or reinterpret a failing case. No inference may resolve an
annotation disagreement. Model scores and implementation outputs remain hidden.

An independent reviewer then checks every case for the same specificity and name
coverage standard, verifies the restricted field changes and all fixed counts,
and records approval or rejection of exact hashes. Both agents may request a
single clearly scoped correction for a documented source-semantic error; repeated
expansion/pruning toward an imagined model result is prohibited. Unresolved
methodological disagreement means NOT APPROVED, not another automatic cycle.

Root receives metadata, counts, field-change verification and hashes only.
Detailed case text and label rationales remain sealed from implementation work.
No new or amended case can be called human-authored or representative workplace
data. This audit establishes prospective annotation consistency, not model
quality. Any eventual execution also needs frozen code/models, sufficient disk,
and the unchanged operational, quality and lifecycle gates.
