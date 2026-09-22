"""Explicit current-contract inputs for mechanical application tests only.

Copying retained scores/spans here creates a NEW synthetic application input.
It does not rerun a model, certify an old output against the current parser, or
turn immutable golden observations into new quality evidence. Call this only
when constructing such an input, never when reading a stored cache or exercising
legacy/missing-marker behavior. Fixture JSON and original metadata stay intact.
"""
import copy


def current_synthetic_result(result):
    from app.ml import relation_syntax

    current = copy.deepcopy(result)
    current['relation_guard_revision'] = relation_syntax.REVISION
    for relation in current['relations']:
        relation['relation_guard_revision'] = relation_syntax.REVISION
    return current


def current_synthetic_metadata(models=None):
    from app.ml.runtime import inference_version

    # Match the alias-role test contract so independent synthetic entity and
    # definition helpers can coexist without accidentally selecting new models.
    models = models or {
        'extractor': {'revision': '72ac19b486cd4557424c8d61114e7530c243e9b0'},
        'embeddings': {'revision': 'fixture-embedding'},
        'syntax': {'revision': '272a31e9d8530d1e075351d30a462d7e80e31da23574f1b274e200f3fff35bf5'},
    }
    return inference_version(models), models['embeddings']['revision'], 1024


def select_synthetic_pipeline(db, metadata):
    from sqlalchemy import text
    from app.ml import policy

    db.execute(text('UPDATE ml_state SET pipeline_version=:version WHERE id=1'),
               {'version': metadata[0] + ':' + policy.VERSION})
