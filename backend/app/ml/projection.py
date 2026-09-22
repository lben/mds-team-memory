"""Generation contract for derived expertise; no model loading on reads."""
from sqlalchemy import and_, exists, func, literal_column, select

from . import identity, policy, runtime
from .models import Evidence, Finding, Source
from .sources import digest


def original_groups(items, questions):
    """Collapse every known copy/problem connection, not just the first one.

    A repeated contribution, document, source origin or question cannot become
    several originals by appearing under different duplicate-group IDs.
    """
    parents = {}

    def root(key):
        parents.setdefault(key, key)
        while parents[key] != key:
            parents[key] = parents[parents[key]]
            key = parents[key]
        return key

    for item in items:
        links = ["item:" + item.id, "text:" + digest(" ".join(item.body.casefold().split()))]
        for field, prefix in (("group_id", "group:"), ("normalized_hash", "hash:"),
                              ("source_item_id", "item:"), ("source_document_id", "document:"),
                              ("source_passage_id", "passage:")):
            value = getattr(item, field)
            if value:
                links.append(prefix + value)
        question = questions.get(item.parent_id) if item.kind == "answer" else None
        if question:
            links.extend(("question:" + question.id,
                          "question-text:" + digest(" ".join(question.body.casefold().split()))))
            if question.group_id:
                links.append("question-group:" + question.group_id)
        for link in links[1:]:
            parents[root(link)] = root(links[0])
    return {item.id: root("item:" + item.id) for item in items}


def contract():
    from ..topic_feedback import CONTRACT_VERSION

    # Placeholder checkpoint names isolate the code/schema contract; the actual
    # selected checkpoints are captured by the stored pipeline version below.
    models = {role: {"revision": "projection-contract"}
              for role in ("extractor", "embeddings", "syntax")}
    return digest(["expertise-explicit-topics-v1", CONTRACT_VERSION,
                   "two-actor-accounts-three-original-groups-one-accepted", policy.VERSION,
                   identity.VERSION, identity.scope_contract(),
                   runtime.inference_version(models)])


def current_confirmations(db):
    from ..topic_feedback import CONTRACT_VERSION, current_predicate
    from ..models import TopicConfirmation

    # Flatten the eligibility predicate out of the nested evidence/JSON EXISTS.
    # NOT MATERIALIZED lets the confirmation primary-key lookup stay indexed.
    eligible = select(TopicConfirmation.id, TopicConfirmation.context_token,
                      TopicConfirmation.concept_id, TopicConfirmation.beneficiary_profile_id).where(
                          current_predicate(db)).cte().prefix_with("NOT MATERIALIZED")
    records = func.json_each(Evidence.features, "$.topic_confirmations").table_valued("value").alias("confirmed_topics")
    valid = exists(select(eligible.c.id).where(
        eligible.c.id == func.json_extract(records.c.value, "$.id"),
        eligible.c.context_token == func.json_extract(records.c.value, "$.context_token"),
        eligible.c.concept_id == func.json_extract(Finding.payload, "$.concept_id"),
        eligible.c.beneficiary_profile_id == func.json_extract(Finding.payload, "$.profile_id"))
        .correlate(records, Finding))
    invalid = exists(select(records.c.value).where(~valid).correlate(Evidence, Finding))
    return and_(Evidence.source_kind == "profile",
                Evidence.source_id == func.json_extract(Finding.payload, "$.profile_id"),
                Evidence.model_version == policy.VERSION, Source.model_version == Evidence.model_version,
                func.json_extract(Evidence.features, "$.topic_feedback_contract") == CONTRACT_VERSION,
                func.json_array_length(Evidence.features, "$.topic_confirmations") > 0, ~invalid)


def current(db):
    generation = exists(select(Source.id).where(
        Source.kind == "profile", Source.id == func.json_extract(Finding.payload, "$.profile_id"),
        Source.valid.is_(True), Source.model_version == policy.VERSION,
        func.json_extract(Source.result, "$.projection_contract") == contract(),
        func.json_extract(Source.result, "$.pipeline_version") == literal_column(
            "(SELECT pipeline_version FROM ml_state WHERE id=1)"),
    ).correlate(Finding))
    evidence = exists(select(Evidence.key).join(Source, and_(
        Source.kind == Evidence.source_kind, Source.id == Evidence.source_id,
        Source.content_hash == Evidence.source_hash, Source.valid.is_(True))).where(
        Evidence.finding_key == Finding.key, Evidence.source_kind == "profile",
        current_confirmations(db)).correlate(Finding))
    return and_(generation, evidence)
