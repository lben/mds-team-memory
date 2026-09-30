# BGE integration evidence — 29 September 2026

The owner selected BGE after the separate non-LLM comparison. Implementation is
committed on `gpt6.1solhigh_bge` (`e06d70b`); the prior research was committed on
`withgpt6.1solhigh` (`d46ef86`, `3aab705`). No push or actual UAT/PROD deployment.

`implementation-plan.json` was frozen before application tests at SHA256
`39964268cb85d3f0ecf93e4ec55ebdd6682da08fce981a32caf958bdbd2750ce`.
Its cosine / one-source / two-source settings were not changed after results.
No model weights, frozen fixture labels or acceptance targets were changed.

## Real model run

`real-model-run/` preserves all 24 scenario reports, 60 production predictions,
API observations and diagnostic JSON. Binary SQLite databases are excluded.
The original artifact directory was `/home/mds/bge-quality/ml-quality-r2yk_h1t`;
its source hashes and model pins remain unchanged in the report.

This used the delivered UBI 8.10 x86_64 CPU runtime, four CPU affinity slots,
a 5 GiB hard container limit and no extra swap. The immutable runtime was not
modified. TestClient needed test-only HTTP dependencies: httpx/httpcore were
provided via a separate diagnostic PYTHONPATH, not installed into deployment.
GLiNER, BGE and spaCy used the delivered weights and dependencies. Each case had
a fresh migrated database; one real production inference child was reused.

Result: **FAIL**; 7 scenarios passed, 15 missed frozen expectations and 2 were
incomplete because required initial findings had not published for withdrawal.
All 60 inference calls completed; no case had an execution error. Sampled peak
process-tree RSS was 4,154,503,168 bytes, maximum CPU affinity 4. Assertions:

| Category | Positive expectations met | Negative expectations met |
| --- | ---: | ---: |
| Concepts | 23 / 38 | 8 / 8 |
| Aliases | 3 / 3 | 5 / 5 |
| Relationships | 3 / 9 | 21 / 21 |
| Expertise | 0 / 2 | 14 / 14 |

These counts are selected assertions, **not complete-output precision**. This
already-spent authored corpus is too small for the 300-decision release gate.
The earlier cross-domain concept-only BGE proxy was 97.69% precision / 78% recall
and missed the unchanged precision minimum. The all-data choice was optimistic
98.53% / 82.67%. Neither result establishes release acceptance.

The manual alias collision guard was added after the model run. It affects
only administrator pin requests; it does not change inference or decision
scores. Source hashes in the model report describe the exact measured snapshot.
Its behavior is covered by the final backend and identity regression tests.

## Capacity run

`capacity-run/` retains the complete report, runtime/model/source hashes,
per-request traces, resource samples and web/worker logs. The binary database
and duplicate source tree are excluded; the source corresponds to `e06d70b`.
The delivered final runtime processed actual inference while 50 clients used
a 50,000-item database under a four-CPU quota and hard 5 GiB/no-extra-swap limit.

Result: **FAIL**, because write p95 rose from 6.855 seconds to 10.305 seconds,
above the fixed 8.569-second paired gate. All 1,417 API requests succeeded and
all 277 writes were preserved without duplicates or corruption. Worker-tree
peak RSS was 4,188,872,704 bytes; sampled cgroup peak was 5,165,117,440 bytes.
Five sources completed during traffic, three more during drain; there were
no job/lock errors or contention retries. This emulated x86 run cannot establish
native Xeon throughput or overnight queue capacity. Thresholds and gates stayed
unchanged; this result is retained as a failure, not production approval.

## Live server flow

`live-initial-held.log` preserves the first exploratory two-post scenario: real
inference worked but the concept lacked enough qualifying evidence. Adding a
third independent substantive post demonstrated concept publication, CVM alias
search, tags, edits, deletion and vector withdrawal. That final functional flow
is in `live-e2e.json`; it is not a new quality observation or a replacement for
the failed scenario. The isolated old account, post and manual concept survived
Qwen-to-BGE Update. Thresholds stayed fixed.

`live_e2e.py` is the historical localhost-only harness with fake credentials.
It expects the ignored `build/bge-update/test.toml` and seeded fake marker data.
The diagnostic server image recipe is `Dockerfile.server`; it has a deliberately
fake SSH password and must be bound to localhost. Its host key is generated at
build time, so configure the actual fingerprint when reproducing. The test
image recipe extends the documented Linux unit-test image with Paramiko.

Full deployment, regression, fresh-checkout and capacity evidence is indexed in
[deployment/BGE_VERIFICATION.md](../../deployment/BGE_VERIFICATION.md).
