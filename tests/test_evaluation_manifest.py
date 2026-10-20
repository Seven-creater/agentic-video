import copy
import json

import pytest

from omni_story.evaluation.manifest import (
    ManifestError, case_method_key, file_sha256, load_manifest, validate_manifest,
)


def manifest(tmp_path, count=20):
    library = tmp_path / 'library.json'
    library.write_text('{"sources": []}')
    value = dict(schema_version=1, experiment_id='frozen_v1', split='heldout',
        target_case_count=20, methods=['baseline_single_pass', 'full'],
        library=dict(version='three_films_v2', manifest_path=str(library),
                     manifest_sha256=file_sha256(library), media_root=str(tmp_path),
                     visual_root=str(tmp_path)),
        output_spec=dict(width=1280, height=720, fps=30, duration_min_s=10, duration_max_s=120),
        budget=dict(max_model_calls=40, max_wall_time_s=1800, per_run_max_model_calls=10, max_cost_cny=10),
        development_references=[dict(topic_id='old_debug_case', sha256='a' * 64)], cases=[])
    for index in range(count):
        reference = tmp_path / f'reference_{index}.mp4'
        reference.write_bytes(f'fixture reference {index}'.encode())
        value['cases'].append(dict(case_id=f'case_{index:02}', topic_id=f'topic_{index}',
            split='heldout', reference=dict(path=reference.name, sha256=file_sha256(reference))))
    value['planned_runs'] = [dict(run_id=case_method_key(value, case, method),
        case_id=case['case_id'], method_id=method) for case in value['cases'] for method in value['methods']]
    return value


def test_frozen_identity_hash_and_preparation_are_explicit(tmp_path):
    value = manifest(tmp_path)
    path = tmp_path / 'manifest.json'
    path.write_text(json.dumps(value))
    result = load_manifest(path)
    assert result['evaluation_ready'] and len(result['planned_runs']) == 40
    assert result['source_manifest_sha256'] == file_sha256(path)
    assert result['cases'][0]['reference']['path'] == str((tmp_path / 'reference_0.mp4').resolve())
    assert result['cases'][0]['case_key'] == 'frozen_v1/case_00'
    preparation = manifest(tmp_path, count=2)
    preparation['budget']['max_cost_cny'] = 0  # Preparation can explicitly prohibit paid execution.
    with pytest.raises(ManifestError, match='incomplete'):
        validate_manifest(preparation, base_path=tmp_path)
    assert not validate_manifest(preparation, base_path=tmp_path, require_complete=False)['evaluation_ready']


@pytest.mark.parametrize('mutation,match', [
    (lambda value: value['cases'][1].update(topic_id=value['cases'][0]['topic_id']), 'duplicate'),
    (lambda value: value['cases'][1]['reference'].update(**value['cases'][0]['reference']), 'duplicate'),
    (lambda value: value['development_references'].append(dict(topic_id=value['cases'][0]['topic_id'], sha256='b' * 64)), 'leaked'),
    (lambda value: value['development_references'].append(dict(topic_id='another', sha256=value['cases'][0]['reference']['sha256'])), 'leaked'),
    (lambda value: value['planned_runs'].pop(), 'missing'),
    (lambda value: value['planned_runs'].append(copy.deepcopy(value['planned_runs'][0])), 'duplicate'),
    (lambda value: value['budget'].update(max_wall_time_s=float('inf')), 'finite'),
    (lambda value: value['output_spec'].update(width=720, height=1280), 'only 1280x720'),
])
def test_invalid_sets_and_unbounded_budgets_are_rejected(tmp_path, mutation, match):
    value = manifest(tmp_path)
    mutation(value)
    with pytest.raises(ManifestError, match=match):
        validate_manifest(value, base_path=tmp_path)


def test_real_files_are_checked_by_default_and_validation_is_read_only(tmp_path):
    value = manifest(tmp_path)
    original = copy.deepcopy(value)
    validate_manifest(value, base_path=tmp_path)
    assert value == original
    (tmp_path / 'reference_0.mp4').write_bytes(b'changed')
    with pytest.raises(ManifestError, match='hash mismatch'):
        validate_manifest(value, base_path=tmp_path)
    assert validate_manifest(value, base_path=tmp_path, verify_files=False)['evaluation_ready']
    (tmp_path / 'reference_0.mp4').unlink()
    with pytest.raises(ManifestError, match='file missing'):
        validate_manifest(value, base_path=tmp_path)
