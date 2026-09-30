# Historical Qwen Update verification — 29 September 2026

This record describes the parent Qwen package. Current BGE results are in
[BGE_VERIFICATION.md](BGE_VERIFICATION.md).

Branch: `withgpt6.1solhigh`, from `with-opus55` at `73e743d`.
This branch is prepared locally for owner review. No actual UAT or PROD server
was contacted, and no commit was pushed.

## Operator flow

After this branch is published and pulled, copy `tools/deploy.example.toml` to
the ignored `tools/deploy.toml` once and fill in both environment sections.
From the repository root:

```powershell
.\Update.cmd --check  # optional: validates config/package without connecting
.\Update.cmd          # UAT by default
.\Update.cmd PROD     # explicit production selection
```

The SSH password is requested once, with console echo disabled, and is never
written into the configuration, command line or logs. First-use SSH host trust
is a separate prompt unless the IT-supplied SHA256 fingerprint is configured.
The same connection transfers code, joins and decompresses models, installs
offline, migrates and starts the web/ML processes using nohup. Success requires
the web health check and active ML worker lease. Subsequent updates reuse the
same model generation only after verifying its hashes.

## Delivered package

| Item | Recorded value |
| --- | --- |
| Model/dependency transfer | 51 ordinary Git files, each at most 95,000,000 bytes |
| Compressed size | About 4.49 GiB |
| Expanded model/wheel bundle | About 5.13 GiB |
| Joined archive SHA256 | `47a6e694d1dc61d2b42f1cfadf9739b086f4f53051b45e73684b86f5efdffd02` |
| Bootstrap SHA256 | `44d6873c714fd5b04083620fdff883c5be9800b647525df3b6f4c3816ffe06ea` |
| Linux interpreter | Python 3.12.14, SQLite 3.53.1 |
| Linux installer | uv 0.12.10 |
| Verifier native wheel | llama-cpp-python 0.3.35; UBI 8.10/GCC 8, portable SSE2 |
| Verifier wheel SHA256 | `ed3aac9053007d1599ce58073d680e2abc62f4c2e12df2fdeaffe8f69c382529` |

The immutable role revisions and file hashes are recorded in
`offline/manifest.json` and the bundle metadata. The four roles are GLiNER2.5
base, BGE-large, spaCy and Qwen3-4B-Instruct-2507 Q4_K_M. The built UI,
bootstrap, dependency lock and license files are checked in alongside the
parts. No Git LFS, npm, Docker, compiler or server internet access is needed
for normal Update runs.

## Checks performed

- First deployment to a clean, isolated nonroot SSH/SFTP server: all 51 parts
  uploaded, joined, hash-checked and extracted; bundled Python/uv and offline
  wheels installed; migration completed; web health and ML worker lease passed.
  One password prompt was recorded. Both daemons remained alive after the SSH
  session closed. See [first deployment log](verification/first-update.log).
- Repeat Update: verified model generation reused without model upload;
  previous daemons stopped, the new release activated and explicit readiness
  passed. One password prompt was recorded.
  See [repeat deployment log](verification/second-update.log).
- Deliberately invalid bind address: startup failed and the previous compatible
  release/configuration was restored without restoring a database over writes.
  See [recovery log](verification/failed-start-recovery.log).
- Real offline inference using the delivered runtime and assets: the full
  production verifier returned a finite class margin (`21.95521354675293`);
  GLiNER extracted grounded concepts and a typed relationship; BGE returned
  finite normalized embeddings of shape `[1, 1024]`; spaCy corroborated the
  `CVM` / `Cryogenic Valve Map` definition. This used four CPUs, a 5 GiB hard
  container memory limit and no extra swap. Reported main-process peak RSS was
  4,123,766,784 bytes; no container OOM occurred.
  See [four-model log](verification/four-model-smoke.log).
- Focused deployment/Update/bundle/assets/preparation suite: **88 passed**,
  no skips or failures, on UBI 8.10 Linux with network access disabled.
  Includes hash corruption, interrupted transfer before stopping a release,
  password reuse, configuration guards and native import rejection.
  See [Linux test log](verification/linux-tools-tests.log).
- Fresh local Git clone with `core.autocrlf=true`: checked-out CMD files had
  CRLF; shell scripts, package manifests, locks and frontend sources retained
  LF. The actual uv-managed Update `--check` passed all 51 parts and source/UI
  fingerprints. Windows-style line endings were tested on macOS; an actual
  Windows host was unavailable.
  See [fresh checkout log](verification/windows-checkout-check.log).
- Frontend `npm ci` and production build passed before packaging. Entrypoint
  help and Git whitespace checks passed.

The SSH checks used a localhost-only UBI 8.10 x86_64 test container, a fake
isolated credential and a separate database. The password-prompt counter is
test instrumentation; the delivered CLI uses the normal interactive prompt.
The ARM host emulated x86_64. These results verify packaging, installation and
lifecycle behavior, not production Xeon speed or the actual host's firewall,
kernel, account restrictions and filesystem.

## Requirements and remaining limits

The deploying PC needs Git, uv and about 10 GiB free. uv's first client setup
needs internet or a pre-populated Python/Paramiko cache. The server needs RHEL
8.10 x86_64, password-capable SSH/SFTP, bash/tar/sha256sum, writable local
storage and roughly 14 GiB free for first-install staging including the 2 GiB
reserve. IT must permit the configured application port. UAT and PROD on one
host must use distinct directories and ports.

The existing worker CPU/RSS ceilings remain unchanged. Update sets a finite
1,800-second cold model-load deadline by default, configurable in the TOML.
Real UAT latency and the new verifier's combined-load capacity remain to be
measured. No model-quality threshold was relaxed; fresh release-quality
validation remains open. A first installation also needs an administrator
created using `bash <root>/mdsctl.sh manage create-admin` before admin login.
nohup survives logout; it does not restart services after a server reboot.

Initial Git publication requires the asset batch commits to be pushed in
sequence. After owner authorization, use `python tools/push_update.py`;
`--dry-run` only displays the commands. Ordinary pulls work once publication
has completed.
