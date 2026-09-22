"""Exact typed dependency support for proposals supplied by the learned model.

This only decides whether an existing learned proposal has exact grammatical
support. It must never be used to originate scored extraction records.
"""
from bisect import bisect_left
import re

REVISION = "r1-typed-predicate-assertions"
VERSION = REVISION
DIRECT = {
    "use": "uses", "utilize": "uses", "require": "depends_on",
    "produce": "produces", "generate": "produces", "emit": "produces",
    "create": "produces", "output": "produces", "replace": "replaces",
    "supersede": "replaces", "include": "part_of", "contain": "part_of",
}
INVERSE = {"include", "contain"}
MEMBERS = {"part", "component", "module", "subset"}
HYPOTHETICAL = {"if", "unless", "whether", "when", "whenever", "until", "provided", "assuming", "suppose"}
REJECTED = {"false", "wrong", "mistaken", "incorrect", "reject", "deny", "dispute", "retract",
            "quote", "rumor", "claim", "assertion", "idea", "unverified", "unconfirmed", "alleged"}
UNCERTAIN = {"perhaps", "possibly", "maybe", "hypothetical", "potential", "propose", "plan",
             "consider", "assume", "suggest", "recommend", "wish", "hope", "intend", "pretend"}
MODALS = {"may", "might", "could", "would", "should", "will", "can", "must", "shall"}
CLAUSES = {"conj", "advcl", "relcl", "ccomp", "xcomp", "acl", "csubj", "csubjpass"}
SAFE_ADVERBS = {"not", "never", "no", "long", "longer", "only", "always", "already", "still", "currently",
                "now", "directly", "successfully", "independently", "jointly", "together"}
CONTEXT_LABEL = re.compile(r"\b(?:hypothes(?:is|es)|hypothetical|rejected|false|examples?|quotations?|quoted|reported|reporting|unverified|unconfirmed|claims?|proposals?|plans?|rumors?|assumptions?|possibilit(?:y|ies))\b", re.I)


class SourceScope:
    """Precompute source context once, including context before a model window."""

    def __init__(self, text):
        self.text = text
        ranges = []
        quote_patterns = (r'"[\s\S]*?"', r'“[\s\S]*?”', r'‘[\s\S]*?’', r"(?<!\w)'[\s\S]*?'(?!\w)")
        for pattern in quote_patterns:
            ranges.extend(m.span() for m in re.finditer(pattern, text))
        blocked, fenced, offset = False, False, 0
        for line in text.splitlines(keepends=True):
            stripped = line.strip()
            marker = bool(re.match(r"^\s*(?:```|~~~)", line))
            if marker:
                fenced = not fenced
            # A heading quoted inside a code sample cannot reset the surrounding
            # source's assertion context.
            if not fenced and not marker:
                heading = (bool(re.match(r"^\s{0,3}#{1,6}\s+", line))
                           or (len(stripped) <= 100 and stripped.endswith(":"))
                           or bool(re.fullmatch(r"(?:Hypothesis|Hypotheses|Examples?|Rejected statement|Unverified report)", stripped, re.I)))
                if heading:
                    blocked = bool(CONTEXT_LABEL.search(stripped))
                if re.search(r"\b(?:following|below|next)\b.*\b(?:false|rejected|hypothetical|quoted|unverified)\b", stripped, re.I):
                    blocked = True
            if blocked or fenced or marker or re.match(r"^\s{0,3}>\s?", line):
                ranges.append((offset, offset + len(line)))
            offset += len(line)
        merged = []
        for start, end in sorted(ranges):
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
            else:
                merged.append((start, end))
        self._ranges = merged
        self._ends = [end for _, end in merged]

    def asserted(self, start, end):
        if not 0 <= start < end <= len(self.text):
            return False
        index = bisect_left(self._ends, start + 1)
        return index == len(self._ranges) or self._ranges[index][0] >= end


def supported_records(row):
    tokens, body = row["tokens"], row["body"]
    if "".join(t["text"] + t["whitespace"] for t in tokens) != body:
        raise ValueError("Parser/source mismatch")
    children = {t["i"]: [tokens[i] for i in t["children"]] for t in tokens}
    chunks = {c["root_token"]: c for c in row["noun_chunks"]}
    output = {}

    def dependents(root, labels):
        return [c["i"] for c in children[root] if c["dep"] in labels]

    def single(values):
        return values[0] if len(values) == 1 else None

    def subject(trigger, passive=False, seen=None):
        seen = set() if seen is None else seen
        if trigger in seen:
            return None, []
        seen.add(trigger)
        label = "nsubjpass" if passive else "nsubj"
        direct = dependents(trigger, {label})
        if direct:
            return single(direct), [trigger]
        token = tokens[trigger]
        # Inherit only a typed subject of a coordinated predicate, never an
        # object or arbitrary nearest name. Explicit opposite-voice subjects
        # prevent inheritance across an active/passive switch.
        if (token["dep"] == "conj" and token["head"] != trigger
                and tokens[token["head"]]["pos"] in {"VERB", "AUX"}
                and not dependents(trigger, {"nsubj", "nsubjpass"})):
            root, origins = subject(token["head"], passive, seen)
            return root, [trigger, *origins]
        return None, [trigger]

    def preposition(root, names, labels={"prep"}):
        return single([obj for p in children[root]
                       if p["dep"] in labels and p["lemma"].lower() in names
                       for obj in dependents(p["i"], {"pobj"})])

    def relative_root(root, trigger):
        if root is None:
            return None
        token, verb = tokens[root], tokens[trigger]
        if (token["pos"] == "PRON" and token["lemma"].lower() in {"which", "that", "who"}
                and verb["dep"] == "relcl" and token["head"] == trigger
                and token["dep"] in {"nsubj", "nsubjpass", "dobj"}
                and tokens[verb["head"]]["pos"] in {"NOUN", "PROPN"}):
            return verb["head"]
        return root

    def family(root, trigger):
        root = relative_root(root, trigger)
        if root is None or tokens[root]["pos"] not in {"NOUN", "PROPN"}:
            return []
        members, todo = [], [root]
        while todo:
            current = todo.pop()
            if current in members:
                continue
            members.append(current)
            todo.extend(c["i"] for c in children[current]
                        if c["dep"] == "conj" and c["pos"] in {"NOUN", "PROPN"})
        conjunctions = [c["lemma"].lower() for m in members for c in children[m] if c["dep"] == "cc"]
        if any(word not in {"and", "nor"} for word in conjunctions):
            return []
        if "nor" in conjunctions and not any(t["lemma"].lower() == "neither" for t in tokens):
            return []
        return members

    def argument(root):
        chunk = chunks.get(root)
        if chunk is None:
            return None
        start = chunk["start_token"]
        while start < chunk["end_token"] and tokens[start]["dep"] in {"det", "predet", "preconj"}:
            start += 1
        if start >= chunk["end_token"]:
            return None
        begin, end = tokens[start]["start"], chunk["end"]
        return {"name": body[begin:end], "start": begin, "end": end}

    def local(trigger):
        found, todo = set(), [trigger]
        while todo:
            current = todo.pop()
            if current in found:
                continue
            found.add(current)
            for c in children[current]:
                if c["dep"] in CLAUSES and c["pos"] in {"VERB", "AUX"}:
                    continue
                todo.append(c["i"])
        return [tokens[i] for i in sorted(found)]

    def coordinator(trigger):
        parent = tokens[trigger]["head"]
        candidates = [c for c in children[parent] if c["dep"] == "cc" and c["i"] < trigger]
        return max(candidates, key=lambda c: c["i"])["lemma"].lower() if candidates else None

    def contrasting_clause(trigger):
        return coordinator(trigger) == "but" and bool(dependents(trigger, {"nsubj", "nsubjpass"}))

    def alternative_clause(trigger):
        # A parser's choice of nesting cannot disambiguate written "A and B
        # or C". Hold the entire noncontrasting coordination if any predicate
        # is an alternative. An explicit "but" clause starts a separate group.
        current, seen = trigger, set()
        while current not in seen:
            seen.add(current)
            token = tokens[current]
            if (token["dep"] != "conj" or tokens[token["head"]]["pos"] not in {"VERB", "AUX"}
                    or contrasting_clause(current)):
                break
            current = token["head"]
        todo, seen = [current], set()
        while todo:
            current = todo.pop()
            if current in seen:
                continue
            seen.add(current)
            for child in children[current]:
                if (child["dep"] != "conj" or child["pos"] not in {"VERB", "AUX"}
                        or contrasting_clause(child["i"])):
                    continue
                if coordinator(child["i"]) == "or":
                    return True
                todo.append(child["i"])
        return False

    def structural_context(trigger):
        current, seen = trigger, set()
        while current not in seen:
            seen.add(current)
            token = tokens[current]
            if token["dep"] == "ROOT":
                return True
            if token["dep"] == "conj" and tokens[token["head"]]["pos"] in {"VERB", "AUX"}:
                current = token["head"]
                continue
            if token["dep"] == "advcl":
                markers = [c["lemma"].lower() for c in children[current] if c["dep"] == "mark"]
                finite = token["tag"] in {"VBD", "VBP", "VBZ"} or any(
                    c["dep"] in {"aux", "auxpass"} and c["tag"] in {"VBD", "VBP", "VBZ"} for c in children[current])
                if (markers not in (["while"], ["although"], ["because"])
                        or not finite
                        or not dependents(current, {"nsubj", "nsubjpass"})):
                    return False
                current = token["head"]
                continue
            if token["dep"] == "relcl":
                antecedent = tokens[token["head"]]
                if antecedent["pos"] not in {"NOUN", "PROPN"}:
                    return False
                # A relative clause must describe a named argument in an
                # asserted containing clause, not a claim/plan complement.
                if antecedent["dep"] not in {"nsubj", "nsubjpass", "dobj", "pobj", "attr"}:
                    return False
                current = antecedent["head"]
                while tokens[current]["dep"] in {"prep", "agent"}:
                    current = tokens[current]["head"]
                continue
            return False
        return False

    def polarity(trigger, origins, endpoints):
        if not structural_context(trigger) or alternative_clause(trigger):
            return None
        source = row.get("source_body", body)
        offset = row.get("source_offset", 0)
        left = min(tokens[trigger]["start"], *(e["start"] for e in endpoints)) + offset
        right = max(tokens[trigger]["end"], *(e["end"] for e in endpoints)) + offset
        scope = row.get("source_scope")
        if scope is None:
            scope = SourceScope(source)
        if not scope.asserted(left, right):
            return None
        sentence = next(s for s in row["sentences"] if s["start_token"] <= trigger < s["end_token"])
        sentence_tokens = tokens[sentence["start_token"]:sentence["end_token"]]
        def is_name(t):
            return any(e["start"] <= t["start"] and t["end"] <= e["end"] for e in endpoints)
        context = [t for t in sentence_tokens if not is_name(t)]
        if re.search(r"\baccording\s+to\b", " ".join(t["text"] for t in context), re.I):
            return None
        if any(t.get("is_quote") or t["text"] == "?" or t["lemma"].lower() in HYPOTHETICAL | REJECTED for t in context):
            return None
        # Explicit subjects do not by themselves remove uncertainty governing
        # a noncontrasting coordination. A separate "but" clause can assert
        # its own fact; existing shared-subject origins remain unchanged.
        uncertainty_origins = list(origins)
        current, seen = trigger, set()
        while (current not in seen and tokens[current]["dep"] == "conj"
               and tokens[tokens[current]["head"]]["pos"] in {"VERB", "AUX"}):
            seen.add(current)
            if contrasting_clause(current):
                break
            current = tokens[current]["head"]
            if current not in uncertainty_origins:
                uncertainty_origins.append(current)
        for origin in uncertainty_origins:
            scope = [t for t in local(origin) if not is_name(t)]
            if any(t["lemma"].lower() in UNCERTAIN
                   or (t["dep"] == "advmod" and t["pos"] == "ADV" and t["lemma"].lower() not in SAFE_ADVERBS)
                   or (t["tag"] == "MD" and t["lemma"].lower() in MODALS) for t in scope):
                return None
        scope = [t for t in local(trigger) if not is_name(t)]
        scope_text = " ".join(t["text"].lower() for t in scope)
        if re.search(r"\bnot\s+only\b", scope_text):
            return None
        inherited_negative = False
        if tokens[trigger]["dep"] == "conj":
            parent = tokens[trigger]["head"]
            operator = coordinator(trigger)
            parent_scope = [t for t in local(parent) if not is_name(t)]
            neither = any(t["lemma"].lower() == "neither" for t in parent_scope)
            parent_negative = neither or any(t["dep"] == "neg" or t["lemma"].lower() == "never" for t in parent_scope)
            if operator == "nor" and neither:
                inherited_negative = True
            elif operator in {"nor", "or"} or (len(origins) > 1 and parent_negative and operator != "but"):
                # Shared-subject 'not ... and ...' has ambiguous scope. Only
                # explicit neither/nor supplies inherited negative support;
                # 'not ... but ...' leaves the contrasting predicate local.
                return None
        negative = any(t["dep"] == "neg" or t["lemma"].lower() in {"never", "neither"}
                       or (t["lemma"].lower() == "no" and t["dep"] in {"det", "advmod", "neg"}) for t in scope)
        return "negative" if negative or inherited_negative else "positive"

    def emit(trigger, predicate, head_root, tail_root, origins, rule):
        for h in family(head_root, trigger):
            for t in family(tail_root, trigger):
                head, tail = argument(h), argument(t)
                if not head or not tail or not (head["end"] <= tail["start"] or tail["end"] <= head["start"]):
                    continue
                state = polarity(trigger, origins, (head, tail))
                if state is None:
                    continue
                key = head["start"], head["end"], predicate, tail["start"], tail["end"], state
                output[key] = {"head": head, "tail": tail, "predicate": predicate,
                               "polarity": state, "literal_support": True, "trigger": trigger,
                               "rule": rule, "guard_version": VERSION}

    for verb in tokens:
        trigger, lemma = verb["i"], verb["lemma"].lower()
        if verb["pos"] not in {"VERB", "AUX"}:
            continue
        active, active_origins = subject(trigger)
        passive, passive_origins = subject(trigger, passive=True)
        obj = single(dependents(trigger, {"dobj"}))
        if lemma in DIRECT:
            predicate = DIRECT[lemma]
            if passive is not None:
                agent = preposition(trigger, {"by"}, {"agent"})
                if lemma in INVERSE:
                    emit(trigger, predicate, passive, agent, passive_origins, "passive_membership")
                else:
                    emit(trigger, predicate, agent, passive, passive_origins, "passive_agent_subject")
            elif active is not None:
                head, tail = (obj, active) if lemma in INVERSE else (active, obj)
                emit(trigger, predicate, head, tail, active_origins, "active_exact_arguments")
        if lemma in {"depend", "rely", "run"} and active is not None and passive is None:
            emit(trigger, "uses" if lemma == "run" else "depends_on", active,
                 preposition(trigger, {"on"}), active_origins, "active_preposition_on")
        if lemma == "be" and active is not None:
            attribute = single(dependents(trigger, {"attr"}))
            if attribute is not None and tokens[attribute]["lemma"].lower() in MEMBERS:
                emit(trigger, "part_of", active, preposition(attribute, {"of"}), active_origins,
                     "copular_component_of")
    return list(output.values())


def check(row, head, tail, predicate):
    def same(first, second):
        return all(first[field] == second[field] for field in ("name", "start", "end"))
    matches = [r for r in supported_records(row) if r["predicate"] == predicate
               and same(r["head"], head) and same(r["tail"], tail)]
    states = {r["polarity"] for r in matches}
    if len(states) != 1:
        return {"literal_support": False, "polarity": "uncertain", "guard_version": VERSION}
    return {"literal_support": True, "polarity": next(iter(states)),
            "rules": sorted({r["rule"] for r in matches}), "guard_version": VERSION}


def serialize(doc):
    """Retain the parser's typed roles; do not infer roles from text proximity."""
    return {"body": doc.text,
            "tokens": [{"i": t.i, "text": t.text, "whitespace": t.whitespace_, "start": t.idx,
                        "end": t.idx + len(t), "lemma": t.lemma_, "pos": t.pos_, "tag": t.tag_,
                        "dep": t.dep_, "head": t.head.i, "children": [c.i for c in t.children],
                        "ancestors": [a.i for a in t.ancestors], "is_quote": t.is_quote} for t in doc],
            "noun_chunks": [{"start_token": s.start, "end_token": s.end, "end": s.end_char,
                             "root_token": s.root.i} for s in doc.noun_chunks],
            "sentences": [{"start_token": s.start, "end_token": s.end} for s in doc.sents]}


class Guard:
    def __init__(self, row, text, offset, source_scope):
        if (type(offset) is not int or offset < 0 or text[offset:offset + len(row["body"])] != row["body"]
                or source_scope.text != text):
            raise ValueError("Parser window does not match the original source")
        self.text = text
        row = {**row, "source_body": text, "source_offset": offset, "source_scope": source_scope}
        self.records = {}
        for record in supported_records(row):
            sentence = next(s for s in row["sentences"]
                            if s["start_token"] <= record["trigger"] < s["end_token"])
            start = offset + row["tokens"][sentence["start_token"]]["start"]
            end = offset + row["tokens"][sentence["end_token"] - 1]["end"]
            head, tail = record["head"], record["tail"]
            key = (offset + head["start"], offset + head["end"], head["name"],
                   record["predicate"], offset + tail["start"], offset + tail["end"], tail["name"])
            self.records.setdefault(key, []).append({"polarity": record["polarity"], "start": start,
                                                     "end": end, "rule": record["rule"]})

    def support(self, head, tail, predicate):
        unsupported = {"literal_support": False, "polarity": "uncertain", "start": 0, "end": 0,
                       "relation_guard_revision": REVISION}
        for span in (head, tail):
            if (type(span.get("start")) is not int or type(span.get("end")) is not int
                    or not 0 <= span["start"] < span["end"] <= len(self.text)
                    or self.text[span["start"]:span["end"]] != span.get("name")):
                return unsupported
        unsupported.update(start=min(head["start"], tail["start"]), end=max(head["end"], tail["end"]))
        key = (head["start"], head["end"], head["name"], predicate, tail["start"], tail["end"], tail["name"])
        records = self.records.get(key, [])
        states = {record["polarity"] for record in records}
        if len(states) != 1:
            return unsupported
        return {"literal_support": True, "polarity": next(iter(states)),
                "start": min(r["start"] for r in records), "end": max(r["end"] for r in records),
                "relation_guard_revision": REVISION}


def prepare(doc, full_text, window_offset=0, *, source_scope=None):
    return Guard(serialize(doc), full_text, window_offset, source_scope or SourceScope(full_text))
