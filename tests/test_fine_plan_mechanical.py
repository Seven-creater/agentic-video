"""Offline replay of S11's received replies, with recorded parent provenance."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from omni_story.library import contracts, plan_normalisation as normalisation, protocol_feedback
from omni_story.library.story_finecut import FPS, PREFIX, StoryFinecut


FIXTURES = Path(__file__).with_name('fixtures')


def read(name):
    return json.loads((FIXTURES / ('s11_' + name + '.json')).read_text(encoding='utf-8'))


def finecut(evidence):
    context, binding = evidence['context'], evidence['binding']
    flow = StoryFinecut.__new__(StoryFinecut)
    flow.state = SimpleNamespace(input_lock=dict(configuration=dict(creative_audio=True)))
    flow.adaptive, flow.knowledge, flow.inspections = True, 'finecut', []
    flow.auth = dict(parent=binding['parent'], reference=binding['reference'],
        target_duration_s=context['target_duration_s'], target_tolerance_s=binding['target_tolerance_s'],
        duration_policy=binding['duration_policy'])
    flow.reference, flow.story_context = context['reference'], context['story_context']
    flow.observed = context['observed']
    flow.render_source = dict(binding['parent'], source_id='chain_parent', audio_stream_index=None)
    return flow


@pytest.mark.parametrize('attempt', [0, 1, 2])
def test_real_failed_replies(attempt):
    flow = finecut(read('failed_context'))
    plan = read('failed_replies')[attempt]
    before, parent = deepcopy(plan), deepcopy(flow.story_context)
    expected_windows = []
    for index, line in enumerate(before['audio_draft']):
        times = {key: line.pop(key) for key in ('start_s', 'end_s') if key in line}
        if times:
            expected_windows.append(dict(path=f'$.audio_draft[{index}]', before=times, after={}, reason='program_line_window'))
    if attempt == 1:
        with pytest.raises(ValueError, match='^fixed_parent_source_and_observed_window_only$'):
            flow.plan_check(plan)
        assert plan == dict(before, plan_normalisations=expected_windows)  # Keep the invalid window choice.
    else:
        flow.plan_check(plan)
        changes = plan.pop('plan_normalisations')
        assert [c for c in changes if c['reason'] == 'program_line_window'] == expected_windows
        mechanical = [c for c in changes if c['reason'] != 'program_line_window']
        if attempt == 0:
            assert len(mechanical) == 22
            assert {c['reason'] for c in mechanical} == {'map_rough_source_time_to_parent'}
            assert plan['audio_draft'] == before['audio_draft']
            for old, segment, rough in zip(before['segments'], plan['segments'], parent['segments']):
                mapping, = rough['source_ranges']
                scale = (mapping['end_s'] - mapping['start_s']) / (mapping['source_out_s'] - mapping['source_in_s'])
                for key in ('source_in_s', 'source_out_s'):
                    expected = mapping['start_s'] + (old[key] - mapping['source_in_s']) * scale
                    assert segment[key] == pytest.approx(expected)
                    old[key] = segment[key]
        else:
            assert len(mechanical) == 11
            assert {c['reason'] for c in mechanical} == {'map_rough_speaker_to_fine'}
            originals = {line['id']: line for line in parent['audio_draft']}
            for old, line in zip(before['audio_draft'], plan['audio_draft']):
                if line.get('mode') == 'dialogue':
                    assert line['speaker'] == originals[line['id']]['speaker']
                    old['speaker'] = line['speaker']
        assert plan == before  # No authored text, voice, relation or edit operation changes.
        plan['plan_normalisations'] = changes
        snapshot = deepcopy(plan)
        flow.plan_check(plan)
        assert plan == snapshot
    assert flow.story_context == parent


def test_attempt_zero_feedback_and_prompt_share_fine_contract():
    flow = finecut(read('failed_context'))
    plan = read('failed_replies')[0]
    with pytest.raises(ValueError, match='library_render_invalid_number:source_in_s') as caught:
        flow._validate_plan(plan)
    normalisation.add_valid_choices(caught.value, plan, parent=flow.story_context, fps=FPS)
    choices = caught.value.violation['valid_choices']
    assert choices['valid_window_ids'] == ['full_parent_observed']
    assert choices['unknown_segment_window_ids'] == []
    assert choices['speaker_form']['required_fields'] == ['window_id', 'role_id']
    assert plan['audio_draft'][1]['speaker'] in choices['valid_speakers']
    report = protocol_feedback.diagnose(PREFIX + 'plan', plan, dict(validation_error=caught.value))
    assert report[0]['valid_choices'] == choices
    prompt = flow.plan_prompt(flow.observed)
    context, _ = json.JSONDecoder().raw_decode(prompt.split('\n\n', 1)[1])
    contract = contracts.fine_plan_contract(flow.story_context, flow.render_source)
    assert context['fine_plan_contract'] == contract
    assert choices['speaker_form'] == contract['speaker_form']


def test_bad_time_still_gets_the_fine_window_choices():
    flow = finecut(read('failed_context'))
    plan = read('failed_replies')[0]
    plan['segments'][0]['source_in_s'] = None
    with pytest.raises(ValueError) as caught:
        flow.plan_check(plan)
    choices = caught.value.violation['valid_choices']
    assert choices['valid_window_ids'] == ['full_parent_observed']
    assert choices['unknown_segment_window_ids'] == []


def test_narration_only_golden_is_unchanged():
    evidence = read('golden')
    flow, plan = finecut(evidence), evidence['reply']
    before = deepcopy(plan)
    for index, line in enumerate(before['audio_draft']):
        times = {key: line.pop(key) for key in ('start_s', 'end_s') if key in line}
        if times:
            before.setdefault('plan_normalisations', []).append(dict(
                path=f'$.audio_draft[{index}]', before=times, after={}, reason='program_line_window'))
    flow.plan_check(plan)
    flow.plan_check(plan)
    assert plan == before


@pytest.mark.parametrize('audio', [False, True])
def test_non_object_reply_keeps_repairable_error_and_fine_choices(audio):
    flow = finecut(read('failed_context'))
    flow.state.input_lock['configuration']['creative_audio'] = audio
    with pytest.raises(ValueError, match='^model_root_object_required$') as caught:
        flow.plan_check([])
    assert caught.value.violation['valid_choices']['valid_window_ids'] == ['full_parent_observed']


def time_plan(start, end):
    return dict(segments=[dict(segment_id='fine', source_id='chain_parent', window_id='full_parent_observed',
        source_in_s=start, source_out_s=end, speed=1, freeze_tail_s=0)], audio_draft=[])


def test_inverse_mapping_respects_speed_position_and_excludes_hold():
    # First rough segment occupies 0..5; the next has 4 s of 2x motion plus a 1 s hold.
    parent = dict(duration_s=10, segments=[dict(start_s=5, end_s=10, source_ranges=[dict(
        source_id='film', source_in_s=100, source_out_s=108, start_s=5, end_s=9)])])
    plan = time_plan(102, 106)
    normalisation.normalise_plan(plan, parent=parent, fps=FPS)
    assert (plan['segments'][0]['source_in_s'], plan['segments'][0]['source_out_s']) == (6, 8)
    snapshot = deepcopy(plan)
    normalisation.normalise_plan(plan, parent=parent, fps=FPS)
    assert plan == snapshot


@pytest.mark.parametrize('kind', ['spanning', 'partial', 'ambiguous', 'inside_parent', 'other_source', 'event_only'])
def test_unsupported_time_mapping_stays_invalid(kind):
    parent = dict(duration_s=10, segments=[dict(source_ranges=[dict(source_id='film',
        source_in_s=100, source_out_s=105, start_s=0, end_s=5)]), dict(source_ranges=[dict(
        source_id='film', source_in_s=105, source_out_s=110, start_s=5, end_s=10)])])
    start, end = dict(spanning=(103, 107), partial=(99, 102), ambiguous=(101, 103),
        inside_parent=(1, 3), other_source=(101, 103), event_only=(101, 103))[kind]
    if kind == 'ambiguous':
        parent['segments'][1]['source_ranges'][0].update(source_in_s=100, source_out_s=105)
    if kind == 'event_only':
        row = parent['segments'][0]
        row['visual_events'] = row.pop('source_ranges')
    plan = time_plan(start, end)
    if kind == 'other_source':
        plan['segments'][0]['source_id'] = 'film'
    before = deepcopy(plan)
    normalisation.normalise_plan(plan, parent=parent, fps=FPS)
    assert plan == before


@pytest.mark.parametrize('speaker', [dict(cast_id='unknown'), dict(cast_id='panda'),
                                    dict(cast_id='panda', role_id='unrecorded')])
def test_unresolved_speakers_are_not_guessed(speaker):
    flow = finecut(read('failed_context'))
    plan = read('failed_replies')[2]
    line = plan['audio_draft'][1]
    # A new line has no stable inherited identity; panda has several recorded bindings.
    line.update(id='new_line', speaker=speaker)
    normalisation.normalise_plan(plan, parent=flow.story_context, fps=FPS)
    assert line['speaker'] == speaker
    with pytest.raises(ValueError, match='speaker/window_id:text_required'):
        flow._validate_plan(plan)
