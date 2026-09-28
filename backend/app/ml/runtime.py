"""Local encoder inference. Imported only by the separately limited ML process."""

import hashlib
import json
import math
import os
import re
from pathlib import Path

from . import relation_syntax, syntax
from .sources import digest


ENTITIES = {
    "named entity": "A specifically named person, organization, place, object, system, project or event.",
    "topic or process": "A specific subject, discipline, method, process or phenomenon discussed in the text.",
    "object or substance": "A specific type of physical object, organism, material or substance discussed in the text.",
}
RELATIONS = {
    "uses": "The head uses the tail as a tool or service.",
    "depends_on": "The head requires the tail to operate.",
    "part_of": "The head is a component of the tail.",
    "produces": "The head creates or emits the tail.",
    "replaces": "The head takes the place of the tail.",
}
ALIAS_SCHEMA = {
    "name": "alias_definition", "mode": "natural", "anchor": "full_name",
    "fields": [
        {"name": "full_name", "dtype": "str", "cardinality": "required_one",
         "description": "The complete full name of an entity or process that the text explicitly equates with a shorter name, abbreviation, or alias as an actual fact. Copy the entire name from the text. Exclude rejected, denied, hypothetical, or merely related names."},
        {"name": "short_name", "dtype": "str", "cardinality": "required_one",
         "description": "The short name, abbreviation, or alias that refers to exactly the same entity or process as this record's full name. Copy the entire short name from the text. Exclude rejected, denied, hypothetical, or merely related names."},
    ],
}
ALIAS_SETTINGS = {"threshold": 0.5, "max_len": 512, "include_confidence": True, "include_spans": True}
NEGATION = re.compile(r"\b(?:not|never|no longer|without|cannot|can['’]t|doesn['’]t|don['’]t|didn['’]t|hasn['’]t|haven['’]t|hadn['’]t|isn['’]t|aren['’]t|wasn['’]t|weren['’]t)\b", re.I)
UNCERTAIN = re.compile(r"\b(?:if|might|may|could|should|would|perhaps|propos\w*|plan|plans|planned|planning|consider\w*|hypothetical)\b", re.I)
GENERIC = frozenset("system service component project application software technology database data process team user server client request response event events code issue problem solution example information documentation work".split())
# Bump for extraction behavior changes outside the schema, such as grounding or windowing.
EXTRACTION_VERSION = "grounded-spans-v11"


def inference_version(models):
    schemas = {"entities": ENTITIES, "relations": RELATIONS}
    roles = ["extractor", "embeddings"]
    if "syntax" in models:
        schemas.update(alias=ALIAS_SCHEMA, alias_settings=ALIAS_SETTINGS, syntax_rules=syntax.REVISION,
                       conflict_rules=syntax.CONFLICT_REVISION, relation_rules=relation_syntax.REVISION)
        roles.append("syntax")
    schema = json.dumps(schemas, sort_keys=True, separators=(",", ":"))
    fingerprint = hashlib.sha256(schema.encode()).hexdigest()[:16]
    revisions = ":".join(models[role]["revision"] for role in roles)
    return f"{revisions}:{EXTRACTION_VERSION}:{fingerprint}"


def normalize(value):
    return " ".join(value.casefold().split())


def specific_name(value):
    name = value.strip()
    return (2 <= len(name) <= 120 and normalize(name) not in GENERIC
            and bool(re.search(r"[A-Za-z]", name))
            and not re.match(r"[.!?]\s", name)
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


def verified_manifest(directory, roles=None):
    """Validate the manifest and hash the files of `roles` (all roles when omitted)."""
    manifest = json.loads((directory / "models.json").read_text(encoding="utf-8"))
    if (manifest.get("version") != 1 or set(manifest.get("models", {})) not in
            ({"extractor", "embeddings"}, {"extractor", "embeddings", "syntax"},
             {"extractor", "embeddings", "syntax", "verifier"})):
        raise ValueError("Unsupported model asset manifest")
    for role, model in manifest["models"].items():
        if roles is not None and role not in roles:
            continue
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


def corroborated_definitions(body, raw, proposals, offset):
    """Intersect exact alias-record fields with unscored syntax arguments."""
    for record in raw.get("alias_definition", []):
        full = grounded_span(record.get("full_name"), body, 0)
        short = grounded_span(record.get("short_name"), body, 0)
        if (not full or not short or not specific_name(full["name"]) or not specific_name(short["name"])
                or normalize(full["name"]) == normalize(short["name"])
                or not (full["end"] <= short["start"] or short["end"] <= full["start"])):
            continue
        rules = sorted({p["rule"] for p in proposals if all(
            (p[field]["text"], p[field]["start"], p[field]["end"]) == (span["name"], span["start"], span["end"])
            for field, span in (("full_name", full), ("short_name", short)))})
        if rules:
            yield {"full_name": {**record["full_name"], "start": full["start"] + offset, "end": full["end"] + offset},
                   "short_name": {**record["short_name"], "start": short["start"] + offset, "end": short["end"] + offset},
                   "syntax_rules": rules, "syntax_rule_revision": syntax.REVISION}


def relation_support(text, head, tail, predicate, *, guard=None):
    """Scores do not establish argument or assertion scope; a parser is required."""
    if guard is not None:
        return guard.support(head, tail, predicate)
    return {"polarity": "uncertain", "literal_support": False,
            "start": min(head["start"], tail["start"]), "end": max(head["end"], tail["end"])}


def repair_relation_spans(text, head, tail, predicate, start, end, *, guard=None):
    """Repair only an unambiguous repeated name in a model-proposed relation."""
    left, right = min(head["start"], tail["start"]), max(head["end"], tail["end"])
    if not re.search(r"[.!?\n]", text[left:right]):
        return head, tail
    candidates = {}
    for endpoint, other, move_head in ((head, tail, True), (tail, head, False)):
        pattern = r"(?<!\w)" + re.escape(endpoint["name"]) + r"(?!\w)"
        for match in re.finditer(pattern, text[start:end]):
            moved = {**endpoint, "start": start + match.start(), "end": start + match.end()}
            first, second = (moved, other) if move_head else (other, moved)
            left, right = min(first["start"], second["start"]), max(first["end"], second["end"])
            if not re.search(r"[.!?\n]", text[left:right]):
                candidates[first["start"], second["start"]] = first, second
    # Count all same-sentence alternatives before considering their predicates.
    if len(candidates) != 1:
        return head, tail
    first, second = next(iter(candidates.values()))
    earlier, later = sorted((first, second), key=lambda span: span["start"])
    middle = text[earlier["end"]:later["start"]]
    support = relation_support(text, first, second, predicate, guard=guard)
    if (not support["literal_support"] or re.search(r"[,;:]|\b(?:and|or|but|while|although|because|that|which|who)\b", middle, re.I)
            or (support["polarity"] == "negative" and not NEGATION.search(middle))):
        return head, tail
    return first, second


def negative_relations(text, spans, *, guard=None):
    """Grounded entity pairs can supply parser-verified vetoes, never confidence.

    The shared window parse already indexes exact directed arguments. Check the
    five supported predicates in both directions, including passive, copular,
    and coordinated negatives that a phrase-between-names check would miss.
    Only the entity pass supplies endpoints; positive relations still require
    an independent scored proposal from the learned relation extractor.
    """
    if guard is None:
        return
    ordered = sorted(spans, key=lambda span: (span["start"], span["end"]))
    for index, first in enumerate(ordered):
        for second in ordered[index + 1:]:
            if second["start"] < first["end"] or normalize(first["name"]) == normalize(second["name"]):
                continue
            for predicate in RELATIONS:
                for head, tail in ((first, second), (second, first)):
                    support = relation_support(text, head, tail, predicate, guard=guard)
                    if support["polarity"] != "negative" or not support["literal_support"]:
                        continue
                    yield {"head": head, "tail": tail, "predicate": predicate, "score": 0.0, **support}


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
        self.manifest = verified_manifest(directory, ("extractor", "embeddings", "syntax"))
        self.version = inference_version(self.manifest["models"])
        self.embedding_version = self.manifest["models"]["embeddings"]["revision"]
        self.extractor = AutoExtractor.from_pretrained(str(directory / "extractor"), local_files_only=True, map_location="cpu")
        self.extractor.eval()
        self.embedding = SentenceTransformer(str(directory / "embeddings"), device="cpu", local_files_only=True, trust_remote_code=False)
        self.embedding.max_seq_length = 512
        self.entity_schema = self.extractor.create_schema().entities(ENTITIES)
        self.relation_schema = self.extractor.create_schema().relations(RELATIONS)
        self.syntax = None
        if "syntax" in self.manifest["models"]:
            import spacy

            spacy.require_cpu()
            self.syntax = spacy.load(str(directory / "syntax"))
            self.alias_schema = self.extractor.create_schema().structure(
                ALIAS_SCHEMA["name"], mode=ALIAS_SCHEMA["mode"], anchor=ALIAS_SCHEMA["anchor"])
            for field in ALIAS_SCHEMA["fields"]:
                self.alias_schema.field(**field)
        self.tokenizer = self.extractor.processor.tokenizer
        self.dimensions = self.embedding.get_sentence_embedding_dimension()
        if self.dimensions not in (768, 1024):
            raise ValueError("Unsupported embedding dimensions")

    def analyze(self, text):
        concepts, endpoints, relations, chunks = {}, {}, {}, []
        definitions, conflicts = [], []
        source_scope = relation_syntax.SourceScope(text) if self.syntax is not None else None
        parser_ran = False
        for start, end, body in windows(text, self.tokenizer, 192):
            window_spans = {}
            parsed = self.syntax(body) if self.syntax is not None else None
            guard = relation_syntax.prepare(parsed, text, start, source_scope=source_scope) if parsed is not None else None
            parser_ran |= parsed is not None
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
                        window_spans[key] = concepts[key]
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
                    head, tail = repair_relation_spans(text, head, tail, predicate, start, end, guard=guard)
                    support = relation_support(text, head, tail, predicate, guard=guard)
                    for span in (head, tail):
                        key = (span["start"], span["end"])
                        if span["score"] > endpoints.get(key, {}).get("score", -1):
                            endpoints[key] = {**span, "label": "relation endpoint"}
                    relation = {"head": head, "tail": tail, "predicate": predicate,
                                "score": min(head["score"], tail["score"]), **support}
                    key = (head["start"], tail["start"], predicate)
                    if relation["score"] > relations.get(key, {}).get("score", -1):
                        relations[key] = relation
            for relation in negative_relations(text, window_spans.values(), guard=guard):
                key = (relation["head"]["start"], relation["tail"]["start"], relation["predicate"])
                relations.setdefault(key, relation)
            if self.syntax is not None:
                raw_aliases = self.extractor.extract(body, self.alias_schema, **ALIAS_SETTINGS)
                proposals = syntax.candidates(parsed)
                for definition in corroborated_definitions(body, raw_aliases, proposals, start):
                    fields = [definition[field] for field in ("full_name", "short_name")]
                    if not source_scope.asserted(min(field["start"] for field in fields),
                                                 max(field["end"] for field in fields)):
                        continue
                    definitions.append({**definition, "source_text_hash": digest(text),
                                        "alias_model_revision": self.manifest["models"]["extractor"]["revision"],
                                        "syntax_model_revision": self.manifest["models"]["syntax"]["revision"]})
                for candidate in syntax.candidates(parsed, conflict=True):
                    if not all(specific_name(candidate[field]["text"]) for field in ("full_name", "short_name")):
                        continue
                    fields = [candidate[field] for field in ("full_name", "short_name")]
                    if not source_scope.asserted(start + min(field["start"] for field in fields),
                                                 start + max(field["end"] for field in fields)):
                        continue
                    conflicts.append({"full_name": {**candidate["full_name"],
                        "start": candidate["full_name"]["start"] + start,
                        "end": candidate["full_name"]["end"] + start},
                        "short_name": {**candidate["short_name"],
                        "start": candidate["short_name"]["start"] + start,
                        "end": candidate["short_name"]["end"] + start},
                        "rule": candidate["rule"], "source_text_hash": digest(text)})
            vector = self.embedding.encode(body, normalize_embeddings=True, batch_size=1,
                                           show_progress_bar=False, convert_to_numpy=True)
            chunks.append({"start": start, "end": end, "vector": vector.astype("<f4").tobytes()})
        # Relation confidence is not entity confidence. Retain omitted names as
        # corroboration, preserving entity evidence wherever that pass found it.
        names = {normalize(span["name"]) for span in concepts.values()}
        corroboration = [span for span in endpoints.values() if normalize(span["name"]) not in names]
        result = {"concepts": [*concepts.values(), *corroboration],
                "relations": list(relations.values()), "chunks": chunks,
                "corroborated_definitions": definitions}
        if self.syntax is not None:
            if not parser_ran:
                # Empty/whitespace sources have no extraction windows. Certify
                # their empty relation result with the configured parser once,
                # so current caches and generation completion can accept it.
                self.syntax(text)
                parser_ran = True
            result.update(conflict_definitions=conflicts, conflict_coverage_revision=syntax.CONFLICT_REVISION)
        if parser_ran:
            result["relation_guard_revision"] = relation_syntax.REVISION
        return result
