"""Unattended creative delivery: real measurements, bounded repairs, one terminal outcome."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from omni_story.library import audio_render, clean_chain, contracts, server_cli, story_context, story_finecut, visual_audio
from omni_story.library.state import json_sha, write_json


def context(speech='none'):
    return dict(reference=dict(strategy=dict(speech_mode=speech, timing_driver='action', adaptation_mode='reinterpret')),
        source_visual_policy='silent_subtitle_masked', audio_draft=[])


def verdict():
    return dict(theme_alignment='matches', speech_clear=True, music_audible=True,
        music_rhythm_status=None, repair_at='none', missing_core_meaning=[], issues=[], limitations=[])


def measured(monkeypatch, *, silent=False, line_silent=False, music_silent=False):
    from omni_story.library.audio_measurement import measure_mix
    tone = np.full((48000, 2), .1, dtype=np.float32)
    zero = np.zeros_like(tone)
    samples = dict(mix=zero if silent else tone, final=zero if silent else tone,
        music=zero if music_silent or silent else tone, voice=zero if line_silent or silent else tone)
    monkeypatch.setattr(audio_render, '_samples', lambda path: samples[str(path)])
    return measure_mix('mix', 'music', 'voice', 'final',
        [dict(id='a', start_s=0, actual_end_s=1)], music_expected=True)


def test_s1_action_without_speech_uses_reference_soundtrack(tmp_path, monkeypatch):
    reference = tmp_path/'reference.mp4'; reference.write_bytes(b'reference')
    monkeypatch.setattr(visual_audio, 'probe_media', lambda path: dict(streams=[dict(codec_type='audio')]))
    assert visual_audio.music_source(reference, context()) == reference.resolve()
    with pytest.raises(visual_audio.LibraryStopped, match='requires_explicit_bed_with_new_speech'):
        visual_audio.music_source(reference, dict(context(), audio_draft=[dict(text='new speech')],
            reference=dict(strategy=dict(speech_mode='none', timing_driver='music'))))
    monkeypatch.setattr(visual_audio, 'probe_media', lambda path: dict(streams=[]))
    with pytest.raises(visual_audio.LibraryStopped, match='reference_has_no_soundtrack'):
        visual_audio.music_source(reference, context())


def test_s1_action_reference_bed_requires_audible_music():
    assert not visual_audio.content_ready(dict(verdict(), music_audible=False), context())


def test_s2_all_zero_and_minus_91_db_stems_are_silent(monkeypatch):
    from omni_story.library.audio_measurement import measure_samples, thresholds
    zero = measure_samples(np.zeros((100, 2)))
    assert zero['silent'] and zero['peak_dbfs'] == thresholds()['zero_floor_dbfs']
    assert zero['rms_dbfs'] < -50
    assert measure_samples(np.full((100, 2), 10**(-91/20)))['silent']
    measurement = measured(monkeypatch, silent=True)
    assert not measurement['passed']
    assert measurement['reasons'] == ['audio_measured_silent', 'audio_music_bed_silent', 'audio_line_silent:a']


@pytest.mark.parametrize('problem', ['mix', 'line', 'music'])
def test_s2_failed_measurement_never_calls_av_review(monkeypatch, problem):
    measurement = measured(monkeypatch, silent=problem == 'mix', line_silent=problem == 'line',
        music_silent=problem == 'music')
    review, ready = visual_audio.review_measured_candidate(dict(audio_measurement=measurement, fully_decoded=True),
        context(), lambda: pytest.fail('A failed measurement must not buy Qwen review'))
    assert not ready and review['repair_at'] == 'audio'
    assert review['issues'] == measurement['reasons']


def test_s2_review_claims_contradict_silent_measurement(monkeypatch):
    from omni_story.library.audio_measurement import review_contradictions
    contradiction = review_contradictions(verdict(), measured(monkeypatch, silent=True))
    assert contradiction == dict(code='review_contradicts_measurement', fields=['music_audible', 'speech_clear'])
    assert not visual_audio.content_ready(dict(verdict(), review_contradicts_measurement=contradiction), context())


def test_s2_normal_mix_passes_and_records_windows(monkeypatch):
    measurement = measured(monkeypatch)
    calls = []
    review, ready = visual_audio.review_measured_candidate(dict(audio_measurement=measurement, fully_decoded=True),
        context(), lambda: calls.append('once') or verdict())
    assert ready and calls == ['once']
    assert measurement['line_windows'][0]['rms_dbfs'] == pytest.approx(-20)
    assert measurement['final_mux']['peak_dbfs'] == pytest.approx(-20)


def failed_calls(tmp_path, stage, repairs=2):
    calls = []
    for i in range(repairs+1):
        request, response = dict(prompt='original' if i == 0 else 'format repair'), dict(received='invalid')
        call = dict(id=f'call_{i}', name=stage + ('' if i == 0 else '_repair' if i == 1 else '_repair2'),
            status='received', repair_of=calls[-1]['id'] if calls else None,
            request_sha256=json_sha(request), response_sha256=json_sha(response), submitted_at=str(i))
        folder = tmp_path/'calls'/call['id']
        for name, value in [('request', request), ('response', response),
                ('protocol_failure', dict(error='bad field', repair_limit=repairs))]:
            write_json(folder/(name+'.json'), value)
        calls.append(call)
    state = SimpleNamespace(output=tmp_path, data=dict(calls=calls), input_lock=dict(configuration=dict(creative_audio=True)))
    state._reload = lambda: None
    state.set_artifact = lambda *args: None
    return state


def fine_fixture(tmp_path, monkeypatch):
    from omni_story.library import opencode_provider
    value = story_finecut.StoryFinecut.__new__(story_finecut.StoryFinecut)
    value.base = tmp_path/'skill_finecut'; value.base.mkdir(parents=True)
    video = value.base/'render_0.mp4'; video.write_bytes(b'already rendered')
    source = dict(path=str(video), sha256=audio_render.sha256_file(video), duration_s=1, audio_stream_index=1)
    value.auth = dict(parent=source, reference=source, target_duration_s=1)
    value.story_context = context()
    value.reference, value.render_source = context()['reference'], source
    value.observed = dict(limitations=[])
    value.knowledge_files, value.initial_request_count, value.knowledge = [], 0, ''
    value.full = dict(path=str(video))
    value.fine_context = lambda *args: context()
    value.status = lambda *args, **kwargs: None
    value.source_scope = lambda *args: {}
    value.plan_prompt = lambda *args, **kwargs: 'plan with local revision contract'
    value.inspect = lambda *args: None
    value.adaptive = True
    value.state = failed_calls(tmp_path, story_finecut.PREFIX+'revise')
    class MCP: pass
    monkeypatch.setattr(opencode_provider, 'OpenCodeMCP', MCP)
    value.mcp = MCP(); value.mcp.package_root = tmp_path; value.mcp.executable = 'fake'; value.mcp.exclusions = []
    monkeypatch.setattr(story_finecut, 'task_usage', lambda *args: dict(requests=3))
    monkeypatch.setattr(story_finecut, 'usage_bindings', lambda *args: {})
    monkeypatch.setattr(story_finecut.trial, 'command', lambda *args: None)
    monkeypatch.setattr(story_finecut, 'probe_media', lambda *args: dict(duration_s=1))
    write_json(value.base/'render_input.json', dict(compiled=dict(plan=dict(limitations=[]))))
    selected = dict(rendered_path=str(video), sha256=source['sha256'], duration_s=1,
        manifest_path=str(value.base/'render_result.json'), provenance=[])
    review = dict(blind=dict(status='partial', problems=[], limitations=['Blind has no audio']),
        target=dict(status='partial', problems=[dict(description='Cut too early')], limitations=['minor issue'],
            next_action='revise', revision_reason='Cut too early', missing_core_meaning=[]))
    return value, selected, review


def test_b2_exhausted_fine_revision_delivers_reviewed_render_partial(tmp_path, monkeypatch):
    value, selected, review = fine_fixture(tmp_path, monkeypatch)
    seen = []
    def call(stage, *args, **kwargs):
        seen.append(stage)
        if stage.endswith('revise'):
            raise ValueError('model_protocol_repair_exhausted:'+stage)
        return dict(story=[], inspect=[], limitations=[]) if stage.endswith('observe') else dict(segments=[], limitations=[])
    value.call = call
    value.render = lambda plan, version: selected if version == 0 else pytest.fail('No render after exhaustion')
    value.review = lambda *args: deepcopy(review)
    def audio(*args, **kwargs):
        seen.append('audio')
        assert kwargs['story_context']['partial'] is True
        return dict(status='delivered', final_video=selected['rendered_path'], final_sha256=selected['sha256'],
            content_delivery_ready=True, technical_delivery_verified=True, audio_measurement=dict(passed=True))
    monkeypatch.setattr(visual_audio, 'execute', audio)
    result = value.run()
    assert result['status'] == 'completed_partial' and result['partial'] is True
    assert result['partial_reasons'] == ['fine_revision_exhausted', 'Cut too early']
    assert result['selected_render'] == selected and Path(result['final_video']).read_bytes() == b'already rendered'
    assert seen == [story_finecut.PREFIX+'observe', story_finecut.PREFIX+'plan', story_finecut.PREFIX+'revise', 'audio']
    assert result['joint_quality_gate'] is False


def test_b3_exhausted_rough_timing_keeps_candidate_and_drops_unfitted_lines(tmp_path, monkeypatch):
    state = failed_calls(tmp_path, 'rough_audio_timing_1')
    story = dict(context('narration'), audio_draft=[dict(id='fits', text='keep'), dict(id='long', text='never truncate')],
        timing_problems=[dict(line_id='long', reason='tts_exceeds_window', actual_duration_s=2, available_s=1)])
    calls = []
    def revise(*args, **kwargs):
        calls.append(kwargs['stage'])
        raise ValueError('model_protocol_repair_exhausted:'+kwargs['stage'])
    monkeypatch.setattr(visual_audio, 'revise_draft_timing', revise)
    result = clean_chain.revise_rough_audio_timing(story, state, None,
        dict(path='rendered.mp4', kind='complete_file', source_sha256='a'*64, source_start_s=0, source_end_s=1), tmp_path)
    assert result['partial'] and result['timing_revision_exhausted']
    assert result['timing_problems'] == story['timing_problems']
    assert story_context.mixable_draft(result) == [story['audio_draft'][0]]
    assert result['dropped_lines'][0]['id'] == 'long'
    assert result['dropped_lines'][0]['reason'] == 'tts_exceeds_window'
    assert calls == ['rough_audio_timing_1'] and result['audio_draft'][1]['text'] == 'never truncate'


@pytest.mark.parametrize('change', ['pass', 'fine', 'ready', 'technical', 'measurement', 'partial'])
def test_f1_joint_true_exactly_for_full_pass(tmp_path, monkeypatch, change):
    assert callable(story_finecut.fine_audio_joint)
    value, selected, review = fine_fixture(tmp_path, monkeypatch)
    review['target']['next_action'] = 'deliver' if change != 'fine' else 'revise'
    review['partial'] = change == 'partial'
    audio = dict(status='delivered', content_delivery_ready=change != 'ready',
        technical_delivery_verified=change != 'technical', audio_measurement=dict(passed=change != 'measurement'),
        final_video=selected['rendered_path'], final_sha256=selected['sha256'], joint_quality_gate=True)
    monkeypatch.setattr(visual_audio, 'execute', lambda *args, **kwargs: audio)
    result = value.finish(selected, review)
    assert result['joint_quality_gate'] is (change == 'pass')
    if change == 'pass': assert result['status'] == 'completed'
    if change == 'partial': assert result['status'] == 'completed_partial'


@pytest.mark.parametrize('outcome', ['delivered', 'partial', 'stopped'])
def test_d_worker_always_writes_run_outcome(tmp_path, monkeypatch, outcome):
    video = tmp_path/'final.mp4'; video.write_bytes(b'rendered')
    def run(args):
        if outcome == 'stopped':
            # A real inner omni_story frame, including code location, is retained.
            contracts.validate_audio_draft(None, [], speech_mode='none')
        return dict(status='completed_partial' if outcome == 'partial' else 'completed',
            final_video=str(video), joint_quality_gate=outcome == 'delivered', partial=outcome == 'partial',
            partial_reasons=['fine_revision_exhausted'] if outcome == 'partial' else [],
            audio_measurement=dict(passed=True), usage=dict(requests=2, cost_estimate=.25))
    monkeypatch.setattr(server_cli, '_run_task', run, raising=False)
    if outcome == 'stopped':
        with pytest.raises(ValueError): server_cli._run(SimpleNamespace(output=tmp_path))
    else:
        server_cli._run(SimpleNamespace(output=tmp_path))
    result = json.loads((tmp_path/'run_outcome.json').read_text())
    assert result['outcome'] == outcome
    assert result['final_video'] == (None if outcome == 'stopped' else str(video))
    if outcome == 'stopped':
        assert result['exception_type'] == 'ValueError'
        assert 'contracts.py:' in result['raised_at'] and result['raised_at'].rsplit(':', 1)[1].isdigit()
        assert result['error'] == 'plan/audio_draft:list_required'
        assert all(key in result for key in ['stage', 'last_call_id', 'protocol_failure'])


def test_b1_audio_contract_shared_by_prompt_validator_and_feedback():
    from omni_story.library.protocol_feedback import diagnose
    contract = contracts.plan_field_contract('mixed')
    line = dict(id='a', text='角色必须亲口说话', voice='Ethan', mode='narration', segment_ids=['s'])
    with pytest.raises(ValueError, match='reference_dialogue_form_missing') as caught:
        contracts.validate_reference_dialogue_form([line], 'mixed')
    detail = caught.value.violation
    assert detail['path'] == '$.audio_draft' and detail['expected'] == contract['dialogue']['expected']
    feedback = diagnose('plan_0', dict(audio_draft=[line], segments=[dict(segment_id='s')]),
        dict(stage='plan', speech_mode='mixed', validation_error=str(caught.value), violation=detail))
    assert feedback[0]['expected'] == contract['dialogue']['expected']
    assert not any(item['path'] == '$.theme' for item in feedback)
    with pytest.raises(ValueError) as caught:
        contracts.validate_audio_draft([dict(line, audio_relation='on_screen')], [dict(segment_id='s')])
    assert caught.value.violation['path'] == '$.audio_draft[0].audio_relation'
    assert caught.value.violation['expected'] == contract['audio_relation']


def audio_runner_fixture(tmp_path, monkeypatch, *, silent, dropped=False):
    from omni_story.library import audio_usage
    value = visual_audio.VisualAudio.__new__(visual_audio.VisualAudio)
    value.root, value.duration = tmp_path, 1
    video = tmp_path/'picture.mp4'; video.write_bytes(b'picture')
    ref = tmp_path/'reference.mp4'; ref.write_bytes(b'reference')
    value.video = dict(path=str(video), sha256=audio_render.sha256_file(video))
    value.reference = dict(path=str(ref), sha256=audio_render.sha256_file(ref))
    value.story_context = dict(context(), received_tts=[], timing_problems=[])
    if dropped:
        value.story_context.update(partial=True, partial_reasons=['rough_audio_timing_exhausted'],
            dropped_lines=[dict(id='long', reason='tts_exceeds_window')],
            audio_draft=[dict(id='long', text='never truncate', voice='Ethan')],
            received_tts=[dict(id='long', audio_path=str(video))],
            timing_problems=[dict(line_id='long', reason='tts_exceeds_window')])
    value.music, value.music_sha256, value.tts, value.mcp = None, None, None, None
    value.tts_cache_dir = tmp_path
    value.state = SimpleNamespace(input_lock=dict(configuration={}), _reload=lambda: None, set_artifact=lambda *args: None)
    value.status = lambda *args, **kwargs: None
    monkeypatch.setattr(story_context, 'synthesize_draft', lambda story, *args, **kwargs: deepcopy(story))
    monkeypatch.setattr(audio_usage, 'task_usage', lambda *args: dict(requests=0))
    monkeypatch.setattr(audio_usage, 'usage_bindings', lambda *args: {})
    measurement = measured(monkeypatch, silent=silent)
    def mix(source, lines, output, **kwargs):
        assert not lines, 'Dropped speech must never be passed to the mixer'
        output.write_bytes(b'mixed candidate')
        receipt = output.with_suffix('.receipt.json')
        record = dict(path=str(output), sha256=audio_render.sha256_file(output), fully_decoded=True,
            receipt_path=str(receipt), audio_artifacts={}, audio_measurement=measurement)
        write_json(receipt, record)
        return record
    monkeypatch.setattr(audio_render, 'mix_speech', mix)
    return value


def test_s2_silent_candidate_reaches_non_delivered_fine_branch(tmp_path, monkeypatch):
    value = audio_runner_fixture(tmp_path, monkeypatch, silent=True)
    value.review = lambda *args: pytest.fail('Silent video must not reach Qwen')
    result = value.run()
    assert result['status'] == 'needs_audio_revision'
    assert not result['content_delivery_ready'] and not result['joint_quality_gate']
    assert 'audio_measured_silent' in result['reason_codes']
    assert json.loads((tmp_path/'audio_result.json').read_text())['audio_measurement'] == result['audio_measurement']
    fine, selected, review = fine_fixture(tmp_path/'fine', monkeypatch)
    review['target']['next_action'] = 'deliver'
    monkeypatch.setattr(visual_audio, 'execute', lambda *args, **kwargs: result)
    stopped = fine.finish(selected, review)
    assert stopped['status'] == 'needs_audio_revision' and 'final_video' not in stopped
    assert stopped['creative_audio']['audio_measurement']['passed'] is False


def test_b3_dropped_rough_lines_mix_remaining_audio_and_finish_partial(tmp_path, monkeypatch):
    value = audio_runner_fixture(tmp_path, monkeypatch, silent=False, dropped=True)
    calls = []
    value.review = lambda *args: calls.append('one AV review') or verdict()
    monkeypatch.setattr(visual_audio, 'revise_draft_timing', lambda *args, **kwargs: pytest.fail('No second timing request'))
    result = value.run()
    assert result['status'] == 'delivered' and result['partial'] and not result['joint_quality_gate']
    assert result['dropped_lines'] == [dict(id='long', reason='tts_exceeds_window')]
    assert result['performed_story_context']['audio_draft'] == []
    assert result['performed_story_context']['planned_audio_draft'][0]['text'] == 'never truncate'
    assert calls == ['one AV review']
    fine, selected, review = fine_fixture(tmp_path/'fine', monkeypatch)
    review['target']['next_action'] = 'deliver'
    monkeypatch.setattr(visual_audio, 'execute', lambda *args, **kwargs: result)
    final = fine.finish(selected, review)
    assert final['status'] == 'completed_partial' and final['partial']


def test_s2_mix_receipt_contains_measurement_for_stems_and_mux(tmp_path, monkeypatch):
    measurement = measured(monkeypatch)
    source, output = tmp_path/'source.mp4', tmp_path/'mux.mp4'
    source.write_bytes(b'source'); output.write_bytes(b'mux')
    info = dict(duration_s=1, width=16, height=16, fps='25', start_s=0, probe={})
    monkeypatch.setattr(audio_render, '_verify_video', lambda *args, **kwargs: info)
    original_samples = audio_render._samples
    monkeypatch.setattr(audio_render, '_samples', lambda path: original_samples('final' if path == str(output) else path))
    record = audio_render._receipt(output, info, source, 'mix_speech',
        music_path='music', lines=[dict(id='a', start_s=0, actual_end_s=1)],
        audio_artifacts={key: dict(path=value) for key, value in
            [('mix', 'mix'), ('music_ducked', 'music'), ('voice', 'voice')]})
    # The decoded mux is named by its actual path in production.
    assert record['audio_measurement']['mix'] == measurement['mix']
    assert 'audio_measurement' in json.loads(Path(record['receipt_path']).read_text())


def test_b3_dropped_lines_are_not_synthesized_or_scheduled(tmp_path):
    story = dict(context('narration'), duration_s=1,
        segments=[dict(id='s', start_s=0, end_s=1, purpose='picture')],
        audio_draft=[dict(id='long', text='do not send again', voice='Ethan', mode='narration',
            segment_ids=['s'], start_s=0, end_s=1)],
        dropped_lines=[dict(id='long', reason='tts_exceeds_window')])
    story['reference'].update(reference_sha256='a'*64, theme='theme', intended_takeaway='takeaway')
    result = story_context.synthesize_draft(story, tmp_path,
        tts=lambda *args, **kwargs: pytest.fail('Dropped line must not buy TTS'))
    assert result['received_tts'] == [] and result['timing_problems'] == []
    assert result['audio_draft'] == story['audio_draft']


def test_d_exhausted_plan_stops_with_exact_call_and_traceback(tmp_path, monkeypatch):
    state = failed_calls(tmp_path, 'plan_0')
    write_json(tmp_path/'library_state.json', state.data)
    def run(args):
        contracts.require(False, 'model_protocol_repair_exhausted:plan_0')
    monkeypatch.setattr(server_cli, '_run_task', run, raising=False)
    with pytest.raises(ValueError, match='model_protocol_repair_exhausted:plan_0'):
        server_cli._run(SimpleNamespace(output=tmp_path))
    outcome = json.loads((tmp_path/'run_outcome.json').read_text())
    assert outcome['outcome'] == 'stopped' and outcome['stage'] == 'plan_0'
    assert outcome['last_call_id'] == 'call_2' and outcome['repairs'] == 2
    assert outcome['protocol_failure'] == str(tmp_path/'calls/call_2/protocol_failure.json')
    assert outcome['raised_at'].split(':')[-1].isdigit()
    assert not (tmp_path/'partial_delivery.json').exists()


def test_d_exhausted_review_delivers_existing_render_without_model_call(tmp_path, monkeypatch):
    state = failed_calls(tmp_path, story_finecut.PREFIX+'review')
    write_json(tmp_path/'library_state.json', state.data)
    video = tmp_path/'fine.mp4'; video.write_bytes(b'best existing render')
    write_json(tmp_path/'skill_finecut/render_1/render_result.json',
        dict(rendered_path=str(video), sha256=audio_render.sha256_file(video)))
    def run(args):
        contracts.require(False, 'model_protocol_repair_exhausted:'+story_finecut.PREFIX+'review')
    monkeypatch.setattr(server_cli, '_run_task', run, raising=False)
    result = server_cli._run(SimpleNamespace(output=tmp_path))
    assert result['status'] == 'completed_partial' and result['final_video'] == str(video)
    outcome = json.loads((tmp_path/'run_outcome.json').read_text())
    assert outcome['outcome'] == 'partial' and outcome['partial_reasons'] == [
        'model_protocol_repair_exhausted:'+story_finecut.PREFIX+'review']
    assert len(json.loads((tmp_path/'library_state.json').read_text())['calls']) == 3


@pytest.mark.parametrize('field', ['cast', 'selected_range', 'caption'])
def test_b1_evidence_rejection_names_field_and_allowed_values(field):
    window = dict(window_id='w', source_id='film', source_sha256='a'*64, status='watched',
        source_start_s=0, source_end_s=4)
    window['observation'] = dict(window_id='w', source_id='film', roles=[], uncertainties=[],
        events=[dict(local_start_s=0, local_end_s=1, observed_fact='first', role_ids=[]),
                dict(local_start_s=2, local_end_s=3, observed_fact='second', role_ids=[])],
        usable_ranges=[dict(local_in_s=0, local_out_s=1, event_indices=[0], role_ids=[], camera='first', continuity_notes='visible'),
            dict(local_in_s=2, local_out_s=3, event_indices=[1], role_ids=[], camera='second', continuity_notes='visible')])
    segment = dict(segment_id='s', slot_id='slot', source_id='film', window_id='w',
        source_in_s=0, source_out_s=1, role_ids=[], speed=1)
    plan = dict(reference_sha256='ref', focus_role_id=None, segments=[segment], fps=25,
        slots=[dict(slot_id='slot', intended_takeaway='theme', segment_ids=['s'])])
    if field == 'selected_range': segment['source_out_s'] = 3
    elif field == 'caption':
        segment['caption'] = dict(text='new caption', start_s=0, end_s=1, position='bottom', font_size=24,
            evidence=[dict(window_id='w', event_indices=[1])])
    else:
        window['observation']['roles'] = [dict(role_id='A', entity_id='a'*64+':observed', identity_confirmed=True,
            state='visible', identity_evidence='picture')]
        plan['cast'] = [dict(cast_id='c', entity_id='a'*64+':invented', bindings=[dict(window_id='w', role_id='A')])]
    codes = dict(cast='plan:cast_entity_not_supported_by_observation',
        selected_range='plan:range_not_supported_by_fine_observation', caption='plan:caption_event_outside_selected_range')
    with pytest.raises(ValueError, match=codes[field]) as caught:
        contracts.validate_plan(plan, dict(sources=[dict(source_id='film', sha256='a'*64, duration_s=4)]),
            [window], 'ref', allow_unfocused=True)
    violation = caught.value.violation
    assert violation['path'].startswith('$.cast' if field == 'cast' else '$.segments[0]')
    assert violation['repair_hint'] == contracts.plan_field_contract()[field]['repair_hint']
    if field == 'cast': assert violation['expected']['entity_id'] == 'a'*64+':observed'
    if field == 'caption': assert violation['expected']['allowed'] == [0]
    if field == 'selected_range': assert len(violation['expected']['allowed']) == 2


def test_d_rough_audio_ready_counts_as_delivered(tmp_path):
    from omni_story.library.run_outcome import write_run_outcome
    write_run_outcome(tmp_path, dict(status='rough_audio_ready', content_delivery_ready=True,
                                     final_video=str(tmp_path / 'final.mp4')))
    saved = json.loads((tmp_path / 'run_outcome.json').read_text())
    assert saved['outcome'] == 'delivered' and saved['final_video'].endswith('final.mp4')


def test_d_partial_lookup_failure_keeps_original_exhaustion_error(tmp_path, monkeypatch):
    from omni_story.library import run_outcome
    def run(args):
        raise ValueError('model_protocol_repair_exhausted:plan_1')
    def broken(output, error):
        raise ValueError('run_outcome_invalid_record:render_result.json')
    monkeypatch.setattr(server_cli, '_run_task', run, raising=False)
    monkeypatch.setattr(run_outcome, 'partial_after_exhaustion', broken)
    with pytest.raises(ValueError, match='repair_exhausted:plan_1'):
        server_cli._run(SimpleNamespace(output=tmp_path))
    saved = json.loads((tmp_path / 'run_outcome.json').read_text())
    assert saved['outcome'] == 'stopped' and saved['error'] == 'model_protocol_repair_exhausted:plan_1'


def test_b3_stop_after_rough_subtitles_only_mixable_lines():
    import inspect
    from omni_story.library import clean_chain
    assert 'subtitle_final_video(settled_video, mixable_draft(final_story)' in inspect.getsource(clean_chain)
