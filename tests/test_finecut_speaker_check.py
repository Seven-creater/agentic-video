"""Finecut speaker preflight and protocol repairs, without media or API calls."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from omni_story.library import protocol_feedback, story_finecut as flow
from omni_story.library.media import sha256_file
from test_protocol_feedback import feedback, harness


def finecut(*, audio=True):
    value = flow.StoryFinecut.__new__(flow.StoryFinecut)
    value.state = SimpleNamespace(input_lock=dict(configuration=dict(creative_audio=audio)))
    reference = dict(reference_sha256='b' * 64, theme='选择与信任', intended_takeaway='守住品质赢得信任',
                     strategy=dict(speech_mode='mixed'))
    value.reference = dict(reference=reference)
    value.auth = dict(parent=dict(duration_s=16),
        reference=dict(duration_s=12, sha256='b' * 64, audio_stream_index=0, path='reference.mp4'),
        target_duration_s=12, target_tolerance_s=2)
    value.render_source = dict(source_id='chain_parent', path='synthetic.mp4', sha256='a' * 64,
                               duration_s=16, audio_stream_index=None)
    value.observed = dict(story=[dict(id='main')])
    cast = [dict(cast_id='vendor', entity_id='entity_a', voice='Serena',
                 bindings=[dict(window_id='window_a', role_id='role_a'),
                           dict(window_id='later_window_a', role_id='later_role_a')])]
    parent_segments = []
    for index, entity in enumerate(('entity_a', 'entity_b', 'entity_a', 'entity_b')):
        start, end = index * 4, index * 4 + 4
        event = dict(start_s=start, end_s=1 if index == 0 else end,
                     observed_fact='角色作出回应', entity_ids=[entity], complete_event=True)
        parent_segments.append(dict(id=f'rough_{index}', start_s=start, end_s=end, purpose='角色回应',
            visible_facts=['角色作出回应'], source_text_context=[], unsupported_claims=[],
            entity_ids=[entity], visual_events=[event]))
    value.story_context = dict(reference=reference, video_sha256='a' * 64, duration_s=16,
        cast=cast, segments=parent_segments,
        audio_draft=[dict(id='aud_a', text='守住品质才能赢得信任。', voice='Serena', mode='narration',
                          segment_ids=['rough_0'], start_s=0, end_s=4)])
    plan = dict(segments=[dict(segment_id=sid, source_id='chain_parent', window_id='full_parent_observed',
        source_in_s=index * 4, source_out_s=index * 4 + 4, speed=1, freeze_tail_s=0,
        look='none', framing='fit', contribution_to=['main'], reason='保留角色的实际回应')
        for index, sid in enumerate(('clip_a', 'clip_b', 'clip_c'))], limitations=[],
        audio_draft=[dict(id='aud_a', text='守住品质。', voice='Serena', mode='dialogue',
            speaker=dict(window_id='window_a', role_id='role_a'), segment_ids=['clip_b'],
            audio_relation=[dict(segment_id='clip_b', relation='reaction')])])
    return value, plan


def rendered(value, plan):
    compiled = flow.compile_library_plan([value.render_source], dict(segments=plan['segments'], audio_mode='silent'),
                                        fps=flow.FPS, width=flow.WIDTH, height=flow.HEIGHT, max_duration_s=None)
    return dict(sha256='c' * 64, duration_s=compiled['duration_s'], provenance=compiled['segments'])


def correct(plan, method):
    result = deepcopy(plan)
    line = result['audio_draft'][0]
    if method == 'rebind':
        line['segment_ids'] = ['clip_a']
        line['audio_relation'] = [dict(segment_id='clip_a', relation='reaction')]
    elif method == 'narration':
        line['mode'] = 'narration'
        line.pop('speaker')
        line['audio_relation'][0]['relation'] = 'narration'
    else:
        line['identity_anchor_segment_id'] = 'clip_a'
    return result


def test_fine_plan_rejects_invisible_speaker_with_actionable_violation():
    value, plan = finecut()
    snapshot = deepcopy((value.story_context, plan))
    with pytest.raises(ValueError, match='^plan:dialogue_speaker_not_in_selected_segments$') as caught:
        value.plan_check(plan)
    violation = caught.value.violation
    assert violation['path'] == '$.audio_draft[0].speaker'
    assert violation['value'] == plan['audio_draft'][0]['speaker']
    assert all(text in violation['expected'] for text in (
        'aud_a', 'window_a/role_a', "实际出现在['clip_a', 'clip_c']", "本句挂在['clip_b']",
        'narration', '改挂说话人可见段', 'identity_anchor_segment_id', '当前或更早'))
    assert (value.story_context, plan) == snapshot


@pytest.mark.parametrize('method', ['rebind', 'narration', 'identity_anchor'])
def test_fine_plan_corrections_pass_the_delivery_context_check(method):
    value, plan = finecut()
    fixed = correct(plan, method)
    snapshot = deepcopy(fixed)
    value.plan_check(fixed)
    context = flow.build_fine_context(value.reference, value.story_context, fixed, rendered(value, fixed))
    assert context['audio_draft'][0]['mode'] == fixed['audio_draft'][0]['mode']
    assert fixed == snapshot


@pytest.mark.parametrize('method', ['rebind', 'narration', 'identity_anchor'])
def test_fine_plan_enters_existing_v3_repairs_and_accepts_second_repair(tmp_path, monkeypatch, method):
    value, plan = finecut()
    fixed = correct(plan, method)
    mcp, media, sent = harness(tmp_path, monkeypatch, [plan, deepcopy(plan), fixed],
                             provider='official_vision_mcp_in_opencode')
    scope = dict(kind='complete_file', source_sha256=sha256_file(media), source_start_s=0, source_end_s=16)
    stage = flow.PREFIX + 'plan'
    assert mcp.call(stage, 'fine plan', media, value.plan_check, scope=scope) == fixed
    assert [row['name'] for row in mcp.state.data['calls']] == [stage, stage + '_repair', stage + '_repair2']
    for request in sent[1:]:
        report = feedback(request)
        assert report['validation_error'] == 'plan:dialogue_speaker_not_in_selected_segments'
        violation = next(row for row in report['violations'] if row['path'] == '$.audio_draft[0].speaker')
        assert all(text in violation['hint'] for text in ('aud_a', 'window_a/role_a', 'clip_a', 'clip_b', 'clip_c',
                                                         'narration', 'identity_anchor_segment_id'))
    assert mcp.call(stage, 'fine plan', media, value.plan_check, scope=scope) == fixed
    assert len(sent) == 3


def test_fine_plan_stops_after_two_failed_v3_repairs(tmp_path, monkeypatch):
    value, plan = finecut()
    mcp, media, sent = harness(tmp_path, monkeypatch, [deepcopy(plan) for _ in range(3)],
                             provider='official_vision_mcp_in_opencode')
    scope = dict(kind='complete_file', source_sha256=sha256_file(media), source_start_s=0, source_end_s=16)
    with pytest.raises(ValueError, match='^model_protocol_repair_exhausted:chain_e2e_v1_fine_plan$') as caught:
        mcp.call(flow.PREFIX + 'plan', 'fine plan', media, value.plan_check, scope=scope)
    assert caught.value.violation['path'] == '$.audio_draft[0].speaker'
    assert len(sent) == 3


def test_visibility_uses_surviving_events_and_cast_aliases():
    value, plan = finecut()
    plan['segments'][0]['source_in_s'] = 2
    plan['segments'][0].update(role_ids=['role_a'], entity_ids=['entity_a'])
    plan['audio_draft'][0]['speaker'] = dict(window_id='later_window_a', role_id='later_role_a')
    plan['audio_draft'][0]['segment_ids'] = ['clip_a']
    plan['audio_draft'][0]['audio_relation'] = [dict(segment_id='clip_a', relation='reaction')]
    with pytest.raises(ValueError, match='dialogue_speaker_not_in_selected_segments') as caught:
        value.plan_check(plan)
    assert "实际出现在['clip_c']" in caught.value.violation['expected']
    plan['audio_draft'][0]['segment_ids'] = ['clip_c']
    plan['audio_draft'][0]['audio_relation'][0]['segment_id'] = 'clip_c'
    value.plan_check(plan)


def test_preflight_uses_plan_cast_as_delivery_does():
    value, plan = finecut()
    plan['cast'] = deepcopy(value.story_context['cast'])
    plan['cast'][0]['bindings'].append(dict(window_id='new_window', role_id='new_role'))
    plan['audio_draft'][0]['speaker'] = dict(window_id='new_window', role_id='new_role')
    value.plan_check(correct(plan, 'rebind'))


def test_inherited_speaker_and_automatic_earlier_anchor_match_delivery_without_mutation():
    value, plan = finecut()
    value.story_context['audio_draft'][0].update(mode='dialogue', speaker=deepcopy(plan['audio_draft'][0]['speaker']))
    plan['audio_draft'][0].pop('speaker')
    snapshot = deepcopy((value.story_context, plan))
    value.plan_check(plan)
    context = flow.build_fine_context(value.reference, value.story_context, plan, rendered(value, plan))
    assert context['audio_draft'][0]['identity_anchor_segment_id'] == 'clip_a'
    assert context['audio_draft'][0]['speaker'] == value.story_context['audio_draft'][0]['speaker']
    assert (value.story_context, plan) == snapshot


@pytest.mark.parametrize('on_screen', [False, True])
def test_bad_visible_anchor_and_on_screen_relation_have_precise_violations(on_screen):
    value, plan = finecut()
    plan = correct(plan, 'identity_anchor' if on_screen else 'rebind')
    if on_screen:
        plan['audio_draft'][0]['audio_relation'][0]['relation'] = 'on_screen'
    else:
        plan['audio_draft'][0]['identity_anchor_segment_id'] = 'clip_b'
        plan['audio_draft'][0]['segment_ids'] = ['clip_c']
        plan['audio_draft'][0]['audio_relation'][0]['segment_id'] = 'clip_c'
    reason = 'on_screen_speaker_not_visible' if on_screen else 'dialogue_identity_anchor_not_established'
    with pytest.raises(ValueError, match=reason) as caught:
        value.plan_check(plan)
    field = 'audio_relation[0].segment_id' if on_screen else 'identity_anchor_segment_id'
    assert caught.value.violation['path'] == '$.audio_draft[0].' + field
    assert caught.value.violation['value'] == 'clip_b'
    assert 'aud_a' in caught.value.violation['expected']
    found = protocol_feedback.diagnose(flow.PREFIX + 'plan', plan, dict(validation_error=caught.value))
    assert [row['path'] for row in found] == [caught.value.violation['path']]


def deletion(value, plan):
    original = deepcopy(plan)
    original['audio_draft'][0]['segment_ids'] = ['clip_c']
    original['audio_draft'][0]['audio_relation'][0]['segment_id'] = 'clip_c'
    value.plan_check(original)
    revised = deepcopy(plan)
    revised['segments'].pop()
    review = dict(target=dict(problems=[dict(start_s=8, end_s=12, description='段落突兀')]))
    return original, revised, rendered(value, original), review


def test_fine_revise_rejects_deleted_speaker_line_moved_to_invisible_neighbor():
    value, plan = finecut()
    original, revised, result, review = deletion(value, plan)
    with pytest.raises(ValueError, match='dialogue_speaker_not_in_selected_segments') as caught:
        value.revision_check(revised, original, result, review)
    assert caught.value.violation['path'] == '$.audio_draft[0].speaker'
    assert "实际出现在['clip_a']" in caught.value.violation['expected']
    assert "本句挂在['clip_b']" in caught.value.violation['expected']


@pytest.mark.parametrize('method', ['narration', 'identity_anchor'])
def test_fine_revise_speaker_failure_enters_repair(tmp_path, monkeypatch, method):
    value, plan = finecut()
    original, revised, result, review = deletion(value, plan)
    fixed = correct(revised, method)
    mcp, media, sent = harness(tmp_path, monkeypatch, [revised, fixed],
                             provider='official_vision_mcp_in_opencode')
    scope = dict(kind='complete_file', source_sha256=sha256_file(media), source_start_s=0, source_end_s=16)
    validator = lambda proposal: value.revision_check(proposal, original, result, review)
    assert mcp.call(flow.PREFIX + 'revise', 'fine revise', media, validator, scope=scope) == fixed
    assert len(sent) == 2
    assert feedback(sent[1])['validation_error'] == 'plan:dialogue_speaker_not_in_selected_segments'


@pytest.mark.parametrize('mode', ['narration', None, 'none', 'disabled_audio'])
def test_no_dialogue_or_disabled_audio_keeps_existing_validation(mode, monkeypatch):
    value, plan = finecut(audio=mode != 'disabled_audio')
    if mode == 'none':
        plan['audio_draft'] = []
        value.story_context['reference']['strategy']['speech_mode'] = 'none'
    elif mode != 'disabled_audio':
        plan['audio_draft'][0]['mode'] = mode
        if mode is None:
            plan['audio_draft'][0].pop('mode')
    monkeypatch.setattr(flow, 'build_fine_context', lambda *args: pytest.fail('unexpected speaker preflight'))
    value.plan_check(plan)


def test_finish_keeps_delivery_speaker_check_for_deliver_review(tmp_path, monkeypatch):
    from omni_story.library import visual_audio
    from omni_story.library.opencode_provider import OpenCodeMCP

    value, plan = finecut()
    value.base = tmp_path
    value.mcp = OpenCodeMCP.__new__(OpenCodeMCP)
    video = tmp_path / 'candidate.mp4'
    video.write_bytes(b'fixture candidate, never decoded')
    selected = dict(rendered(value, plan), rendered_path=str(video), sha256=sha256_file(video),
                    manifest_path=str(tmp_path / 'render' / 'render_result.json'))
    flow.write_json(tmp_path / 'render' / 'render_input.json', dict(compiled=dict(plan=plan)))
    review = dict(target=dict(missing_core_meaning=[], next_action='deliver'))
    monkeypatch.setattr(visual_audio, 'execute', lambda *args, **kwargs: pytest.fail('unexpected audio submission'))
    with pytest.raises(ValueError, match='dialogue_speaker_not_in_selected_segments') as caught:
        value.finish(selected, review)
    assert caught.value.violation['path'] == '$.audio_draft[0].speaker'
