"""Narrow opt-in warning compatibility; no model or real-run changes."""
from copy import deepcopy
import shutil

import pytest

from omni_story.library import active_observation_compat as compat, semantic_audit as audit
from omni_story.library.finecut_continuation import execute_finecut_continuation
from omni_story.library.state import json_sha
from test_library_execute import _bridge, _read, inputs
from test_library_finecut_continuation import _setup, _answer, _name
from test_library_semantic_audit import slice_data, comparison, SOURCE_SHA


def object_warning():
    return [{'description': 'Local identity remains uncertain.'}]


def test_default_and_old_validator_reject_description_objects(slice_data):
    _, segment, proxy, observation = slice_data
    observation['uncertainties'] = object_warning()
    for validator in (audit.validate_segment_observation, compat.validate_observation):
        with pytest.raises(ValueError):
            validator(observation, segment, SOURCE_SHA, proxy)


def test_opt_in_validates_copy_and_returns_original_complete_object(slice_data):
    _, segment, proxy, observation = slice_data
    observation['uncertainties'] = ['A plain warning.', *object_warning()]
    before = deepcopy(slice_data)
    assert compat.validate_observation(observation, segment, SOURCE_SHA, proxy, enabled=True) is observation
    assert slice_data == before
    assert isinstance(observation['uncertainties'][1], dict)


@pytest.mark.parametrize('warning', [{}, {'description': ''}, {'description': ' '}, {'description': 1},
    {'description': 'warning', 'confidence': .5}, {'message': 'warning'}, ['warning'], None])
def test_extra_fields_or_other_uncertainty_structures_rejected(slice_data, warning):
    _, segment, proxy, observation = slice_data
    observation['uncertainties'] = [warning]
    with pytest.raises(ValueError):
        compat.validate_observation(observation, segment, SOURCE_SHA, proxy, enabled=True)


@pytest.mark.parametrize('mutation', [
    lambda o: o['evidence'][0].update(local_end_s=8.01),
    lambda o: o.update(source_in_s=2280),
    lambda o: o.update(source_sha256='d' * 64),
    lambda o: o.update(proxy_sha256='d' * 64),
    lambda o: o['evidence'][3].update(basis_evidence_ids=[]),
    lambda o: o['evidence'][3].update(basis_evidence_ids=['src_inference']),
    lambda o: o['evidence'][0].update(character_ids=['invented_character'])])
def test_time_sha_characters_and_inference_remain_strict(slice_data, mutation):
    _, segment, proxy, observation = slice_data
    observation['uncertainties'] = object_warning()
    mutation(observation)
    before = deepcopy(observation)
    with pytest.raises(ValueError):
        compat.validate_observation(observation, segment, SOURCE_SHA, proxy, enabled=True)
    assert observation == before


def test_original_time_error_keeps_same_failure_text_before_warning_shape_check(slice_data):
    _, segment, proxy, observation = slice_data
    observation['evidence'][0]['local_end_s'] = 9
    observation['uncertainties'] = [{'description': 'warning', 'extra': 'must reject'}]
    errors = []
    for enabled in (False, True):
        with pytest.raises(ValueError) as error:
            compat.validate_observation(observation, segment, SOURCE_SHA, proxy, enabled=enabled)
        errors.append(str(error.value))
    assert errors[0] == errors[1]


def test_claims_opt_in_keeps_original_observation_hash_and_root_object(slice_data):
    plan, segment, _, observation = slice_data
    observation['uncertainties'] = object_warning()
    claims, check = comparison(plan, segment, observation)
    check['uncertainties'] = object_warning()
    before = deepcopy(check)
    with pytest.raises(ValueError):
        compat.validate_claims(check, observation, claims)
    assert compat.validate_claims(check, observation, claims, enabled=True) is check
    assert check == before and check['observation_sha256'] == json_sha(observation)
    check['observation_sha256'] = 'd' * 64
    with pytest.raises(ValueError, match='observation_changed'):
        compat.validate_claims(check, observation, claims, enabled=True)


@pytest.mark.parametrize('change', [
    lambda c: c['uncertainties'][0].update(extra=True),
    lambda c: c['claim_checks'][0].update(evidence_ids=['unknown_evidence']),
    lambda c: c['claim_checks'][0].update(status='supported', evidence_ids=['src_inference'])])
def test_claim_compatibility_never_promotes_bad_facts_or_structures(slice_data, change):
    plan, segment, _, observation = slice_data
    claims, check = comparison(plan, segment, observation)
    check['uncertainties'] = object_warning()
    change(check)
    with pytest.raises(ValueError):
        compat.validate_claims(check, observation, claims, enabled=True)


@pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'), reason='Requires production media')
def test_continuation_reuses_known_repair_with_object_warnings_without_third_fact_post(inputs, monkeypatch):
    _setup(inputs)
    reference, library, output = inputs
    answer = _answer(inputs)
    def warnings_reply(job):
        fixture_job = {**job, 'arguments': {**job['arguments'],
            'prompt': job['arguments']['prompt'].split('\n上次输出未通过本地协议校验。', 1)[0]}}
        value = answer(fixture_job)
        name = _name(job)
        if name.startswith('semantic_slice_4_'):
            value['uncertainties'] = object_warning()
            if not name.endswith('_repair'):
                value['evidence'][0]['local_end_s'] = value['observed_duration_s'] + 1
        elif name.startswith('semantic_claims_4_'):
            value['uncertainties'] = object_warning()
        return value
    real_enable, real_observation, real_claims = compat.enable, compat.observation_validator, compat.claim_validator
    # First collect exactly the historical failure pattern with default checks.
    monkeypatch.setattr(compat, 'enable', lambda state: None)
    monkeypatch.setattr(compat, 'observation_validator', lambda state: audit.validate_segment_observation)
    monkeypatch.setattr(compat, 'claim_validator', lambda state: audit.validate_segment_claim_check)
    with _bridge(output, warnings_reply) as jobs:
        with pytest.raises(ValueError, match='model_protocol_repair_exhausted:semantic_slice_4_'):
            execute_finecut_continuation(reference, library, output)
        failed = [c for c in _read(output / 'library_state.json')['calls'] if c['name'].startswith('semantic_slice_4_')]
        assert len(failed) == 2 and all(c['status'] == 'received' for c in failed)
        historical = {p: p.read_bytes() for c in failed for p in (output / 'calls' / c['id']).iterdir() if p.is_file()}
        first_fact_name = failed[0]['name']
        count = len(jobs)
        monkeypatch.setattr(compat, 'enable', real_enable)
        monkeypatch.setattr(compat, 'observation_validator', real_observation)
        monkeypatch.setattr(compat, 'claim_validator', real_claims)
        result = execute_finecut_continuation(reference, library, output)
        first_fact_jobs = [j for j in jobs if _name(j).removesuffix('_repair') == first_fact_name]
        assert len(first_fact_jobs) == 2  # Original and sole known repair only.
        assert len(jobs) > count  # Remaining source/claims/output stages progress.
        assert all(p.read_bytes() == old for p, old in historical.items())
        repaired = _read(output / 'calls' / failed[1]['id'] / 'parsed.json')
        assert repaired['uncertainties'] == object_warning()
        original_reply = _read(output / 'calls' / failed[1]['id'] / 'response.json')
        import json
        assert repaired == json.loads(original_reply['result']['content'][0]['text'])
        policy = _read(_read(output / 'library_state.json')['artifacts'][compat.ARTIFACT][0]['path'])
        assert policy['policy'] == compat.POLICY and policy['opt_in'] is True
        assert result['observation_compatibility_policy'] == compat.POLICY
        manifest = _read(result['semantic_evidence_path'])
        assert all(o['uncertainties'] == object_warning() for o in manifest['observations'])
        assert all(c['uncertainties'] == object_warning() for c in manifest['segment_checks'])
        before_resume = len(jobs)
        assert execute_finecut_continuation(reference, library, output) == result
        assert len(jobs) == before_resume
        compatibility_file = _read(output / 'library_state.json')['artifacts'][compat.ARTIFACT][0]['path']
        from pathlib import Path
        Path(compatibility_file).write_text('{"changed":"synthetic policy tamper"}', encoding='utf-8')
        with pytest.raises(ValueError, match='completed_file_changed'):
            execute_finecut_continuation(reference, library, output)
        assert len(jobs) == before_resume
