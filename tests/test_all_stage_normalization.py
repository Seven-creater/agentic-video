from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from omni_story.library import contracts, editing, protocol_feedback, semantic_audit, slot_finecut_contracts
from omni_story.library import active_finecut, audio_contracts, audio_segments, visual_story_trial
from omni_story.library.story_finecut import StoryFinecut
from omni_story.library.state import LibraryState, LibraryStopped, json_sha, write_json
from omni_story.library.media import sha256_file
from test_protocol_feedback import SHA, feedback, harness


DURATION = 40.93


CREATIVE = dict(provider='official_vision_mcp_in_opencode', creative_audio=True)


def creative_flow():
    flow = StoryFinecut.__new__(StoryFinecut)
    flow.state = SimpleNamespace(input_lock=dict(configuration=CREATIVE))
    return flow


def creative_mcp(mcp):
    mcp.state.input_lock['configuration'].update(CREATIVE)
    return mcp


def review(start=38, end=47):
    return dict(status='partial', apparent_story='看到行动', limitations=[],
                problems=[dict(start_s=start, end_s=end, description='末尾行动太快')])



@pytest.mark.parametrize('blind', [False, True])
def test_fine_review_clamps_actual_output_and_records_each_change(blind):
    value = review(-2)
    if blind:
        value['facts'] = [dict(start_s=38, end_s=47, description='动作完成')]
    creative_flow().review_check(value, DURATION, blind=blind)
    assert (value['problems'][0]['start_s'], value['problems'][0]['end_s']) == (0, DURATION)
    assert dict(path='$.problems[0].end_s', original=47, normalized=DURATION) in value['normalization']
    if blind:
        assert value['facts'][0]['end_s'] == DURATION
    before = deepcopy(value)
    creative_flow().review_check(value, DURATION, blind=blind)
    assert value == before



@pytest.mark.parametrize('start,end', [(45, 47), (3, 2), (2, 2), (-3, -1)])
def test_empty_optional_review_intervals_are_dropped(start, end):
    value = review(start, end)
    original = deepcopy(value['problems'][0])
    creative_flow().review_check(value, DURATION)
    assert value['problems'] == []
    assert value['normalization'] == [dict(path='$.problems[0]', original=original, dropped=True)]



def test_required_blind_evidence_rejects_only_after_all_rows_drop():
    value = dict(observed_story='行动', apparent_theme='关系', main_characters=[], confusions=[],
        evidence=[dict(start_s=45, end_s=47, observed_fact='动作')])
    with pytest.raises(ValueError, match='blind/evidence:list_required:empty_after_normalization'):
        contracts.validate_blind_reading(value, DURATION, normalize=True)
    assert value['evidence'] == [] and value['normalization'][0]['dropped'] is True



def fine():
    window = dict(window_id='w', source_id='movie', source_start_s=100, source_end_s=100 + DURATION)
    value = dict(window_id='w', source_id='movie', roles=[], uncertainties=[],
        events=[dict(local_start_s=-2, local_end_s=47, observed_fact='实际动作', role_ids=[])],
        usable_ranges=[dict(local_in_s=-1, local_out_s=47, event_indices=[0], role_ids=[],
                            continuity_notes='连续')])
    return value, window



def test_fine_local_event_and_usable_range_clamp_to_window_not_source_clock():
    value, window = fine()
    contracts.validate_fine(value, window, normalize=True)
    duration = window['source_end_s'] - window['source_start_s']
    assert value['events'][0]['local_start_s'] == value['usable_ranges'][0]['local_in_s'] == 0
    assert value['events'][0]['local_end_s'] == value['usable_ranges'][0]['local_out_s'] == duration
    assert len(value['normalization']) == 4
    before = deepcopy(value)
    contracts.validate_fine(value, window, normalize=True)
    assert value == before



def test_dropping_events_keeps_surviving_index_identity_and_rejects_removed_reference():
    value, window = fine()
    value['events'].insert(0, dict(local_start_s=45, local_end_s=47, observed_fact='越界', role_ids=[]))
    value['usable_ranges'][0]['event_indices'] = [1]
    value['affordances'] = [dict(event_indices=[1])]
    contracts.validate_fine(value, window, normalize=True)
    assert len(value['events']) == 1
    assert value['usable_ranges'][0]['event_indices'] == value['affordances'][0]['event_indices'] == [0]
    assert any(item['path'] == '$.affordances[0].event_indices' for item in value['normalization'])
    before = deepcopy(value)
    contracts.validate_fine(value, window, normalize=True)
    assert value == before
    value, window = fine()
    value['events'][0].update(local_start_s=45, local_end_s=47)
    with pytest.raises(ValueError, match='fine:unknown_event_index'):
        contracts.validate_fine(value, window, normalize=True)



@pytest.mark.parametrize('invalid', [True, '47', None, float('nan'), float('inf')])
def test_observed_numbers_stay_strict_with_indexed_violation(invalid):
    value = review()
    value['problems'][0]['end_s'] = invalid
    with pytest.raises(ValueError, match='^finite_number_out_of_range$') as caught:
        creative_flow().review_check(value, DURATION)
    assert caught.value.violation['path'] == 'problems[0].end_s'
    assert caught.value.violation['expected'] == '0 ≤ x ≤ 40.93'



def test_numeric_helpers_keep_codes_and_attach_precise_bounds():
    with pytest.raises(ValueError, match='^finite_number_out_of_range$') as caught:
        visual_story_trial.number(47, 0, DURATION, 'problems[2].end_s')
    assert caught.value.violation == dict(path='problems[2].end_s', value=47, expected='0 ≤ x ≤ 40.93')
    with pytest.raises(ValueError, match='^fine/event:invalid_or_out_of_bounds$') as caught:
        contracts._interval(0, 47, DURATION, 'fine/event', json_path='$.events[2]',
                            fields=('local_start_s', 'local_end_s'))
    assert caught.value.violation == dict(path='$.events[2].local_end_s', value=47, expected='0 ≤ x ≤ 40.93')
    with pytest.raises(ValueError, match='^field:finite_number_required$') as caught:
        contracts.number(-1, 'field')
    assert caught.value.violation['path'] == 'field' and '0 ≤ x' in caught.value.violation['expected']



@pytest.mark.parametrize('field,invalid,code,expected', [
    ('speed', 3, 'plan:speed_out_of_range', '0.5 ≤ x ≤ 2'),
    ('freeze_tail_s', 11, 'plan:freeze_tail_exceeds_10_seconds', '0 ≤ x ≤ 10')])
def test_strict_cut_transform_bounds_also_have_precise_violations(field, invalid, code, expected):
    observation, window = fine()
    contracts.validate_fine(observation, window, normalize=True)
    window.update(source_sha256=SHA, status='watched', observation=observation)
    value = dict(reference_sha256=SHA, segments=[dict(segment_id='seg', slot_id='slot',
        source_id='movie', window_id='w', role_ids=[], source_in_s=100, source_out_s=110,
        speed=1, freeze_tail_s=0, look='none', framing='fit')], audio_mode='silent',
        slots=[dict(slot_id='slot', intended_takeaway='画面', segment_ids=['seg'])],
        width=640, height=360, fps=30, limitations=[])
    value['segments'][0][field] = invalid
    before = deepcopy(value)
    with pytest.raises(ValueError, match='^' + code + '$') as caught:
        contracts.validate_plan(value, dict(sources=[dict(source_id='movie', sha256=SHA, duration_s=200)]),
                                [window], SHA, allow_unfocused=True)
    assert caught.value.violation == dict(path=f'$.segments[0].{field}', value=invalid, expected=expected)
    assert value == before
    assert any(item['path'] == f'$.segments[0].{field}' and item['expected'] == expected for item in
               protocol_feedback.diagnose('plan', value, dict(validation_error=caught.value)))



def test_unknown_stage_scans_all_nested_seconds_and_merges_exception_without_mutation():
    value = dict(groups=[dict(local_in_s=-2, end_s=47, offset_s=float('inf'),
                              step_s=float('nan'), valid_s=1, other=1000)])
    before = deepcopy(value)
    violation = dict(path='$.groups[0].end_s', value=47, expected='0 ≤ x ≤ 40.93')
    found = protocol_feedback.diagnose('unknown_future_stage', value,
        dict(media_duration_s=DURATION, duration_s=999, violation=violation, validation_error='bad'))
    assert {row['path'] for row in found} == {
        '$.groups[0].local_in_s', '$.groups[0].end_s', '$.groups[0].offset_s', '$.groups[0].step_s'}
    assert all(row['expected'] == '0 ≤ x ≤ 40.93' for row in found)
    assert all('不要向上取整' in row['hint'] for row in found)
    assert json.dumps(value) == json.dumps(before)
    assert all(len(row['value']) <= 200 for row in found)



@pytest.mark.parametrize('bound', ['scope', 'lineage', 'probe'])
def test_pipeline_unknown_stage_feedback_uses_sent_media_not_reply_duration(tmp_path, monkeypatch, bound):
    from omni_story.library import pipeline
    value = dict(end_s=47, duration_s=999)
    mcp, media, sent = harness(tmp_path, monkeypatch, [value, dict(end_s=1)])
    scope = dict(kind='continuous_window', source_sha256=SHA, source_start_s=100, source_end_s=140.93)
    if bound == 'lineage':
        write_json(media.parent / 'lineage.json', scope)
    monkeypatch.setattr(pipeline, 'probe_media', lambda path: dict(duration_s=DURATION))
    def validate(value):
        if value['end_s'] > DURATION:
            raise ValueError('unknown_time_error')
    assert mcp.call('unknown_stage', 'prompt', media, validate,
                    scope=scope if bound == 'scope' else None) == dict(end_s=1)
    found = feedback(sent[1])['violations']
    assert any(row['path'] == '$.end_s' and row['value'] == '47' and
               row['expected'].startswith('0 ≤ x ≤ 40.93') for row in found)



@pytest.mark.parametrize('channel', ['configuration', 'audio_subtask'])
@pytest.mark.parametrize('saved_kind', ['raw', 'normalized', 'legacy_clamped'])
def test_fine_review_revalidation_keeps_paid_parsed_bytes_and_detects_tamper(tmp_path, monkeypatch, saved_kind, channel):
    raw = review()
    mcp, media, sent = harness(tmp_path, monkeypatch, [raw])
    if channel == 'configuration':
        creative_mcp(mcp)
    else:
        mcp.state.normalize_observation_times = True
    validate = lambda value: creative_flow().review_check(value, DURATION)
    result = mcp.call('chain_e2e_v1_fine_review', 'prompt', media, validate)
    path = mcp.output / 'calls' / mcp.state.data['calls'][0]['id'] / 'parsed.json'
    assert json.loads(path.read_text()) == raw
    reply = json.loads(path.with_name('response.json').read_text())
    assert contracts.parse_model_json(reply['result']['content'][0]['text']) == raw
    saved = deepcopy(raw if saved_kind == 'raw' else result)
    if saved_kind == 'legacy_clamped':
        del saved['normalization']
    write_json(path, saved)
    before = path.read_bytes()
    assert mcp.call('chain_e2e_v1_fine_review', 'prompt', media, validate) == result
    assert path.read_bytes() == before and len(sent) == 1
    saved = json.loads(path.read_text())
    saved['apparent_story'] = 'changed'
    write_json(path, saved)
    with pytest.raises(LibraryStopped, match='recorded_parsed_reply_modified'):
        mcp.call('chain_e2e_v1_fine_review', 'prompt', media, validate)



def test_observation_clamping_does_not_relax_inspection_requests_or_frame_ranges():
    auth = dict(parent=dict(duration_s=DURATION))
    query = dict(target='parent', start_s=38, end_s=47, question='看动作', step_s=.2)
    with pytest.raises(ValueError, match='finite_number_out_of_range') as caught:
        visual_story_trial.inspect_check([query], auth)
    assert caught.value.violation['path'] == 'inspect[0].end_s' and query['end_s'] == 47
    value = dict(facts=[dict(start_s=100, end_s=147, description='动作', basis='picture')],
                 frame_ranges=[dict(start_s=100, end_s=147)], uncertainties=[])
    with pytest.raises(ValueError, match='finite_number_out_of_range') as caught:
        StoryFinecut.facts_check(value, 100, 100 + DURATION, normalize=True)
    assert caught.value.violation['path'] == 'frame_ranges[0].end_s'
    assert value['facts'][0]['end_s'] == 100 + DURATION
    assert value['frame_ranges'][0]['end_s'] == 147



def semantic_observation():
    segment = dict(segment_id='seg', source_id='movie', source_in_s=100, source_out_s=140.93)
    proxy = dict(source_id='movie', source_sha256=SHA, sha256='b' * 64,
                 source_start_s=100, source_end_s=140.93, duration_s=DURATION)
    value = dict(protocol=semantic_audit.SEMANTIC_PROTOCOL, **segment, source_sha256=SHA,
        proxy_sha256=proxy['sha256'], observed_duration_s=DURATION, characters=[], uncertainties=[],
        evidence=[dict(evidence_id='e', kind='visual_action', local_start_s=-1, local_end_s=47,
                       description='动作', basis_evidence_ids=[], character_ids=[])])
    return value, segment, proxy



def test_all_stage_scan_stays_bounded():
    found = protocol_feedback.diagnose('unknown', dict(rows=[dict(end_s=47)] * 100), dict(media_duration_s=DURATION))
    assert len(found) == 30
    assert all(row['path'].startswith('$.rows[') for row in found)



def test_pipeline_preserves_nonfinite_number_code_and_serializable_violation(tmp_path, monkeypatch):
    mcp, media, sent = harness(tmp_path, monkeypatch, [dict(end_s=float('inf')), dict(end_s=1)])
    def validate(value):
        visual_story_trial.number(value['end_s'], 0, DURATION, 'end_s')
    scope = dict(kind='continuous_window', source_sha256=SHA, source_start_s=0, source_end_s=DURATION)
    assert mcp.call('future', 'prompt', media, validate, scope=scope) == dict(end_s=1)
    record = feedback(sent[1])
    assert record['validation_error'] == 'finite_number_out_of_range'
    assert any(row['path'].endswith('end_s') and row['expected'] == '0 ≤ x ≤ 40.93'
               for row in record['violations'])
    failure = mcp.output / 'calls' / mcp.state.data['calls'][0]['id'] / 'protocol_failure.json'
    assert json.loads(failure.read_text())['violation']['value'] == 'inf'



def test_full_parent_observation_and_zero_origin_inspection_normalize_local_facts():
    value = dict(story=[dict(id='beat', contribution='信息')], inspect=[],
                 facts=[dict(start_s=-1, end_s=47)])
    visual_story_trial.observe_check(value, dict(parent=dict(duration_s=DURATION)), normalize=True)
    assert value['facts'][0] == dict(start_s=0, end_s=DURATION)
    facts = dict(facts=[dict(start_s=-1, end_s=47, description='动作', basis='picture')], uncertainties=[])
    StoryFinecut.facts_check(facts, 0, DURATION, normalize=True)
    assert facts['facts'][0]['end_s'] == DURATION and facts['normalization']



@pytest.mark.parametrize('start', [38, 45])
def test_final_canvas_review_normalizes_sent_proxy_observations(tmp_path, monkeypatch, start):
    from omni_story.library import final_visual_review
    video = tmp_path / 'video.bin'
    video.write_bytes(b'local fixture, never decoded')
    digest = sha256_file(video)
    media = dict(path=str(video), sha256=digest, source_sha256=digest, kind='continuous_window',
                 source_start_s=0, source_end_s=DURATION, spec=dict(roi=None))
    write_json(tmp_path / 'lineage.json', media)
    monkeypatch.setattr(final_visual_review, 'probe_media', lambda path: dict(duration_s=DURATION))
    monkeypatch.setattr(final_visual_review, 'proxy', lambda *args, **kwargs: media)
    write_json(tmp_path / 'calls' / 'call' / 'request.json', {})
    write_json(tmp_path / 'calls' / 'call' / 'response.json', {})
    state = SimpleNamespace(output=tmp_path, data=dict(calls=[]), _reload=lambda: None)
    def call(stage, prompt, path, validate, **options):
        value = dict(status='needs_revision', **{key: key != 'framing_ok' for key in final_visual_review.CHECKS},
            issues=[dict(start_s=start, end_s=47, description='主体偏小')], visual_notes=['实际构图'], limitations=[])
        validate(value)
        state.data['calls'].append(dict(id='call', name=stage, status='received'))
        return value
    result = final_visual_review.review_final_layout(video, tmp_path / 'review', SimpleNamespace(call=call, state=state), normalize=True)
    assert result['normalization']
    assert result['issues'][0]['end_s'] == DURATION if start == 38 else result['issues'] == []



@pytest.mark.parametrize('saved_kind', ['raw', 'normalized', 'legacy_clamped'])
def test_opencode_parent_revalidates_normalized_review_without_paid_history_changes(tmp_path, saved_kind):
    from omni_story.library.opencode_provider import OpenCodeMCP
    media = tmp_path / 'media.bin'
    media.write_bytes(b'local fixture, never decoded')
    mcp = OpenCodeMCP.__new__(OpenCodeMCP)
    mcp.state = LibraryState(tmp_path / 'current', dict(configuration=CREATIVE))
    scope = dict(kind='continuous_window', source_sha256=SHA, source_start_s=0, source_end_s=DURATION)
    request = dict(tool='analyze_video', media_sha256=sha256_file(media), observation_scope=scope,
        provider=mcp.provider, policy_version='fixture', arguments=dict(video_source=str(media), prompt='original'))
    raw = review()
    validate = lambda value: creative_flow().review_check(value, DURATION)
    saved = deepcopy(raw)
    if saved_kind != 'raw':
        validate(saved)
        if saved_kind == 'legacy_clamped':
            del saved['normalization']
    reply = dict(result=dict(content=[dict(type='text', text=json.dumps(raw))]))
    name = 'fine_' + 'a' * 16
    call = dict(id='call', name=name, status='received', request_sha256=json_sha(request),
                response_sha256=json_sha(reply))
    folder = tmp_path / 'parent' / 'calls' / 'call'
    for key, value in [('request', request), ('response', reply), ('parsed', saved)]:
        write_json(folder / (key + '.json'), value)
    ledger = tmp_path / 'parent' / 'library_state.json'
    write_json(ledger, dict(task_id='parent', calls=[call]))
    mcp.parent_baseline = dict(linked_ledgers=[dict(path=str(ledger), sha256=sha256_file(ledger))])
    before = {path: path.read_bytes() for path in folder.iterdir()}
    result = mcp._parent_observation(name, request, scope, validate)
    assert result['problems'][0]['end_s'] == DURATION
    assert {path: path.read_bytes() for path in folder.iterdir()} == before
    assert mcp.state.data['calls'] == []
    saved['apparent_story'] = 'tampered'
    write_json(folder / 'parsed.json', saved)
    with pytest.raises(LibraryStopped, match='parent_observation_parsed_changed'):
        mcp._parent_observation(name, request, scope, validate)



@pytest.mark.parametrize('creative', [False, True])
def test_shared_audio_observation_requires_explicit_opt_in(creative):
    value = dict(actors=[], limitations=[], events=[dict(id='event', start_s=0, end_s=47,
        visible='实际画面', actor_ids=[], speech_mode='none', speech_actor_id=None)])
    before = deepcopy(value)
    if creative:
        audio_contracts.validate_observation(value, DURATION, normalize=True)
        assert value['events'][0]['end_s'] == DURATION and value['normalization']
    else:
        with pytest.raises(ValueError, match=r'observation.events\[0\].time_range') as caught:
            audio_contracts.validate_observation(value, DURATION)
        assert caught.value.violation['path'].endswith('end_s') and value == before


@pytest.mark.parametrize('lane', ['fine', 'slot', 'semantic', 'economy', 'editing', 'craft', 'audio', 'review'])
def test_legacy_observation_lanes_keep_raw_bounds_and_codes(lane):
    value, window = fine()
    value['events'][0]['local_start_s'] = value['usable_ranges'][0]['local_in_s'] = 0
    if lane == 'fine':
        validate = lambda: contracts.validate_fine(value, window)
        code = 'fine/event:invalid_or_out_of_bounds'
    elif lane == 'slot':
        parent = dict(baseline_id='parent', sha256=SHA, duration_s=200)
        slot = dict(slot_id='slot', start_s=100, end_s=140.93)
        value = dict(baseline_id='parent', parent_sha256=SHA, slot_id='slot', slot_start_s=100,
            slot_end_s=140.93, time_domain='slot_local_output', limitations=[], uncertainties=[],
            evidence=[dict(evidence_id='fact', start_s=0, end_s=47, observed_fact='动作',kind='action',basis='visual')])
        validate = lambda: slot_finecut_contracts.validate_facts(value,parent,slot)
        code = 'slot_finecut/fact:outside_bound_interval'
    elif lane == 'semantic':
        value, segment, proxy = semantic_observation()
        value['evidence'][0]['local_start_s'] = 0
        validate = lambda: semantic_audit.validate_segment_observation(value,segment,SHA,proxy)
        code = 'semantic/evidence:outside_observed_slice'
    elif lane == 'economy':
        rendered = dict(sha256=SHA, measured_duration_s=DURATION,
                        provenance=[dict(segment_id='seg', output_in_s=0, output_out_s=DURATION)])
        value = dict(video_sha256=SHA,economy_status='partial',narrative_readability='pass',limitations=[],
            segment_checks=[dict(segment_id='seg',status='uncertain',reason='快',
                output_evidence=[dict(start_s=0,end_s=47,observed_fact='动作')])])
        validate = lambda: active_finecut.validate_economy_review(value,rendered)
        code = 'finecut:evidence_outside_output_segment'
    elif lane == 'editing':
        contracts.validate_fine(value,window,normalize=True)
        value.pop('normalization')
        value['editing_observations']=[dict(method_id='m',local_start_s=0,local_end_s=47)]
        validate=lambda: editing.validate_fine_editing(value,window,dict(methods=[dict(method_id='m')]))
        code='fine_editing:range_outside_window'
    elif lane == 'craft':
        from omni_story.library import reference_craft
        value=dict(reference_sha256=SHA,visual_takeaway='动作',coverage=[dict(start_s=0,end_s=47)])
        validate=lambda: reference_craft.validate_selection(value,dict(sha256=SHA,duration_s=DURATION),{})
        code='source_range_out_of_bounds'
    elif lane == 'audio':
        from omni_story.library.visual_audio import review_check
        value=dict(status='partial',issues=[dict(start_s=0,end_s=47,description='快')],limitations=[])
        validate=lambda: review_check(value,DURATION)
        code='audio_review_actual_interval_required'
    else:
        value=review()
        validate=lambda: StoryFinecut.__new__(StoryFinecut).review_check(value,DURATION)
        code='finite_number_out_of_range'
    before=deepcopy(value)
    with pytest.raises(ValueError) as caught:
        validate()
    assert str(caught.value)==code
    assert caught.value.violation['value']==47 and value==before


@pytest.mark.parametrize('configuration,enabled', [({},False),
    ({'creative_audio':True},False), ({'provider':'official_vision_mcp_in_opencode'},False),
    ({'provider':'other_official_vision_mcp_in_opencode','creative_audio':True},False), (CREATIVE,True)])
def test_channel_selection_is_explicit(configuration,enabled):
    assert contracts.creative_time_normalization(configuration) is enabled


def test_legacy_trial_observe_keeps_its_original_acceptance_without_mutation():
    value=dict(story=[dict(id='beat',contribution='动作')],facts=[dict(start_s=0,end_s=47)],inspect=[])
    before=deepcopy(value)
    visual_story_trial.observe_check(value,dict(parent=dict(duration_s=DURATION)))
    assert value==before


def test_legacy_numeric_overflow_keeps_exception_and_message():
    for validate in (lambda: contracts.number(10**400,'field'),
                     lambda: visual_story_trial.number(10**400,0,DURATION,'end_s')):
        with pytest.raises(OverflowError,match='int too large to convert to float') as caught:
            validate()
        assert caught.value.violation['value']==10**400


def test_legacy_microcut_retains_segment_error_without_normalizing():
    from omni_story.library.goal_feedback_continuation import _validate_economy
    manifest = dict(sha256=SHA, measured_duration_s=DURATION,
                    provenance=[dict(segment_id='seg', output_in_s=0, output_out_s=DURATION)])
    value = dict(video_sha256=SHA, economy_status='partial', narrative_readability='pass', limitations=[],
        segment_checks=[dict(segment_id='seg', status='uncertain', reason='快',
            output_evidence=[dict(start_s=0,end_s=1,observed_fact='动作')])],
        microcut_checks=[dict(segment_id='seg',status='necessary',reason='动作',
            necessary_intervals=[dict(start_s=0,end_s=47,observed_fact='动作')],removable_intervals=[])])
    before=deepcopy(value)
    with pytest.raises(ValueError,match='^goal:microcut_evidence_outside_segment$') as caught:
        _validate_economy(value,manifest)
    assert caught.value.violation['value']==47 and value==before


def test_creative_review_accepts_plain_method_names_and_clamps_problems():
    from omni_story.library import visual_story_trial as trial
    value = dict(apparent_story='s', status='partial', next_action='revise',
                 editing_methods_used=['省略与跳切', '加速压缩过渡'],
                 problems=[dict(start_s=52, end_s=66, description='a'), dict(start_s=71, end_s=72.8, description='b')])
    trial.review_check(value, 63.67, normalize=True)
    assert value['editing_methods_used'] == ['省略与跳切', '加速压缩过渡']
    assert value['problems'] == [dict(start_s=52, end_s=63.67, description='a')]


def test_creative_review_timed_method_objects_still_checked():
    import pytest
    from omni_story.library import visual_story_trial as trial
    value = dict(apparent_story='s', status='pass', problems=[],
                 editing_methods_used=[dict(operation='跳切', output_start_s='x', output_end_s=3)])
    with pytest.raises(ValueError):
        trial.review_check(value, 10, normalize=True)


def test_non_object_problem_carries_violation():
    import pytest
    from omni_story.library import visual_story_trial as trial
    with pytest.raises(ValueError, match='review_time_evidence_object_required') as caught:
        trial.review_check(dict(apparent_story='s', status='fail', problems=['坏']), 10, normalize=True)
    assert getattr(caught.value, 'violation', None)
