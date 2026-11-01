"""Cast schema recovery uses confirmed speakers, never appearance similarity."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from omni_story.library import contracts, pipeline, plan_normalisation as n, replay_check
from test_plan_normalisation import evidence_plan


def fixture():
    plan, catalog, window = evidence_plan()
    n.normalise_plan(plan, [window])
    plan.pop('plan_normalisations', None)
    plan.update(focus_role_id=None, focus_role_bindings=[])
    return plan, catalog, window


def add_role(plan, window):
    role = dict(window['observation']['roles'][0], role_id='B', entity_id=window['source_sha256'] + ':second')
    window['observation']['roles'].append(role)
    for event in window['observation']['events']:
        event['role_ids'].append('B')
    for usable in window['observation']['usable_ranges']:
        usable['role_ids'].append('B')
    plan['segments'][0]['role_ids'].append('B')
    plan['audio_draft'].append(dict(plan['audio_draft'][0], id='second', voice='Moon',
        speaker=dict(window_id=window['window_id'], role_id='B')))
    return role


def test_valid_cast_is_untouched():
    plan, _, window = fixture()
    before = deepcopy(plan)
    n.normalise_plan(plan, [window])
    assert plan == before


@pytest.mark.parametrize('bad_cast', [None, {'bindings': []}, [{'bindings': []}],
    [{'window_id': 'w', 'role_id': 'A', 'identity_confirmed': True}]])
def test_unusable_cast_is_derived_and_idempotent_without_changing_creative_fields(bad_cast):
    plan, _, window = fixture()
    if bad_cast is None:
        plan.pop('cast')
    else:
        plan['cast'] = deepcopy(bad_cast)
    before = deepcopy(plan)
    n.normalise_plan(plan, [window])
    character, = plan['cast']
    assert character == dict(cast_id='cast_A', entity_id=window['observation']['roles'][0]['entity_id'],
        voice='Ethan', bindings=[dict(window_id=window['window_id'], role_id='A')])
    assert plan['audio_draft'][0]['cast_id'] == 'cast_A'
    change = next(c for c in plan['plan_normalisations'] if c['path'] == '$.cast')
    assert change == dict(path='$.cast', before=bad_cast, after=[character], reason='cast_derived_from_speakers')
    for field in ('segments', 'focus_role_id', 'focus_role_bindings'):
        assert plan[field] == before[field]
    assert [{k: v for k, v in line.items() if k != 'cast_id'} for line in plan['audio_draft']] == before['audio_draft']
    snapshot = deepcopy(plan)
    n.normalise_plan(plan, [window])
    assert plan == snapshot
    contracts.validate_task_cast(plan['cast'], [window])


@pytest.mark.parametrize('invalid_entry', [False, True])
def test_partial_cast_completed_and_valid_entry_preserved(invalid_entry):
    plan, _, window = fixture()
    kept = deepcopy(plan['cast'][0])
    role = add_role(plan, window)
    if invalid_entry:
        plan['cast'].append(dict(bindings=[]))
    n.normalise_plan(plan, [window])
    assert plan['cast'][0] == kept
    assert plan['cast'][1]['entity_id'] == role['entity_id']
    assert plan['audio_draft'][1]['cast_id'] == plan['cast'][1]['cast_id']
    contracts.validate_task_cast(plan['cast'], [window])


@pytest.mark.parametrize('unknown', [False, True])
def test_unconfirmed_or_unknown_speaker_does_not_create_cast(unknown):
    plan, _, window = fixture()
    plan.pop('cast')
    if unknown:
        plan['audio_draft'][0]['speaker']['role_id'] = 'unknown'
    else:
        window['observation']['roles'][0]['identity_confirmed'] = False
    original = deepcopy(plan['audio_draft'])
    n.normalise_plan(plan, [window])
    assert 'cast' not in plan
    assert plan['audio_draft'] == original


@pytest.mark.parametrize('same_entity', [False, True])
def test_window_scoped_entities_determine_grouping_and_safe_unique_ids(same_entity):
    plan, _, first = fixture()
    second = deepcopy(first)
    second['window_id'] = 'second_window'
    second['observation']['window_id'] = second['window_id']
    second['qfs_identity'] = []
    if not same_entity:
        second['observation']['roles'][0]['entity_id'] = first['source_sha256'] + ':film_second_window_A'
    segment = dict(plan['segments'][0], segment_id='second_segment', window_id=second['window_id'])
    plan['segments'].append(segment)
    plan['audio_draft'].append(dict(plan['audio_draft'][0], id='second_line', segment_ids=[segment['segment_id']],
        speaker=dict(window_id=second['window_id'], role_id='A')))
    plan.pop('cast')
    n.normalise_plan(plan, [first, second])
    assert [c['cast_id'] for c in plan['cast']] == (['cast_A'] if same_entity else ['cast_A', 'cast_A_2'])
    assert len(plan['cast'][0]['bindings']) == (2 if same_entity else 1)
    contracts.validate_task_cast(plan['cast'], [first, second])
    snapshot = deepcopy(plan)
    n.normalise_plan(plan, [first, second])
    assert plan == snapshot


def test_bindings_include_confirmed_appearances_in_used_windows_without_dialogue():
    plan, _, window = fixture()
    role = dict(window['observation']['roles'][0], role_id='another_appearance')
    window['observation']['roles'].append(role)
    plan['segments'][0]['role_ids'].append(role['role_id'])
    plan.pop('cast')
    n.normalise_plan(plan, [window])
    assert plan['cast'][0]['bindings'] == [dict(window_id=window['window_id'], role_id=r)
        for r in ('A', 'another_appearance')]


def test_voice_conflict_and_existing_line_cast_id_are_left_to_validators():
    plan, _, window = fixture()
    plan.pop('cast')
    plan['audio_draft'].append(dict(plan['audio_draft'][0], id='conflict', voice='Moon'))
    n.normalise_plan(plan, [window])
    assert plan['cast'][0]['voice'] == 'Ethan'
    assert [line['voice'] for line in plan['audio_draft']] == ['Ethan', 'Moon']
    with pytest.raises(ValueError, match='^plan:dialogue_speaker_voice_changed$'):
        contracts.validate_dialogue_speakers(plan['audio_draft'], plan['segments'], cast=plan['cast'], windows=[window])
    plan['audio_draft'][0]['cast_id'] = 'model_choice'
    snapshot = deepcopy(plan['audio_draft'][0])
    n.normalise_plan(plan, [window])
    assert plan['audio_draft'][0] == snapshot


EVIDENCE = Path('/Users/Admin/Desktop/agentic_video_all/.agent-team/out/s21/evidence')


@pytest.mark.parametrize('stage', ['glm_023_plan_0', 'glm_024_plan_0_repair', 'glm_025_plan_0_repair2'])
def test_real_film_replies_pass_the_unchanged_plan_validator(stage):
    folder = EVIDENCE / 'calls' / stage
    if not folder.is_dir():
        pytest.skip('External S21 evidence is not installed')
    context = replay_check.prompt_context(json.loads((folder / 'request.json').read_text()),
        'reference', 'catalog', 'watched_windows')
    reference = context['reference']
    windows = json.loads((EVIDENCE / 'watched_windows.json').read_text())
    config = dict(creative_audio=True, material_search='film', workflow='reference_rough_skill_v1',
        provider='official_vision_mcp_in_opencode', output_canvas=n.landscape_canvas_key())
    # Re-evaluate the received text as a fresh film task; old locks keep their failure order.
    context['output_canvas'] = config['output_canvas']
    validate = replay_check.nested_validator(pipeline.execute, 'validate_current_plan', context=context,
        creative_audio=config['creative_audio'], editing_reference=None, editing_v2=False, maximum_segments=32,
        progress_driven=config['workflow'] == 'reference_rough_skill_v1',
        ref=dict(sha256=reference['reference_sha256'], duration_s=context['reference_duration_s'],
            audio_stream_index=context.get('reference_audio_stream_index')), reference_reading=reference,
        semantic_audit=False, semantic_pipeline=None, sources=context['catalog'],
        strategy=contracts.reference_strategy(reference, creative_audio=True), visual_only=True, windows=windows)
    value = contracts.parse_model_json(json.loads((folder / 'protocol_failure.json').read_text())['model_text'])
    validate(value)
    assert len(value['cast']) == 6
    assert any(c['reason'] == 'cast_derived_from_speakers' for c in value['plan_normalisations'])
    before = deepcopy(value)
    validate(value)
    assert value == before
