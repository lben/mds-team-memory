# TestData on UAT

Pull this branch, configure `tools/deploy.toml`, then run `Update.cmd UAT` once
to install the TestData server code, datasets and UAT environment marker.
Use your existing app account to browse the resulting UAT feed, search and graph.
No administrator password is required by TestData; server commands use the same
SSH transport as Update. Paramiko requests the SSH password once; native OpenSSH uses
the configured binary and its normal console prompts. First host trust is a
separate prompt unless its fingerprint is configured. No server internet, root
access, model download or additional runtime dependencies are needed.

## Commands

```powershell
.\TestData.cmd datasets
.\TestData.cmd UAT preview --dataset expanded --percent 25 --seed 42
.\TestData.cmd UAT add --dataset expanded --percent 25 --seed 42
.\TestData.cmd UAT add --dataset cross-domain --topics astronomy,software --percent 100 --seed 42
.\TestData.cmd UAT add --dataset capacity --percent 10 --seed 42
.\TestData.cmd UAT batches
.\TestData.cmd UAT status --batch <batch-id>
.\TestData.cmd UAT status --batch <batch-id> --wait --timeout 36000
.\TestData.cmd UAT remove --batch <batch-id> --timeout 1800
.\TestData.cmd UAT remove --all-batches --timeout 1800
```

UAT is the default: `TestData.cmd add ...` is equivalent. `PROD` is refused
before configuration/password handling. The server independently checks its
deployed `MDS_ENVIRONMENT=uat`; pointing a UAT client at a PROD deployment fails.
`MDS_ENVIRONMENT` is managed by deployment and cannot be set in `[target.env]`.
Older deployments without the marker must be updated before TestData can run.
You can put `--config path/to/test.toml` after any server subcommand to use a
different single TOML configuration. Local `datasets`/`preview` need no config
or connection. uv's first client/Python setup can still require internet/cache.

macOS/Linux: `./TestData [UAT] <command> ...`. PowerShell also supports
`TestData.ps1`. The equivalent Python entry point is
`uv run --system-certs --python 3.12 tools/testdata.py [UAT] <command> ...`.
The launchers use system TLS certificates for uv's client downloads; see
[deployment certificate setup](README.md#deployment-uat--prod) for company CAs.

## Datasets and selection

| Dataset | Pool | Topics / content |
| --- | --- | --- |
| `cross-domain` | 24 scenarios, 60 posts | Astronomy, software, electronics, maintenance, digital media, industrial engineering, home automation and software operations |
| `expanded` | 300 scenarios, 1,475 posts | 12 domains including data systems, libraries, music, manufacturing, transport, food, astronomy, ecology, sports, crafts, education and buildings |
| `capacity` | 50,000 generated notes | Software operations involving 20 technology names; up to 50 test contributors, including medium/long notes |

The first two contain the initial content from already-spent synthetic quality
fixtures, including questions, answers, accepted answers and generic helpful
feedback. They exclude evaluation labels, expected outputs, automatic edit/delete
actions and scripted topic-feedback assertions. Some scenarios intentionally
contain questions, hypotheticals, ambiguity, repetition, negation or private
notes; not every scenario should publish a finding. Private notes become
scratchpads belonging to the test actors, and stay out of the team feed/ML.
TestData does not pre-create concepts, aliases, relationships or expertise.
It never trains or retunes models. This shared UAT import is not an isolated
quality evaluation: scenarios and existing content may corroborate each other.

`capacity` uses the load-check content templates but creates ordinary notes;
it does not recreate the benchmark's traffic, seeded manual vocabulary, fixed
timestamps or question/answer mixture. Its percentage is a content volume,
not a number of simulated users. Use `tools/ml_load_check.py` for the separate
capacity acceptance check in its diagnostic database.

`--percent` is required, accepts a number greater than 0 through 100, and applies
after topic filtering. Quality datasets sample whole scenarios, preserving their
parent questions/answers and supporting posts. Capacity samples individual notes.
Counts round up: 25% of 10 eligible scenarios imports 3. Tiny positive percentages
still select at least one unit. `datasets` lists exact topic names; quote a list
containing spaces, for example:

```powershell
.\TestData.cmd preview --dataset expanded --topics "computing/data systems,libraries/archives" --percent 25 --seed 42
```

Selection uses a stable SHA256 ranking by seed and scenario/item ID, so larger
percentages with the same seed/topics include the smaller selection. Preview
shows exact post/unit counts, dataset SHA256 and selection fingerprint. For
example, expanded 25% / seed 42 selects 75 scenarios and 378 posts. Identical
parameters and dataset bytes reproduce the same inputs. The server checks the
fingerprint before creating any batch; stale local/deployed content is refused.

## Batches and processing

`add` prints a batch ID and import progress. It inserts ordinary contributions
through the app's save processing, with nonadmin test accounts named `td-...`
and profiles labeled `TestData <scenario>: <actor>`. Account passwords are
random and discarded; no reusable test-login credentials are exposed. Accounts
are scoped per quality scenario, or shared among up to 50 capacity actors.

The worker processes data continuously as it arrives. `add` does not wait for
ML. `status` shows imported/remaining/processed posts, batch/global queue counts,
worker liveness, active/held findings supported by batch sources, and vector
counts. Findings can also have non-test support; counts are diagnostics, not
quality grades or exclusively batch-owned objects. Only 20 pending/error job
details are shown so a large queue does not produce an enormous response.

`status --wait` waits until batch work and current vocabulary/backfill settle.
It fails explicitly if the worker is stopped, a batch job has an error, an import
failed/interrupted, or the timeout expires. The default timeout is 1,800 seconds;
choose a larger value for large imports, at most 604,800 seconds. A timeout does
not delete the data: run status again. Queue completion does not mean every
concept or relationship was published; extraction/relevance/evidence rules can
hold or miss a finding. Existing BGE quality/capacity gates remain failed; see
[BGE verification](deployment/BGE_VERIFICATION.md).

Overlapping scenarios/items in a live batch of the same dataset are rejected,
including repeats with a different percentage or seed. The error lists existing
batch IDs. Remove them before loading an overlapping selection. Concurrent
add/remove commands are refused while the UAT mutation lock is held. Partial
imports retain their receipt and object IDs; remove the failed/adding batch,
then import again. There is no untracked partial dataset after a process crash.
If removal fails partway through, status records the cause as `cleanup-blocked`
instead of implying that inference is slow. Resolve it and run remove again.

## Removing test data

`remove` deletes only the selected batch's recorded content, test identities
and their dependent data, then waits for normal ML withdrawal. `--all-batches`
does this for all nonremoved TestData batches; it does not reset the database.
Receipts stay in `batches` with state `removed`, and a selection can then be
imported again. Removal is repeatable and can resume after interruption. If ML
is stopped or cleanup times out, start/fix the worker and run remove again.

Before deleting, cleanup refuses a batch with non-test answers/corrections
attached to its items, new content/uploads/scratchpads owned by its test profiles,
rebound identities or promoted/renamed test accounts. It does not discard a real
contribution to make cleanup succeed. The refusal identifies the preserved data;
resolve it deliberately or retain the batch. Each item is also checked under a
SQLite write lock during deletion to protect against a concurrent human answer.
Unrelated accounts, uploads, posts and manual concepts/overrides are preserved.
Automatic findings supported by remaining real data continue to exist.

The UAT command creates three small journal tables in the same SQLite database
as the application. Object creation and tracking share transactions with the
imported records. These optional tables require no Alembic schema change, survive
Update/backup, and are erased by the existing full database reset. Do not use
`reset-database` for selective cleanup: it destroys all data/accounts/uploads.
