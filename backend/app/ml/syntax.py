"""Unscored, source-grounded alias candidates from grammatical roles."""

import re


REVISION = "r7-explicit-acronym-grammar"
CONFLICT_REVISION = "conflict-v2"
UNCERTAIN = frozenset("if unless whether perhaps maybe possibly hypothetical potential unverified unconfirmed propose proposal plan consider assume assumption suppose suggest recommend wish hope intend pretend reject deny dispute rumor incorrect false wrong mistaken misleading".split())
MODALS = {"may", "might", "could", "would", "should", "will"}


def candidates(doc, *, conflict=False):
    return propose({
        "body": doc.text,
        "tokens": [{"i": t.i, "text": t.text, "whitespace": t.whitespace_,
                    "start": t.idx, "end": t.idx + len(t), "lemma": t.lemma_,
                    "pos": t.pos_, "tag": t.tag_, "dep": t.dep_, "head": t.head.i,
                    "children": [c.i for c in t.children], "ancestors": [a.i for a in t.ancestors],
                    "is_quote": t.is_quote} for t in doc],
        "noun_chunks": [{"start_token": s.start, "end_token": s.end,
                         "end": s.end_char, "root_token": s.root.i} for s in doc.noun_chunks],
        "sentences": [{"start_token": s.start, "end_token": s.end} for s in doc.sents],
    }, conflict=conflict)


def propose(row, *, conflict=False):
    tokens, body = row["tokens"], row["body"]
    if "".join(t["text"] + t["whitespace"] for t in tokens) != body:
        raise ValueError("Syntax tokens do not match their source")
    chunks = {s["root_token"]: s for s in row["noun_chunks"]}
    children = {t["i"]: [tokens[i] for i in t["children"]] for t in tokens}
    proposed = {}

    def dependents(root, labels):
        return [t for t in children[root] if t["dep"] in labels]

    def one(items):
        return items[0]["i"] if len(items) == 1 else None

    def prep_object(root, prepositions):
        return one([o for p in children[root] if p["dep"] == "prep" and p["lemma"].lower() in prepositions
                    for o in children[p["i"]] if o["dep"] == "pobj"])

    def argument(root):
        if root is None or root not in chunks or tokens[root]["pos"] not in {"NOUN", "PROPN"}:
            return None
        raw = chunks[root]
        start = raw["start_token"]
        while start < raw["end_token"] and tokens[start]["dep"] in {"det", "poss"}:
            start += 1
        if start >= raw["end_token"]:
            return None
        begin, end = tokens[start]["start"], raw["end"]
        return {"text": body[begin:end], "start": begin, "end": end, "raw_chunk": raw,
                "removed_leading_tokens": range(raw["start_token"], start)}

    def emit(rule, trigger, full_root, short_root):
        full, short = argument(full_root), argument(short_root)
        if full is None or short is None:
            return
        if (not (full["end"] <= short["start"] or short["end"] <= full["start"])
                or full["text"].casefold() == short["text"].casefold()):
            return
        if rule in {"active_subject_definition", "copular_acronym_expansion", "compact_refers_to"}:
            capitals = "".join(c for c in short["text"] if c.isupper())
            initials = "".join(word[0].upper() for word in re.findall(r"[A-Za-z]+", full["text"]))
            if (not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]{1,11}", short["text"])
                    or len(capitals) < 2 or capitals != initials):
                return
        sentence = next(s for s in row["sentences"] if s["start_token"] <= trigger < s["end_token"])
        left, right = sentence["start_token"], sentence["end_token"]
        for t in tokens[sentence["start_token"]:trigger]:
            if t["text"] == ";" or "\n" in t["whitespace"]:
                left = t["i"] + 1
        for t in tokens[trigger:sentence["end_token"]]:
            if t["text"] == ";" or "\n" in t["whitespace"]:
                right = t["i"]
                break
        endpoints = (full, short)
        if any(tokens[i]["lemma"].lower() in {"this", "that", "these", "those"}
               for p in endpoints for i in p["removed_leading_tokens"]):
            return
        if any(not (tokens[left]["start"] <= p["start"] and p["end"] <= tokens[right - 1]["end"])
               for p in endpoints):
            return
        for t in tokens[left:right]:
            if any(p["start"] <= t["start"] and t["end"] <= p["end"] for p in endpoints):
                continue
            if (t.get("is_quote") or t["text"] == "?" or t["dep"] == "neg"
                    or t["lemma"].lower() in {"never", "without"}
                    or (t["dep"] == "det" and t["lemma"].lower() == "no")
                    or t["lemma"].lower() in UNCERTAIN
                    or (t["tag"] == "MD" and t["lemma"].lower() in MODALS)):
                return
        for index in [trigger, *tokens[trigger]["ancestors"]]:
            t = tokens[index]
            if not left <= index < right:
                break
            embedded = t["dep"] in {"ccomp", "xcomp", "advcl", "relcl", "csubj", "csubjpass"}
            embedded |= t["dep"] == "acl" and not (index == trigger and rule == "passive_or_nominal_naming")
            if conflict and index == trigger:
                if rule == "object_control_naming" and t["dep"] == "xcomp":
                    embedded = False
                heading = tokens[t["head"]]
                subject = one(dependents(trigger, {"nsubj"}))
                if (t["dep"] == "acl" and t["tag"] in {"VBD", "VBP", "VBZ"}
                        and subject is not None and heading["dep"] == "ROOT"
                        and heading["pos"] in {"NOUN", "PROPN"}
                        and heading["i"] < subject < trigger
                        and any(p["text"] == ":" for p in tokens[heading["i"] + 1:subject])):
                    # A noun heading can own the parse of a finite assertion.
                    # Keep the entire sentence's negation/uncertainty checks.
                    embedded = False
            if embedded and left <= t["head"] < right:
                return
        key = (rule, trigger, full["start"], full["end"], short["start"], short["end"])
        proposed[key] = {"rule": rule, "trigger_token": trigger,
                         "full_name": {k: full[k] for k in ("text", "start", "end")},
                         "short_name": {k: short[k] for k in ("text", "start", "end")}}

    for t in tokens:
        i, lemma = t["i"], t["lemma"].lower()
        subject = one(dependents(i, {"nsubj"}))
        passive = one(dependents(i, {"nsubjpass"}))
        obj = one(dependents(i, {"dobj"}))
        complement = one(dependents(i, {"oprd"}))
        if (lemma in {"call", "name", "abbreviate", "know"} or (conflict and lemma == "shorten")) and (passive is not None or t["dep"] == "acl"):
            full = passive if passive is not None else t["head"]
            short = complement if complement is not None else prep_object(i, {"as", "to"})
            emit("passive_or_nominal_naming", i, full, short)
        elif (lemma in {"mean", "name", "abbreviate"} and t["pos"] == "VERB" and subject is not None
              and obj is not None and complement is None and prep_object(i, {"as", "to"}) is None):
            emit("active_subject_definition", i, obj, subject)
        elif conflict and lemma == "mean" and t["dep"] == "xcomp" and subject is None and obj is not None:
            parent = tokens[t["head"]]
            if (parent["lemma"].lower() == "use" and parent["pos"] == "VERB"
                    and one(dependents(parent["i"], {"nsubj"})) is not None
                    and any(c["dep"] == "aux" and c["lemma"].lower() == "to" for c in children[i])):
                emit("object_control_naming", i, obj, one(dependents(parent["i"], {"dobj"})))
        elif lemma == "abbreviate" and obj is not None:
            emit("active_abbreviation", i, obj, prep_object(i, {"as", "to"}))
        elif lemma == "stand":
            # A fronted context ("For maintenance, ...") is not the expansion.
            full = one([o for p in children[i] if p["dep"] == "prep"
                        and p["lemma"].lower() == "for" and p["i"] > i
                        for o in children[p["i"]] if o["dep"] == "pobj"])
            emit("stands_for", i, full, subject)
        elif lemma == "expand" and prep_object(i, {"to"}) is not None:
            emit("expands_to", i, prep_object(i, {"to"}), obj if obj is not None else subject)
        elif lemma == "denote":
            emit("denotes", i, obj, subject)
        elif lemma == "refer" and subject is not None:
            emit("compact_refers_to", i, prep_object(i, {"to"}), subject)
        elif lemma == "define":
            full = prep_object(obj, {"as"}) if obj is not None else None
            if full is None:
                full = prep_object(i, {"as"})
            emit("defines_as", i, full, obj)
            for conjunct in dependents(i, {"conj"}) + (dependents(obj, {"conj"}) if obj is not None else []):
                if conjunct["pos"] in {"NOUN", "PROPN"}:
                    emit("coordinated_defines_as", i, prep_object(conjunct["i"], {"as"}), conjunct["i"])
        elif lemma == "be":
            for adjective in dependents(i, {"acomp"}):
                if adjective["lemma"].lower() == "short":
                    emit("is_short_for", i, prep_object(adjective["i"], {"for"}), subject)
            attribute = one(dependents(i, {"attr"}))
            emit("copular_acronym_expansion", i, attribute, subject)
            for naming, alias in ((subject, attribute), (attribute, subject)):
                if naming is None or tokens[naming]["lemma"].lower() not in {
                        "name", "title", "label", "form", "abbreviation", "alias"}:
                    continue
                explicit = tokens[naming]["lemma"].lower() in {"abbreviation", "alias"} or any(
                    c["lemma"].lower() in {"short", "shorten", "abbreviate", "abbreviated", "alternate", "another"}
                    for c in children[naming])
                if explicit:
                    full = prep_object(naming, {"for", "of"})
                    if not any(c["dep"] == "prep" and c["lemma"].lower() in {"for", "of"}
                               for c in children[naming]):
                        relative = one([c for c in children[naming] if c["dep"] == "relcl"
                                        and c["lemma"].lower() == "use"
                                        and one(dependents(c["i"], {"nsubj"})) is not None
                                        and not dependents(c["i"], {"dobj", "dative", "oprd", "ccomp", "xcomp"})])
                        if relative is not None:
                            full = prep_object(relative, {"for", "of"})
                    emit("copular_name_for", i, full, alias)
        if t["dep"] == "appos":
            alias = argument(i)
            if (alias and re.fullmatch(r"[A-Za-z][A-Za-z0-9-]{1,11}", alias["text"])
                    and sum(c.isupper() for c in alias["text"]) >= 2):
                start, end = alias["raw_chunk"]["start_token"], alias["raw_chunk"]["end_token"]
                if start > 0 and end < len(tokens) and tokens[start - 1]["text"] == "(" and tokens[end]["text"] == ")":
                    emit("parenthetical_compact_name", i, t["head"], i)
            full, short = argument(i), argument(t["head"])
            if full and short and re.fullmatch(r"[A-Z][A-Z0-9-]{1,11}", short["text"]):
                start, end = full["raw_chunk"]["start_token"], full["raw_chunk"]["end_token"]
                if start > 0 and end < len(tokens) and tokens[start - 1]["text"] == "(" and tokens[end]["text"] == ")":
                    emit("parenthetical_full_name", i, i, t["head"])
    return list(proposed.values())
