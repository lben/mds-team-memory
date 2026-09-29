# Isolated non-LLM evaluation — 29 September 2026

Open `report.html` for the comparison, or `report.json` for the complete results.
The original application and deployment package were unchanged by this research.
No strategy passed both concept-screen precision and recall minima. BGE was the
strongest coverage/cost tradeoff: 97.69% precision, 78% selected recall.

This is exploratory evidence on the previously spent 300-case synthetic
development corpus. It is a concept-only publication proxy, not the full
application, fresh validation, or release acceptance. Qwen and both Decide
controls replay retained inference; BGE, MiniLM and structural scoring were new.
The hybrid is a separately registered follow-up after observing BGE's result.

The scripts and plans retain their original content and hashes. Their working
directory was `build/nonllm-evaluation`; receipts record that historical context.
All metrics, score ledgers and retained input/control files are archived here.
The large downloaded MiniLM checkpoint is excluded; its immutable revision and
SHA256 are in `nli-download.json`. No pretrained weights were fitted.

The frozen input corpus remains `backend/tests/fixtures/ml_heldout.json` at
SHA256 `823ed8b564ba6d8009b5b9b331a81cf10c5870faa329f15af37f267f059d3464`.
`retained-inputs/` mirrors the historical ignored paths; its inventory is in
`retained-inputs.json`. For reproduction, restore those files to their matching
repository-relative paths and copy the archived experiment files to
`build/nonllm-evaluation`. Install the pinned local ML environment and model
assets; run `score.py structural`, `score.py bge`, and `score.py nli`, then
`evaluate.py`. The optional hybrid has its separate plan and `hybrid.py`.
Neither reproduction nor the archived results authorize changing acceptance
targets. Keep unopened validation fixtures untouched.
