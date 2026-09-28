"""Explicit current-contract inputs for mechanical application tests only.

Copying retained scores/spans here creates a NEW synthetic application input.
It does not rerun a model, certify an old output against the current parser, or
turn immutable golden observations into new quality evidence. Call this only
when constructing such an input, never when reading a stored cache or exercising
legacy/missing-marker behavior. Fixture JSON and original metadata stay intact.
"""
import copy


def current_synthetic_result(result, text):
    from app.ml import relation_syntax

    current = copy.deepcopy(result)
    current['relation_guard_revision'] = relation_syntax.REVISION
    for relation in current['relations']:
        relation['relation_guard_revision'] = relation_syntax.REVISION
    return judged(keep_single_source_publication(current), text)


def keep_single_source_publication(result):
    """Keep a recorded scenario's one-source concept publication under policy v9.

    Recorded spans at 0.985-0.995 published from one source under policy v8.
    Mechanics tests replaying them need that publication, so the synthetic port
    raises them to the v9 floor. The floor itself is tested in test_ml_eligibility.
    """
    for span in result['concepts']:
        if 0.985 <= span['score'] < 0.995:
            span['score'] = 0.995
    return result


def judged(result, text):
    """Add a synthetic judgment that every name production would check is substantive."""
    from app.ml import eligibility
    from app.ml.runtime import normalize

    result['eligibility'] = {'version': 'synthetic', 'margins': {
        normalize(name): 10.0 for name, _ in eligibility.candidates(text, result['concepts'])}}
    return result


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
