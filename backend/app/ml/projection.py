"""Generation contract for derived expertise; no model loading on reads."""
from sqlalchemy import exists, func, literal_column, select

from . import identity, policy, runtime
from .models import Finding, Source
from .sources import digest


def contract():
    # Placeholder checkpoint names isolate the code/schema contract; the actual
    # selected checkpoints are captured by the stored pipeline version below.
    models = {role: {"revision": "projection-contract"}
              for role in ("extractor", "embeddings", "syntax")}
    return digest(["expertise-projection-v1", policy.VERSION, identity.VERSION,
                   runtime.inference_version(models)])


def current():
    return exists(select(Source.id).where(
        Source.kind == "profile", Source.id == func.json_extract(Finding.payload, "$.profile_id"),
        Source.valid.is_(True), Source.model_version == policy.VERSION,
        func.json_extract(Source.result, "$.projection_contract") == contract(),
        func.json_extract(Source.result, "$.pipeline_version") == literal_column(
            "(SELECT pipeline_version FROM ml_state WHERE id=1)"),
    ).correlate(Finding))
