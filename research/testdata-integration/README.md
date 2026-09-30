# TestData integration evidence

Implementation `e68c3e9` on `gpt6.1solhigh_bge`. Source hashes are in
`receipt.json`; final Linux tests passed 818 cases with zero skips.
Deployment, live commands and Windows-style checkout proof are indexed in
[TestData verification](../../deployment/TESTDATA_VERIFICATION.md).

`e2e.json` records 18 live CLI commands and public API observations on a
disposable localhost-only Red Hat UBI 8.10 x86_64 server. It retains both PROD
guards, one-password server operations, repeat selection, imported batch counts,
actual HST/relationship publication, human-answer protection, withdrawal and
SQLite integrity. `final-live.json` records receipt preservation after Update
and final CLI processing/removal. The workshop relationship expectation failed;
its failure is retained independently of CLI success. Existing BGE quality and
capacity acceptance remain unresolved.

The scripts are historical QA harnesses with deliberately fake localhost
credentials, not deploying-PC tools. They assert localhost port 22296 and a
dedicated `/home/mds/mds-uat` root. Run from the repository with an ignored
`build/testdata/test.toml` using that fake server and its actual host fingerprint;
the public server image recipe is in `../bge-integration/Dockerfile.server`.
Use the real Update to install the code first. `run_client.py` counts injected
fake SSH password prompts; normal TestData uses interactive getpass. The primary
harness expects an initially empty diagnostic database. The final harness
continues its tracked workshop observation; it is not a standalone fresh run.
No real environment configuration, passwords, uploaded files or DB binaries
are included in these artifacts.
