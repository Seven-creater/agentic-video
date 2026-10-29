"""Real faa499b shared slots, complete feedback, and bounded local continuation."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from omni_story.library import clean_chain, contracts, line_windows, protocol_feedback, story_context, visual_audio
from omni_story.library.render import compile_library_plan
from omni_story.library.run_outcome import write_run_outcome
from omni_story.library.state import LibraryStopped
from test_mask_and_line_window import short_plan
from test_protocol_feedback import harness, feedback
from test_story_finecut import instance


FIXTURE = json.loads((Path(__file__).parent / 'fixtures/plan_faa499b_line_windows.json').read_text())


@pytest.mark.parametrize('row,seconds', list(zip(FIXTURE, [.9230769230769231, 2 / 3])))
def test_real_reply_reports_every_line_and_shared_derivation(row, seconds):
    with pytest.raises(ValueError) as caught:
        contracts.validate_audio_draft(row['audio_draft'], row['segments'])
    violation = caught.value.violation
    offenders = violation['offending_lines']
    assert offenders == caught.value.diagnostics['line_window_warnings']
    assert [item['index'] for item in offenders] == [0, 1]
    assert offenders[0]['window_s'] == pytest.approx(seconds)
    assert offenders[0]['min_window_s'] == pytest.approx(1.8 if row == FIXTURE[0] else .9)
    for index, item in enumerate(offenders):
        assert item['text'] == row['audio_draft'][index]['text']
        assert item['spoken_chars'] == sum(c.isalnum() for c in item['text'])
        assert item['segment_ids'] == ['seg_1']
        assert item['segments'][0]['output_duration_s'] == 2
        assert item['derivation'] == line_windows.draft_window_derivation()
        assert all(word in item['repair_hint'] for word in ['更长', '延长', '更早', '更晚', 'start_s/end_s', '缩短'])
    diagnosed = protocol_feedback.diagnose('plan_0', row, dict(stage='plan',
        validation_error=str(caught.value), violation=violation))
    assert [item['window_s'] for item in diagnosed if item.get('code') == str(caught.value)] == [
        item['window_s'] for item in offenders]
    prompt = line_windows.line_window_prompt(row['audio_draft'], segments=row['segments'])
    assert line_windows.draft_window_derivation() in prompt
    assert json.dumps(line_windows.draft_line_window_details(row['audio_draft'], row['segments']),
                      ensure_ascii=False) in prompt


def rough():
    value, catalog, window = short_plan()
    catalog['sources'][0]['path'] = 'synthetic-source.mp4'
    value['segments'][0]['source_out_s'] = 19.05
    value['segments'][0]['segment_id'] = 'seg_1'
    value['slots'][0]['segment_ids'] = ['seg_1']
    value['audio_draft'] = deepcopy(FIXTURE[-1]['audio_draft'][:2])
    for line in value['audio_draft']:
        line['mode'] = 'narration'
        line.pop('speaker', None)
    def validate(value):
        contracts.validate_plan(value, catalog, [window], value['reference_sha256'],
                                require_audio_draft=True, allow_unfocused=True)
        if value.get('other_error'):
            raise ValueError('plan:another_violation_after_floor')
    validate.protocol_context = dict(stage='plan', windows=[window])
    return value, validate, catalog


def fine(tmp_path):
    value, _, _ = rough()
    flow = instance(tmp_path)
    flow.state = SimpleNamespace(input_lock=dict(configuration=dict(creative_audio=True)))
    flow.adaptive, flow.knowledge, flow.inspections = True, 'skill', []
    flow.reference = dict(reference=dict(reference_sha256='b' * 64, theme='主题', intended_takeaway='表达'))
    flow.story_context = dict(reference=flow.reference['reference'], duration_s=147,
        video_sha256='a' * 64, segments=[dict(id='parent', start_s=0, end_s=147, purpose='表达')],
        audio_draft=[])
    flow.observed = dict(story=[dict(id='beat_a')])
    value['segments'][0].update(source_id='chain_parent', window_id='full_parent_observed',
        contribution_to=['beat_a'], reason='表达')
    return value, flow.plan_check, [flow.render_source]


@pytest.mark.parametrize('kind,stage', [('rough', 'plan_0'), ('fine', 'chain_e2e_v1_fine_plan'),
                                       ('fine', 'chain_e2e_v1_fine_revise'),
                                       ('fine', 'chain_e2e_v1_fine_revise_1')])
def test_only_floor_exhaustion_continues_without_resend(tmp_path, monkeypatch, kind, stage):
    value, validate, sources = fine(tmp_path) if kind == 'fine' else rough()
    mcp, media, sent = harness(tmp_path, monkeypatch, [deepcopy(value) for _ in range(3)],
                              provider='official_vision_mcp_in_opencode')
    accepted = mcp.call(stage, 'plan prompt', media, validate)
    assert len(accepted['line_window_warnings']) == 2
    # Failed paid replies stay failures; acceptance is a separate bound artifact.
    assert all(not (mcp.output / 'calls' / call['id'] / 'parsed.json').exists()
               for call in mcp.state.data['calls'])
    assert len(feedback(sent[-1])['validation_diagnostics']['line_window_warnings']) == 2
    count = len(sent)
    assert mcp.call(stage, 'changed prompt', media, validate) == accepted
    assert len(sent) == count == 3
    assert len(mcp.state.data['artifacts']['line_window_exhaustion']) == 1
    compiled = compile_library_plan(sources, accepted)
    reference = dict(reference_sha256=value['reference_sha256'], theme='主题', intended_takeaway='表达')
    context = story_context.build_rough_context(reference, accepted, dict(sha256='d' * 64,
        duration_s=compiled['duration_s'], provenance=compiled['segments']))
    assert context['line_window_warnings'] == accepted['line_window_warnings']
    calls = []
    def tts(text, voice, output, **kwargs):
        calls.append(text)
        return dict(audio_path='received.wav', audio_sha256='e' * 64, duration_s=2)
    timed = story_context.synthesize_draft(context, tmp_path / 'tts', tts=tts)
    assert len(calls) == 2 and timed['timing_problems']
    stages = []
    def revise(story, *args, stage, **kwargs):
        stages.append(stage)
        return story
    monkeypatch.setattr(visual_audio, 'revise_draft_timing', revise)
    timed = clean_chain.revise_rough_audio_timing(timed, mcp.state, mcp, dict(path=str(media),
        kind='complete_file', source_sha256='a' * 64, source_start_s=0, source_end_s=2), tmp_path)
    dropped = story_context.drop_timing_lines(timed)
    assert stages == ['rough_audio_timing_1'] and dropped['dropped_lines'] and dropped['partial']
    outcome = write_run_outcome(mcp.output, dict(status='rough_audio_ready', content_delivery_ready=True,
                                               final_video='preview.mp4'))
    assert outcome['line_window_warnings'] == accepted['line_window_warnings']


@pytest.mark.parametrize('kind', ['rough', 'fine'])
@pytest.mark.parametrize('other', ['before', 'after'])
def test_other_violation_still_exhausts(tmp_path, monkeypatch, kind, other):
    value, validate, _ = fine(tmp_path) if kind == 'fine' else rough()
    if other == 'before':
        value['segments'][0]['speed'] = 4
    elif kind == 'rough':
        value['other_error'] = True
    else:
        check = validate
        def validate(value):
            check(value)
            raise ValueError('fine:another_violation_after_floor')
    stage = 'plan_0' if kind == 'rough' else 'chain_e2e_v1_fine_plan'
    mcp, media, sent = harness(tmp_path, monkeypatch, [deepcopy(value) for _ in range(3)],
                              provider='official_vision_mcp_in_opencode')
    with pytest.raises(ValueError, match='^model_protocol_repair_exhausted:' + stage + '$'):
        mcp.call(stage, 'plan prompt', media, validate)
    assert len(sent) == 3
    assert not mcp.state.data['artifacts'].get('line_window_exhaustion')


def test_explicit_windows_are_already_permitted_and_override_proportions():
    value, validate, _ = rough()
    value['segments'][0]['source_out_s'] = 21.05
    value['audio_draft'][0].update(start_s=0, end_s=1)
    value['audio_draft'][1].update(start_s=1, end_s=4)
    validate(value)
    from omni_story.library.prompts import plan_prompt
    prompt = plan_prompt(dict(creative_audio=True))
    assert 'start_s/end_s' in prompt and line_windows.draft_window_derivation() in prompt


def test_feedback_never_truncates_the_list_of_offenders():
    row = deepcopy(FIXTURE[-1])
    row['audio_draft'] = [dict(row['audio_draft'][0], id=f'l{index}') for index in range(40)]
    with pytest.raises(ValueError) as caught:
        contracts.validate_audio_draft(row['audio_draft'], row['segments'])
    items = protocol_feedback.diagnose('plan_0', row, dict(stage='plan',
        validation_error=str(caught.value), violation=caught.value.violation))
    assert len([item for item in items if item.get('code') == 'audio_line_window_below_minimum']) == 40


def test_both_fine_prompts_use_the_same_derivation(tmp_path, monkeypatch):
    from omni_story.library import active_finecut
    _, check, _ = fine(tmp_path)
    assert line_windows.draft_window_derivation() in check.__self__.plan_prompt(check.__self__.observed)
    monkeypatch.setattr(active_finecut, 'handbook', lambda state: 'skill')
    value, _, _ = rough()
    prompt = active_finecut.refinement_prompt(None, value, {})
    assert line_windows.draft_window_derivation() in prompt
    assert json.dumps(line_windows.draft_line_window_details(value['audio_draft'], value['segments']),
                      ensure_ascii=False) in prompt


def test_nested_finecut_plan_exhaustion_records_warnings_on_the_plan(tmp_path, monkeypatch):
    value, check, _ = rough()
    def validate(reply):
        check(reply['plan'])
        assert reply['decisions'] == []
    reply = dict(plan=value, decisions=[])
    mcp, media, sent = harness(tmp_path, monkeypatch, [deepcopy(reply) for _ in range(3)],
                              provider='official_vision_mcp_in_opencode')
    accepted = mcp.call('finecut_0', 'prompt', media, validate)
    assert len(sent) == 3 and len(accepted['plan']['line_window_warnings']) == 2


def test_later_workflow_guard_preserves_the_original_exhaustion(tmp_path, monkeypatch):
    value, check, _ = rough()
    def validate(reply):
        check(reply)
        raise LibraryStopped('another_execution_guard')
    mcp, media, sent = harness(tmp_path, monkeypatch, [deepcopy(value) for _ in range(3)],
                              provider='official_vision_mcp_in_opencode')
    with pytest.raises(ValueError, match='^model_protocol_repair_exhausted:plan_0$'):
        mcp.call('plan_0', 'prompt', media, validate)
    assert len(sent) == 3 and not mcp.state.data['artifacts'].get('line_window_exhaustion')
