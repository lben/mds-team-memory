import hashlib
import re
from difflib import SequenceMatcher

_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)


def normalize(text: str) -> str:
    """Normalization used for duplicate detection: lowercase, no punctuation, single spaces."""
    return _WS.sub(" ", _PUNCT.sub(" ", text.lower())).strip()


def normalized_hash(text: str) -> str:
    return hashlib.sha256(normalize(text).encode()).hexdigest()


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, normalize(a), normalize(b)).ratio()


# Function words carry no signal on their own: a result that matches only "is"
# is noise, so these are never searched for unless the query is nothing else.
STOPWORDS = frozenset(
    """
    a about an and any are as at be been being but by can cannot could did do does
    doing done for from had has have having he her him his how i if in into is it
    its me my no nor not of on once only or our ours out over own she should so
    some such than that the their them then there these they this those to too
    us very was we were what when where which while who whom why will with would
    you your yours
    """.split()
)


def query_terms(query: str) -> list[str]:
    """Plain lowercase words of a query (quotes and operators stripped)."""
    return [t for t in re.findall(r"\w+", query.lower()) if t]


def content_terms(query: str) -> list[str]:
    """The meaningful words of a query, in order, without duplicates.

    Falls back to every word when the query is only function words, so a search
    for "how are you" still does something rather than nothing.
    """
    words = query_terms(query)
    meaningful = [w for w in words if w not in STOPWORDS]
    return list(dict.fromkeys(meaningful or words))


def build_fts_match(
    query: str, alias_groups: dict[str, list[str]] | None = None, operator: str = "AND"
) -> str:
    """Convert a user query into a safe FTS5 MATCH expression.

    Supports quoted phrases and trailing-* prefix terms. Every other token is
    quoted so FTS operators in user input cannot break the query. When a term
    matches a known concept alias, it is expanded to an OR group of all the
    concept's aliases. Function words are dropped unless the query is nothing
    but function words; quoted phrases are always kept verbatim.
    """
    keep = set(content_terms(query))
    parts: list[str] = []
    for phrase, word in re.findall(r'"([^"]+)"|(\S+)', query):
        if phrase:
            parts.append('"' + phrase.replace('"', " ") + '"')
            continue
        prefix = word.endswith("*")
        # "zeta-123" style words become the phrase "zeta 123" (FTS tokenizes on punctuation).
        token = re.sub(r"[^\w]+", " ", word).strip()
        if not token:
            continue
        if not prefix and not any(w in keep for w in token.lower().split()):
            continue
        if prefix:
            parts.append(f'"{token}"*')
        elif alias_groups and token.lower() in alias_groups:
            variants = alias_groups[token.lower()]
            parts.append("(" + " OR ".join(f'"{v}"' for v in variants) + ")")
        else:
            parts.append(f'"{token}"')
    # Explicit operator: FTS5 rejects implicit AND before a parenthesized OR group.
    return f" {operator} ".join(parts)


def find_matches(content: str, query: str, max_hits: int = 20) -> list[dict]:
    """Line-level keyword search inside one text blob (used for scratchpads).

    Prefers lines containing all terms, falls back to lines with any term.
    """
    terms = query_terms(query)
    if not terms:
        return []
    lines = content.splitlines()
    all_hits, any_hits = [], []
    for idx, line in enumerate(lines):
        low = line.lower()
        found = [t for t in terms if t in low]
        if not found:
            continue
        hit = {"line": idx + 1, "text": line.strip()}
        (all_hits if len(found) == len(terms) else any_hits).append(hit)
    return (all_hits or any_hits)[:max_hits]
