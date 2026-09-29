"""Exercise local model files on CPU; this is a compatibility check, not an evaluation."""

import json
import os
from pathlib import Path
import resource
import sqlite3
import sys
import subprocess

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ["TOKENIZERS_PARALLELISM"] = "false"

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from backend.app.ml import runtime
from backend.app.ml.queue import sqlite_is_safe


assert os.getuid() != 0
assert sqlite_is_safe(sqlite3.sqlite_version_info)
assets = Path(sys.argv[1]).resolve()
manifest = runtime.verified_manifest(assets)
if set(manifest["models"]) != {"extractor", "embeddings", "syntax", "verifier"}:
    raise ValueError("Automatic maintenance requires extractor, embeddings, syntax and verifier assets")
# Complete the verifier check in its own process before loading the extraction
# models, matching the production worker's memory constraint.
verifier_check = subprocess.check_output([sys.executable, "-c", '''
import json, math, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from backend.app.ml.eligibility import Verifier
assets = Path(sys.argv[2])
spec = json.loads((assets / "models.json").read_text())["models"]["verifier"]
verifier = Verifier(assets / "verifier" / spec["files"][0]["path"])
margins = verifier.margins("Glacier Parcel is our inventory routing service.", [["Glacier Parcel", 0]])
assert len(margins) == 1 and all(math.isfinite(value) for value in margins.values())
print(json.dumps({"verifier_margins": margins}))
''', str(Path(__file__).resolve().parents[2]), str(assets)], text=True)
models = runtime.LocalModels(assets)
assert models.torch.version.cuda is None
text = "Earth is part of the Solar System. The Sun is also part of the Solar System and lies at its center."
result = models.analyze(text)
spans = result["concepts"]
assert spans
assert all(text[span["start"]:span["end"]] == span["name"] for span in spans)
assert any(relation["literal_support"] for relation in result["relations"])

vectors = np.stack([np.frombuffer(chunk["vector"], dtype="<f4") for chunk in result["chunks"]])
assert vectors.shape[1] == 1024
assert np.isfinite(vectors).all()
assert np.allclose(np.linalg.norm(vectors, axis=1), 1, atol=1e-5)

# This exact development source has retained successful model observations.
# Exercise the third model and the actual scored alias path, not just imports.
definition_text = "CVM (Cryogenic Valve Map) shows the isolation valves between the separator and the cold return."
definitions = models.analyze(definition_text)["corroborated_definitions"]
assert any(record["full_name"]["text"] == "Cryogenic Valve Map"
           and record["short_name"]["text"] == "CVM" for record in definitions)
print(json.dumps({
    "verifier": json.loads(verifier_check),
    "entities": spans, "relations": result["relations"], "embedding_shape": list(vectors.shape),
    "corroborated_definitions": definitions, "model_version": models.version,
    "peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024,
    "torch": models.torch.__version__, "sqlite": sqlite3.sqlite_version,
}))
