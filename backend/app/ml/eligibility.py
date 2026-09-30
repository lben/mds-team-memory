"""Concept relevance from the existing BGE encoder; no generative model.

The signal is cosine similarity between a source and a candidate's name. It is
semantic relevance, not a probability or proof that the name is substantive.
Quality acceptance remains separate from this inference contract.
"""

import hashlib
import json
import re

from .runtime import DESCRIPTOR_SUFFIX, named_part, normalize, specific_name, windows

CONTEXT_TOKENS = 512
WINDOW_OVERLAP = 64
FINGERPRINT = hashlib.sha256(json.dumps([
    "bge-source-name-cosine-v1", CONTEXT_TOKENS, WINDOW_OVERLAP,
    "candidate-containing-windows-max", DESCRIPTOR_SUFFIX.pattern,
]).encode()).hexdigest()[:16]


def version(models):
    return f"bge:{models['embeddings']['revision']}:{FINGERPRINT}"


def candidates(text, spans):
    """Every name that can become concept evidence, with its source position."""
    from . import resolution

    found = {}
    spans = [named_part(span) for span in spans]
    for span in [*spans, *resolution.definitions(text, spans)]:
        name = span["name"]
        if specific_name(name):
            found.setdefault(normalize(name), (name, span.get("name_start", span["start"])))
    return [[name, start] for name, start in found.values()]


class Relevance:
    """Share LocalModels.embedding rather than loading a second checkpoint."""

    def __init__(self, embedding):
        self.embedding = embedding

    def scores(self, text, names, source_vectors=None):
        import numpy as np

        if not names:
            return {}
        source_vectors = source_vectors or {}
        specials = self.embedding.tokenizer.num_special_tokens_to_add(pair=False)
        budget = min(CONTEXT_TOKENS, self.embedding.max_seq_length) - specials
        if budget <= WINDOW_OVERLAP:
            raise ValueError("BGE context is too small for eligibility windows")
        pieces = list(windows(text, self.embedding.tokenizer, budget, overlap=WINDOW_OVERLAP))
        if not pieces:
            raise ValueError("Eligibility names require a nonempty source")
        selections = {}
        for name, position in names:
            if not isinstance(name, str) or not isinstance(position, int):
                raise ValueError("Invalid eligibility candidate")
            occurrences = [(m.start(), m.end()) for m in re.finditer(re.escape(name), text, re.I)]
            matching = [i for i, (start, end, _) in enumerate(pieces)
                        if any(start <= left and right <= end for left, right in occurrences)]
            if not matching:
                raise ValueError(f"Eligibility candidate is not contained in its source: {name}")
            selections[normalize(name)] = matching
        needed = sorted({i for indices in selections.values() for i in indices})
        vectors = {}
        missing = []
        for i in needed:
            body = pieces[i][2]
            if body in source_vectors:
                vectors[i] = np.asarray(source_vectors[body], dtype=np.float32)
            else:
                missing.append(i)
        if missing:
            encoded = self.embedding.encode([pieces[i][2] for i in missing], normalize_embeddings=True,
                                            batch_size=16, show_progress_bar=False, convert_to_numpy=True)
            vectors.update(zip(missing, encoded))
        candidate_vectors = self.embedding.encode([name for name, _ in names], normalize_embeddings=True,
                                                 batch_size=16, show_progress_bar=False, convert_to_numpy=True)
        result = {}
        for (name, _), vector in zip(names, candidate_vectors):
            score = max(float(vectors[i] @ vector) for i in selections[normalize(name)])
            if not np.isfinite(score) or not -1.00001 <= score <= 1.00001:
                raise ValueError("Invalid BGE eligibility cosine")
            result[normalize(name)] = min(1.0, max(-1.0, score))
        return result
