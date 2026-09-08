# Automatic knowledge maintenance — implementation plan

Status: implementation approved on 8 September 2026. Audit baseline: commit `3243b0b5e81a37afda56a024e5d3b62a5c1ea367`. The implementation runs as a persistent Codex goal with gauntlet review. The clarifications below address the subsequent concurrency, evaluation, storage, and deployment requirements.

Verification status: the offline UBI 8.10/Python 3.12.14 suite passes all 200 tests without skips. Real local models publish concepts and a typed relationship without admin login. Browser checks cover live updates, topic correction, suppression/restoration, and persistent relationship direction edits. Offline bundle roundtrip, worker process limits, restart, generation upgrade, deployment recovery and rollback have been exercised.

The paired 50,000-item/50-client container load check passes: 503 writes preserved, no HTTP errors or unhandled database locks, and write p95 of 5.48 seconds with the worker versus 5.12 seconds without it (6.40-second fixed comparison gate). The worker completed real inference and peaked at 2.77 GiB RSS. These emulated-host results do not establish Xeon capacity. Earlier failed runs remain recorded; a measured feed index correction resolved the observed request timeouts.

Release validation remains open: final fresh integration review, Windows split/assembly execution, and representative labeled accuracy evaluation. The prepared GLiNER2.5 base/BGE-large combination has runtime and development-probe evidence; it has not passed the release quality gate. Conservative publication rules remain uncalibrated by default. Optional fitted decision models carry an explicit unverified quality status.

The first separate integration review identified duplicate acronym identities, incomplete alias matching, and a disk-reserve bypass for cached/profile evidence. Corrections resolve explicit definitions before creating identities, match token-preserving spelling variants, and recognize affirmative contextual naming statements. Existing distinct canonical identities remain intact. Global aliases still require independent support. Worker transactions now enforce the filesystem reserve across all charged derived data, including cached results and expertise; cleanup remains possible below the reserve. Contention counters cover acquisition, renewal, application, and maintenance operations. The corrected real-model alias and complete-retry load checks are pending.

**Outcome and limits**

Team contributions should build and maintain concepts, aliases, links, and expertise without an admin approval queue. Findings with sufficient evidence take effect automatically. Other findings wait for more evidence. Admins can inspect, edit, suppress, or restore automation for a finding at any time.

Support English, approximately 50,000 lifetime posts, and uploaded document passages. Posts can be long, so capacity must be measured in text chunks as well as post count. Use local pretrained extraction and embedding encoders; no generative LLM or external inference service. Models are acquired outside work and transferred through GitHub in parts.

The server is shared. Its 48 logical CPUs and 755 GiB RAM are not our resource allocation. Proposed initial ML budget: one inference worker, at most four logical CPUs, and 8 GiB resident memory for the ML processes together. These are ceilings, not targets. Use small batches, low CPU and I/O priority, and idle backoff. Benchmark within this budget before release. Do not expand it automatically.

**Current admin work and replacement**

| Current action | Proposed automatic behavior | Optional human control |
|---|---|---|
| Create concepts and choose canonical names | Extract specific technical entities and topic phrases from team text. Publish source-grounded, validated candidates. Select a stable name from actual source wording. | Edit name; suppress concept; restore automation. |
| Add/remove aliases | Detect explicit acronym definitions, spelling variants, and contextual equivalence. Publish only high-confidence equivalences. | Edit aliases; permanently exclude a false alias. |
| Resolve conflicting vocabulary | Resolve candidates against existing concepts before creation. Reuse the existing identity when equivalence is established. Keep ambiguous abbreviations contextual. | Correct the mapping. Preserve existing manually defined identities. |
| Approve/reject suggested links | Evaluate supporting passages, relationship direction, negation, and independent evidence. Activate qualified links automatically. | Suppress or pin a link; change its type or direction. |
| Create links and write evidence notes | Extract supported relationships with exact source passages and offsets. Show a factual evidence summary generated from stored data. | Add a manual assertion or note. |
| Create/rename/delete relationship types | Ship a small, stable vocabulary and map extracted predicates to it. Initial labels: related to, uses, depends on, part of, produces, and replaces. Use related to when a more specific claim is unsupported. | Keep current custom types and editing. New custom labels are not automatically trusted by the classifier. |
| Read endorsements and assign experts | Build topic-specific expertise evidence from accepted answers, helpful contributions, and endorsements by distinct people. Automatically publish qualified mappings for account holders. | Pin or suppress each person/topic mapping. |
| Remove obsolete mappings | Recompute when supporting items, outcomes, or account status change. Withdraw unsupported automatic mappings. | Manual mappings remain authoritative until explicitly released. |
| Test routing manually | Use the same effective concepts and expertise mappings in routing, preview, question details, and question priority. Retry unresolved questions when relevant findings change. | Preview remains a diagnostic, with reasons. |
| Inspect occurrences and manage the curation table | Replace the approval-focused view with automatically applied, awaiting evidence, suppressed, and manually fixed findings. Evidence inspection is optional. | Filter, inspect, edit, suppress, or restore. No required review count or daily approval task. |
| Adopt a contributor's correction | Keep the existing author/admin adoption action. A proposed correction can flag conflicting evidence but cannot establish truth by itself. | This remains an optional contributor-content decision. It does not block automatic knowledge maintenance. |
| Create admin accounts; deploy, reset, back up, or restore the service | Keep explicit account and operational controls. Automate worker lifecycle through the deployment tooling. | ML does not grant access, delete source content, accept answers, or award impact points. |

The last two rows are explicit scope boundaries for agreement. Correction adoption currently records adoption, a revision, and impact; it does not replace the original text. Automatically adopting corrections would change attribution and the meaning of impact, and requires a separate product rule. Answer acceptance belongs to the asker. Endorsements and “helped me” are contributor actions, not required admin approvals.

**Code audit: where findings have effects**

Paths below are relative to this project. Function names identify the integration boundaries.

| Area | Current code | Required integration |
|---|---|---|
| Vocabulary and tagging | `backend/app/concepts.py`: `match_concept_ids`, `retag_item`, `retag_passage`, `retag_everything` | Combine permitted exact matches with active contextual findings. Current retagging removes any tag not found by word matching; it must not erase valid ML tags or recreate suppressed tags. Queue bounded vocabulary backfills. |
| Admin changes | `backend/app/routers/admin.py`; `frontend/src/components/MapAdminPanel.vue`; `frontend/src/views/AdminExpertiseView.vue` | Record persistent overrides from every existing create/edit/delete action, including alias removal, relationship-type edits, and expertise removal. Preserve legacy manual records. |
| Source intake | `backend/app/knowledge.py`: `process_after_save`; `backend/app/docstore.py`: `save_uploaded_document`; items, questions, documents, and scratchpad routers | Queue team notes, questions, answers, published excerpts, and document passages. Observe edits, deletes, correction state, and outcome changes. Private scratchpads and private items are excluded. |
| Search | `backend/app/searchsvc.py`: `search_all`; `backend/app/text.py`: `build_fts_match` | Active global aliases improve current FTS expansion. Include active contextual concept matches through the existing concept/tag path, with duplicate removal and readable snippets. Keep direct lexical relevance primary. Tentative findings cannot expand search. |
| Item and question details | `backend/app/routers/items.py`: `item_detail`; `backend/app/routers/questions.py`: `question_detail` | Read the same effective tags used by the graph. These handlers currently repeat word matching at read time and would otherwise disagree with queued ML tags. |
| Expertise and routing | `concepts.py`: `route_question`; questions and expertise routers; admin routing preview | Refresh expert directory, suggested experts, and `matches_me`. Notify eligible people about still-unresolved questions when evidence becomes sufficient. Retain self-routing exclusion and notification deduplication. |
| Graph and evidence | `backend/app/relationships.py`; `backend/app/routers/graph.py`; `KnowledgeGraph.vue`; `EvidenceModal.vue` | Apply confidence and direction consistently in global and local graphs. Read evidence attached to the actual claim. Invalidate stale counts and links after edits or removal. Bound visible nodes/edges as vocabulary grows. |
| Live updates | `backend/app/live.py`; notifications router; `frontend/src/store.ts`; Home and open detail views | Worker commits need a database-backed revision signal. An open page must refresh affected graph, results, details, expert routing, and unread notifications after a revision changes. |
| Feed, duplicates, and impact | `knowledge.py`, `searchsvc.py`, feed and impact modules | Preserve current duplicate grouping and impact rules. Do not use embedding similarity to label contributions as corroboration or to distribute points. Deduplicate those sources when calculating ML confidence. |
| Delivery and lifecycle | `tools/deploy.py`, `tools/mdsctl.sh`, `tools/serverctl.py`; Alembic setup | Package the separate ML environment and assets. Stop the worker for migration, backup, activation, rollback, and reset; restart the matching worker with the matching release. |

Specific findings from code and public API checks:

- Concepts must already exist before normal tagging can find them. Vocabulary creation/edit currently scans and retags all items and passages synchronously.
- One shared occurrence creates a suggested link by default. Occurrence count is not confidence. A document passage and a copied contribution can count as separate occurrences.
- A standalone document upload tags its passages but does not directly run relationship discovery.
- Editing away a concept can leave a link with a stored count of one while its evidence endpoint reports zero. The admin link-list read recounts links; ordinary graph reads do not.
- Adding expertise makes an old question display the expert, but does not send that expert a routing notification for the question.
- The notification hub is process-local. Browser polling stops while its websocket is connected, so a worker cannot rely on the existing hub to signal its commits.
- Terms are globally unique across concepts. Concept links are treated as one link per unordered pair by the API. Typed, directed ML claims cannot simply be inserted into those structures without an explicit representation rule.

**Recommended model approach**

Start with two encoder families and small statistical decision models. Final checkpoints are selected by measured quality within the resource budget, not by size alone.

| Purpose | Initial candidate | Selection rule |
|---|---|---|
| Technical concepts and typed relationships | `fastino/gliner2-large-v1`, compared with the English GLiNER2.5 base checkpoint | Evaluate extraction spans, direction, negation, domain abbreviations, and CPU memory. Ship one winning extractor. GLiNER2 supports schema-based extraction and local CPU inference. [Maintainer documentation](https://github.com/fastino-ai/GLiNER2), [large model card](https://huggingface.co/fastino/gliner2-large-v1). |
| Candidate matching and contextual equivalence | `BAAI/bge-large-en-v1.5`, compared with its base variant | Use embeddings to retrieve possible matches, then apply evidence checks. Similarity alone cannot establish an alias, a fact, or expertise. These English models support local embedding inference. [Model documentation](https://huggingface.co/BAAI/bge-large-en-v1.5). |
| Confidence and expertise | Regularized logistic models with calibration, using extraction scores and observed evidence features | Use labeled development data where available. For cold-start expertise, use conservative evidence rules and abstention until there are enough outcomes for a useful fitted model. Do not train an encoder from scratch on a 50-person team's data. |

Use full-precision CPU execution as the reference. Evaluate ONNX for supported embedding exports. Quantization is optional and must retain the quality gate; do not assume an extractor has a working ONNX export. Older x86 CPUs can have quantization accuracy and speed issues, so server-specific measurements decide. [ONNX Runtime guidance](https://onnxruntime.ai/docs/performance/model-optimizations/quantization.html).

Development comparison on 8 September 2026 used 29 authored extraction sources and 16 authored retrieval triples, frozen before execution. Both extractors used the same application schema and source-grounding code. Each process ran offline with four CPU threads and an 8 GiB RSS ceiling on this Mac. Timings are native Mac measurements, not RHEL or Xeon throughput.

| Candidate | Observed development result | Inference time | Peak process-tree RSS |
|---|---|---|---|
| GLiNER2.5 base with BGE-large | 38 of 57 expected concept names at the fixed score floor; 5 additional names. 10 expected and 2 additional literal relation candidates. | 15.50 s | 2.85 GiB |
| GLiNER2 large with BGE-large | 27 of 57 expected concept names; 1 additional name. No literal relation candidates survived the same pipeline. | 30.70 s | 3.98 GiB |
| BGE-large | Related passage ranked above unrelated passage in 16 of 16 triples. | 6.69 s | 1.57 GiB |
| BGE-base | Related passage ranked above unrelated passage in 15 of 16 triples. | 3.13 s | 0.79 GiB |

These are candidate-span and retrieval probes, not end-to-end publication accuracy. Alternate phrase boundaries can count as additional candidates; publication also requires identity resolution and independent evidence. The 9-window long source reached its last sentence with valid original offsets. No thresholds were changed after this comparison. Retain GLiNER2.5 base/BGE-large provisionally; representative held-out validation remains required. The authored fixture hash is `13c4589cb4793118e4a37c346dc62d885c3a0dbc9c85a22cedc434e530a328e3`; local inputs, model pins and raw results are under `data/ml-model-comparison/` and are excluded from application commits.

Long notes and document pages are split into overlapping, sentence-aware windows within the selected model's actual input limit. Preserve original passage locators and character offsets. Repeated windows must not count as independent evidence. Initially use existing extracted text; OCR for scanned PDFs and broader document-format extraction are outside this change.

**Evidence and opt-out rules**

1. Extract candidates only from team-visible source text. Concept names must be anchored to a real span; filter generic phrases. Questions identify topics but their wording does not prove a factual relationship. Proposed corrections are competing claims, not established facts.
2. Resolve candidates against existing canonical names and aliases first. An ambiguous term stays contextual and is not added as a global search alias. Do not destructively merge existing concepts. New ML candidates can resolve to an existing identity; distinct manual identities remain intact.
3. Store the source identifier, source version/hash, passage locator, offsets, model version, raw score, evidence features, and decision policy for each finding. Keep polarity and direction for relationship evidence. Account for independent authors and original documents; copied passages, reposts, and duplicate groups cannot multiply confidence.
4. Separate model confidence, publication state, and human override. The override modes are automatic, pinned, and suppressed. Editing pins the relevant fields. Suppression survives new evidence, restart, model upgrades, reprocessing, and recreation attempts. Only an explicit restore-to-automatic action removes it.
5. Apply qualified concepts, aliases, mentions, relationships, and expertise automatically. Unsupported findings remain awaiting evidence without requiring review. Qualified findings can be withdrawn if their supporting sources disappear or contradict them. Human pins are preserved but show when support is absent.
6. Confidence means confidence that the source supports the extracted claim, not proof that the claim is true in the world. Use measured precision and calibration; never display cosine similarity or a raw extractor score as “probability correct.”
7. Keep source-specific and concept-wide opt-outs. Suppressing a concept removes its automatic effects across graph, search expansion, tags, routing, and expertise. Suppressing one mistaken occurrence affects only that source. Rejecting one relationship does not remove valid concept mentions.

For expertise, weight accepted answers and independent positive outcomes above posting volume. Uploading a document, asking questions, or copying text does not establish expertise. Do not use the model's own routing decisions as positive training labels. An ignored finding is unlabeled, not an approval. Optional human edits and new independent outcomes can improve future matching; more content alone does not guarantee higher accuracy.

**Graph behavior**

| Line | Meaning | Effect outside graph |
|---|---|---|
| Solid | Active automatic relationship that meets its quality policy, or a manual assertion | Available as an active relationship. Origin and evidence are visible on click. |
| Dashed | Plausible relationship awaiting more evidence | Display only; excluded from routing, expertise, and search expansion. |
| Dotted | Weak but useful association from shared context | Display only, behind a “show weak associations” option. Label as association, not a specific factual predicate. |
| Hidden | Below the display floor, suppressed, or unsupported after source invalidation | No automatic effects. |

Tentative links connect only published concepts; an uncertain link must not silently publish uncertain endpoints. Use active links for cluster membership so weak associations do not join the whole map into one cluster. Structural document/item links keep labels that distinguish an exact mention from an inferred topic match. Use arrows only for directional predicates.

Keep multiple typed/directed claims in ML storage, keyed by endpoints, predicate, and direction. For compatibility, the current pair-based relationship record remains the effective summary link. Show its strongest supported claim; expose other claims and conflicts in evidence. If no specific claim is safely dominant, show “related to” without an asserted direction. Preserve manually fixed labels. This avoids changing the existing relationship uniqueness constraint in the first implementation.

Add confidence/origin labels and a legend. Bound the overview and offer focus/search to reach omitted concepts; show the total count so omission is explicit. Prefer active links in the already-limited local graph. Refresh without discarding the user's current focus or pan position.

**Module and queue design**

Put inference, candidate resolution, decision rules, queue processing, and application adapters in a new `backend/app/ml/` package. Keep heavyweight imports in the worker entry point. The web app uses only its lightweight adapter and API. Use separate ML requirements and an ML environment so dependency changes do not force upgrades in the current web runtime.

Use additive `ml_*` tables in the existing SQLite database for jobs, findings/overrides, source evidence, embeddings, and revision/worker status. A small Alembic migration registers these tables and narrowly scoped queue triggers. Do not rename existing columns, move existing modules, replace SQLite, or introduce Redis/Celery/a vector database.

Use database triggers on relevant source and outcome changes to enqueue durable, coalesced work in the same transaction as the change. Cover inserts, semantic edits, deletes, visibility, correction/answer status, outcome events, and profile account eligibility. Do not enqueue on reads or on worker-only metadata changes. This avoids adding inference hooks to every posting route and catches changes made through other routes in the newer work version, provided its schema contract matches.

The worker claims a bounded job with a lease, reads a committed source version, releases database locks, performs inference, and applies results in a short transaction. At application time, check the lease, source hash, visibility, model version, and latest human overrides again. Obsolete jobs cannot publish. Crash retries are idempotent; expired leases recover automatically. Repeated failures are isolated so they cannot block all later content.

SQLite keeps WAL mode, with an explicit 5,000 ms busy timeout for web connections and a shorter 250 ms worker timeout. A busy worker rolls back and retries with bounded backoff; it never holds a transaction during model loading or inference. Bound each apply transaction to one source and each backfill page to 25 sources. Retain the 1,000-page automatic checkpoint as a safety net; use passive checkpoints between batches when the WAL grows. Back off at a 64 MiB WAL high-water mark. Do not force a blocking truncate checkpoint during web traffic. Require local filesystem storage, not NFS/SMB. Verify the runtime SQLite includes the WAL-reset fix (3.51.3+, or a documented fixed backport). [SQLite WAL behavior and fixes](https://www.sqlite.org/wal.html).

Concurrency acceptance: a real web process and separate worker process, a seeded 50,000-item database, and 50 concurrent clients with a mixed read/write workload. Require zero lost or duplicated writes, zero unhandled lock errors, and web-write p95 no worse than the larger of 1 second or 1.25 times the worker-off baseline. Record absolute timings, WAL peak, and lock retries. The paired container check establishes contention overhead; repeat on the deployment host to establish its absolute capacity.

Publish canonical records and accepted tags through one adapter using the existing tables. Preserve stable IDs and provenance. The existing tagging functions must use this same effective result instead of overwriting it. Old manual records become pinned; old rejected relationships become suppressed. Legacy automatic suggestions are re-evaluated, not blanket-approved.

Vocabulary changes queue bounded backfills over existing text. Source removal invalidates its evidence immediately and queues affected recomputation; reads exclude invalid support while work catches up. Prioritize new contributions and unresolved questions over historical backfill. Use cached embeddings and bounded nearest-candidate retrieval, not a full corpus cross-product per post.

Each applied batch increments a database revision. A small shared browser watcher checks it periodically and on return to the tab, then refreshes affected views and notifications. It must work even while the existing websocket is healthy. This provides cross-process updates without adding a message broker. Routing only sends still-relevant, deduplicated notifications; backfill does not flood users with resolved historical questions.

**Shared-server controls and operations**

- Limit the inference worker and native math libraries to the CPU budget. Restrict CPU affinity within the allocation available to our account. Use one worker initially; do not spawn one worker per detected CPU.
- Use an available user-level cgroup for an enforced memory/CPU quota. Where unavailable, enforce CPU affinity/thread limits and monitor the complete ML process tree's RSS; terminate an over-budget inference child and retain its job safely. RSS monitoring allows a short overshoot and is not a hard kernel memory cap. Establish the permitted enforcement method before production deployment.
- Start with batch size one and increase only within the fixed budget after measurements. Load models sequentially if simultaneous residency exceeds the budget. Do not silently deploy a less accurate checkpoint to hide a resource failure.
- Measure app request latency, ML peak RSS, CPU use, disk growth, queue age, and completed text chunks per minute while normal app activity runs. Pause/back off background work on resource or database pressure. Limits and backoff include backfills and local calibration.
- Cap stored embeddings at 4 GiB, including active and staged model generations and reserved row/index overhead. BGE-large uses 1,024 float32 values: 4,096 bytes per raw vector; multiple windows per post multiply this cost. Keep embeddings in binary form. Prune deleted-source and superseded vectors in bounded batches. Reserve space before a model rebuild, publish the new generation only when complete, then retire the superseded generation. If the quota cannot fit a rebuild, pause that rebuild and keep the current generation usable. [BGE dimensions and input limit](https://huggingface.co/BAAI/bge-large-en-v1.5).
- Use a separate 16 GiB ceiling for managed model bundles, unpacked weights, ML runtime/wheels, and transfer staging. Reserve at least 2 GiB filesystem free space before any managed allocation; this does not reserve space for unrelated services. Bound derived evidence/job retention and report shared database growth separately. Never remove user uploads, source contributions, manual decisions, or unrelated files to meet a quota. Disk exhaustion stops ML growth, not normal knowledge capture while storage remains available to the app.
- Extend current no-root process tooling to start, stop, inspect, and restart the worker. Keep assets and caches outside individual application releases. Backups and resets must include or deliberately invalidate derived state. A stopped worker leaves capture, ordinary search, and the last valid published knowledge usable.

No server throughput or latency promise is made from the hardware screenshots. Before building the installation bundle, inspect the actual RHEL release, glibc, Python version, CPU features, allowed affinity, free disk, and available user-level limits. Build the wheel bundle for that Linux target, not for this Mac or the Windows transfer machine.

Use a pinned Red Hat UBI 8.10 `linux/amd64` Docker image to test compatible RHEL userspace, uv-managed Python 3.12, non-root execution, migrations, offline dependencies/inference, worker lifecycle, and resource limits. The ARM Mac will emulate x86; Docker does not reproduce the deployment kernel, SELinux policy, filesystem, or Xeon performance. Record those limits and retain a final server preflight. The documented app serves its compiled frontend through FastAPI; do not add Caddy unless a real deployment proxy requirement is identified.

**Model split and assembly delivery**

Implement a small cross-platform Python utility with `split`, `assemble`, and `verify` operations. It will use the standard library so assembly needs no model dependencies or internet access.

- Prepare complete pinned model directories outside work: weights, tokenizer, configuration, pooling files, any required encoder/tokenizer assets, licenses, and a version manifest. Resolve all transitive model dependencies before packing.
- Split each archive into numbered parts of at most 95,000,000 bytes. This stays below the requested 100 MB and GitHub's 100 MiB repository file limit. GitHub's browser upload limit is lower; use Git for repository parts. Prefer release assets if reachable, otherwise a separate artifact repository. Keep binaries out of this application's Git history. [GitHub limits](https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github).
- Record part order, byte lengths, SHA-256 per part, final archive hash, model revisions, licenses, and target runtime. Stream assembly, detect missing/repeated/corrupt parts, and verify before activation. A trusted manifest accompanies the chosen release; hashes alone do not establish who supplied it.
- Assemble to a temporary file and atomically rename only after verification. Safely extract within the target directory. A failed transfer must leave the previous working installation intact.
- Include a separately pinned CPU-only Python wheel bundle for the target server and an offline installation path. Do not rely on Hugging Face, PyPI, Git LFS endpoints, automatic downloads, or cloud fallback being reachable at work.
- Verify a fresh installation with network access disabled, including the first tokenizer/model load and real inference. Verify the assembled archive is byte-identical and that split/assemble works from Windows Python as well as Linux.

Preparing tools and local bundles is in scope after agreement. Publishing binaries to a GitHub repository is a separate external action; no repository has been selected for that yet.

**Quality gate and implementation order**

Quality is assessed per decision type. Acceptance targets for automatically applied findings: at least 98% measured precision for concepts and typed relationships, 99% for global aliases, and 95% for expertise mappings. Require at least 50% recall of eligible labeled positives in each category. Each category needs at least 300 independent labeled evaluation decisions, including at least 150 positives, 100 hard negatives, and 100 automatically applied predictions. Related source copies count as one evaluation group. Below any minimum, the outcome is insufficient evidence, not PASS. These are frozen initial release targets, not demonstrated performance or raw-score thresholds. Report 95% confidence intervals, sample counts, coverage/abstention, and CPU cost alongside the pass/fail result. No target may be lowered after inspecting held-out results. Include a separate chronological cold-start/backfill scenario proving that activation occurs without an admin.

Build labeled development examples and a separate frozen evaluation set covering technical phrases, acronym ambiguity, negation, hypothetical questions, reversed relationships, contradictions, repeated documents, edits/deletes, private content, and sparse expertise outcomes. Group related passages and duplicate sources into the same split. Fit/calibrate on development data; evaluate on held-out data. Do not treat model-generated labels, synthetic examples alone, or an admin's silence as proof of real-world quality.

Public benchmarks and authored fixtures can establish a baseline here. Quality on the work team's vocabulary remains unverified until representative, appropriately labeled work examples can be evaluated locally. This is a release-validation need, not a recurring admin approval workflow. If representative data is unavailable, report that limit and keep unvalidated decision categories conservative; do not invent calibrated percentages for them.

1. **Offline model and resource proof.** Select the smallest set of checkpoints meeting the quality target within the fixed budget. Deliver verified split/assembly tools, a reproducible asset manifest, and the offline runtime plan.
2. **Complete concept/alias slice.** Add additive storage, durable jobs, source-version checks, extraction, automatic publication, and persistent overrides. Verify new content and historical backfill through graph, search, item details, and documents.
3. **Complete relationship slice.** Add typed evidence, independent-source counting, confidence states, source invalidation, graph styles, and optional finding management. Verify uncertain links have no downstream decision effects.
4. **Complete expertise slice.** Add evidence-based automatic mapping and retraction, unresolved-question routing, and consistent expert directory/preview/priority. Preserve contributor-driven impact and account eligibility.
5. **Integration and deployment proof.** Complete live refresh, bounded backfill, worker lifecycle, offline installation, migration/rollback, and CPU/RAM enforcement. Verify normal web traffic while queued inference runs.

Implementation uses gauntlet mode because it spans inference, data integrity, background processes, and several visible workflows. Each material milestone requires real-path verification and an independent read-only critic. Tests are limited to the agreed behavior and material invariants; use the existing backend and browser suites for regression coverage.

Final acceptance is an offline, CPU-only end-to-end demonstration: users post and upload content; concepts, qualified relationships, and experts appear without admin login; new evidence upgrades a tentative link; weak evidence remains display-only; edits/deletes withdraw stale automatic effects; admin edits and suppressions survive reprocessing; an open browser updates; interruption resumes without duplicate findings or notifications; model parts reassemble correctly; resource limits hold while the app stays usable.

**Merge expectations**

Most new logic and dependencies can be isolated. Zero conflicts cannot be promised without the newer work tree. Correct behavior requires small edits to existing tagging, readers, admin controls, graph UI, refresh, migrations, and deployment tooling.

Keep module work, the additive migration, and adapter/UI changes in separate focused commits. Use an explicit schema/API contract and a clear failure when it does not match. Rebase the new migration onto the actual work migration head before transfer. Preserve custom relationship types and manual records. Do not rewrite migration history or carry model binaries in application commits. Before merging at work, compare those integration boundaries against the newer version and run its existing tests plus the offline user journey.
