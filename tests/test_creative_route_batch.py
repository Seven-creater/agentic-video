"""Creative route batch regressions; local fakes, no paid requests or git mutations."""
from copy import deepcopy
import inspect
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from omni_story.library import (audio_contracts, clean_chain, contracts, pipeline, prompts,
                                protocol_feedback, render, replay_check, story_finecut as flow,
                                visual_audio, visual_story_trial as trial)
from omni_story.library.media import sha256_file
from omni_story.library.state import LibraryStopped, write_json
from test_story_finecut import instance, plan, State
from test_finecut_speaker_check import finecut, correct


def target_validator(value):
    return replay_check.nested_validator(flow.StoryFinecut.review, 'target_check',
        self=value, result=dict(duration_s=20))


@pytest.mark.parametrize('reason,problems,path', [(None, True, '$.revision_reason'),
    (' ', True, '$.revision_reason'), ([], True, '$.revision_reason'), ('fix', False, '$.problems')])
def test_a2_review_revision_rejects_repairable(reason, problems, path, tmp_path):
    value = instance(tmp_path)
    reply = dict(status='partial', apparent_story='visible', limitations=[], next_action='revise',
        revision_reason=reason, problems=[dict(start_s=1, end_s=2, description='slow')] if problems else [])
    with pytest.raises(ValueError) as caught:
        target_validator(value)(reply)
    assert caught.value.violation['path'] == path
    assert 'hint' not in caught.value.violation
    reply.update(missing_core_meaning=['missing action'])
    target_validator(value)(reply)
    reply.update(next_action='deliver', missing_core_meaning=[])
    target_validator(value)(reply)


def test_a1_selected_review_repair2_and_reuse_guard(tmp_path, monkeypatch):
    stage = 'selected_review_v2_0'
    parent = dict(sha256='a', duration_s=20)
    rough = dict(selected_round=0, final_sha256='a')
    write_json(tmp_path/'render_0/render_result.json', dict(sha256='a', measured_duration_s=20, provenance=[]))
    write_json(tmp_path/'plan_0.json', {})
    write_json(tmp_path/'blind_reading_0.json', {})
    state = SimpleNamespace(data=dict(calls=[]), _reload=lambda: None)
    monkeypatch.setattr(clean_chain, 'proxy', lambda *a, **k: dict(path=__file__))
    review = {}
    def call(*args, **kwargs):
        state.data['calls'].append(dict(id='received2', name=stage+'_repair2', status='received'))
        return review
    result = clean_chain._selected_review(state, SimpleNamespace(call=call), rough, parent,
        dict(sha256='r'), dict(reference={}, evidence_limit='limit'), tmp_path)
    assert result[2] == 'received2'
    (tmp_path/(stage+'.json')).unlink()
    with pytest.raises(LibraryStopped, match='selected_review_already_used'):
        clean_chain._selected_review(state, SimpleNamespace(call=call), rough, parent,
            dict(sha256='r'), dict(reference={}, evidence_limit='limit'), tmp_path)
    # The needs_material_reselection handoff must recognize the same received attempt.
    source = inspect.getsource(clean_chain.execute)
    assert "review_path.stem + '_repair2'" in source


@pytest.mark.parametrize('ranges,frames,pages', [([dict(start_s=0, end_s=4)], 40, 7),
    ([dict(start_s=i/10, end_s=i/10+.01) for i in range(7)], 7, 7)])
def test_a3_a4_frame_ranges_budget_repair(ranges, frames, pages):
    reply = dict(facts=[], uncertainties=[], frame_ranges=ranges)
    with pytest.raises(ValueError) as caught:
        flow.StoryFinecut.facts_check(reply, 0, 6, step=.1, actual_end=6)
    violation = caught.value.violation
    assert violation['path'] == '$.frame_ranges'
    assert violation['value']['frames'] == frames and violation['value']['pages'] == pages
    assert violation['expected'] == dict(max_frames=36, max_pages=6, step_s=.1)
    sampling = flow.frame_sampling([dict(start_s=0, end_s=1.2)], .1, 1.2)
    assert sampling['frames'] == 12 and len(sampling['pages']) == 2


def test_a3_a4_inspect_prompt_and_execution_share_budget(tmp_path, monkeypatch):
    microclip_frames = SimpleNamespace(extract_grid=lambda *a, **k: pytest.fail('over-budget frames executed'),
        frame_catalog=lambda *a: dict(source=dict(source_end_s=147)))
    monkeypatch.setitem(sys.modules, 'omni_story.library.microclip_frames', microclip_frames)
    value = instance(tmp_path)
    value.state = SimpleNamespace(input_lock=dict(configuration=dict(creative_audio=True)))
    value.story_context, value.inspections, value.roi = {}, [], dict(crop=None)
    value.auth['parent'].update(path=__file__, sha256='p')
    monkeypatch.setattr(trial, 'proxy', lambda *a, **k: dict(path=__file__, metadata=dict(duration_s=6)))
    monkeypatch.setattr(microclip_frames, 'frame_catalog', lambda *a: dict(source=dict(source_end_s=147)))
    calls = []
    def call(name, prompt, path, scope, validate, **kwargs):
        calls.append(name)
        assert '最多36帧、6页' in prompt and '实际step_s=0.2' in prompt
        reply = dict(facts=[], uncertainties=[], frame_ranges=[dict(start_s=0, end_s=6)]*2)
        validate(reply)
    value.call = call
    with pytest.raises(ValueError) as caught:
        value.inspect([dict(target='parent', start_s=0, end_s=6, step_s=.2, question='boundary')])
    assert caught.value.violation['path'] == '$.frame_ranges'
    assert calls == [flow.PREFIX+'inspect_0']


def test_a5_duplicate_inspection_names_both_indices(tmp_path):
    value = instance(tmp_path)
    row = dict(target='parent', start_s=0, end_s=1, step_s=.2, question='detail')
    with pytest.raises(ValueError) as caught:
        value.observation_check(dict(story=[dict(id='a', contribution='a')], facts=[],
            limitations=[], inspect=[row, deepcopy(row)]))
    assert caught.value.violation['path'] == '$.inspect[1]'
    assert 'inspect[0]' in caught.value.violation['expected']


def search_fixture():
    catalog = dict(sources=[dict(source_id='film', sha256='a', duration_s=100)])
    window = dict(window_id='old', status='watched', source_sha256='a', source_start_s=20, source_end_s=30)
    reply = dict(windows=[dict(source_id='film', start_s=21, end_s=29, question='detail',
                              role_ids=[], revisit_window_id='old')], reason='detail')
    return catalog, window, reply


@pytest.mark.parametrize('change', [dict(start_s=19), dict(end_s=31), dict(revisit_window_id='missing')])
def test_a6_revisit_containment_is_repairable_in_search(change):
    catalog, window, reply = search_fixture()
    reply['windows'][0].update(change)
    with pytest.raises(ValueError) as caught:
        pipeline._validate_library_search(reply, catalog, [], max_windows=12, received_windows=[window])
    assert str(caught.value) == 'shared_search:revisit_requires_received_source_range'
    assert caught.value.violation['path'] == '$.windows[0].revisit_window_id'
    for request in [dict(reply['windows'][0], start_s=20, end_s=30, revisit_window_id='old'),
                    dict(reply['windows'][0], start_s=19.9999999, end_s=30, revisit_window_id='old')]:
        candidate = dict(reply, windows=[request])
        if request['start_s'] == 20:
            pipeline._validate_library_search(candidate, catalog, [], max_windows=12, received_windows=[window])
        else:
            with pytest.raises(ValueError):
                pipeline._validate_library_search(candidate, catalog, [], max_windows=12, received_windows=[window])


@pytest.mark.parametrize('shared', [False, True])
def test_a8_timing_returns_candidate_after_one_revision(tmp_path, monkeypatch, shared):
    from omni_story.library import audio_render, audio_usage, story_context
    value = visual_audio.VisualAudio.__new__(visual_audio.VisualAudio)
    value.root, value.duration = tmp_path, 10
    video, wav = tmp_path/'video.mp4', tmp_path/'speech.wav'
    video.write_bytes(b'candidate'); wav.write_bytes(b'speech')
    value.video = value.reference = dict(path=str(video), sha256=sha256_file(video))
    value.story_context = dict(source_visual_policy='silent_subtitle_masked' if shared else 'other', audio_draft=[])
    value.state, value.mcp, value.tts, value.tts_cache_dir = State(tmp_path), None, None, tmp_path
    line = dict(id='a', text='a', voice='Serena', start_s=0, end_s=1, audio_path=str(wav))
    timing = [dict(line_id='a', actual_duration_s=2, available_s=1)]
    calls = []
    def synthesize(context, *a, **k):
        calls.append('synthesize')
        return dict(context, audio_draft=[line], received_tts=[dict(line)], timing_problems=timing)
    monkeypatch.setattr(story_context, 'synthesize_draft', synthesize)
    monkeypatch.setattr(visual_audio, 'revise_draft_timing', lambda context, *a, **k:
        (write_json(tmp_path/'draft_timing_revision.json', context) or context))
    monkeypatch.setattr(trial, 'proxy', lambda *a, **k: dict(path=str(video), kind='continuous_window',
        source_sha256='a', source_start_s=0, source_end_s=10))
    value.prepare_picture = lambda: (video, video, dict(limitations=[]))
    def make_plan(*a):
        calls.append('plan'); result = dict(lines=[line], limitations=[])
        write_json(tmp_path/f'audio_plan_v{calls.count("plan")-1}.json', result)
        return result
    value.plan = make_plan
    value.synthesize = lambda plan: ([line], timing)
    monkeypatch.setattr(audio_usage, 'task_usage', lambda *a: {})
    monkeypatch.setattr(audio_usage, 'usage_bindings', lambda *a: {})
    monkeypatch.setattr(audio_render, 'mix_speech', lambda *a, **k: pytest.fail('timing cannot be mixed'))
    value.review = lambda *a: pytest.fail('timing cannot be reviewed as complete AV')
    result = value.run()
    assert result['status'] == 'needs_audio_revision' and result['timing_problems'] == timing
    assert Path(result['final_video']).read_bytes() == b'candidate'
    assert result['selected_version'] == 1 and not result['content_delivery_ready']
    assert calls.count('synthesize' if shared else 'plan') == 2
    assert (tmp_path/'audio_result.json').is_file()
    from omni_story.library import opencode_provider
    finishing = instance(tmp_path/'fine')
    finishing.base.mkdir()
    finishing.state = State(tmp_path)
    finishing.state.input_lock = dict(configuration=dict(creative_audio=True))
    finishing.auth['reference'].update(path=str(video), sha256=sha256_file(video), audio_stream_index=0)
    finishing.mcp = SimpleNamespace()
    monkeypatch.setattr(opencode_provider, 'OpenCodeMCP', SimpleNamespace)
    monkeypatch.setattr(visual_audio, 'execute', lambda *a, **k: result)
    monkeypatch.setattr(flow, 'task_usage', lambda *a: dict(requests=0))
    selected = dict(rendered_path=str(video), sha256=sha256_file(video), duration_s=10)
    final = finishing.finish(selected, dict(target=dict(missing_core_meaning=[], next_action='deliver')))
    assert final['status'] == 'needs_audio_revision' and final['candidate_video'] == str(video)
    assert final['candidate_sha256'] == sha256_file(video) and 'final_video' not in final


@pytest.mark.parametrize('cached', [False, True])
def test_a9_proxy_reencodes_until_under_limit_and_binds_sha(tmp_path, monkeypatch, cached):
    value = visual_audio.VisualAudio.__new__(visual_audio.VisualAudio)
    value.duration = 10
    source, target = tmp_path/'source.mp4', tmp_path/'proxy.mp4'
    source.write_bytes(b'source')
    if cached:
        with target.open('wb') as f: f.truncate(8_000_000)
        write_json(target.with_suffix('.binding.json'), dict(input=dict(input_path=str(source),
            input_sha256=sha256_file(source), policy='original_canvas_time_mute_h264_crf27_fast_v1'),
            output_sha256=sha256_file(target)))
    calls = []
    def encode(args, **kwargs):
        calls.append(args)
        assert '-an' in args and '-ss' not in args and '-t' not in args
        with Path(args[-1]).open('wb') as f: f.truncate(8_100_000 if len(calls) < 3 else 1000)
    monkeypatch.setattr(visual_audio.subprocess, 'run', encode)
    monkeypatch.setattr(visual_audio, 'probe_media', lambda *a: dict(duration_s=10, streams=[]))
    assert value.picture_proxy(source, target) == target
    assert target.stat().st_size < 8_000_000 and len(calls) == 3
    saved = visual_audio.read(target.with_suffix('.binding.json'))
    assert saved['output_sha256'] == sha256_file(target)
    assert '-b:v' in calls[-1] and '-vf' in calls[-1]
    value.picture_proxy(source, target)
    assert len(calls) == 3


def test_c1_prompt_and_validator_share_original_output_table(tmp_path):
    value = instance(tmp_path)
    value.knowledge, value.reference, value.inspections = 'skill', {}, []
    original = plan()
    review = dict(target=dict(problems=[dict(start_s=1, end_s=2), dict(start_s=3, end_s=4),
                                       dict(start_s=11, end_s=12)]))
    prompt = value.plan_prompt(value.observed, revision=dict(plan=original, review=review))
    context, _ = json.JSONDecoder().raw_decode(prompt.split('\n\n')[1])
    table = context['actual_output_feedback']['segment_output_table']
    assert table[0]['output_in_s'] == 0 and table[0]['output_out_s'] == 11
    assert table[0]['editable'] and table[1]['deletable']
    assert context['actual_output_feedback']['merged_problem_intervals'][0] == [1, 4]
    # Half-open contact at 11s does not authorize changes to the first segment.
    review['target']['problems'] = [dict(start_s=11, end_s=12)]
    changed = deepcopy(original); changed['segments'][0]['speed'] = 1.01
    with pytest.raises(ValueError) as caught:
        value.revision_check(changed, original, dict(duration_s=21.9), review)
    assert 'segment_output_table' in caught.value.violation['expected']
    deleted = deepcopy(original); deleted['segments'].pop(0)
    with pytest.raises(ValueError) as caught:
        value.revision_check(deleted, original, dict(duration_s=21.9), review)
    assert 'segment_output_table' in caught.value.violation['expected']


def test_c2_observe_example_has_exact_local_bounds(tmp_path):
    value = instance(tmp_path)
    value.knowledge, value.reference = 'skill', {}
    captured = []
    value.full = dict(path=__file__)
    value.auth['parent']['sha256'] = 'a'
    def call(name, prompt, *a, **k):
        captured.append(prompt)
        raise RuntimeError('captured')
    value.call = call
    with pytest.raises(RuntimeError, match='captured'):
        value.run()
    prompt = captured[0]
    assert '"step_s":0.2' in prompt and '1/30 ≤ step_s ≤ 1' in prompt
    assert 'duration ≤ 6s' in prompt and 'duration/step_s ≤ 36' in prompt
    trial.inspect_check([dict(target='parent', start_s=0, end_s=6, step_s=.2, question='detail')], value.auth)


def test_c3_cast_prompt_requires_confirmed_exact_identity():
    contract = pipeline._task_cast_contract()
    assert 'identity_confirmed=true' in contract['bindings']
    assert 'entity_id必须逐字复制' in contract['bindings']
    assert "'identity_confirmed'" in inspect.getsource(pipeline.execute)


@pytest.mark.parametrize('ident', ['bad id', '1bad', 'a'*65, '非ASCII'])
def test_c4_search_slot_safe_id(ident):
    catalog, window, reply = search_fixture()
    reply['slots'] = [dict(slot_id=ident, intended_takeaway='goal', coverage=dict(visual_goal='v', audio_goal='a'))]
    with pytest.raises(ValueError) as caught:
        pipeline._validate_library_search(reply, catalog, [], max_windows=12, received_windows=[window])
    assert caught.value.violation['path'] == '$.slots[0].slot_id'
    assert '[A-Za-z][A-Za-z0-9_-]{0,63}' in prompts.search_prompt({})
    reply['slots'] = []
    reply['retrieval_queries'] = [dict(query='detail', visual_goal='v', visual_path='p', slot_id=ident)]
    with pytest.raises(ValueError) as caught:
        pipeline._validate_library_search(reply, catalog, [], max_windows=12, received_windows=[window])
    assert caught.value.violation['path'] == '$.retrieval_queries[0].slot_id'


def test_c5_prompts_document_camera_and_required_role_ids():
    assert 'role_ids必须始终提供，可为[]' in prompts.search_prompt({})
    prompt = prompts.fine_prompt({}, {})
    assert '"camera"' in prompt and 'event_indices=[]' in prompt and 'camera为非空字符串' in prompt


def test_d1_fine_plan_without_slots_gets_no_phantom_slot_errors():
    value = dict(segments=[dict(segment_id='seg', source_in_s=0, source_out_s=1)], audio_draft=[])
    items = protocol_feedback.diagnose(flow.PREFIX+'plan', value, dict(stage='plan', validation_error='plan:test', speech_mode='none'))
    assert not any('slot' in row['path'] for row in items)
    value['segments'][0]['slot_id'] = 's'
    items = protocol_feedback.diagnose(flow.PREFIX+'plan', value, dict(stage='plan', validation_error='plan:test', speech_mode='none'))
    assert any(row['path'] == '$.slots' for row in items)
    # Rough plan still requires slots at its own validator.
    with pytest.raises(ValueError, match='plan/slots:list_required'):
        contracts.validate_plan(dict(reference_sha256='a', focus_role_id=None, segments=[dict(segment_id='seg')]),
            dict(sources=[dict(source_id='film', sha256='a', duration_s=10)]), {}, 'a', allow_unfocused=True)


def test_d2_fallback_uses_root_and_error_code():
    items = protocol_feedback.diagnose('inspect', {}, dict(validation_error='error_code'))
    assert items[0]['path'] == '$' and 'error_code' in items[0]['expected'] and items[0]['hint']
    items = protocol_feedback.diagnose('inspect', [], dict(validation_error='root_error'))
    assert items[0]['path'] == '$' and 'root_error' in items[0]['expected']


def test_d2_source_scope_primary_violation_and_scan_cap(tmp_path, monkeypatch):
    value = dict(facts=[dict(start_s=101+i/10, end_s=101.05+i/10) for i in range(12)], other='bad')
    context = dict(validation_error='bad', media_duration_s=6,
        observation_scope=dict(source_start_s=100, source_end_s=106),
        violation=dict(path='$.other', value='bad', expected='good', repair_hint='fix'))
    items = protocol_feedback.diagnose(flow.PREFIX+'inspect_0', value, context)
    assert [row['path'] for row in items] == ['$.other']
    value['facts'] = [dict(start_s=200+i, end_s=201+i) for i in range(12)]
    items = protocol_feedback.diagnose(flow.PREFIX+'inspect_0', value, context)
    assert items[0]['path'] == '$.other' and len(items) <= 6
    assert '106' in items[1]['expected']
    from test_protocol_feedback import harness, feedback
    bad = dict(facts=[dict(start_s=101, end_s=102)], other='bad')
    good = dict(bad, other='good')
    mcp, media, sent = harness(tmp_path, monkeypatch, [bad, good])
    def validate(reply):
        if reply['other'] != 'good':
            raise contracts.numeric_error('inspect:bad', '$.other', reply['other'], 'good')
    scope = dict(kind='continuous_window', source_sha256=sha256_file(media), source_start_s=100, source_end_s=106)
    assert mcp.call(flow.PREFIX+'inspect_0', 'inspect', media, validate, scope=scope) == good
    assert [row['path'] for row in feedback(sent[1])['violations']] == ['$.other']


@pytest.mark.parametrize('confirmed,entity', [(False, 'a:person'), (True, 'a:other')])
def test_d3_cast_binding_reports_observed_entity_and_confirmation(confirmed, entity):
    catalog, window, _ = search_fixture()
    window.update(source_id='film')
    window['observation'] = dict(window_id='old', source_id='film', events=[], usable_ranges=[], uncertainties=[],
        roles=[dict(role_id='person', entity_id='a:person', identity_confirmed=confirmed,
                    state='visible', identity_evidence='picture')])
    cast = [dict(cast_id='cast', entity_id=entity, bindings=[dict(window_id='old', role_id='person')])]
    with pytest.raises(ValueError, match='plan:cast_entity_not_supported_by_observation') as caught:
        contracts.validate_task_cast(cast, [window])
    violation = caught.value.violation
    assert violation['path'] == '$.cast[0].bindings[0]'
    assert violation['expected']['entity_id'] == 'a:person' and violation['expected']['identity_confirmed'] is True


@pytest.mark.parametrize('cast', [[{}], [dict(cast_id='x', entity_id='e', bindings=[])],
    [dict(cast_id='x', entity_id='e', voice='bad', bindings=[{}])]])
def test_d3_cast_structural_errors_have_violations(cast):
    with pytest.raises(ValueError) as caught:
        contracts.validate_task_cast(cast)
    assert caught.value.violation['path'].startswith('$.cast')


@pytest.mark.parametrize('field,ids', [('observation_ids', ['bad']), ('observation_ids', ['ok','ok']),
    ('rejected_observation_ids', ['bad']), ('rejected_observation_ids', ['ok','ok'])])
def test_d3_search_bad_ids_list_candidates(field, ids):
    catalog, window, reply = search_fixture()
    reply[field] = ids
    with pytest.raises(ValueError) as caught:
        pipeline._validate_library_search(reply, catalog, [dict(observation_id='ok')],
            max_windows=12, received_windows=[window])
    violation = caught.value.violation
    assert violation['path'] == '$.'+field and violation['value']
    assert 'ok' in violation['expected']['valid_candidates']


def test_d3_reference_dialogue_required_reports_audio_draft(monkeypatch):
    monkeypatch.setattr(contracts, 'validate_plan', lambda *a, **k: None)
    validate = replay_check.nested_validator(pipeline.execute, 'validate_current_plan', creative_audio=True,
        editing_reference=None, editing_v2=False, maximum_segments=32, progress_driven=False,
        ref=dict(sha256='a', duration_s=10, audio_stream_index=None), reference_reading={},
        semantic_audit=False, semantic_pipeline=None, sources={}, strategy=dict(speech_mode='mixed'),
        visual_only=False, windows=[])
    with pytest.raises(ValueError, match='reference_dialogue_form_missing_from_audio_draft') as caught:
        validate(dict(audio_draft=[]))
    assert caught.value.violation['path'] == '$.audio_draft'
    assert 'mode=dialogue' in caught.value.violation['expected']


def test_d3_usable_event_nonoverlap_reports_both_intervals():
    window = dict(window_id='w', source_id='film', source_start_s=0, source_end_s=10)
    value = dict(window_id='w', source_id='film', roles=[], uncertainties=[],
        events=[dict(local_start_s=0, local_end_s=1, observed_fact='visible', role_ids=[])],
        usable_ranges=[dict(local_in_s=1, local_out_s=2, event_indices=[0], role_ids=[], continuity_notes='c')])
    with pytest.raises(ValueError, match='fine:usable_range_event_does_not_overlap') as caught:
        contracts.validate_fine(value, window)
    violation = caught.value.violation
    assert violation['path'] == '$.usable_ranges[0].event_indices[0]'
    assert violation['expected']['usable_interval'] == [1, 2]
    assert violation['expected']['event_interval'] == [0, 1]


@pytest.mark.parametrize('key', ['facts', 'problems'])
def test_d3_review_missing_description_names_row(key, tmp_path):
    value = instance(tmp_path)
    reply = dict(status='partial', apparent_story='s', limitations=[], facts=[], problems=[])
    reply[key] = [dict(start_s=0, end_s=1)]
    with pytest.raises(ValueError, match='review_time_evidence_required') as caught:
        value.review_check(reply, 10, blind=True)
    assert caught.value.violation['path'] == f'$.{key}[0]'
    assert caught.value.violation['expected']['required_field'] == 'description'


def test_d3_dialogue_voice_change_names_audio_line():
    value, proposed = finecut()
    proposed = correct(proposed, 'rebind')
    proposed['audio_draft'][0]['voice'] = 'Ethan'
    with pytest.raises(ValueError, match='dialogue_speaker_voice_changed') as caught:
        value.plan_check(proposed)
    assert caught.value.violation['path'] == '$.audio_draft[0]'
    assert caught.value.violation['expected']['voice'] == 'Serena'


def test_e1_trial_bounds_and_local_limits_accept_only_epsilon():
    trial.number(2+1e-12, 0, 2)
    trial.number(-1e-12, 0, 2)
    with pytest.raises(ValueError): trial.number(2+2e-6, 0, 2)
    with pytest.raises(ValueError): trial.number(True, 0, 2)
    auth = dict(parent=dict(duration_s=20))
    row = dict(target='parent', start_s=0, end_s=6+1e-12, step_s=1/6, question='detail')
    trial.inspect_check([row], auth)
    row['end_s'] = 6+2e-6
    with pytest.raises(ValueError): trial.inspect_check([row], auth)
    row.update(end_s=3.6+1e-12, step_s=.1)
    trial.inspect_check([row], auth)
    row['end_s'] = 3.6+2e-6
    with pytest.raises(ValueError): trial.inspect_check([row], auth)


def test_e1_render_clamps_epsilon_bounds():
    assert render._number(10+1e-12, 0, 10, 'source_out_s') == 10
    assert render._number(-1e-12, 0, 10, 'source_in_s') == 0
    with pytest.raises(ValueError, match='library_render_invalid_number'):
        render._number(10+2e-6, 0, 10, 'source_out_s')
    with pytest.raises(ValueError): render._number(True, 0, 10, 'source_in_s')


def test_e1_mask_time_and_rectangle_epsilon():
    value = dict(masks=[dict(x=.5+1e-12, y=.5+1e-12, w=.5+1e-12, h=.5+1e-12,
        start_s=0, end_s=10+1e-12, color='#000000', reason='subtitle')], protected_text=[], limitations=[])
    audio_contracts.validate_regions(value, 10)
    assert value['masks'][0]['x'] + value['masks'][0]['w'] <= 1
    assert value['masks'][0]['end_s'] == 10
    value['masks'][0]['end_s'] = 10+2e-6
    with pytest.raises(ValueError): audio_contracts.validate_regions(value, 10)
    value['masks'][0].update(end_s=10, x=.5+2e-6)
    with pytest.raises(ValueError): audio_contracts.validate_regions(value, 10)


def test_e1_unaffected_freeze_zero_equivalence_and_backfill(tmp_path):
    value = instance(tmp_path)
    original, revised = plan(), plan()
    revised['segments'][0]['freeze_tail_s'] = 1e-12
    review = dict(target=dict(problems=[dict(start_s=12, end_s=13)]))
    value.revision_check(revised, original, dict(duration_s=21.9), review)
    assert revised['segments'][0] == original['segments'][0]
    original['segments'][0].pop('freeze_tail_s')
    revised = deepcopy(original); revised['segments'][0]['freeze_tail_s'] = 1e-12
    value.revision_check(revised, original, dict(duration_s=21.9), review)
    assert revised['segments'][0] == original['segments'][0]
    revised['segments'][0]['freeze_tail_s'] = .03
    with pytest.raises(ValueError, match='revision_changes_unrelated_segment'):
        value.revision_check(revised, original, dict(duration_s=21.9), review)
