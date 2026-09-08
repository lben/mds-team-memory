"""Exercise local model files on CPU; this is a compatibility check, not an evaluation."""

import json
import os
from pathlib import Path
import resource
import sqlite3
import sys

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ["TOKENIZERS_PARALLELISM"] = "false"

import numpy as np
import torch
from gliner2 import AutoExtractor
from sentence_transformers import SentenceTransformer


assert os.getuid() != 0
assert sqlite3.sqlite_version_info >= (3, 51, 3)
assert torch.version.cuda is None
torch.set_num_threads(4)
torch.set_num_interop_threads(1)
assets = Path(sys.argv[1]).resolve()
extractor = AutoExtractor.from_pretrained(str(assets / "extractor"), map_location="cpu")
text = "Aurora uses PostgreSQL to store audit events. Kafka sends events to Aurora."
entities = extractor.extract_entities(
    text, ["software system", "database", "message broker"],
    include_spans=True, include_confidence=True,
)
relations = extractor.extract_relations(
    text, ["uses", "sends events to"], include_spans=True, include_confidence=True,
)
spans = [span for group in entities["entities"].values() for span in group]
assert spans
assert all(text[span["start"]:span["end"]] == span["text"] for span in spans)
assert any(relations["relation_extraction"].values())

embeddings = SentenceTransformer(str(assets / "embeddings"), device="cpu", local_files_only=True)
vectors = embeddings.encode(
    ["PostgreSQL stores audit events.", "Kafka transports messages."],
    batch_size=2, normalize_embeddings=True, show_progress_bar=False,
)
assert vectors.shape == (2, 1024)
assert np.isfinite(vectors).all()
assert np.allclose(np.linalg.norm(vectors, axis=1), 1, atol=1e-5)
print(json.dumps({
    "entities": entities, "relations": relations, "embedding_shape": list(vectors.shape),
    "peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024,
    "torch": torch.__version__, "sqlite": sqlite3.sqlite_version,
}))
