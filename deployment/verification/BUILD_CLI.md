# Build command verification

Workflow: `git pull --ff-only`, `Build.cmd`, `Update.cmd UAT` (or `PROD`).
Build reuses matching shipped/local UI or installs the locked npm dependencies,
builds the frontend and atomically publishes an ignored local UI generation and
manifest. Update validates/selects that generation and uploads it in the normal
release. Model parts, wheels, bootstrap and the shipped manifest remain intact.

Checks completed:

- 49 focused Build/Update/TestData tests passed locally and on Red Hat UBI 8.10.
  Coverage includes source freshness, CRLF handling, exact asset hashes, failed
  rebuild recovery, concurrent build refusal, malformed/corrupt local manifests,
  package changes after a pull, dependency-lock refusal and local UI selection
  through Update's release archive assembly.
- A real forced build ran Node 22.23.2, `npm ci --no-audit --no-fund`, `vue-tsc`
  and Vite 8.2.2 successfully. A subsequent Build reused the result. Update's
  complete local check verified all 25 model parts and selected the local UI.
- In a disposable checkout, a genuine frontend title change was built and
  packaged, a second Build reused it, and Update `--check` selected it. The
  actual assembled release archive contained the changed UI and all generated
  assets; the shared deployment package was unchanged. The checkout was removed.
- Actual Build.sh, Update and TestData launchers passed in the Red Hat container
  with uv 0.12.10, using the real generated local UI. The container's Build reused
  the matching UI without Node/npm, as intended.
- Generated UI/pointer/lock files are ignored by Git. No shipped model or UI
  asset, bootstrap or shared package manifest changed. Node system certificates
  were enabled; TLS verification and registry configuration were retained.

The new command was not run on native Windows. Its Windows launchers, CRLF
behavior and portable logic are covered, but company Node/npm/Artifactory access
must be available for a genuine rebuild on the work PC. No actual UAT or PROD
server was contacted. This verifies build/package/deployment selection, not ML
quality or server throughput. Build does not replace a full ML/dependency package.
