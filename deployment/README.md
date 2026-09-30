# Offline deployment package

This directory is delivered by an ordinary Git pull. It contains the built UI,
a Linux Python 3.12.14 / uv 0.12.10 bootstrap, and 25 numbered parts of the
compressed model and Linux dependency bundle (about 2.19 GiB). No Git LFS client,
manual model download, Node or Docker is needed on the deploying PC.

Configure `tools/deploy.toml` from its example, then run `Update.cmd [UAT|PROD]`
in PowerShell or `./Update [UAT|PROD]` on macOS/Linux. UAT is the default. The
CLI validates all hashes locally and again on the server, joins the parts,
decompresses the bundle, installs offline and starts the app and ML daemon.
See the repository README and SERVER_SETUP.md for requirements and recovery.

The models retain the selected GLiNER2.5-base, BGE-large-en-v1.5 and spaCy
en_core_web_trf pins. BGE handles both embeddings and concept relevance in the
same inference process. This payload contains no generative LLM. Each role's licenses and immutable
revision/checksum metadata are in `offline/manifest.json`; model license/source
files are also included in the bundle where supplied by the original assets.
Qwen, llama.cpp and its wheel are absent from this package. Historical Qwen
assets remain in Git history and in an existing server generation retained for
rollback; Update never deletes generations still referenced by old releases.
Package checks and daemon startup do not establish release-quality acceptance.
Recorded deployment, compatibility and recovery results are in
[BGE_VERIFICATION.md](BGE_VERIFICATION.md).

For maintainers: build the UI, then use `tools/package_update.py` with verified
three-role assets, pinned Linux wheels and a Linux-created bootstrap archive.
Do not edit numbered parts or hashes. Frontend source changes require a new UI
package. The dependency lock must match exactly.

The initial publication uses asset batch commits because GitHub enforces a
2 GB push limit. After the owner approves publication, `python tools/push_update.py`
pushes those commits in order, then the final branch. `--dry-run` shows the
commands without publishing. All numbered parts are below 100 MB. See
[GitHub repository limits](https://docs.github.com/en/repositories/creating-and-managing-repositories/repository-limits).
