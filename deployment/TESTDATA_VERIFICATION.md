# TestData verification — 30 September 2026 UTC

Implementation: `e68c3e9`, branch `gpt6.1solhigh_bge`. The owner authorized
publication to GitHub after implementing TestData. Actual UAT/PROD servers
were not contacted; all SSH/API mutation used an isolated localhost-only
Red Hat UBI 8.10 x86_64 server with fake credentials and separate data.

## Results

- Final complete Linux backend suite: **818 passed, zero skipped**, two
  deprecation warnings, 580.41 seconds. Includes 15 TestData cases covering
  selection, whole-scenario dependencies, topic filtering, nonadmin actors,
  accepted/helpful outcomes, private scratchpads, overlaps, interrupted imports,
  interrupted/concurrent removal, promoted accounts, human-data preservation,
  stopped-worker/timeout reporting, fingerprint mismatch and both environment
  boundaries. [Final regression log](verification/testdata/linux-final-regressions.log).
- Real Update installation: bundled Python 3.12.14/SQLite 3.53.1/uv 0.12.10,
  all 25 BGE parts verified and assembled, offline environments, nonroot web/ML
  startup and one fake-password prompt. Repeat Updates reused the model
  generation. The final server code matches the implementation receipt.
  [First install](verification/testdata/update.log),
  [final Update](verification/testdata/update-receipts-preserved.log).
- Eighteen live CLI operations completed their expected outcomes: local
  datasets/preview without a password; repeatable 25% expanded selection
  (75 scenarios / 378 posts); local PROD refusal without connecting; independent
  server refusal with its environment marked PROD; astronomy import, overlap
  rejection, status/wait, protected human answer, removal, repeat removal,
  reimport after cleanup, expanded/capacity imports and remove-all.
  Each server command used exactly one SSH password prompt.
  [Live log](verification/testdata/e2e.log),
  [complete results](../research/testdata-integration/e2e.json).
- The real worker processed the 11-post astronomy batch, publishing Hubble Space
  Telescope, HST and a typed relationship. Removal withdrew the findings and
  left zero batch vectors/evidence. A human answer attached to a test question
  blocked removal before mutation; after deliberately deleting that answer,
  removal succeeded. The separate admin, ordinary post and manual preservation
  concept survived. Expanded 1% loaded 15 posts; capacity 0.01% loaded five notes.
  Final SQLite quick_check was ok, foreign-key errors were empty, and zero
  recorded test accounts/profiles/posts/scratchpads remained live.
- Four removed receipts survived a later Update. The final deployed code then
  imported and processed the two-post workshop sample and selectively removed
  it; its vectors/evidence were zero and the preserved non-test post remained.
  The web homepage also responded successfully.
  [Final CLI check](verification/testdata/final-live.log),
  [full result](../research/testdata-integration/final-live.json).
- Fresh Git checkout of `e68c3e9` with `core.autocrlf=true`: TestData.cmd had
  CRLF, TestData shell had LF, and both dataset files matched the original bytes.
  Actual uv-managed preview reproduced the selection fingerprint, and Update
  --check passed all 25 parts and source/UI fingerprints offline.
  Tested on macOS with Windows-style endings, not an actual Windows host.
  [Checkout proof](verification/testdata/checkout.log).

## Separate model expectation failure

The final workshop sample **did not publish the relationship expected by its
historical quality fixture**, despite successful processing. Its diagnostic
result and the original failed assertion are preserved. The initial astronomy
scenario did publish a typed relationship; these observations are not a new
quality acceptance claim. No model, policy threshold, fixture label or gate was
changed to make the CLI tests pass. Shared UAT imports also differ from the
isolated per-scenario quality runner.
[Workshop expectation miss](verification/testdata/final-workshop-quality-miss.log),
[observed findings and failure field](../research/testdata-integration/final-live.json).
The earlier BGE quality and paired capacity gates remain failed; see
[BGE verification](BGE_VERIFICATION.md). TestData does not resolve those limits.

## Operational limits

All tests used emulated x86_64 UBI userspace, four server CPU quota slots,
a hard 5 GiB server memory limit and no extra swap. No container OOM was
observed. The regression container ran separately with a 2 GiB hard limit and
network disabled. These measurements establish functional behavior, not native
UAT performance, actual RHEL kernel/firewall/filesystem behavior or Windows
execution. The complete 50,000-item TestData import/ML drain was not run;
percentages were tested using small samples and deterministic selection tests.
No model/dependency package rebuild was needed: TestData uses the web runtime,
the normal queue and the existing three models. It never seeds expected findings.

Journal tables are optional UAT-only metadata within the app DB, not a new
Alembic contract. Existing compatibility checks and backup preserve them.
Removal can refuse real contributions attached to a batch rather than erase
them; partial operations retain receipts and can resume. See [operator guide](../TESTDATA.md).
