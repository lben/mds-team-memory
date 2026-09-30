# Update source manifest and TLS launcher correction

The previous source freshness check included frontend Git/editor metadata and
the root README and compared raw checkout bytes. Those are not UI build inputs;
LF/CRLF conversion also changes raw hashes without changing frontend code.

The shared packaging/checking helper now excludes that metadata, normalizes
LF/CRLF for known text source types and still rejects changed, added or removed
build inputs. Binary source assets and all shipped UI/bootstrap/model assets
retain byte-exact checksums. The source manifest uses the explicit
`frontend-build-inputs-lf-v1` policy. Its build inputs match the previously frozen
manifest; only metadata entries were removed. No UI or model rebuild was needed.

Launchers use `uv run --system-certs` and require uv 0.11 or newer, without
setting the deprecated `UV_NATIVE_TLS` variable. Normal Update uses the published
UI; custom frontend changes still require a matching maintainer build/package.

Validation:

- 33 Update/TestData tests passed locally and on Red Hat UBI 8.10, including
  actual code changes, added/deleted sources and corrupted shipped UI rejection.
- A temporary checkout of the actual tracked frontend files with all recognized
  text sources converted to CRLF and an edited `.gitignore` passed the complete
  package check, including all 25 model/dependency parts and shipped UI hashes.
- The real launchers ran in a disposable Red Hat container with uv 0.12.10:
  Update `--check` passed, and expanded 25% TestData preview selected 75 scenarios
  / 378 posts. Client dependencies installed from the public index without a
  native-TLS deprecation warning. This does not verify the company's CA chain.
- The shipped UI, bootstrap, model parts and wheels were unchanged. Actual UAT
  and PROD were not contacted. Native Windows execution was not available;
  Windows line-ending differences were exercised explicitly.
