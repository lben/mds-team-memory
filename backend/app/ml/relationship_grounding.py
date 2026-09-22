"""Lightweight generation checks for parser-grounded typed relationships."""


def current_result(result):
    from . import relation_syntax

    return (result.get("relation_guard_revision") == relation_syntax.REVISION
            and isinstance(result.get("relations"), list)
            and all(row.get("relation_guard_revision") == relation_syntax.REVISION
                    for row in result["relations"]))


def current_version(version):
    from .runtime import inference_version

    parts = version.split(":")
    if len(parts) != 5:
        return False
    models = {role: {"revision": revision} for role, revision in
              zip(("extractor", "embeddings", "syntax"), parts[:3])}
    return version == inference_version(models)
