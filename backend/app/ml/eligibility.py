"""Concept eligibility: is an extracted name a substantive subject of its source?

An entity extractor finds names; it cannot judge whether the text treats a name
as a reusable subject of knowledge or mentions it in passing. A local
instruction model answers one fixed Yes/No question per candidate. Nothing is
generated: the decision signal is the next-token logit margin of "Yes" over
"No". The llama.cpp runtime is imported only by the separately limited
verification process.
"""

import hashlib
import json
import math

from . import resolution
from .runtime import normalize, specific_name

DEFINITION = """You audit concept names that another model extracted from a team knowledge-base post.

A concept is a distinct, reusable subject of knowledge: a named entity, a domain topic, a method or process, a tool or artifact, a phenomenon, or a material or substance. It does not need to be a proper noun or be familiar outside its domain.

Accept a candidate only when BOTH conditions hold:
1. The candidate, exactly as written, names a specific identifiable subject whose meaning is clear from the post alone. Reject generic words without an identifiable domain sense, arbitrary descriptive fragments, measurements, quantities and values, formatting or configuration values, and names missing words needed to identify the subject.
2. The post treats that subject substantively: it explains the subject's behavior or role, asks about it, compares it, reports an outcome about it, or uses it as a meaningful participant in a domain claim. Mere occurrence is not enough: a subject that only appears as an incidental circumstance, setting, occasion or background detail does not qualify.

A question, denial, quotation or hypothetical can still discuss a substantive subject."""
SYSTEM_PREFIX = f"<|im_start|>system\n{DEFINITION}<|im_end|>\n<|im_start|>user\n"
QUESTION = ("Candidate: {name}\n\nIs this candidate an acceptable concept under both conditions? "
            "Answer Yes or No.<|im_end|>\n<|im_start|>assistant\n")
CONTEXT_TOKENS = 2048
# System prompt and question use roughly 400 tokens of the 2,048-token context.
POST_TOKENS = 1400
FINGERPRINT = hashlib.sha256(json.dumps([DEFINITION, QUESTION, CONTEXT_TOKENS, POST_TOKENS]).encode()).hexdigest()[:16]


def version(models):
    return f"{models['verifier']['revision']}:{FINGERPRINT}"


def post(body):
    return f"Post:\n<<<\n{body}\n>>>\n\n"


def candidates(text, spans):
    """Every name that can become concept evidence for this source, with one position each."""
    found = {}
    for span in [*spans, *resolution.definitions(text, spans)]:
        name = span["name"]
        if specific_name(name):
            found.setdefault(normalize(name), (name, span.get("name_start", span["start"])))
    return [[name, start] for name, start in found.values()]


class Verifier:
    def __init__(self, path):
        from llama_cpp import Llama

        self.llm = Llama(model_path=str(path), n_ctx=CONTEXT_TOKENS, n_threads=4, n_threads_batch=4,
                         n_gpu_layers=0, seed=0, verbose=False)
        self.system = self._tokens(SYSTEM_PREFIX)
        yes, no = self._tokens("Yes"), self._tokens("No")
        if len(yes) != 1 or len(no) != 1:
            raise ValueError("The verifier vocabulary must encode Yes and No as single tokens")
        self.yes, self.no = yes[0], no[0]
        self.llm.reset()
        self.llm.eval(self.system)

    def _tokens(self, text):
        return self.llm.tokenize(text.encode(), add_bos=False, special=True)

    def _margin(self, prefix_length, question):
        import numpy as np

        self.llm.n_tokens = prefix_length
        self.llm.eval(self._tokens(question))
        # llama-cpp-python keeps only the final position's logits unless every
        # position is retained; read them directly from the context.
        logits = np.ctypeslib.as_array(self.llm._ctx.get_logits(), shape=(self.llm.n_vocab(),))
        margin = float(logits[self.yes]) - float(logits[self.no])
        if not math.isfinite(margin):
            raise ValueError("Non-finite eligibility margin")
        return margin

    def _prefix(self, body):
        self.llm.n_tokens = len(self.system)
        self.llm.eval(self._tokens(post(body)))
        return self.llm.n_tokens

    def margins(self, text, names):
        """Map normalized candidate names to margins; long sources use a window around each name."""
        result = {}
        if len(self._tokens(post(text))) <= POST_TOKENS:
            shared = self._prefix(text)
            for name, _ in names:
                result[normalize(name)] = self._margin(shared, QUESTION.format(name=name))
            return result
        for name, start in names:
            radius = 4000
            while True:
                window = text[max(0, start - radius):start + len(name) + radius]
                if len(self._tokens(post(window))) <= POST_TOKENS or radius <= 200:
                    break
                radius //= 2
            result[normalize(name)] = self._margin(self._prefix(window), QUESTION.format(name=name))
        return result
