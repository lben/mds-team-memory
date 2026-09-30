# BGE Update verification — 29 September 2026

Branch: `gpt6.1solhigh_bge`, from `withgpt6.1solhigh` at `3aab705`.
BGE implementation: `e06d70b`. Prior non-LLM evidence was committed before
branch creation. At the time of this record, work was local with no push or
real UAT/PROD deployment. Later TestData implementation and authorized GitHub
publication are covered by [TestData verification](TESTDATA_VERIFICATION.md).

**Deployment and functional checks pass; quality and capacity gates fail.**
The real 24-case authored run completed its inference but failed expectations.
The 50-client capacity run preserved writes but exceeded its paired latency gate.
Use this as a BGE UAT candidate, not evidence of production-quality approval.

## Delivered models and behavior

| Model | Pin | Role |
| --- | --- | --- |
| GLiNER2.5-base-v1 | `72ac19b486cd4557424c8d61114e7530c243e9b0` | Grounded entities, typed relationships and scored alias fields |
| BGE-large-en-v1.5 | `d4aa6901d3a41ba39fb536a557fa166f842b0e09` | 1,024-dimensional source vectors and source/name cosine relevance |
| spaCy en_core_web_trf 3.8.0 | `272a31e9d8530d1e075351d30a462d7e80e31da23574f1b274e200f3fff35bf5` | Syntax corroboration, assertion scope and alias definitions |

One inference child loads all three. BGE relevance shares the existing encoder
and reuses source vectors where its token windows match extraction chunks.
Long sources use candidate-containing windows of at most 512 BGE tokens with
64-token overlap. New inference includes relevance immediately; a bounded idle
sweep checks current caches missing a current judgment in that same child.
There is no Qwen checkpoint, llama.cpp wheel or generative LLM in this payload.
Historical Qwen blobs remain in Git history and retained rollback generations.

The fixed rule is cosine >= 0.6423084735870361 with extractor confidence >=
0.995 from one source, or >= 0.8 with two independent provenance groups.
Extraction/policy identities advance to v12; upgrades reapply sources and do
not trust historical Qwen-scale margins. Manual pins/suppressions remain
explicit. A conflicting alias pin now returns HTTP 400 before mutation, keeping
already-established concept IDs and relationships intact.

## Package and operator flow

| Item | Value |
| --- | --- |
| Model/dependency parts | 25, each <= 95,000,000 bytes |
| Compressed archive | 2,348,262,935 bytes (2.19 GiB), down from 4.49 GiB |
| Expanded models/wheels | 2.79 GiB, down from 5.13 GiB |
| Archive SHA256 | `25354fabc6c2612567b9a1fa4f49b853529b082dcfa3908a8021f8556d04f1f3` |
| Linux wheels | 88; llama.cpp and diskcache removed |
| Bootstrap | Python 3.12.14, SQLite 3.53.1, uv 0.12.10 |
| Bootstrap SHA256 | `44d6873c714fd5b04083620fdff883c5be9800b647525df3b6f4c3816ffe06ea` |

After publication and pull, fill the ignored `tools/deploy.toml` from its
example with both servers' accounts, addresses, SSH/application ports and
separate roots. Bundled Python/uv defaults avoid prerequisite binary setup.

```powershell
.\Update.cmd --check  # optional local checks; no connection
.\Update.cmd          # UAT by default
.\Update.cmd PROD     # explicit production selection
```

Enter the SSH password once. First host trust is separate unless IT's SHA256
fingerprint is configured. Update uploads code and any missing parts, joins,
verifies, decompresses, installs offline using uv, migrates and starts the
web/ML daemons with nohup. Success requires web health and a live ML lease.
Later updates verify and reuse the generation. The first install needs a
server-side `mdsctl.sh manage create-admin` before administrator login.
nohup survives logout; it does not restart after a server reboot.

The PC needs Git, uv and roughly 14 GiB free, plus internet or a populated uv
cache for initial Python/Paramiko client setup. The server needs RHEL 8.10
x86_64, password-capable SSH/SFTP, bash/tar/sha256sum, a writable local filesystem
and about 14 GiB free including the 2 GiB reserve. IT must allow the app port.
UAT/PROD on one host need distinct roots and ports. Retained old generations can
increase storage; Update refuses an allocation over its managed 16 GiB budget.
The smaller current payload does not remove old assets from Git clone history.

## Verified operational checks

- Real Qwen-to-BGE SSH upgrade on nonroot UID 10001: one password prompt,
  all 25 parts joined/verified/extracted, offline installation, schema migration,
  web health and live worker. Old account, post and manual concept preserved.
  [Upgrade log](verification/bge/bge-upgrade.log).
- Clean first BGE installation into a separate empty UBI 8.10 server; three
  roles and no installed llama.cpp module. One prompt, healthy web/ML daemons.
  [First install](verification/bge/bge-first-install.log),
  [actual Python/SQLite/uv runtime](verification/bge/clean-runtime.log).
- Repeat Update reused the verified generation without model upload. Final
  committed code was also installed normally into the clean server.
  [Repeat](verification/bge/bge-repeat-upgrade.log),
  [final Update](verification/bge/bge-final-update.log).
- Deliberately unavailable bind address failed startup; the previous compatible
  BGE release/configuration was restored without restoring a database over
  human writes. [Recovery](verification/bge/bge-failed-start.log).
- Real HTTP capture and background inference published the concept and CVM
  alias from sufficient independent evidence; search/tags worked; edits and
  deletion retired the derived vectors and finding. Manual marker data survived.
  [Live flow](verification/bge/live-e2e.log).
- Delivered runtime smoke: actual entities and typed `part_of` relationship,
  normalized `[1,1024]` vectors, syntax-corroborated CVM definition, finite BGE
  cosine scores, and an assertion that both uses share the same encoder.
  Main-process peak RSS 4,076,359,680 bytes; four CPUs, hard 5 GiB limit,
  no extra swap. [Three-model smoke](verification/bge/three-model-smoke.log).
- Fresh local Git checkout at `e06d70b` with `core.autocrlf=true`: CMD had CRLF;
  server scripts, manifests, locks and UI source kept LF; binary parts retained
  their verified hashes.
  Actual uv-managed `Update --check` passed all 25 parts and fingerprints offline.
  Windows-style endings were tested on macOS, not an actual Windows host.
  [Checkout check](verification/bge/windows-checkout.log).

- Final Red Hat UBI 8.10 Linux backend regression suite on committed source:
  **803 passed, zero skipped**, two deprecation warnings, 635.95 seconds.
  This covers application behavior and supervised worker integration; real
  model quality is measured separately below.
  [Complete suite](verification/bge/linux-complete-check.log).

## Capacity result: FAIL

The unchanged paired test used 50,000 initial items, 50 concurrent clients,
60-second traffic targets per phase and a 30-second drain observation. Both
phases held the same models resident; only queue consumption changed. It used
the final committed deployed code, four CPU quota slots, a hard 5 GiB memory
limit and zero additional swap on the emulated x86_64 UBI server.

| Measurement | Worker off | Worker active |
| --- | ---: | ---: |
| Requests / API errors | 751 / 0 | 666 / 0 |
| Successful writes | 147 | 130 |
| Write p95 | 6.855 seconds | 10.305 seconds |
| Read p95 | 8.200 seconds | 8.903 seconds |

The write acceptance gate was 8.569 seconds (1.25 times baseline, with a
1-second floor), so the capacity verdict is **FAIL**. All 277 successful writes
were present with correct content and no duplicates; SQLite quick_check was
ok and foreign-key errors were empty. Five real sources/chunks completed
during traffic and three more during drain. No job errors, unhandled lock
errors or worker contention retries were observed; the worker stayed alive.

Sampled worker-tree peak RSS was 4,188,872,704 bytes, web-tree 384,352,256 bytes,
and total cgroup memory 5,165,117,440 bytes below its 5 GiB hard limit. Cold start
through first inference took 53.41 seconds. No OOM was observed. A shutdown
semaphore cleanup warning remains in the log. This is a bounded load observation,
not proof that the 50,000-item queue can drain overnight. No limits or acceptance
criteria were relaxed after this result. Native UAT measurements are still needed
to establish server throughput and acceptable foreground impact.
[Capacity report](../research/bge-integration/capacity-run/report.json),
[request traces and runtime/source hashes](../research/bge-integration/capacity-run/),
[capacity log](verification/bge/bge-capacity.log).

## Separate quality results and limitations

The real 24-case run completed **60/60 inference calls**, with zero execution
errors. Overall: **FAIL** (7 PASS, 15 FAIL, 2 INCOMPLETE). Positive expectations
met: concepts 23/38, aliases 3/3, relationships 3/9, expertise 0/2. All 48
selected negative expectations passed. These are assertion counts, not
complete-output precision. The two incomplete scenarios could not prove
withdrawal because expected initial findings never published. The thresholds
and frozen labels were not changed to make results pass.

The diagnostic runner used the delivered runtime with separate test-only HTTP
modules for TestClient; the deployed immutable runtime was not modified.
Sampled process-tree peak RSS was 4,154,503,168 bytes and maximum affinity was
four CPUs. A shutdown resource-tracker semaphore cleanup warning was retained;
all measured inference completed and no scenario had an execution error.
[Full reports and observations](../research/bge-integration/real-model-run/report.json),
[model-run log](verification/bge/real-quality.log).

Prior domain-fold BGE proxy: 97.69% complete-output concept precision / 78%
selected recall, below the unchanged 98% precision gate. The frozen all-spent-data
selection gave optimistic 98.53% / 82.67%; it is not independent validation.
No weights were trained. Neither corpus establishes fresh release acceptance
for any category. See [research evidence](../research/bge-integration/README.md).

All SSH/model checks used isolated localhost-only Red Hat UBI 8.10 x86_64
containers with fake credentials and separate data. The ARM host emulated x86.
They establish deployment/runtime behavior, not production Xeon throughput or
actual RHEL host kernel, firewall, account and filesystem behavior. ML retains
four-CPU affinity, nice +10, idle I/O where supported and a monitored 8 GiB RSS
ceiling lowered to the cgroup limit. RSS monitoring is not a hard kernel quota;
these tests supplied a 5 GiB hard container limit. A worker keeps its models
resident while idle. Cold-load deadline defaults to 300 seconds, per-source
inference to 1,800 seconds; both are configurable.

The initial Git publication requires the asset batch commits in sequence.
After explicit owner authorization, use `python tools/push_update.py`;
`--dry-run` displays commands without publishing. No push has been performed.
