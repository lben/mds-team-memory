"""Local encoder inference. Imported only by the separately limited ML process."""

import hashlib
import json
import math
import os
import re
from pathlib import Path


ENTITIES = {
    "technology": "Named software, database, programming language, framework or protocol.",
    "technical topic": "Specific technical method or topic, such as access control or query optimization.",
    "named system": "Named internal application, service, component or project.",
    "business process": "Specific named business procedure or domain process.",
}
RELATIONS = {
    "uses": "The head uses the tail as a tool or service.",
    "depends_on": "The head requires the tail to operate.",
    "part_of": "The head is a component of the tail.",
    "produces": "The head creates or emits the tail.",
    "replaces": "The head takes the place of the tail.",
}
# These checks verify literal support for a model-extracted predicate. They do
# not turn a high model score or co-occurrence into a factual assertion.
CUES = {
    "uses": (r"\b(?:uses?|using|utilizes?|runs? on)\b", r"\b(?:used|utilized) by\b"),
    "depends_on": (r"\b(?:depends? on|requires?|relies? on)\b", r"\brequired by\b"),
    "part_of": (r"\b(?:part|component|module|subset) of\b", r"\b(?:includes?|contains?)\b"),
    "produces": (r"\b(?:produces?|generates?|emits?|creates?|outputs?)\b", r"\b(?:produced|generated|emitted|created) by\b"),
    "replaces": (r"\b(?:replaces?|supersedes?)\b", r"\b(?:replaced|superseded) by\b"),
}
NEGATION = re.compile(r"\b(?:not|never|no longer|without|cannot|can't|doesn't|don't|isn't|aren't|wasn't|weren't)\b", re.I)
UNCERTAIN = re.compile(r"\b(?:if|might|may|could|should|would|perhaps|propos\w*|plan\w*|consider\w*|hypothetical)\b", re.I)
GENERIC = frozenset("system service component project application software technology database data process team user server client request response event events code issue problem solution example information documentation work".split())


def normalize(value):
    return " ".join(value.casefold().split())


def specific_name(value):
    name = value.strip()
    return (2 <= len(name) <= 120 and normalize(name) not in GENERIC
            and bool(re.search(r"[A-Za-z]", name))
            and not re.search(r"[\n\r<>={}]", name)
            and len(name.split()) <= 10)


def configure_cpu():
    for name in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ[name] = "4"
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1",
                      TOKENIZERS_PARALLELISM="false", CUDA_VISIBLE_DEVICES="")
    if hasattr(os, "sched_getaffinity"):
        os.sched_setaffinity(0, sorted(os.sched_getaffinity(0))[:4])
    if hasattr(os, "nice") and os.nice(0) < 10:
        os.nice(10 - os.nice(0))


def verified_manifest(directory):
    manifest = json.loads((directory / "models.json").read_text(encoding="utf-8"))
    if manifest.get("version") != 1 or set(manifest.get("models", {})) != {"extractor", "embeddings"}:
        raise ValueError("Unsupported model asset manifest")
    for role, model in manifest["models"].items():
        for entry in model["files"]:
            relative = Path(entry["path"])
            if relative.is_absolute() or ".." in relative.parts or "\\" in entry["path"]:
                raise ValueError("Unsafe model asset path")
            path = directory / role / relative
            if not path.resolve().is_relative_to(directory.resolve()) or path.is_symlink():
                raise ValueError("Model assets must remain inside their verified directory")
            if path.stat().st_size != entry["size"]:
                raise ValueError(f"Model asset length mismatch: {role}/{relative}")
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
            if digest.hexdigest() != entry["sha256"]:
                raise ValueError(f"Model asset hash mismatch: {role}/{relative}")
    return manifest


def windows(text, tokenizer, budget, overlap=24):
    """Sentence-aware windows with original offsets and bounded token overlap."""
    offsets = tokenizer(text, add_special_tokens=False, return_offsets_mapping=True,
                        truncation=False)["offset_mapping"]
    offsets = [(a, b) for a, b in offsets if b > a]
    boundaries = [match.end() for match in re.finditer(r"[.!?](?:\s+|$)|\n+", text)]
    cursor = 0
    while cursor < len(offsets):
        stop = min(cursor + budget, len(offsets))
        if stop < len(offsets):
            low, high = offsets[cursor + (stop - cursor) // 2][1], offsets[stop - 1][1]
            cut = next((position for position in reversed(boundaries) if low <= position <= high), None)
            if cut is not None:
                while stop > cursor + 1 and offsets[stop - 1][1] > cut:
                    stop -= 1
        start, end = offsets[cursor][0], offsets[stop - 1][1]
        yield start, end, text[start:end]
        if stop == len(offsets):
            return
        cursor = max(cursor + 1, stop - overlap)


def grounded_span(value, text, offset):
    if not isinstance(value, dict):
        return None
    start, end, score = value.get("start"), value.get("end"), value.get("confidence")
    if (type(start) is not int or type(end) is not int or not 0 <= start < end <= len(text)
            or text[start:end] != value.get("text") or type(score) not in (int, float)
            or not math.isfinite(score) or not 0 <= score <= 1):
        return None
    return {"name": text[start:end], "start": start + offset, "end": end + offset, "score": score}


def relation_support(text, head, tail, predicate):
    left, right = min(head["start"], tail["start"]), max(head["end"], tail["end"])
    before = list(re.finditer(r"[.!?\n]", text[:left]))
    after = re.search(r"[.!?\n]", text[right:])
    start = before[-1].end() if before else 0
    end = right + after.end() if after else len(text)
    sentence = text[start:end]
    if re.search(r"[.!?\n]", text[left:right]):
        return {"polarity": "uncertain", "literal_support": False, "start": start, "end": end}
    polarity = "negative" if NEGATION.search(sentence) else "uncertain" if "?" in sentence or UNCERTAIN.search(sentence) else "positive"
    forward = head["start"] < tail["start"]
    middle = text[head["end"]:tail["start"]] if forward else text[tail["end"]:head["start"]]
    cue = CUES[predicate][0 if forward else 1]
    return {"polarity": polarity, "literal_support": bool(re.search(cue, middle, re.I)), "start": start, "end": end}


class LocalModels:
    def __init__(self, directory):
        configure_cpu()
        import torch
        from gliner2 import AutoExtractor
        from sentence_transformers import SentenceTransformer

        self.torch = torch
        torch.set_num_threads(4)
        torch.set_num_interop_threads(1)
        directory = Path(directory).resolve()
        self.manifest = verified_manifest(directory)
        self.version = ":".join(self.manifest["models"][role]["revision"] for role in ("extractor", "embeddings"))
        self.embedding_version = self.manifest["models"]["embeddings"]["revision"]
        self.extractor = AutoExtractor.from_pretrained(str(directory / "extractor"), local_files_only=True, map_location="cpu")
        self.extractor.eval()
        self.embedding = SentenceTransformer(str(directory / "embeddings"), device="cpu", local_files_only=True, trust_remote_code=False)
        self.embedding.max_seq_length = 512
        self.entity_schema = self.extractor.create_schema().entities(ENTITIES)
        self.relation_schema = self.extractor.create_schema().relations(RELATIONS)
        self.tokenizer = self.extractor.processor.tokenizer
        self.dimensions = self.embedding.get_sentence_embedding_dimension()
        if self.dimensions not in (768, 1024):
            raise ValueError("Unsupported embedding dimensions")

    def analyze(self, text):
        concepts, relations, chunks = {}, {}, []
        for start, end, body in windows(text, self.tokenizer, 192):
            entities = self.extractor.extract(body, self.entity_schema, include_confidence=True,
                                              include_spans=True, max_len=512)
            for label, values in entities.get("entities", {}).items():
                for value in values:
                    span = grounded_span(value, body, start)
                    if span and specific_name(span["name"]):
                        span["label"] = label
                        key = (span["start"], span["end"])
                        if span["score"] > concepts.get(key, {}).get("score", -1):
                            concepts[key] = span
            extracted = self.extractor.extract(body, self.relation_schema, include_confidence=True,
                                               include_spans=True, max_len=512)
            for predicate, values in extracted.get("relation_extraction", {}).items():
                if predicate not in RELATIONS:
                    continue
                for value in values:
                    head = grounded_span(value.get("head"), body, start)
                    tail = grounded_span(value.get("tail"), body, start)
                    if not head or not tail or normalize(head["name"]) == normalize(tail["name"]):
                        continue
                    if not specific_name(head["name"]) or not specific_name(tail["name"]):
                        continue
                    support = relation_support(text, head, tail, predicate)
                    relation = {"head": head, "tail": tail, "predicate": predicate,
                                "score": min(head["score"], tail["score"]), **support}
                    key = (head["start"], tail["start"], predicate)
                    if relation["score"] > relations.get(key, {}).get("score", -1):
                        relations[key] = relation
            vector = self.embedding.encode(body, normalize_embeddings=True, batch_size=1,
                                           show_progress_bar=False, convert_to_numpy=True)
            chunks.append({"start": start, "end": end, "vector": vector.astype("<f4").tobytes()})
        return {"concepts": list(concepts.values()), "relations": list(relations.values()), "chunks": chunks}
