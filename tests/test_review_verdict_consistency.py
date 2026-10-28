"""Contradictory review verdicts must enter the existing protocol repair path."""
from copy import deepcopy
import json
import pytest

from omni_story.library import protocol_feedback, story_finecut as flow
from omni_story.library.replay_check import nested_validator


def reviewer(*, story_context):
    value = flow.StoryFinecut.__new__(flow.StoryFinecut)
    value.story_context = {} if story_context else None
    return value


def target_validator(value):
    return nested_validator(flow.StoryFinecut.review, 'target_check',
                            self=value, result=dict(duration_s=50))


def verdict(action, missing):
    return dict(apparent_story='实际行动和结果', status='partial', limitations=[],
        problems=[dict(start_s=32, end_s=34, description='循环镜头打断动作')],
        editing_methods_used=[], next_action=action, missing_core_meaning=missing,
        revision_reason='移除32–34秒循环镜头，补清35–43秒投掷物的因果')


@pytest.mark.parametrize('story_context', [True, False], ids=['story', 'legacy'])
@pytest.mark.parametrize('action,missing', [
    ('revise', ['投掷物的原因和结果尚缺失']),
    ('deliver', ['投掷物的原因和结果尚缺失']),
    ('reselect_material', []),
])
def test_inconsistent_verdict_is_repairable_and_consistent_resolutions_remain_valid(
        tmp_path, monkeypatch, action, missing, story_context):
    value = reviewer(story_context=story_context)
    validate = target_validator(value)
    reply = verdict(action, missing)
    original = deepcopy(reply)
    with pytest.raises(ValueError, match='story_finecut:review_verdict_inconsistent') as caught:
        validate(reply)
    assert reply == original  # Choosing the verdict remains the model's job.
    violation = caught.value.violation
    assert violation['path'] in {'$.next_action', '$.missing_core_meaning'}
    assert violation['value'] == reply[violation['path'].removeprefix('$.')]
    assert violation['expected'] and violation['repair_hint']
    feedback = protocol_feedback.diagnose(flow.PREFIX + 'review', reply,
        dict(validation_error=caught.value, duration_s=50))
    assert feedback[0]['path'] == violation['path']
    assert violation['repair_hint'] in json.dumps(feedback, ensure_ascii=False)
    json.dumps(violation, ensure_ascii=False, allow_nan=False)

    # Both evidence-based resolutions are valid; local revision still needs evidence.
    for resolved in (verdict('deliver', []), verdict('revise', []),
                     verdict('reselect_material', ['真实核心缺项'])):
        validate(resolved)
    for field, empty in (('revision_reason', ''), ('problems', [])):
        invalid = verdict('revise', [])
        invalid[field] = empty
        with pytest.raises(ValueError, match='story_finecut:revision_requires_actual_output_problem'):
            validate(invalid)
    if story_context:
        invalid = verdict('deliver', [])
        del invalid['missing_core_meaning']
        with pytest.raises(ValueError, match='story_finecut:review_missing_core_meaning_string_array_required'):
            validate(invalid)

    # The actual prompt and rejection use the same contract, without media or requests.
    value.knowledge, value.reference, value.observed = '', {}, {'story': []}
    value.inspections, value.base = [], tmp_path
    value.auth = dict(parent=dict(duration_s=50), target_duration_s=50)
    monkeypatch.setattr(value, 'blind_proxy', lambda result: dict(path='blind.mp4'))
    monkeypatch.setattr(flow.trial, 'proxy', lambda *args, **kwargs: dict(path='actual.mp4'))
    prompts = []

    def call(name, prompt, media, scope, validator, **kwargs):
        prompts.append(prompt)
        if name == flow.PREFIX + 'blind':
            return dict(apparent_story='可见动作', status='partial', limitations=[], problems=[], facts=[])
        validator(deepcopy(reply))
        pytest.fail('contradictory target must be rejected before review returns')

    monkeypatch.setattr(value, 'call', call)
    with pytest.raises(ValueError, match='story_finecut:review_verdict_inconsistent'):
        value.review({}, dict(sha256='a' * 64, rendered_path='actual.mp4', duration_s=50), 0)
    assert json.dumps(flow.review_verdict_contract(), ensure_ascii=False) in prompts[-1]
    assert violation['repair_hint'] in prompts[-1]


@pytest.mark.parametrize('name,rejected', [('yewen', True), ('nick', True), ('heihu', False)])
def test_real_server_review_replies_from_c296937(name, rejected):
    from pathlib import Path
    from omni_story.library.contracts import parse_model_json
    path = Path(__file__).parent / 'fixtures/review_verdicts' / f'{name}_fine_review_c296937.json'
    reply = parse_model_json(path.read_text(encoding='utf-8'))
    validate = target_validator(reviewer(story_context=True))
    if rejected:
        with pytest.raises(ValueError, match='story_finecut:review_verdict_inconsistent') as caught:
            validate(reply)
        assert caught.value.violation['path'] == '$.next_action'
    else:
        validate(reply)
