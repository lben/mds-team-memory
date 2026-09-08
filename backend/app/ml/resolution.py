"""Bounded identity matching with source evidence; similarity is not identity."""

import re

from sqlalchemy import literal_column

from ..models import ConceptTerm
from . import policy
from .runtime import NEGATION, UNCERTAIN, normalize, specific_name


def spelling_key(name):
    # Preserve word boundaries and meaningful symbols (C++, C#, re-sign).
    return tuple(re.split(r"[ _-]+", normalize(name)))


def spelling_concept(db, name):
    compact = re.sub(r"[ _-]", "", normalize(name))
    expression = literal_column("replace(replace(replace(concept_terms.term,' ',''),'-',''),'_','')")
    candidates = db.query(ConceptTerm).filter(expression == compact).limit(33).all()
    if len(candidates) > 32:
        return None
    identities = {term.concept_id for term in candidates if spelling_key(term.term) == spelling_key(name)}
    return next(iter(identities)) if len(identities) == 1 else None


def definitions(text, spans):
    """Find affirmative definitions supported by extracted endpoint spans."""
    found = []
    for definition in policy.acronym_definitions(text):
        supporting = [span for span in spans
                      if definition["start"] <= span["start"] < span["end"] <= definition["end"]]
        if supporting:
            found.append({**definition, "name_start": definition["start"],
                          "name_end": definition["start"] + len(definition["name"]),
                          "score": max(span["score"] for span in supporting)})
    ordered = sorted(spans, key=lambda span: (span["start"], span["end"]))
    for index, first in enumerate(ordered):
        for second in ordered[index + 1:]:
            if second["start"] - first["end"] > 64:
                break
            if second["start"] < first["end"]:
                continue
            middle = text[first["end"]:second["start"]].strip(" ,")
            forward = re.fullmatch(r"(?:(?:is|are) )?(?:also (?:known as|called|named|spelled)|aka)", middle, re.I)
            reverse = re.fullmatch(r"(?:is|are) (?:another name|an alias) for", middle, re.I)
            if not (forward or reverse):
                continue
            name, alias = (second, first) if reverse else (first, second)
            if not specific_name(name["name"]) or not specific_name(alias["name"]):
                continue
            found.append({"name": name["name"], "alias": alias["name"], "start": first["start"],
                          "end": second["end"], "alias_start": alias["start"], "alias_end": alias["end"],
                          "name_start": name["start"], "name_end": name["end"],
                          "score": min(first["score"], second["score"])})
    for definition in found:
        start, end = definition["start"], definition["end"]
        before = list(re.finditer(r"[.!?\n]", text[:start]))
        after = re.search(r"[.!?\n]", text[end:])
        sentence = text[before[-1].end() if before else 0:end + after.end() if after else len(text)]
        if not ("?" in sentence or NEGATION.search(sentence) or UNCERTAIN.search(sentence)):
            yield definition
