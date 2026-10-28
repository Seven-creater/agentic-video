from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from omni_story.library import contracts, pipeline, reference_craft, semantic_continuation
from omni_story.library.media import sha256_file
from omni_story.library.state import LibraryStopped, json_sha, write_json
from omni_story.library.story_finecut import StoryFinecut
from test_all_stage_normalization import semantic_observation


from test_all_stage_normalization import CREATIVE, creative_mcp, fine


SHA = 'a' * 64


def received_call(root, ident, value, media, *, failed=False):
    folder = root / 'calls' / ident
    request = dict(media_sha256=sha256_file(media))
    reply = dict(result=dict(content=[dict(type='text', text=json.dumps(value))]))
    write_json(folder / 'request.json', request)
    write_json(folder / 'response.json', reply)
    write_json(folder / ('protocol_failure.json' if failed else 'parsed.json'),
               dict(error='fine:unknown_event_index') if failed else value)
    return dict(id=ident, name='fine_' + ident, status='received',
                request_sha256=json_sha(request), response_sha256=json_sha(reply))



def watched_window(root, ident, *, normalized=False, legacy=False):
    media = root / ident / 'window.bin'
    media.parent.mkdir(parents=True)
    media.write_bytes(ident.encode())
    window = dict(window_id=ident, source_id='film', source_sha256=SHA,
        source_start_s=100, source_end_s=110, path=str(media), sha256=sha256_file(media),
        status='watched', kind='continuous_window', audio_present=False,
        source_visual_policy='silent_subtitle_masked', source_subtitle_masks=[])
    start, end = (10.0002, 10.0008) if legacy else (-1, 12) if normalized else (0, 10)
    raw = dict(window_id=ident, source_id='film', roles=[], uncertainties=[],
        events=[dict(local_start_s=start, local_end_s=end, observed_fact='实际动作', role_ids=[])],
        usable_ranges=[dict(local_in_s=start, local_out_s=end, event_indices=[0], role_ids=[],
                            continuity_notes='连续')], affordances=[dict(event_indices=[0])])
    saved = deepcopy(raw)
    if normalized:
        contracts.validate_fine(saved, window, normalize=True)
    window['observation'] = saved
    write_json(media.parent / 'lineage.json', {k: v for k, v in window.items() if k != 'observation'})
    return window, raw



def file_bytes(root):
    return {path: path.read_bytes() for path in root.rglob('*') if path.is_file()}



@pytest.mark.parametrize('visual_only', [False, True])
@pytest.mark.parametrize('normalized', [False, True])
def test_received_windows_match_before_validating_other_windows_and_failed_calls(
        tmp_path, monkeypatch, visual_only, normalized):
    windows, calls = [], []
    for ident in ('early', 'later'):
        window, raw = watched_window(tmp_path, ident, normalized=normalized)
        if not visual_only:
            window['source_visual_policy'] = 'original_audio'
        windows.append(window)
        calls.append(received_call(tmp_path, ident, raw, window['path']))
        failed = deepcopy(raw)
        failed['events'] = [dict(local_start_s=0, local_end_s=0, observed_fact='', role_ids=[])]
        failed['usable_ranges'][0]['event_indices'] = [99]
        calls.append(received_call(tmp_path, ident + '_failed', failed, window['path'], failed=True))
    malformed = received_call(tmp_path, 'malformed', {}, windows[-1]['path'], failed=True)
    reply = dict(result=dict(content=[dict(type='text', text='{unfinished')]))
    write_json(tmp_path / 'calls' / 'malformed' / 'response.json', reply)
    malformed['response_sha256'] = json_sha(reply)
    calls.append(malformed)
    calls.append(dict(id='pending', name='fine_pending', status='uncertain'))
    write_json(tmp_path / 'watched_windows.json', windows)
    state = SimpleNamespace(output=tmp_path, data=dict(calls=calls))
    state_before = deepcopy(state.data)
    before = file_bytes(tmp_path)
    validated = []
    original = contracts.validate_fine
    def validate(value, window, **kwargs):
        validated.append((value['window_id'], window['window_id']))
        return original(value, window, **kwargs)
    monkeypatch.setattr(contracts, 'validate_fine', validate)
    assert pipeline._received_windows(state, visual_only=visual_only, normalize=True) == windows
    assert set(validated) == {('early', 'early'), ('later', 'later')}
    assert file_bytes(tmp_path) == before and state.data == state_before



@pytest.mark.parametrize('shared', [False, True])
@pytest.mark.parametrize('saved_kind', ['unchanged', 'normalized', 'legacy_tolerance'])
def test_fine_replay_preserves_bound_observations_and_legacy_referenced_events(tmp_path, shared, saved_kind):
    window, raw = watched_window(tmp_path, 'old', legacy=saved_kind == 'legacy_tolerance',
                                 normalized=saved_kind == 'normalized')
    call = received_call(tmp_path, 'old', raw, window['path'])
    write_json(tmp_path / 'watched_windows.json', [window])
    ledger = tmp_path / 'library_state.json'
    write_json(ledger, dict(calls=[call]))
    observation = deepcopy(window['observation'])
    record = dict(observation_id='old', window=window, received_observation=deepcopy(observation),
        observation_method=dict(usable_range_policy='retain_valid_ranges_v1'),
        evidence=dict(status='received', ledger_path=str(ledger), call_id=call['id'],
                      request_sha256=call['request_sha256'], response_sha256=call['response_sha256']))
    before = file_bytes(tmp_path)
    if saved_kind == 'legacy_tolerance':
        with pytest.raises(LibraryStopped, match='recorded_reply_incompatible_with_current_contract:.*fine:unknown_event_index'):
            if shared:
                pipeline._received_library_window(record, tmp_path / 'shared',
                    source=dict(source_id='film', sha256=SHA, duration_s=200), normalize=True)
            else:
                pipeline._received_windows(SimpleNamespace(output=tmp_path, data=dict(calls=[call])), visual_only=True, normalize=True)
        assert file_bytes(tmp_path) == before
        return
    if shared:
        result = pipeline._received_library_window(record, tmp_path / 'shared',
            source=dict(source_id='film', sha256=SHA, duration_s=200), normalize=True)
    else:
        state = SimpleNamespace(output=tmp_path, data=dict(calls=[call]))
        result, = pipeline._received_windows(state, visual_only=True, normalize=True)
    assert result['observation'] == observation
    assert result['observation']['usable_ranges'][0]['event_indices'] == [0]
    assert result['observation']['affordances'][0]['event_indices'] == [0]
    assert file_bytes(tmp_path) == before
    window['observation']['events'][0]['observed_fact'] = 'changed'
    if shared:
        with pytest.raises(LibraryStopped, match='shared_visual_derived_observation_changed'):
            pipeline._received_library_window(record, tmp_path / 'shared',
                source=dict(source_id='film', sha256=SHA, duration_s=200), normalize=True)
    else:
        write_json(tmp_path / 'watched_windows.json', [window])
        with pytest.raises(LibraryStopped, match='watched_window_not_bound_to_received_fine'):
            pipeline._received_windows(state, visual_only=True, normalize=True)



def test_old_reconciled_observation_tolerates_end_clamp_and_still_rejects_fact_changes(tmp_path):
    raw, segment, proxy = semantic_observation()
    raw['evidence'][0].update(local_start_s=1, local_end_s=proxy['duration_s'] + .0005)
    raw.pop('uncertainties')
    calls = [received_call(tmp_path, ident, raw, _fixture_media(tmp_path), failed=True)
             for ident in ('original', 'repair')]
    calls[0]['name'] = calls[1]['name'] = 'observe'
    calls[1]['repair_of'] = calls[0]['id']
    bindings = []
    for call in calls:
        folder = tmp_path / 'calls' / call['id']
        request = dict(media_sha256=proxy['sha256'])
        failure = dict(error='semantic/uncertainties:list_required')
        write_json(folder / 'request.json', request)
        write_json(folder / 'protocol_failure.json', failure)
        call['request_sha256'] = json_sha(request)
        bindings.append(dict(call_id=call['id'], request_sha256=call['request_sha256'],
            response_sha256=call['response_sha256'], failure_sha256=json_sha(failure)))
    note = ('Program protocol limitation: the model omitted its uncertainties field. '
            'The absence of further uncertainty is unknown. Any extra untyped inference is not validated evidence.')
    old = dict(deepcopy(raw), uncertainties=[note])
    saved = dict(source_call_id=calls[1]['id'], call_bindings=bindings,
                 raw_model_object=raw, observation=old)
    path = tmp_path / 'reconciliation.json'
    write_json(path, saved)
    entry = dict(path=str(path), sha256=json_sha(saved))
    state = SimpleNamespace(output=tmp_path, data=dict(calls=calls,
        artifacts=dict(source_observation_reconciliation=[entry])))
    before = file_bytes(tmp_path)
    result = semantic_continuation.reconcile_missing_observation_uncertainties(
        SimpleNamespace(state=state), 'observe', segment, dict(sha256=SHA), proxy)
    assert result['evidence'][0]['local_end_s'] == proxy['duration_s'] + .0005
    assert 'normalization' not in result and file_bytes(tmp_path) == before
    saved['observation']['evidence'][0]['description'] = 'changed'
    write_json(path, saved)
    entry['sha256'] = json_sha(saved)
    with pytest.raises(ValueError, match='reconciled_observation_facts_changed'):
        semantic_continuation.reconcile_missing_observation_uncertainties(
            SimpleNamespace(state=state), 'observe', segment, dict(sha256=SHA), proxy)



def _fixture_media(root):
    path = root / 'media.bin'
    path.write_bytes(b'fixture')
    return path



def test_shared_range_policy_normalizes_before_filtering_and_is_pure(tmp_path):
    window, raw = watched_window(tmp_path, 'window', normalized=True)
    before = deepcopy(raw)
    view = pipeline._partial_library_ranges(raw, window, normalize=True)
    assert raw == before
    assert view['events'][0]['local_end_s'] == 10
    assert view['usable_ranges'][0]['local_out_s'] == 10
    assert pipeline._partial_library_ranges(view, window, normalize=True) == view



def test_received_failure_without_a_parsed_record_cannot_become_a_full_pass(tmp_path, monkeypatch):
    from test_protocol_feedback import harness
    raw = dict(facts=[])
    mcp, media, sent = harness(tmp_path, monkeypatch, [raw])
    creative_mcp(mcp)
    mcp.call('observe', 'prompt', media, lambda value: None)
    folder = mcp.output / 'calls' / mcp.state.data['calls'][0]['id']
    (folder / 'parsed.json').unlink()
    write_json(folder / 'protocol_failure.json', dict(error='old_failure'))
    before = file_bytes(tmp_path)
    with pytest.raises(LibraryStopped, match='historical_reply_requires_explicit_derived_binding'):
        mcp.call('observe', 'prompt', media, lambda value: None)
    assert file_bytes(tmp_path) == before and len(sent) == 1



@pytest.mark.parametrize('tamper', [False, True])
def test_parent_parsed_tamper_is_reported_even_when_raw_fails_current_contract(tmp_path, tamper):
    from omni_story.library.opencode_provider import OpenCodeMCP
    media = _fixture_media(tmp_path)
    scope = dict(kind='continuous_window', source_sha256=SHA, source_start_s=0, source_end_s=10)
    mcp = OpenCodeMCP.__new__(OpenCodeMCP)
    mcp.state = SimpleNamespace(input_lock=dict(configuration=CREATIVE))
    request = dict(tool='analyze_video', media_sha256=sha256_file(media), observation_scope=scope,
        provider=mcp.provider, policy_version='fixture', arguments=dict(video_source=str(media), prompt='original'))
    raw = dict(fact='original')
    call = received_call(tmp_path, 'parent', raw, media)
    call['name'] = 'fine_' + 'a' * 16
    call['request_sha256'] = json_sha(request)
    write_json(tmp_path / 'calls' / 'parent' / 'request.json', request)
    write_json(tmp_path / 'calls' / 'parent' / 'parsed.json', dict(fact='tampered' if tamper else 'original'))
    ledger = tmp_path / 'library_state.json'
    write_json(ledger, dict(calls=[call]))
    mcp.parent_baseline = dict(linked_ledgers=[dict(path=str(ledger), sha256=sha256_file(ledger))])
    def invalid(value):
        raise ValueError('required_field_missing')
    before = file_bytes(tmp_path)
    with pytest.raises(LibraryStopped, match='parent_observation_parsed_changed' if tamper else
                       'recorded_reply_incompatible_with_current_contract'):
        mcp._parent_observation(call['name'], request, scope, invalid)
    assert file_bytes(tmp_path) == before



@pytest.mark.parametrize('start', [0, 100])
def test_continuous_facts_normalize_translated_local_clock_but_frame_requests_stay_strict(start):
    value = dict(facts=[dict(start_s=start - 2, end_s=start + 12, description='动作', basis='picture'),
                        dict(start_s=start + 11, end_s=start + 12, description='越界', basis='picture')],
                 uncertainties=[])
    StoryFinecut.facts_check(value, start, start + 10, normalize=True)
    assert len(value['facts']) == 1
    assert (value['facts'][0]['start_s'], value['facts'][0]['end_s']) == (start, start + 10)
    assert value['normalization'] == [dict(path='$.facts[0].start_s', original=start - 2, normalized=start),
        dict(path='$.facts[0].end_s', original=start + 12, normalized=start + 10),
        dict(path='$.facts[1]', original=dict(start_s=start + 11, end_s=start + 12,
            description='越界', basis='picture'), dropped=True)]
    value['frame_ranges'] = [dict(start_s=start, end_s=start + 12)]
    with pytest.raises(ValueError, match='finite_number_out_of_range'):
        StoryFinecut.facts_check(value, start, start + 10, normalize=True)
    assert value['frame_ranges'][0]['end_s'] == start + 12



def test_creative_view_is_pure_and_idempotent():
    raw,window=fine()
    before=deepcopy(raw)
    validate=lambda value:contracts.validate_fine(value,window,normalize=True)
    view=contracts.normalized_view(raw,validate)
    assert raw==before and contracts.normalized_view(view,validate)==view
    assert contracts.normalized_view(raw,validate)==view


@pytest.mark.parametrize('failure', ['empty','reference'])
@pytest.mark.parametrize('tamper', [False,True])
def test_creative_incompatible_record_is_distinct_from_tamper(tmp_path,monkeypatch,failure,tamper):
    from test_protocol_feedback import harness,reference
    if failure=='empty':
        raw=reference()
        raw['visible_evidence'][0].update(start_s=45,end_s=47)
        validate=lambda value:contracts.validate_reference(value,SHA,40.84)
    else:
        raw,window=fine()
        raw['events'][0].update(local_start_s=45,local_end_s=47)
        validate=lambda value:contracts.validate_fine(value,window,normalize=True)
    mcp,media,sent=harness(tmp_path,monkeypatch,[raw])
    creative_mcp(mcp)
    mcp.call('observe','prompt',media,lambda value:None)
    if tamper:
        saved=deepcopy(raw)
        saved['theme' if failure=='empty' else 'source_id']='篡改'
        write_json(mcp.output/'calls'/mcp.state.data['calls'][0]['id']/'parsed.json',saved)
    before=file_bytes(tmp_path)
    with pytest.raises(LibraryStopped,match='recorded_parsed_reply_modified' if tamper else
                       'recorded_reply_incompatible_with_current_contract'):
        mcp.call('observe','prompt',media,validate)
    assert file_bytes(tmp_path)==before and len(sent)==1


@pytest.mark.parametrize('route',['revision','semantic','capacity'])
def test_legacy_continuations_reject_clipped_raw_unchanged(tmp_path,monkeypatch,route):
    from omni_story.library import revision,server_capacity_recovery
    window,raw=watched_window(tmp_path,'window_old',normalized=True)
    call=received_call(tmp_path,'old',raw,window['path'])
    write_json(tmp_path/'watched_windows.json',[window])
    write_json(tmp_path/'reference_reading.json',{})
    source=dict(source_id='film',sha256=SHA,duration_s=200)
    state=SimpleNamespace(output=tmp_path,data=dict(calls=[call],artifacts={},
        input_lock=dict(reference_sha256=SHA,library_sources=[dict(source_id='film',sha256=SHA)])))
    policy=dict(fine_window_limit=1,original_calls=[call])
    if route=='revision':
        monkeypatch.setattr(revision,'_state',lambda output:state)
        monkeypatch.setattr(revision,'_authorization',lambda state:policy)
        monkeypatch.setattr(revision,'_catalog',lambda *args:dict(sources=[source]))
        execute=lambda:revision._execute_editing_revision(_fixture_media(tmp_path),'library',tmp_path)
    elif route=='semantic':
        monkeypatch.setattr(semantic_continuation,'_authorization',lambda state:policy)
        monkeypatch.setattr(semantic_continuation,'_catalog',lambda *args:dict(sources=[source]))
        execute=lambda:semantic_continuation._execute(_fixture_media(tmp_path),'library',state)
    else:
        execute=lambda:server_capacity_recovery._remaining_windows(tmp_path,dict(calls=[call]),[dict(window,observation=raw)],1)
    code = {'revision':'revision:window_not_bound_to_received_fine_observation',
            'semantic':'semantic_continuation:watched_observation_not_completed',
            'capacity':'fine/event/start:finite_number_required'}[route]
    with pytest.raises(ValueError,match=code) as caught:
        execute()
    if route=='capacity':
        assert caught.value.violation['value']==-1
    assert 'normalization' not in raw


@pytest.mark.parametrize('stored_view', ['raw', 'normalized', 'legacy_clamped'])
@pytest.mark.parametrize('attempt', [0, 2])
@pytest.mark.parametrize('inherited', [False, True])
@pytest.mark.parametrize('tamper', [False, True])
def test_creative_clean_chain_carries_observe_and_inspect_views_without_rewriting(
        tmp_path, stored_view, attempt, inherited, tamper):
    from omni_story.library import clean_chain
    source = dict(sha256=SHA, duration_s=200, video_stream_index=0, audio_stream_index=None)
    reference = dict(source, duration_s=40.93)
    query = dict(target='parent', start_s=100, end_s=106, step_s=.5, question='动作')
    observed = dict(story=[dict(id='beat', contribution='动作')], limitations=[], inspect=[query],
                    facts=[dict(start_s=0, end_s=247, description='整片动作')])
    continuous = dict(facts=[dict(start_s=98, end_s=112, description='局部动作', basis='picture')], uncertainties=[])
    saved_observed, saved_continuous = deepcopy(observed), deepcopy(continuous)
    if stored_view != 'raw':
        from omni_story.library.visual_story_trial import observe_check
        observe_check(saved_observed, dict(parent=source, reference=reference), normalize=True)
        StoryFinecut.facts_check(saved_continuous, 100, 106, normalize=True)
        if stored_view == 'legacy_clamped':
            saved_observed.pop('normalization')
            saved_continuous.pop('normalization')
    if tamper:
        saved_continuous['facts'][0]['description'] = '篡改'
    write_json(tmp_path/'skill_finecut/observed.json', saved_observed)
    write_json(tmp_path/'skill_finecut/inspection_results.json', [dict(request=query,
        request_sha256=json_sha(query), continuous=saved_continuous, frame_sheets=[])])
    calls = []
    for stage, raw, start, end in [('observe', observed, 0, 200), ('inspect_0', continuous, 100, 106)]:
        media = tmp_path/(stage+'.bin')
        media.write_bytes(stage.encode())
        request = dict(tool='analyze_video', media_sha256=sha256_file(media),
            observation_scope=dict(kind='continuous_window', source_sha256=SHA,
                                   source_start_s=start, source_end_s=end),
            arguments=dict(video_source=str(media)))
        parent = None
        for index in range(attempt + 1):
            suffix = ('_repair2' if index == 2 else '_repair' if index else '')
            ident = stage + suffix
            call = received_call(tmp_path, ident, raw, media, failed=index < attempt)
            call['name'] = 'chain_e2e_v1_fine_'+stage+suffix
            if parent:
                call['repair_of'] = parent
            request['arguments']['prompt'] = 'inspection ' + str(index)
            write_json(tmp_path/'calls'/ident/'request.json', request)
            call['request_sha256'] = json_sha(request)
            calls.append(call)
            parent = call['id']
    current = tmp_path
    state = dict(calls=calls)
    old_input = dict(parent=source, reference=reference, reference_context_sha256='bound-context')
    if inherited:
        write_json(tmp_path/'library_state.json', state)
        write_json(tmp_path/'chain_result.json', {})
        write_json(tmp_path/'skill_finecut/input.json', old_input)
        current = tmp_path/'evaluation'
        from omni_story.library.visual_story_trial import observe_check
        child_observed, child_continuous = deepcopy(observed), deepcopy(continuous)
        observe_check(child_observed, dict(parent=source, reference=reference), normalize=True)
        StoryFinecut.facts_check(child_continuous, 100, 106, normalize=True)
        child_observed.pop('normalization')
        child_continuous.pop('normalization')
        write_json(current/'skill_finecut/observed.json', child_observed)
        write_json(current/'skill_finecut/inspection_results.json', [dict(request=query,
            request_sha256=json_sha(query), continuous=child_continuous, frame_sheets=[])])
        receipt = dict(parent_task=str(tmp_path.resolve()),
            parent_ledger_sha256=sha256_file(tmp_path/'library_state.json'), files={
                str(tmp_path/'skill_finecut/observed.json'): sha256_file(tmp_path/'skill_finecut/observed.json'),
                str(tmp_path/'skill_finecut/inspection_results.json'): sha256_file(tmp_path/'skill_finecut/inspection_results.json')})
        path = current/'reuse.json'
        write_json(path, receipt)
        state = dict(calls=[], artifacts=dict(clean_chain_fine_observation_reuse=[dict(path=str(path),sha256=json_sha(receipt))]))
    before = file_bytes(tmp_path)
    execute = lambda: clean_chain._continued_fine_observations(current, state,
        old_input, source, reference, normalize=True)
    if tamper:
        with pytest.raises(LibraryStopped, match='clean_chain:inherited_skill_observation_changed'):
            execute()
    else:
        result = execute()
        assert result['observed']['facts'][0]['end_s'] == 200
        facts = result['inspections'][0]['continuous']
        assert (facts['facts'][0]['start_s'], facts['facts'][0]['end_s']) == (100, 106)
        assert bool(facts.get('normalization')) is (not inherited)
    assert file_bytes(tmp_path) == before


def test_creative_raw_parsed_passes_python_and_native_chain_guards(tmp_path, monkeypatch):
    from omni_story.library import server_chain_authorization as authorization
    from test_protocol_feedback import harness, node_guard_case, register_node_chain, run_node_guard
    from test_all_stage_normalization import creative_flow
    raw = dict(story=[dict(id='beat', contribution='动作')], facts=[dict(start_s=0, end_s=47)],
               limitations=[], inspect=[])
    mcp, media, sent = harness(tmp_path, monkeypatch, [raw], provider=CREATIVE['provider'])
    creative_mcp(mcp)
    flow = creative_flow()
    flow.auth = dict(parent=dict(duration_s=40.93), reference=dict(duration_s=40.84))
    view = mcp.call('chain_e2e_v1_fine_observe', 'observing', media, flow.observation_check)
    paid = deepcopy(mcp.state.data['calls'][0])
    root = mcp.output.resolve()
    parsed_path = root/'calls'/paid['id']/'parsed.json'
    assert json.loads(parsed_path.read_text()) == raw and view != raw and len(sent) == 1
    state, fixture_calls = node_guard_case(root, True)
    old = state['calls'][:35]
    rough_blind = fixture_calls[0]
    write_json(root/'calls'/rough_blind['id']/'parsed.json', {})
    rough_review = dict(rough_blind, id='rough_review', name='chain_e2e_v1_rough_review')
    pending = dict(rough_blind, id='fine_plan', name='chain_e2e_v1_fine_plan', status='submitted')
    for call in (rough_review, pending):
        request = json.loads((root/'calls'/rough_blind['id']/'request.json').read_text())
        request['arguments']['prompt'] = call['name']
        call['request_sha256'] = json_sha(request)
        write_json(root/'calls'/call['id']/'request.json', request)
        if call['status'] == 'received':
            reply = dict(result=dict(content=[dict(type='text', text='{}')]))
            call['response_sha256'] = json_sha(reply)
            write_json(root/'calls'/call['id']/'response.json', reply)
            write_json(root/'calls'/call['id']/'parsed.json', {})
    state['calls'] = old + [rough_blind, rough_review, paid, pending]
    state['request_count'] = len(state['calls'])
    state['input_lock']['configuration'].update(CREATIVE)
    register_node_chain(root, state, old, root/'media.jpg')
    write_json(root/'library_state.json', state)
    before = parsed_path.read_bytes()
    authorization.load(root)
    result = run_node_guard(root, state)
    assert result.returncode == 0 and result.stdout.strip() == 'Infinity', result.stdout + result.stderr
    assert parsed_path.read_bytes() == before
    write_json(parsed_path, view)
    with pytest.raises(LibraryStopped, match='new_parsed_reply_changed'):
        authorization.load(root)
    result = run_node_guard(root, state)
    assert result.returncode != 0 and 'parsed_reply_changed' in result.stdout


def test_legacy_semantic_cache_keeps_tolerated_end_and_exact_hash_echo(tmp_path):
    from omni_story.library import extension_budget, semantic_audit as audit
    raw,segment,proxy=semantic_observation()
    raw['evidence'][0].update(local_start_s=0,local_end_s=proxy['duration_s']+.0005)
    before=deepcopy(raw)
    audit.validate_segment_observation(raw,segment,SHA,proxy)
    assert raw==before and 'normalization' not in raw
    claim=dict(protocol=audit.SEMANTIC_PROTOCOL,segment_id='seg',observation_sha256=json_sha(raw),
               claim_checks=[],uncertainties=[])
    audit.validate_segment_claim_check(claim,raw,[])
    call=received_call(tmp_path,'legacy',raw,_fixture_media(tmp_path))
    files=file_bytes(tmp_path)
    assert extension_budget._model_value(tmp_path,call)[1]==raw
    assert file_bytes(tmp_path)==files
    changed=deepcopy(raw)
    changed['evidence'][0]['description']='篡改'
    write_json(tmp_path/'calls/legacy/parsed.json',changed)
    with pytest.raises(LibraryStopped,match='extension:cached_parsed_differs_from_reply'):
        extension_budget._model_value(tmp_path,call)
    with pytest.raises(ValueError,match='semantic:claim_check_observation_changed'):
        audit.validate_segment_claim_check(deepcopy(claim),changed,[])


def test_creative_pipeline_reaches_both_shared_library_searches_with_raw_seed(tmp_path,monkeypatch):
    from omni_story.library import visual_library
    from test_protocol_feedback import reference
    reading=reference()
    reading['visible_evidence'][0]['end_s']=41
    reading['strategy']=dict(adaptation_mode='reinterpret',speech_mode='none',timing_driver='action')
    seed=dict(full_response=dict(reference=reading),evidence_limit='received raw reference')
    media=_fixture_media(tmp_path)
    source=dict(source_id='film',sha256=SHA,duration_s=40.84,path=str(media),audio_stream_index=None)
    monkeypatch.setattr(pipeline,'_catalog',lambda *args:dict(sources=[source]))
    shared = tmp_path/'shared'
    shared.mkdir()
    (shared/'content_ranges.json').write_text(json.dumps(dict(schema_version='content_ranges_v1',
        sources={SHA: dict(content_start_s=0, content_end_s=40.84, excluded=[], verified_by='synthetic fixture')})))
    searches=[]
    class ReachedSearch(Exception):
        pass
    class Library:
        def __init__(self,root, **kwargs):
            pass
        def entity_revision_ids(self,sha):
            return []
        def search(self,query,*,source_sha256s,limit,revision_ids):
            searches.append(query)
            if len(searches)==2:
                raise ReachedSearch()
            return []
    monkeypatch.setattr(visual_library,'VisualLibrary',Library)
    before=deepcopy(seed)
    with pytest.raises(ReachedSearch):
        pipeline.execute(media,media,tmp_path/'creative',asr=False,max_fine=None,max_requests=None,
            model_factory=lambda state:SimpleNamespace(state=state,exclusions=[]),
            provider_config=dict(CREATIVE,workflow='reference_rough_skill_v1',
                progress_policy='finite_stages_no_retry_v1',visual_library_root=str(tmp_path/'shared')),
            reference_seed=seed)
    assert len(searches)==2 and searches[1]==''
    assert seed==before
    assert json.loads((tmp_path/'creative/reference_reading.json').read_text())['visible_evidence'][0]['end_s']==40.84


def test_creative_bound_plan_correction_also_persists_raw(tmp_path,monkeypatch):
    from omni_story.library import search_recovery,main_plan_binding
    from test_protocol_feedback import harness
    mcp,media,sent=harness(tmp_path,monkeypatch,[dict(evidence=[dict(start_s=0,end_s=47)])])
    creative_mcp(mcp)
    state=mcp.state
    snapshot=tmp_path/'baseline.json'
    snapshot.write_bytes(state.path.read_bytes())
    request=dict(tool='analyze_video',arguments=dict(video_source=str(media),prompt='bound correction'),
                 media_sha256=sha256_file(media))
    proof=tmp_path/'proof.json'
    write_json(proof,dict(plan_name='plan_1',baseline_snapshot=str(snapshot),state_sha256=sha256_file(snapshot),
                         expected_request=request))
    write_json(state.output/'plan_cast_correction_v1.claim.json',dict(proof_path=str(proof),proof_sha256=sha256_file(proof)))
    monkeypatch.setattr(main_plan_binding,'load',lambda *args:None)
    validate=lambda value:contracts.normalize_intervals(value,value,'evidence',40.93)
    view=search_recovery.correct_plan(state,mcp,'plan_1',validate)
    assert view['evidence'][0]['end_s']==40.93 and view['normalization']
    parsed=state.output/'calls'/state.data['calls'][0]['id']/'parsed.json'
    assert json.loads(parsed.read_text())==dict(evidence=[dict(start_s=0,end_s=47)])
    before=parsed.read_bytes()
    assert search_recovery.correct_plan(state,mcp,'plan_1',validate)==view
    assert parsed.read_bytes()==before and len(sent)==1
