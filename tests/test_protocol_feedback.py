import base64
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from omni_story.library import contracts, pipeline, protocol_feedback
from omni_story.library.media import sha256_file
from omni_story.library.state import LibraryState, LibraryStopped, json_sha, write_json


SHA = 'a' * 64
MARKER = '\n上次输出未通过本地协议校验。只修复JSON字段、ID和时间域，不得补造画面证据。'


def reference():
    return dict(reference_sha256=SHA, theme='陪伴', intended_takeaway='珍惜陪伴', viewer_takeaway='彼此关心',
        visible_evidence=[dict(start_s=0, end_s=1, observed_fact='两人微笑', supports='亲近')],
        editing_methods=[dict(start_s=0, end_s=1, method='停留', function='感受陪伴', visual_evidence='同框')],
        value_judgment=dict(core_message='珍惜陪伴', stance='抒情', what_is_upheld='陪伴', what_is_rejected='无',
            evidence=[dict(start_s=0, end_s=1, basis='visible', note='微笑')]), cultural_context=[], uncertainties=[])


@pytest.mark.parametrize('duration,end', [(40.84, 41), (40.84, 45), (40.84, 41.5), (400, 410)])
def test_all_annotation_groups_clamp_and_record_each_change(duration, end):
    value = reference()
    value['strategy'] = dict(visual_form=dict(evidence=[dict(start_s=-2, end_s=end)]))
    groups = [value['visible_evidence'], value['editing_methods'], value['value_judgment']['evidence'],
              value['strategy']['visual_form']['evidence']]
    for group in groups:
        group[0].update(start_s=-2, end_s=end)
    contracts.validate_reference(value, SHA, duration, require_value_fields=True)
    assert all((group[0]['start_s'], group[0]['end_s']) == (0, duration) for group in groups)
    assert len(value['normalization']) == 8
    assert {entry['original'] for entry in value['normalization']} == {-2, end}
    assert all(set(entry) == {'path', 'original', 'normalized'} for entry in value['normalization'])
    before = deepcopy(value)
    contracts.validate_reference(value, SHA, duration)
    assert value == before


@pytest.mark.parametrize('key', ['visible_evidence', 'editing_methods', 'value_judgment'])
def test_drop_invalid_ranges_and_reject_required_list_only_when_empty(key):
    value = reference()
    group = value[key]['evidence'] if key == 'value_judgment' else value[key]
    dropped = dict(group[0], start_s=41, end_s=45)
    group.append(dropped)
    contracts.validate_reference(value, SHA, 40.84)
    assert len(group) == 1
    assert value['normalization'][-1]['original'] == dropped
    assert value['normalization'][-1]['dropped'] is True
    group[0].update(start_s=41, end_s=45)
    with pytest.raises(ValueError, match='list_required:empty_after_normalization'):
        contracts.validate_reference(value, SHA, 40.84)
    assert group == []


@pytest.mark.parametrize('field', ['start_s', 'end_s'])
@pytest.mark.parametrize('invalid', [True, False, '41', None, float('nan'), float('inf'), -float('inf')])
def test_annotation_numbers_remain_strict(field, invalid):
    value = reference()
    value['visible_evidence'][0][field] = invalid
    with pytest.raises(ValueError, match='finite_number_required'):
        contracts.validate_reference(value, SHA, 40.84)


@pytest.mark.parametrize('strategy', [None, [], 'invalid', 42])
@pytest.mark.parametrize('nested', [False, True])
def test_reference_strategy_type_always_produces_protocol_error(strategy, nested):
    value = reference()
    value['strategy'] = strategy
    if nested:
        value['reference'] = dict(strategy={})
    with pytest.raises(ValueError, match='^reference/strategy:object_required$'):
        contracts.validate_reference(value, SHA, 40.84)


@pytest.mark.parametrize('start,end', [(-2, 12), (10, 21), (21, 22)])
def test_cut_decisions_remain_strict_and_are_never_normalized(start, end):
    window = dict(window_id='w1', source_id='movie', source_sha256=SHA, source_start_s=10,
                  source_end_s=20, status='watched')
    window['observation'] = dict(window_id='w1', source_id='movie', roles=[], events=[],
        usable_ranges=[dict(local_in_s=0, local_out_s=10, role_ids=[], event_indices=[],
                            camera='环境', continuity_notes='连续')], uncertainties=[])
    catalog = dict(sources=[dict(source_id='movie', sha256=SHA, duration_s=40.84)])
    value = dict(reference_sha256=SHA, segments=[dict(segment_id='seg', slot_id='slot',
        source_id='movie', window_id='w1', role_ids=[], source_in_s=start, source_out_s=end,
        speed=1, look='none', framing='fit')], slots=[dict(slot_id='slot', intended_takeaway='环境',
        segment_ids=['seg'])], audio_mode='silent', width=640, height=360, fps=30, limitations=[])
    before = deepcopy(value)
    with pytest.raises(ValueError, match='finite_number_required|segment_outside_watched_window'):
        contracts.validate_plan(value, catalog, [window], SHA, allow_unfocused=True)
    assert value == before


def plan():
    return dict(segments=[dict(segment_id='seg_6', slot_id='slot', window_id='w1', role_ids=['A'],
                              source_in_s=10, source_out_s=12, speed=1),
                          dict(segment_id='seg_7', slot_id='slot', window_id='w1', role_ids=['B'],
                              source_in_s=12, source_out_s=14, speed=1)],
        slots=[dict(slot_id='slot', segment_ids=['seg_6', 'seg_7'])],
        audio_draft=[dict(id='aud_6', text='你好', voice='Ethan', mode='dialogue',
                         speaker=dict(window_id='w1', role_id='B'), segment_ids=['seg_6'])])


def test_missing_audio_draft_has_precise_feedback_for_mixed_speech():
    value = plan()
    del value['audio_draft']
    found = protocol_feedback.diagnose('plan', value, dict(speech_mode='mixed'))
    assert len(found) == 1 and found[0]['path'] == '$.audio_draft'
    assert '不用原声' in found[0]['hint'] and 'segment_ids' in found[0]['hint']


def test_dialogue_feedback_names_line_and_actual_visible_segments():
    found = protocol_feedback.diagnose('plan', plan(), {})
    assert len(found) == 1
    assert all(word in found[0]['hint'] for word in ('aud_6', 'B', 'seg_7', 'narration', 'identity_anchor_segment_id'))
    assert found[0]['expected']['visible_segment_ids'] == ['seg_7']


def test_three_errors_are_collected_without_mutating_reply():
    value = plan()
    value['audio_draft'][0]['voice'] = 'invented'
    value['segments'][0]['source_out_s'] = 45
    before = deepcopy(value)
    found = protocol_feedback.diagnose('plan', value,
        dict(windows={'w1': dict(window_id='w1', source_start_s=10, source_end_s=20)}))
    assert len(found) == 3
    assert any('aud_6' in item['hint'] and 'seg_7' in item['hint'] for item in found)
    assert any('[10, 20]' in item['hint'] for item in found)
    assert any(item['path'].endswith('.voice') for item in found)
    assert value == before


def test_slot_membership_caption_and_audio_fields_are_diagnosed_together():
    value = plan()
    value['fps'] = 30
    value['slots'][0]['segment_ids'] = ['seg_7']
    value['segments'][0]['caption'] = dict(start_s=0, end_s=3)
    value['audio_draft'][0].update(text='长' * 601, segment_ids=['seg_7', 'seg_6'])
    found = protocol_feedback.diagnose('plan', value, {})
    paths = {item['path'] for item in found}
    assert {'$.segments[0].slot_id', '$.segments[0].caption', '$.audio_draft[0].text',
            '$.audio_draft[0].segment_ids'} <= paths
    assert all(len(item['value']) <= 200 for item in found)


def test_reference_feedback_covers_independent_fields_after_normalization():
    value = reference()
    value.update(reference_sha256='changed', theme='', strategy=dict(speech_mode='bad'),
        cultural_context=[dict(claim='', kind='bad', confidence='bad', supports='')])
    value['visible_evidence'][0]['end_s'] = 45
    value['editing_methods'][0]['function'] = ''
    value['value_judgment']['evidence'][0]['basis'] = 'audio'
    before = deepcopy(value)
    found = protocol_feedback.diagnose('reference', value,
        dict(reference_sha=SHA, duration_s=40.84, require_value_fields=True))
    paths = {item['path'] for item in found}
    assert {'$.reference_sha256', '$.theme', '$.strategy.speech_mode', '$.editing_methods[0].function',
        '$.value_judgment.evidence[0].basis', '$.cultural_context[0].kind',
        '$.cultural_context[0].confidence'} <= paths
    assert '$.visible_evidence[0].end_s' in paths
    assert not any(path == '$.visible_evidence[0]' for path in paths)
    assert value == before


def test_diagnostics_are_bounded_and_unknown_errors_never_return_empty():
    found = protocol_feedback.diagnose('reference', dict(visible_evidence=[{}] * 100), {})
    assert len(found) == 30
    found = protocol_feedback.diagnose('other', {}, dict(validation_error='other:original_error'))
    assert found == [dict(path='$', value='{}',
        expected='other:original_error: valid local protocol fields, IDs and time domains',
        hint='按validation_error核对必填字段、类型、枚举、ID及时间域；保留实际证据，不补造画面。')]


def harness(tmp_path, monkeypatch, replies, *, provider=pipeline.CodexMCP.provider):
    media = tmp_path / 'media.bin'
    media.write_bytes(b'fixture, never decoded')
    state = LibraryState(tmp_path / 'run', dict(configuration=dict(provider=provider)))
    mcp = pipeline.CodexMCP.__new__(pipeline.CodexMCP)
    mcp.provider = provider
    mcp.state, mcp.output = state, state.output
    sent = []

    def submit(name, request, *, repair_of=None):
        state._reload()
        existing = next((row for row in state.data['calls'] if row['request_sha256'] == json_sha(request)), None)
        if existing:
            if existing['status'] != 'received':
                raise LibraryStopped('recorded_request_not_received_no_replay:' + existing['id'])
            return pipeline.CodexMCP._submit(mcp, name, request, repair_of=repair_of)
        call, _ = state.begin_call(name, request, repair_of=repair_of)
        sent.append(deepcopy(request))
        value = replies.pop(0)
        if isinstance(value, Exception):
            state.fail_call(call, str(value), uncertain=True)
            raise value
        result_text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        reply = dict(result=dict(content=[dict(type='text', text=result_text)]))
        state.complete_call(call, reply)
        return call, reply

    monkeypatch.setattr(mcp, '_submit', submit)
    return mcp, media, sent


def validate_fields(value):
    if not value.get('first'):
        raise ValueError('first:text_required')
    if not value.get('second'):
        raise ValueError('second:text_required')


def feedback(request):
    return json.loads(request['arguments']['prompt'].split(MARKER, 1)[1])


def test_second_repair_succeeds_and_reports_new_error_with_first_repair_reply(tmp_path, monkeypatch):
    mcp, media, sent = harness(tmp_path, monkeypatch, [{}, dict(first=True), dict(first=True, second=True)],
                             provider='official_vision_mcp_in_opencode')
    value = mcp.call('stage', 'prompt', media, validate_fields)
    assert value == dict(first=True, second=True)
    assert [row['name'] for row in mcp.state.data['calls']] == ['stage', 'stage_repair', 'stage_repair2']
    assert feedback(sent[1])['validation_error'] == 'first:text_required'
    assert feedback(sent[2])['validation_error'] == 'second:text_required'
    assert json.loads(feedback(sent[2])['previous_response']) == dict(first=True)
    assert all(feedback(request)['violations'] for request in sent[1:])
    assert mcp.call('stage', 'changed prompt', media, validate_fields) == value
    assert len(sent) == 3


def test_two_failed_repairs_keep_exhaustion_error(tmp_path, monkeypatch):
    mcp, media, sent = harness(tmp_path, monkeypatch, [{}, {}, {}],
                             provider='official_vision_mcp_in_opencode')
    with pytest.raises(ValueError, match='^model_protocol_repair_exhausted:stage$'):
        mcp.call('stage', 'prompt', media, validate_fields)
    assert len(sent) == 3


@pytest.mark.parametrize('provider', ['official_vision_mcp_in_codex', 'other_official_vision_mcp_in_opencode'])
@pytest.mark.parametrize('stage', ['sf_0_outline', 'opencode_stage', 'chain_e2e_v1_rough_blind'])
def test_other_lanes_stop_after_first_repair_without_recording_or_sending_second(
        tmp_path, monkeypatch, provider, stage):
    mcp, media, sent = harness(tmp_path, monkeypatch, [{}, {}, dict(first=True, second=True)], provider=provider)
    for _ in range(2):
        with pytest.raises(ValueError, match='^model_protocol_repair_exhausted:' + stage + '$'):
            mcp.call(stage, 'prompt', media, validate_fields)
        assert [row['name'] for row in mcp.state.data['calls']] == [stage, stage + '_repair']
        assert all(row['status'] == 'received' for row in mcp.state.data['calls'])
        assert len(sent) == mcp.state.data['request_count'] == 2
    for call in mcp.state.data['calls']:
        failure = json.loads((mcp.output / 'calls' / call['id'] / 'protocol_failure.json').read_text())
        assert failure['repair_limit'] == 1


def test_missing_audio_then_invisible_speaker_are_repaired_with_all_feedback(tmp_path, monkeypatch):
    original, corrected = plan(), plan()
    del original['audio_draft']
    corrected['audio_draft'][0]['segment_ids'] = ['seg_7']
    mcp, media, sent = harness(tmp_path, monkeypatch, [original, plan(), corrected],
                             provider='official_vision_mcp_in_opencode')

    def validator(value):
        contracts.validate_audio_draft(value.get('audio_draft'), value['segments'], speech_mode='mixed')
        contracts.validate_dialogue_speakers(value['audio_draft'], value['segments'])

    validator.protocol_context = dict(stage='plan', speech_mode='mixed')
    assert mcp.call('plan', 'prompt', media, validator) == corrected
    assert feedback(sent[1])['validation_error'] == 'plan/audio_draft:list_required'
    assert feedback(sent[2])['validation_error'] == 'plan:dialogue_speaker_not_in_selected_segments'
    assert any(item['path'] == '$.audio_draft' for item in feedback(sent[1])['violations'])
    assert any('aud_6' in item['hint'] and 'seg_7' in item['hint'] for item in feedback(sent[2])['violations'])


@pytest.mark.parametrize('status', ['submitted', 'uncertain'])
@pytest.mark.parametrize('provider', ['official_vision_mcp_in_codex', 'official_vision_mcp_in_opencode'])
def test_unsettled_repair_is_never_replayed_or_followed_by_repair2(tmp_path, monkeypatch, status, provider):
    mcp, media, sent = harness(tmp_path, monkeypatch, [{}, LibraryStopped('transport_unknown')], provider=provider)
    with pytest.raises(LibraryStopped, match='transport_unknown'):
        mcp.call('stage', 'prompt', media, validate_fields)
    mcp.state._reload()
    mcp.state.data['calls'][-1]['status'] = status
    mcp.state._save()
    with pytest.raises(LibraryStopped, match='no_replay'):
        mcp.call('stage', 'prompt', media, validate_fields)
    assert len(sent) == 2 and len(mcp.state.data['calls']) == 2


def test_transport_failure_never_enters_format_repair(tmp_path, monkeypatch):
    mcp, media, sent = harness(tmp_path, monkeypatch, [LibraryStopped('transport_failed')])
    with pytest.raises(LibraryStopped, match='transport_failed'):
        mcp.call('stage', 'prompt', media, validate_fields)
    assert len(sent) == 1


def test_nested_reference_with_invalid_top_level_strategy_enters_repair(tmp_path, monkeypatch):
    invalid = dict(reference(), reference=dict(strategy={}), strategy=[])
    mcp, media, sent = harness(tmp_path, monkeypatch, [invalid, reference()])
    result = mcp.call('reference', 'prompt', media,
                      lambda value: contracts.validate_reference(value, SHA, 40.84))
    assert result == reference() and len(sent) == 2
    assert feedback(sent[1])['validation_error'] == 'reference/strategy:object_required'


def test_parse_failures_get_two_repairs_and_nonempty_violations(tmp_path, monkeypatch):
    mcp, media, sent = harness(tmp_path, monkeypatch, ['not json', '{', dict(first=True, second=True)],
                             provider='official_vision_mcp_in_opencode')
    assert mcp.call('stage', 'prompt', media, validate_fields) == dict(first=True, second=True)
    assert all(feedback(row)['violations'] for row in sent[1:])


@pytest.mark.parametrize('valid', [False, True])
@pytest.mark.parametrize('provider', ['official_vision_mcp_in_codex', 'official_vision_mcp_in_opencode'])
def test_old_one_repair_run_keeps_exact_hash_and_stopping_boundary(tmp_path, monkeypatch, valid, provider):
    mcp, media, sent = harness(tmp_path, monkeypatch, [], provider=provider)
    original = dict(tool='analyze_video', arguments=dict(video_source=str(media), prompt='old prompt'),
        media_sha256=sha256_file(media), provider=mcp.provider, policy_version=pipeline.prompts.POLICY_VERSION)
    parent = None
    for name, request, value in [('stage', original, {}),
            ('stage_repair', dict(original, arguments=dict(original['arguments'], prompt='old prompt' + MARKER +
                json.dumps(dict(validation_error='old:error', previous_response='{}')))),
             dict(first=True, second=True) if valid else {})]:
        call, folder = mcp.state.begin_call(name, request, repair_of=parent)
        reply = dict(result=dict(content=[dict(type='text', text=json.dumps(value))]))
        mcp.state.complete_call(call, reply)
        write_json(folder / ('parsed.json' if valid and parent else 'protocol_failure.json'),
                   value if valid and parent else dict(error='old:error', attempt=int(parent is not None)))
        parent = call
    before = deepcopy(mcp.state.data['calls'])
    if valid:
        assert mcp.call('stage', 'new prompt', media, validate_fields) == dict(first=True, second=True)
    else:
        with pytest.raises(ValueError, match='^model_protocol_repair_exhausted:stage$'):
            mcp.call('stage', 'new prompt', media, validate_fields)
    assert mcp.state.data['calls'] == before and sent == []


@pytest.mark.parametrize('saved_kind', ['raw', 'normalized', 'legacy_clamped'])
def test_cached_reference_accepts_raw_or_previously_normalized_parsed_reply(tmp_path, monkeypatch, saved_kind):
    raw = reference()
    raw['visible_evidence'][0]['end_s'] = 41
    mcp, media, sent = harness(tmp_path, monkeypatch, [raw])
    validator = lambda value: contracts.validate_reference(value, SHA, 40.84)
    result = mcp.call('reference', 'prompt', media, validator)
    path = mcp.output / 'calls' / mcp.state.data['calls'][0]['id'] / 'parsed.json'
    assert json.loads(path.read_text()) == raw
    if saved_kind == 'raw':
        write_json(path, raw)
    if saved_kind != 'raw':
        saved = deepcopy(result)
        if saved_kind == 'legacy_clamped':
            del saved['normalization']
        write_json(path, saved)
    before = path.read_bytes()
    assert mcp.call('reference', 'prompt', media, validator) == result
    assert path.read_bytes() == before and len(sent) == 1
    tampered = json.loads(path.read_text())
    tampered['theme'] = 'edited'
    write_json(path, tampered)
    with pytest.raises(LibraryStopped, match='recorded_parsed_reply_modified'):
        mcp.call('reference', 'prompt', media, validator)


@pytest.mark.parametrize('group', ['visible_evidence', 'editing_methods', 'value_judgment'])
def test_old_submillisecond_evidence_reports_contract_change_without_tampering_or_new_call(
        tmp_path, monkeypatch, group):
    raw = reference()
    evidence = raw[group]['evidence'] if group == 'value_judgment' else raw[group]
    evidence[0].update(start_s=40.84, end_s=40.8405)
    assert contracts._interval(40.84, 40.8405, 40.84, 'legacy') == (40.84, 40.8405)
    mcp, media, sent = harness(tmp_path, monkeypatch, [])
    request = dict(tool='analyze_video', arguments=dict(video_source=str(media), prompt='old prompt'),
        media_sha256=sha256_file(media), provider=mcp.provider, policy_version=pipeline.prompts.POLICY_VERSION)
    call, folder = mcp.state.begin_call('reference', request)
    mcp.state.complete_call(call, dict(result=dict(content=[dict(type='text', text=json.dumps(raw))])))
    write_json(folder / 'parsed.json', raw)
    before = {path: path.read_bytes() for path in folder.iterdir()}
    calls = deepcopy(mcp.state.data['calls'])
    with pytest.raises(LibraryStopped, match='^recorded_reply_incompatible_with_current_contract:.*'
            'empty_after_normalization$') as caught:
        mcp.call('reference', 'new prompt', media,
                 lambda value: contracts.validate_reference(value, SHA, 40.84))
    assert 'recorded_parsed_reply_modified' not in str(caught.value)
    assert {path: path.read_bytes() for path in folder.iterdir()} == before
    assert sent == [] and mcp.state.data['calls'] == calls


@pytest.mark.parametrize('parent_as_dict', [False, True])
def test_state_accepts_only_two_immediate_named_repairs(tmp_path, parent_as_dict):
    state = LibraryState(tmp_path / 'run', dict(configuration={}))
    calls = []
    for index, name in enumerate(('stage', 'stage_repair', 'stage_repair2')):
        parent = calls[-1] if calls else None
        if parent and not parent_as_dict:
            parent = parent['id']
        call, _ = state.begin_call(name, dict(attempt=index), repair_of=parent)
        state.complete_call(call, {})
        calls.append(call)
    assert [call['repair_of'] for call in calls] == [None, calls[0]['id'], calls[1]['id']]


@pytest.mark.parametrize('depth,name,parent_index', [
    (0, 'stage_repair2', 0), (0, 'stage', 0), (0, 'other_repair', 0),
    (1, 'stage', 1), (1, 'stage_repair2', 0), (1, 'stage_repair', 1),
    (1, 'stage_repair_repair', 1), (2, 'stage_repair3', 2),
    (2, 'stage_repair2', 2), (2, 'stage', 2)])
def test_state_rejects_skipped_unsuffixed_misbound_or_third_repairs(tmp_path, depth, name, parent_index):
    state = LibraryState(tmp_path / 'run', dict(configuration={}))
    calls = []
    for index, stage in enumerate(('stage', 'stage_repair', 'stage_repair2')[:depth + 1]):
        call, _ = state.begin_call(stage, dict(attempt=index), repair_of=calls[-1] if calls else None)
        state.complete_call(call, {})
        calls.append(call)
    before = deepcopy(state.data)
    with pytest.raises(LibraryStopped, match='^format_repair_limit_exhausted$'):
        state.begin_call(name, dict(attempt='invalid'), repair_of=calls[parent_index])
    assert state.data == before


@pytest.mark.parametrize('depth', [0, 1])
def test_state_rejects_nonimmediate_repair(tmp_path, depth):
    state = LibraryState(tmp_path / 'run', dict(configuration={}))
    parent = None
    for index, name in enumerate(('stage', 'stage_repair')[:depth + 1]):
        parent, _ = state.begin_call(name, dict(attempt=index), repair_of=parent)
        state.complete_call(parent, {})
    other, _ = state.begin_call('other', dict(attempt='other'))
    state.complete_call(other, {})
    with pytest.raises(LibraryStopped, match='^format_repair_limit_exhausted$'):
        state.begin_call('stage_repair2' if depth else 'stage_repair', dict(attempt='invalid'), repair_of=parent)


def test_server_chain_stage_names_and_dependencies_include_second_repair():
    from omni_story.library import server_chain_authorization as authorization
    assert authorization.allowed_stage('chain_e2e_v1_fine_plan_repair2')
    assert authorization.stage_dependencies('chain_e2e_v1_fine_plan_repair2') == ('chain_e2e_v1_fine_observe',)
    assert not authorization.allowed_stage('chain_e2e_v1_fine_plan_repair3')


def server_chain_calls(tmp_path):
    old_calls = []
    reply = dict(result=dict(content=[dict(type='text', text='{}')]))
    for index in range(35):
        request = dict(media_sha256='old', observation_scope=dict(source_sha256='old'),
                       arguments=dict(prompt=f'old {index}'))
        call = dict(id=f'old_{index}', name='plan_1' if index == 31 else f'old_{index}',
            status='received', repair_of=None, request_sha256=json_sha(request), response_sha256=json_sha(reply))
        folder = tmp_path / 'calls' / call['id']
        write_json(folder / 'request.json', request)
        write_json(folder / 'response.json', reply)
        if index == 31:
            write_json(folder / 'parsed.json', {})
        old_calls.append(call)
    stage = 'chain_e2e_v1_rough_blind'
    calls = []
    for index, suffix in enumerate(('', '_repair', '_repair2')):
        request = dict(media_sha256=SHA, observation_scope=dict(source_sha256=SHA),
            arguments=dict(prompt='original' + (MARKER + str(index) if index else '')))
        call = dict(id=f'new_{index}', name=stage + suffix, status='received',
                    repair_of=calls[-1]['id'] if index else None,
                    request_sha256=json_sha(request), response_sha256=json_sha(reply))
        folder = tmp_path / 'calls' / call['id']
        write_json(folder / 'request.json', request)
        write_json(folder / 'response.json', reply)
        if index == 2:
            write_json(folder / 'parsed.json', {})
        calls.append(call)
    return old_calls, calls


def test_server_chain_accepts_only_immediate_bound_second_repair(tmp_path):
    from omni_story.library import server_chain_authorization as authorization
    old_calls, calls = server_chain_calls(tmp_path)
    authorization._new_calls(tmp_path, dict(calls=old_calls + calls), dict(calls=old_calls))
    calls[-1]['repair_of'] = calls[0]['id']
    with pytest.raises(LibraryStopped, match='extra_or_unbound_repair'):
        authorization._new_calls(tmp_path, dict(calls=old_calls + calls), dict(calls=old_calls))


@pytest.mark.parametrize('change', ['unsuffixed_first', 'unsuffixed_second', 'unsuffixed_chain',
    'skip_first', 'wrong_parent', 'third', 'nested_suffix', 'unsettled_parent', 'changed_input',
    'changed_prompt', 'changed_parent_record', 'nonimmediate'])
def test_server_chain_rejects_other_repair_chains(tmp_path, change):
    from omni_story.library import server_chain_authorization as authorization
    old_calls, calls = server_chain_calls(tmp_path)
    change_repair_chain(tmp_path, calls, change)
    with pytest.raises(LibraryStopped):
        authorization._new_calls(tmp_path, dict(calls=old_calls + calls), dict(calls=old_calls))


def change_repair_chain(root, calls, change):
    stage = calls[0]['name']
    if change == 'unsuffixed_first':
        calls[1]['name'] = stage
    elif change == 'unsuffixed_second':
        calls[2]['name'] = stage
    elif change == 'unsuffixed_chain':
        for call in calls:
            call['name'] = stage
    elif change == 'skip_first':
        calls.pop(1)
        calls[-1]['repair_of'] = calls[0]['id']
    elif change == 'wrong_parent':
        calls[-1]['repair_of'] = calls[0]['id']
    elif change == 'third':
        call = dict(calls[-1], id='new_3', name=stage + '_repair3', repair_of=calls[-1]['id'])
        request = json.loads((root / 'calls' / calls[-1]['id'] / 'request.json').read_text())
        request['arguments']['prompt'] += 'third'
        call['request_sha256'] = json_sha(request)
        write_json(root / 'calls' / call['id'] / 'request.json', request)
        write_json(root / 'calls' / call['id'] / 'response.json',
                   dict(result=dict(content=[dict(type='text', text='{}')])))
        calls.append(call)
    elif change == 'nested_suffix':
        calls[-1]['name'] = stage + '_repair_repair2'
    elif change == 'unsettled_parent':
        calls[1]['status'] = 'uncertain'
    elif change in {'changed_input', 'changed_prompt'}:
        path = root / 'calls' / calls[-1]['id'] / 'request.json'
        request = json.loads(path.read_text())
        if change == 'changed_input':
            request['media_sha256'] = 'b' * 64
        else:
            request['arguments']['prompt'] = 'changed'
        write_json(path, request)
        calls[-1]['request_sha256'] = json_sha(request)
    elif change == 'changed_parent_record':
        write_json(root / 'calls' / calls[1]['id'] / 'response.json', {})
    elif change == 'nonimmediate':
        request = json.loads((root / 'calls' / calls[1]['id'] / 'request.json').read_text())
        request['arguments']['prompt'] = 'intervening'
        call = dict(calls[1], id='intervening', repair_of=None, request_sha256=json_sha(request),
                    name='chain_e2e_v1_rough_review' if stage.startswith('chain_') else 'coarse_zoom_search')
        write_json(root / 'calls' / call['id'] / 'request.json', request)
        write_json(root / 'calls' / call['id'] / 'response.json',
                   dict(result=dict(content=[dict(type='text', text='{}')])))
        write_json(root / 'calls' / call['id'] / 'parsed.json', {})
        write_json(root / 'calls' / calls[1]['id'] / 'parsed.json', {})
        calls.insert(2, call)


def node(code):
    if not shutil.which('node'):
        pytest.skip('Node required for actual native POST guard')
    return subprocess.run(['node', '--input-type=module', '-e', code],
                          capture_output=True, text=True, timeout=15)


def node_guard_case(root, chain):
    media = root / 'media.jpg'
    media.write_bytes(b'local image fixture, never decoded')
    old, calls = server_chain_calls(root)
    if not chain:
        old = []
    for index, call in enumerate(calls):
        if not chain:
            call['name'] = 'reference' + ('_repair2' if index == 2 else '_repair' if index else '')
        request = dict(provider='official_vision_mcp_in_opencode', policy_version='fixture',
            tool='analyze_image', media_sha256=sha256_file(media),
            arguments=dict(image_source=str(media), prompt='original' + (MARKER + str(index) if index else '')))
        call['request_sha256'] = json_sha(request)
        write_json(root / 'calls' / call['id'] / 'request.json', request)
    calls[-1]['status'] = 'submitted'
    configuration = dict(provider=request['provider'], progress_policy='finite_stages_no_retry_v1',
        transport='opencode_cli_official_mcp_v1', vision_model='glm-5.3-flash',
        agent_model='zhipuai-coding-plan/glm-5.3-flash')
    state = dict(task_id='fixture', input_lock=dict(configuration=configuration), policy_version='fixture',
                 max_requests=None, calls=old + calls, request_count=len(old + calls), artifacts={})
    if chain:
        register_node_chain(root, state, old, media)
    return state, calls


def register_node_chain(root, state, old, media, *, repairs_per_stage=2):
    baseline = dict(state, calls=old, request_count=35, artifacts={})
    baseline_path = root / 'baseline.json'
    write_json(baseline_path, baseline)
    catalog = dict(sources=[])
    ref = dict(path=str(media), sha256=sha256_file(media), duration_s=40.84)
    windows = [dict(window_id=f'w{index}') for index in range(12)]
    write_json(root / 'catalog/inventory.json', catalog)
    write_json(root / 'reference_catalog/inventory.json', dict(sources=[ref]))
    write_json(root / 'watched_windows.json', windows)
    write_json(root / 'draft_plan_1.json', {})
    (root / 'mcp_http.jsonl').write_bytes(b'local historical HTTP fixture\n')
    old_files = sorted(str(path) for call in old for path in (root / 'calls' / call['id']).rglob('*')
                       if path.is_file())
    stat = media.stat()
    auth = dict(policy='opencode_one_chain_e2e_v1', recovery_name='one_chain_e2e_v1',
        stage_prefix='chain_e2e_v1_', output=str(root), task_id=state['task_id'], input_lock=state['input_lock'],
        baseline_request_count=35, render_grant=dict(rough=1, fine=2, total=3), new_candidate_rounds=0,
        max_fine_actual_revisions=1, repairs_per_stage=repairs_per_stage, no_unknown_replay=True, goal_resumed=False,
        old_wrapper_consumed=False, user_instruction='local fixture',
        execution_directory=str(root / 'artifacts/one_chain_e2e_v1'),
        rough_directory=str(root / 'artifacts/one_chain_e2e_v1/rough'),
        fine_target_duration_s=40.84, fine_target_tolerance_s=2,
        source_integrity_policy='actual_SHA_at_registration_then_immutable_catalog_and_stat_on_load',
        semantic_joint_policy='actual_rough_content_and_character_echo_then_picture_only_fine_review',
        rough_edl_is_original_model_plan=True, old_152_second_refinement_is_not_fine_input=True,
        original_failures_preserved=True, baseline_state_path=str(baseline_path),
        baseline_state_sha256=sha256_file(baseline_path), old_call_files=old_files,
        protected_files=[dict(path=file, sha256=sha256_file(file)) for file in old_files],
        http_prefix_bytes=(root / 'mcp_http.jsonl').stat().st_size,
        http_prefix_sha256=sha256_file(root / 'mcp_http.jsonl'), catalog=catalog, reference=ref, windows=windows,
        rough_plan={}, rough_plan_sha256=json_sha({}), paid_rough_plan_call_id=old[31]['id'],
        paid_rough_plan_request_sha256=old[31]['request_sha256'],
        paid_rough_plan_response_sha256=old[31]['response_sha256'],
        source_files=[dict(path=str(media), sha256=ref['sha256'], size_bytes=stat.st_size, mtime_ns=stat.st_mtime_ns)])
    proof_path = root / 'chain_authorization.json'
    write_json(proof_path, auth)
    state['artifacts']['server_one_chain_e2e_authorization'] = [dict(path=str(proof_path), sha256=json_sha(auth))]
    return auth


@pytest.mark.parametrize('repairs_per_stage', [1, 2])
@pytest.mark.parametrize('stage', ['chain_e2e_v1_rough_blind', 'sf_0_outline'])
def test_codex_enables_second_repair_only_for_authorized_chain_stage(
        tmp_path, monkeypatch, repairs_per_stage, stage):
    from omni_story.library import server_chain_authorization as authorization
    mcp, media, sent = harness(tmp_path, monkeypatch, [{}, {}, dict(first=True, second=True)])
    old, _ = server_chain_calls(mcp.output)
    mcp.state.max_requests = None
    mcp.state.data.update(calls=old, request_count=len(old), max_requests=None)
    register_node_chain(mcp.output, mcp.state.data, old, media, repairs_per_stage=repairs_per_stage)
    mcp.state._save()
    if authorization.allowed_stage(stage):
        assert mcp.call(stage, 'prompt', media, validate_fields) == dict(first=True, second=True)
        assert len(sent) == 3 and mcp.state.data['calls'][-1]['name'] == stage + '_repair2'
    else:
        with pytest.raises(ValueError, match='^model_protocol_repair_exhausted:' + stage + '$'):
            mcp.call(stage, 'prompt', media, validate_fields)
        assert len(sent) == 2 and mcp.state.data['calls'][-1]['name'] == stage + '_repair'


@pytest.mark.parametrize('repairs_per_stage', [1, 2])
def test_python_and_node_accept_chain_proof_without_rewriting_issued_records(tmp_path, repairs_per_stage):
    from omni_story.library import server_chain_authorization as authorization
    root = tmp_path.resolve()
    state, _ = node_guard_case(root, True)
    media = root / 'media.jpg'
    auth = register_node_chain(root, state, state['calls'][:35], media, repairs_per_stage=repairs_per_stage)
    write_json(root / 'library_state.json', state)
    paths = [root / 'chain_authorization.json', root / 'baseline.json', root / 'library_state.json']
    before = {path: path.read_bytes() for path in paths}
    assert authorization.load(root) == auth
    result = run_node_guard(root, state)
    assert result.returncode == 0 and result.stdout.strip() == 'Infinity', result.stdout + result.stderr
    assert {path: path.read_bytes() for path in paths} == before


@pytest.mark.parametrize('repairs_per_stage', [0, 3, True, '2'])
def test_python_and_node_reject_unsupported_chain_proof_limits(tmp_path, repairs_per_stage):
    from omni_story.library import server_chain_authorization as authorization
    root = tmp_path.resolve()
    state, _ = node_guard_case(root, True)
    register_node_chain(root, state, state['calls'][:35], root / 'media.jpg', repairs_per_stage=repairs_per_stage)
    write_json(root / 'library_state.json', state)
    with pytest.raises(LibraryStopped, match='authorization_scope_changed'):
        authorization.load(root)
    result = run_node_guard(root, state)
    assert result.returncode == 2 and 'authorization_scope_changed' in result.stdout, result.stdout + result.stderr


def test_python_and_node_reject_chain_proof_limit_changed_without_digest_update(tmp_path):
    from omni_story.library import server_chain_authorization as authorization
    root = tmp_path.resolve()
    state, _ = node_guard_case(root, True)
    auth = register_node_chain(root, state, state['calls'][:35], root / 'media.jpg', repairs_per_stage=1)
    write_json(root / 'library_state.json', state)
    write_json(root / 'chain_authorization.json', dict(auth, repairs_per_stage=2))
    with pytest.raises(LibraryStopped, match='authorization_scope_changed'):
        authorization.load(root)
    result = run_node_guard(root, state)
    assert result.returncode == 2 and 'authorization_changed' in result.stdout, result.stdout + result.stderr


def test_new_chain_registration_declares_two_repairs(tmp_path, monkeypatch):
    from omni_story.library import server_chain_authorization as authorization
    root = tmp_path.resolve()
    state, _ = node_guard_case(root, True)
    auth = json.loads((root / 'chain_authorization.json').read_text())
    state.update(calls=state['calls'][:35], request_count=35, artifacts={},
                 policy_version=pipeline.prompts.POLICY_VERSION)
    write_json(root / 'library_state.json', state)
    inputs = {key: auth[key] for key in ('catalog', 'reference', 'windows', 'source_files', 'rough_plan',
        'rough_plan_sha256', 'paid_rough_plan_call_id', 'paid_rough_plan_request_sha256',
        'paid_rough_plan_response_sha256')}
    monkeypatch.setattr(authorization, '_inputs', lambda *args, **kwargs: inputs)
    monkeypatch.setattr(authorization, '_terminal', lambda *args: dict(job_id='failed_fixture'))
    (root / 'artifacts').mkdir()
    path = authorization.register(root, 'local fixture', registry_path=root / 'registry.json')
    proof = json.loads(path.read_text())
    assert proof['repairs_per_stage'] == 2
    assert authorization.load(root) == proof


def run_node_guard(root, state):
    write_json(root / 'library_state.json', state)
    call = state['calls'][-1]
    request = json.loads((root / 'calls' / call['id'] / 'request.json').read_text())
    image = Path(request['arguments']['image_source']).read_bytes()
    body = dict(model='glm-5.3-flash', stream=False, messages=[dict(role='system', content='fixture'),
        dict(role='user', content=[dict(type='image_url', image_url=dict(url='data:image/jpeg;base64,' +
            base64.b64encode(image).decode())), dict(type='text', text=request['arguments']['prompt'])])])
    module = (Path(pipeline.__file__).parent / 'mcp_opencode_guard.mjs').as_uri()
    code = 'import {opencodeRequestLimit} from ' + json.dumps(module) + ';\n'
    code += 'try {console.log(opencodeRequestLimit(' + ','.join(map(json.dumps,
        (str(root), dict(job_id=call['id']), body))) + '));}catch(e){console.log(e.message);process.exitCode=2;}'
    return node(code)


@pytest.mark.parametrize('chain', [False, True])
@pytest.mark.parametrize('attempt', [0, 1, 2])
def test_node_native_guard_accepts_original_and_both_bound_repairs(tmp_path, chain, attempt):
    root = tmp_path.resolve()
    state, calls = node_guard_case(root, chain)
    del state['calls'][len(state['calls']) - 3 + attempt + 1:]
    state['calls'][-1]['status'] = 'submitted'
    state['request_count'] = len(state['calls'])
    result = run_node_guard(root, state)
    assert result.returncode == 0 and result.stdout.strip() == 'Infinity', result.stdout + result.stderr


@pytest.mark.parametrize('chain', [False, True])
@pytest.mark.parametrize('change', ['unsuffixed_first', 'unsuffixed_second', 'unsuffixed_chain',
    'skip_first', 'wrong_parent', 'third', 'nested_suffix', 'unsettled_parent', 'changed_input',
    'changed_prompt', 'changed_parent_record', 'nonimmediate'])
def test_node_native_guard_rejects_other_repair_chains(tmp_path, chain, change):
    root = tmp_path.resolve()
    state, calls = node_guard_case(root, chain)
    old = state['calls'][:-3]
    change_repair_chain(root, calls, change)
    calls[-1]['status'] = 'submitted'
    state['calls'] = old + calls
    state['request_count'] = len(state['calls'])
    result = run_node_guard(root, state)
    assert result.returncode == 2 and ('library_mcp_opencode_' in result.stdout or
                                     'server_chain_authorization_' in result.stdout), result.stdout + result.stderr


def test_node_and_python_chain_stage_names_and_dependencies_match():
    from omni_story.library import server_chain_authorization as authorization
    names = [authorization.STAGE_PREFIX + stage + suffix
        for stage in ('rough_blind', 'fine_plan', 'fine_blind_r', 'fine_inspect_3', 'fine_detail_3_5',
                      'fine_inspect_4', 'fine_detail_0_6')
        for suffix in ('', '_repair', '_repair2', '_repair3', '_repair_repair2')]
    module = (Path(pipeline.__file__).parent / 'server_chain_authorization.mjs').as_uri()
    result = node('import {chainStage,chainDependencies} from ' + json.dumps(module) + ';\n' +
        'console.log(JSON.stringify(' + json.dumps(names) +
        '.map(name=>[chainStage(name),chainStage(name)?chainDependencies(name):[]])));')
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == [[authorization.allowed_stage(name),
        list(authorization.stage_dependencies(name))] for name in names]


def test_opencode_parent_reuses_received_second_repair_without_new_submission(tmp_path):
    from omni_story.library.opencode_provider import OpenCodeMCP
    media = tmp_path / 'media.bin'
    media.write_bytes(b'fixture')
    scope = dict(kind='continuous_window', source_sha256=SHA, source_start_s=0, source_end_s=1)
    current = LibraryState(tmp_path / 'current', dict(configuration={}))
    mcp = OpenCodeMCP.__new__(OpenCodeMCP)
    mcp.state = current
    name = 'fine_' + 'a' * 16
    original = dict(tool='analyze_image', media_sha256=sha256_file(media), observation_scope=scope,
        provider=mcp.provider, policy_version='fixture', arguments=dict(image_source=str(media), prompt='original'))
    calls = []
    for index, suffix in enumerate(('', '_repair', '_repair2')):
        request = dict(original, arguments=dict(original['arguments'],
            prompt='original' + (MARKER + str(index) if index else '')))
        value = dict(first=True, second=True) if index == 2 else {}
        reply = dict(result=dict(content=[dict(type='text', text=json.dumps(value))]))
        call = dict(id=f'call_{index}', name=name + suffix, status='received',
            repair_of=calls[-1]['id'] if index else None,
            request_sha256=json_sha(request), response_sha256=json_sha(reply))
        folder = tmp_path / 'parent' / 'calls' / call['id']
        write_json(folder / 'request.json', request)
        write_json(folder / 'response.json', reply)
        if index == 2:
            write_json(folder / 'parsed.json', value)
        calls.append(call)
    ledger = tmp_path / 'parent' / 'library_state.json'
    write_json(ledger, dict(task_id='parent', calls=calls))
    mcp.parent_baseline = dict(linked_ledgers=[dict(path=str(ledger), sha256=sha256_file(ledger))])
    assert mcp._parent_observation(name, original, scope, validate_fields) == dict(first=True, second=True)
    assert current.data['calls'] == []
    receipt = current.data['artifacts']['parent_observation_reuse'][0]
    assert json.loads(open(receipt['path']).read())['source_call_id'] == 'call_2'


def test_explicit_claim_input_accepts_new_feedback_and_rejects_unrecognized_fields():
    from omni_story.library import explicit_claims
    payload = dict(observation={}, required_claims=[], role_hypotheses=[])
    original = '\n输入事实与待核说法：\n' + json.dumps(payload)
    record = dict(validation_error='error', previous_response='{}', repair_attempt=2,
                  violations=[dict(path='field', value='', expected='text', hint='fill')])
    assert explicit_claims.old_claim_input(original + MARKER + json.dumps(record)) == ({}, [], [])
    assert explicit_claims.old_claim_input(original + MARKER + json.dumps(dict(record, invented=True))) is None
