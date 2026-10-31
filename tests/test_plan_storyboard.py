"""Storyboard planning/review contracts; in-memory evidence and fake transport only."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from omni_story.library import contracts, pipeline, plan_normalisation, prompts, replay_check, story_finecut
from omni_story.library.clean_chain import ProgressRoughPrompts
from omni_story.library.story_context import core_missing
from omni_story.library.storyboard import storyboard_plan_policy


SHA = 'a' * 64


def fixture(mode='narration'):
    cells = [dict(id=f's{i+1}', start_s=i*4, end_s=(i+1)*4, roles=['A'], action='可见动作',
        lines=[dict(speaker='A', intent='发言', ref_asr_ids=[f'asr{i}'])],
        core=i == 0, story_role='铺垫', basis='visible+asr') for i in range(7)]
    reference = dict(reference_sha256=SHA, strategy=dict(speech_mode=mode),
                     reference_storyboard=dict(storyboard=cells, pace='先快后慢'))
    segments = [dict(segment_id=f'seg{i}', slot_id=f'b{i+1}', source_id='film', window_id='w',
        source_in_s=i*4, source_out_s=(i+1)*4, speed=1, role_ids=[], look='none', framing='fit') for i in range(7)]
    slots = [dict(slot_id=f'b{i+1}', intended_takeaway='推进', segment_ids=[f'seg{i}'],
                  storyboard_cell_ids=[f's{i+1}']) for i in range(7)]
    draft = [dict(id=f'l{i}', text='你好。', mode='narration', voice='Ethan', segment_ids=[f'seg{i}']) for i in range(5)]
    plan = dict(reference_sha256=SHA, slots=slots, segments=segments, audio_draft=draft,
                audio_mode='silent', width=640, height=360, fps=30, limitations=[], storyboard_merges=[])
    window = dict(window_id='w', source_id='film', source_sha256=SHA, source_start_s=0, source_end_s=60, status='watched',
        observation=dict(window_id='w', source_id='film', roles=[], uncertainties=[],
            events=[dict(local_start_s=0, local_end_s=60, role_ids=[], observed_fact='可见动作')],
            usable_ranges=[dict(local_in_s=0, local_out_s=60, event_indices=[0], role_ids=[], continuity_notes='连续')]))
    catalog = dict(sources=[dict(source_id='film', sha256=SHA, duration_s=60)])
    beats = [dict(id=f'b{i+1}', reference_evidence=dict(source='storyboard', cell_ids=[f's{i+1}']),
        candidates=[dict(source_id='film', film_in_s=i*4, film_out_s=(i+1)*4)]) for i in range(7)]
    return plan, reference, [window], catalog, beats


def check(plan, reference, windows, catalog, beats):
    contracts.validate_plan_storyboard(plan, reference, 28, windows, catalog, storyboard_beats=beats)


def violation(code, data):
    before = deepcopy(data[0])
    with pytest.raises(ValueError, match='^plan:storyboard_' + code + '$') as caught:
        check(*data)
    assert type(caught.value) is ValueError
    assert data[0] == before  # Validators never rewrite creative content.
    assert caught.value.violation['expected']['repair_hint']
    json.dumps(caught.value.diagnostics, allow_nan=False)
    return caught.value


def test_actual_route_prompt_legacy_snapshot_and_storyboard_targets():
    context = {'reference': {'theme': '固定测试'}, 'reference_duration_s': 30, 'creative_audio': True,
               'catalog': {'sources': []}, 'render_capabilities': {'max_duration_s': None, 'max_segments': None}}
    # Captured from the unmodified ProgressRoughPrompts output before step 3.
    hashes = ['69b78f548c5a14255eef06bfaa007c44e68c2b683ea823590c3b930d32fe92db',
              '8d437180a368d04714f2a78946d72279b33b7e0fa48df7cf43a48a94a328db20']
    _, reference, _, _, _ = fixture()
    reference['storyboard_merges'] = [dict(beat_id='b2', into='b1', cell_ids=['s2'], reason='no_eligible_footage_non_core')]
    for round_no, expected in enumerate(hashes):
        template = ProgressRoughPrompts().for_round(round_no).plan_prompt
        legacy = prompts.storyboard_plan_prompt(template, context)
        assert hashlib.sha256(legacy.encode()).hexdigest() == expected
        assert legacy == template(context)
        current = prompts.storyboard_plan_prompt(lambda c: template(c) + '\n' + pipeline.REINTERPRET_POLICY,
                                                dict(context, reference=reference))
        assert '不必复刻参考故事结构' not in current
        assert '按分镜的段落顺序、对话轮次和节奏组织' in current
        assert '"target_duration_s": [24.0, 36.0]' in current
        assert '"cells": [{"cell_id": "s1", "start_s": 0, "end_s": 4, "suggested_s": 4' in current
        assert '"speakers": ["A"], "line_count": 1' in current
        assert '"dialogue_lines_min": 5' in current
        assert '"storyboard_merges": ' + json.dumps(reference['storyboard_merges'], ensure_ascii=False) in current
        assert 'storyboard_cell_ids' in current and 'into_slot_id' in current
        assert pipeline.REINTERPRET_POLICY in current
    assert context['reference'] == {'theme': '固定测试'}


@pytest.mark.parametrize('duration', [22.4, 28, 33.6])
def test_duration_inclusive_bounds_and_speed(duration):
    data = fixture()
    for segment in data[0]['segments']:
        segment['speed'] = 28 / duration
    check(*data)


@pytest.mark.parametrize('duration', [22.3, 33.7])
def test_duration_feedback_with_unused_ranges_and_cell_suggestions(duration):
    data = fixture()
    for segment in data[0]['segments']:
        segment['speed'] = 28 / duration
    detail = violation('duration_out_of_range', data).diagnostics
    assert detail['actual_duration_s'] == pytest.approx(duration)
    assert detail['target_duration_s'] == [22.400000000000002, 33.6]
    assert len(detail['shortest_slots']) == 5
    assert detail['shortest_slots'][0]['cells'] == [dict(cell_id='s1', suggested_s=4)]
    assert detail['lengthening_options'][0]['remaining_usable_s'] == 32
    assert detail['lengthening_options'][0]['remaining_usable_ranges'][0]['source_in_s'] == 28


def test_lengthening_options_use_union_content_bounds_and_cross_window_usage():
    plan, reference, windows, catalog, beats = fixture()
    windows.append(dict(deepcopy(windows[0]), window_id='other'))
    windows[0]['observation']['usable_ranges'].append(dict(local_in_s=20, local_out_s=50, role_ids=[]))
    catalog['sources'][0]['content_range'] = dict(content_start_s=5, content_end_s=55)
    plan['segments'][0].update(source_out_s=2, window_id='other')
    options = contracts._storyboard_remaining_ranges(plan, {w['window_id']: w for w in windows}, catalog)
    assert [o['remaining_usable_s'] for o in options] == [27, 27]
    assert all(r['source_in_s'] >= 28 and r['source_out_s'] <= 55
               for o in options for r in o['remaining_usable_ranges'])


def test_core_coverage_and_diagnostics_for_its_observed_beat():
    data = fixture()
    check(*data)
    data[0]['slots'][0]['storyboard_cell_ids'] = []
    detail = violation('core_cell_uncovered', data).diagnostics
    assert detail['uncovered_cells'][0]['cell_id'] == 's1'
    assert detail['uncovered_cells'][0]['beats'] == [dict(beat_id='b1', observed_window_ids=['w'])]
    # A named cell without a segment does not count as covered.
    data[0]['slots'][0]['storyboard_cell_ids'] = ['s1']
    data[0]['slots'][0]['segment_ids'] = []
    violation('core_cell_uncovered', data)


def test_noncore_requires_slot_or_documented_merge():
    data = fixture()
    data[0]['slots'][1]['storyboard_cell_ids'] = []
    assert violation('merge_invalid', data).diagnostics['unaccounted_cell_ids'] == ['s2']
    data[0]['storyboard_merges'] = [dict(cell_id='s2', into_slot_id='b1', reason='相邻反应镜头承载')]
    check(*data)


def test_merge_cap_counts_unique_search_and_plan_cells_and_ignores_unmerged_gaps():
    data = fixture()
    plan, reference = data[:2]
    reference['storyboard_merges'] = [dict(cell_ids=['s2', 's3'], into='b1', reason='no_eligible_footage_non_core'),
        dict(cell_ids=['s7'], into=None, reason='merge_cap_reached')]
    plan['storyboard_merges'] = [dict(cell_id='s2', into_slot_id='b1', reason='相邻段承载'),
                                dict(cell_id='s4', into_slot_id='b5', reason='相邻段承载')]
    check(*data)  # Seven cells allow ceil(2.1) == 3, not floor(2.1).
    plan['storyboard_merges'].append(dict(cell_id='s5', into_slot_id='b6', reason='相邻段承载'))
    detail = violation('merge_invalid', data).diagnostics
    assert detail['merged_cells_max'] == 3
    assert detail['merged_cell_ids'] == ['s2', 's3', 's4', 's5']
    assert detail['issues'][-1]['reason'] == 'merge_cap_exceeded'


@pytest.mark.parametrize('search_stage', [False, True])
def test_core_merge_is_rejected_even_when_core_is_covered(search_stage):
    data = fixture()
    if search_stage:
        data[1]['storyboard_merges'] = [dict(cell_ids=['s1'], into='b2', reason='no_eligible_footage_non_core')]
    else:
        data[0]['storyboard_merges'] = [dict(cell_id='s1', into_slot_id='b2', reason='不可丢核心')]
    assert violation('merge_invalid', data).diagnostics['issues'][0]['reason'] == 'core_cell_merged'


def test_unknown_merge_target_and_explicit_plan_target_override():
    data = fixture()
    data[1]['storyboard_merges'] = [dict(cell_ids=['s2'], into='gone', reason='no_eligible_footage_non_core')]
    issue = violation('merge_invalid', data).diagnostics['issues'][0]
    assert issue['error'] == 'unknown_into_slot_id' and 'b1' in issue['valid_slot_ids']
    data[0]['storyboard_merges'] = [dict(cell_id='s2', into_slot_id='b1', reason='相邻slot改名')]
    check(*data)


@pytest.mark.parametrize('mode', ['dialogue', 'mixed', 'narration', 'none'])
def test_known_speaker_line_minimum_uses_entries_not_turns_and_skips_none(mode):
    data = fixture(mode)
    data[1]['reference_storyboard']['storyboard'][0]['lines'].append(dict(speaker=None, intent='未知', ref_asr_ids=[]))
    data[1]['reference_storyboard']['dialogue_turns'] = 1
    if mode == 'none':
        data[0]['audio_draft'] = []
    check(*data)
    if mode != 'none':
        data[0]['audio_draft'].pop()
        detail = violation('lines_too_few', data).diagnostics
        assert (detail['actual_lines'], detail['dialogue_lines_min'], detail['storyboard_speaker_lines']) == (4, 5, 7)


def test_normalisation_fills_only_missing_ids_from_exact_beat_and_records_it():
    plan, reference, windows, catalog, beats = fixture()
    del plan['slots'][0]['storyboard_cell_ids']
    plan['slots'][1]['storyboard_cell_ids'] = []
    plan['slots'][2]['slot_id'] = 'creative_slot'
    del plan['slots'][2]['storyboard_cell_ids']
    plan_normalisation.normalise_plan(plan, storyboard_beats=beats)
    assert plan['slots'][0]['storyboard_cell_ids'] == ['s1']
    assert plan['slots'][1]['storyboard_cell_ids'] == []
    assert 'storyboard_cell_ids' not in plan['slots'][2]
    assert plan['plan_normalisations'] == [dict(path='$.slots[0].storyboard_cell_ids', before=None,
        after=['s1'], reason='storyboard_beat_cell_ids')]
    plan_normalisation.normalise_plan(plan, storyboard_beats=beats)
    assert len(plan['plan_normalisations']) == 1


def test_real_pipeline_validator_returns_storyboard_error_through_repair_feedback():
    plan, reference, windows, catalog, beats = fixture()
    plan['audio_draft'].pop()
    validator = replay_check.nested_validator(pipeline.execute, 'validate_current_plan',
        context=dict(slots=[dict(slot_id=b['id'], cell_ids=b['reference_evidence']['cell_ids']) for b in beats],
                     storyboard_beats=beats),
        creative_audio=True, editing_reference=None, editing_v2=False, maximum_segments=32, progress_driven=True,
        ref=dict(sha256=SHA, duration_s=28, audio_stream_index=None), reference_reading=reference,
        semantic_audit=False, semantic_pipeline=None, sources=catalog,
        strategy=contracts.reference_strategy(reference, creative_audio=True), visual_only=True, windows=windows,
        qfs_report=dict(beats=beats))
    with pytest.raises(ValueError, match='plan:storyboard_lines_too_few') as caught:
        validator(plan)
    assert caught.value.violation['expected']['dialogue_lines_min'] == 5
    assert 'valid_choices' in caught.value.violation
    assert caught.value.diagnostics['actual_lines'] == 4


def test_legacy_validator_and_normalisation_ignore_storyboard_fields_without_reference():
    data = fixture()
    data[1].pop('reference_storyboard')
    data[0]['slots'][0]['storyboard_cell_ids'] = 'malformed legacy extra field'
    data[0]['segments'][0]['source_out_s'] = 1
    check(*data)
    plan = dict(slots=[dict(slot_id='b1')])
    before = deepcopy(plan)
    plan_normalisation.normalise_plan(plan)
    assert plan == before


def fine_review(monkeypatch, reference=None, coverage=None):
    value = story_finecut.StoryFinecut.__new__(story_finecut.StoryFinecut)
    value.reference = reference or {}
    value.story_context = None
    value.auth = dict(parent=dict(duration_s=28), target_duration_s=28, reference=dict(duration_s=28))
    value.observed, value.inspections, value.knowledge, value.base = dict(story=[]), [], '审核知识', '/unused'
    value.state = SimpleNamespace(input_lock={}, set_artifact=lambda *a: None)
    value.blind_proxy = lambda result: dict(path='blind.mp4')
    monkeypatch.setattr(story_finecut.trial, 'proxy', lambda *a, **k: dict(path='actual.mp4'))
    requests = []

    def call(stage, prompt, media, scope, validate, **kwargs):
        requests.append(prompt)
        reply = dict(status='pass', apparent_story='可见过程', problems=[], limitations=[])
        if 'blind' in stage:
            reply['facts'] = []
        else:
            reply.update(next_action='deliver', editing_methods_used=[])
            if coverage is not None:
                reply['storyboard_coverage'] = deepcopy(coverage)
        validate(reply)
        return reply

    value.call = call
    result = dict(sha256=SHA, rendered_path='candidate.mp4', duration_s=28)
    return value, value.review({}, result, 0), requests, result


def test_fine_review_legacy_request_snapshot(monkeypatch):
    _, reviewed, requests, _ = fine_review(monkeypatch)
    # Byte snapshots checked against the original review method before step 3.
    assert [hashlib.sha256(p.encode()).hexdigest() for p in requests] == [
        '34b443fc238ad9abde8e9ac8d404b3a6016445d3bb298734a5671ac94f684a9a',
        'c6f789bc131b982e7b6229b24518d9230323c15e1074b50415af71c59ec925fa']
    assert reviewed['target']['next_action'] == 'deliver'
    assert 'storyboard_coverage' not in reviewed['target']


@pytest.mark.parametrize('told', [True, False])
def test_fine_review_core_cell_false_uses_existing_material_reselection_path(monkeypatch, told):
    reference = fixture()[1]
    coverage = [dict(cell_id='s1', told=told, note='实际画面与配音的覆盖依据')]
    value, reviewed, requests, selected = fine_review(monkeypatch, reference, coverage)
    assert '"storyboard_cells":' in requests[1] and 'storyboard_coverage' in requests[1]
    assert reviewed['target']['storyboard_coverage'] == coverage
    assert core_missing(reviewed['target']) is (not told)
    if not told:
        assert reviewed['target']['status'] == 'fail'
        assert '分镜s1未讲到' in reviewed['target']['missing_core_meaning'][0]
        monkeypatch.setattr(story_finecut, 'sha256_file', lambda path: SHA)
        monkeypatch.setattr(story_finecut.trial, 'write_once', lambda *a: None)
        value.base = Path('/unused')
        value.status = lambda *a: None
        monkeypatch.setattr(story_finecut, 'task_usage', lambda *a: {})
        assert value.finish(selected, reviewed)['status'] == 'needs_material_reselection'


@pytest.mark.parametrize('coverage', [None, [], [dict(cell_id='s1', told='false', note='缺失')]])
def test_fine_review_requires_typed_complete_core_coverage(monkeypatch, coverage):
    with pytest.raises(ValueError, match='storyboard_coverage_required'):
        fine_review(monkeypatch, fixture()[1], coverage)


def test_policy_records_thresholds_and_reason():
    policy = storyboard_plan_policy(fixture()[1]['reference_storyboard'], 28)
    assert policy['target_duration_s'] == pytest.approx([22.4, 33.6])
    assert policy['merged_cells_max'] == 3 and policy['dialogue_lines_min'] == 5
    assert policy['reason'] and policy['core_cell_coverage_min'] == 1
