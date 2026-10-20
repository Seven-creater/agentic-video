import json
from types import SimpleNamespace

import pytest

from omni_story.library import pipeline, search_recovery
from omni_story.library.media import sha256_file
from omni_story.library.state import LibraryStopped, json_sha


CATALOG = {'sources': [dict(source_id='film', sha256='a'*64, duration_s=10)]}

def selection(*ids):
    return dict(reason='Use received action evidence.', windows=[], observation_ids=list(ids),
        slots=[dict(slot_id='action', intended_takeaway='A visible action',
            coverage=dict(visual_goal='Action', audio_goal='Music', gaps=[]),
            observation_ids=list(ids))])


def test_selection_can_reuse_received_window_without_relabeling_it():
    value = selection('card_a', 'window_new')
    assert pipeline._validate_library_search(value, CATALOG,
        [{'observation_id': 'card_a'}], max_windows=None,
        received_windows=[dict(window_id='window_new', status='watched')]) == value


@pytest.mark.parametrize('window', [[], [dict(window_id='window_new', status='submitted')],
    [dict(window_id='window_elsewhere', status='watched')]])
def test_unknown_or_pending_window_is_still_rejected(window):
    with pytest.raises(ValueError, match='unknown_or_duplicate'):
        pipeline._validate_library_search(selection('window_new'), CATALOG, [],
            max_windows=None, received_windows=window)


def test_duplicate_selection_and_rejecting_a_watched_window_are_invalid():
    windows = [dict(window_id='window_new', status='watched')]
    with pytest.raises(ValueError, match='unknown_or_duplicate'):
        pipeline._validate_library_search(selection('window_new', 'window_new'), CATALOG, [],
            max_windows=None, received_windows=windows)
    value = selection(); value['rejected_observation_ids'] = ['window_new']
    with pytest.raises(ValueError, match='invalid_rejected'):
        pipeline._validate_library_search(value, CATALOG, [], max_windows=None,
            received_windows=windows)


def test_revisit_is_checked_inside_the_existing_one_repair_boundary(tmp_path):
    from omni_story.library.state import LibraryState
    state = LibraryState(tmp_path/'run', {'reference': 'fixed'})
    media = tmp_path/'navigation.jpg'; media.write_bytes(b'fixed navigation bytes')
    invalid = selection()
    invalid['windows'] = [dict(source_id='film', start_s=1, end_s=2, role_ids=[],
        question='Resolve a visible detail.', revisit_window_id='unloaded_card')]
    repaired = json.loads(json.dumps(invalid))
    del repaired['windows'][0]['revisit_window_id']
    class ReceivedOnlyModel(pipeline.CodexMCP):
        def __init__(self):
            self.state=state; self.output=state.output; self.proposals=iter((invalid, repaired))
        def _submit(self, name, request, *, repair_of=None):
            call, _ = state.begin_call(name, request, repair_of=repair_of)
            reply = dict(result=dict(content=[dict(type='text', text=json.dumps(next(self.proposals)))]))
            state.complete_call(call, reply); state._reload()
            return next(row for row in state.data['calls'] if row['id']==call['id']), reply
    result = ReceivedOnlyModel().call('search_1', 'Select real source ranges.', media,
        lambda value: pipeline._validate_library_search(value, CATALOG, [], max_windows=None), image=True)
    assert result == repaired
    calls = state.data['calls']
    assert len(calls)==2 and all(call['status']=='received' for call in calls)
    assert calls[1]['repair_of']==calls[0]['id']
    failure = json.loads((state.output/'calls'/calls[0]['id']/'protocol_failure.json').read_text())
    assert failure['error']=='shared_search:revisit_requires_received_source_range'
    assert not (state.output/'calls'/calls[0]['id']/'parsed.json').exists()


def test_selected_revisit_still_requires_verified_loader_and_original_range():
    value = selection('card_a')
    value['windows'] = [dict(source_id='film', start_s=2, end_s=3, role_ids=[],
        question='Inspect detail within received footage.', revisit_window_id='card_a')]
    cards = [dict(observation_id='card_a')]
    window = dict(window_id='card_a', status='watched', source_sha256='a'*64,
        source_start_s=1, source_end_s=4)
    loader = lambda ident: window
    assert pipeline._validate_library_search(value, CATALOG, cards, max_windows=None,
        selected_window_loader=loader)==value
    for changes in ({'status': 'submitted'}, {'source_sha256': 'b'*64}, {'source_end_s': 2.5}):
        with pytest.raises(ValueError, match='revisit_requires_received_source_range'):
            pipeline._validate_library_search(value, CATALOG, cards, max_windows=None,
                selected_window_loader=lambda ident: dict(window, **changes))


def test_received_search_recovery_never_calls_model_and_keeps_original_failure(tmp_path):
    media = tmp_path/'received.jpg'; media.write_bytes(b'original navigation bytes')
    folder = tmp_path/'calls/call_repair'; folder.mkdir(parents=True)
    value = selection('window_new')
    request = dict(arguments=dict(image_source=str(media)), media_sha256=sha256_file(media))
    reply = dict(result=dict(content=[dict(type='text', text=json.dumps(value))]))
    (folder/'request.json').write_text(json.dumps(request)); (folder/'response.json').write_text(json.dumps(reply))
    failure = folder/'protocol_failure.json'; failure.write_text('original ID rejection')
    state = SimpleNamespace(output=tmp_path, data=dict(calls=[dict(id='call_repair',
        name='search_1_repair', status='received', request_sha256=json_sha(request),
        response_sha256=json_sha(reply))]))
    assert pipeline._received_library_search(state, 'search_1', CATALOG, [],
        max_windows=None, received_windows=[dict(window_id='window_new', status='watched')]) == value
    assert failure.read_text() == 'original ID rejection'
    assert not (folder/'parsed.json').exists()
    media.write_bytes(b'changed navigation bytes')
    with pytest.raises(LibraryStopped, match='received_search_media_changed'):
        pipeline._received_library_search(state, 'search_1', CATALOG, [], max_windows=None)


def test_one_claim_preserves_settled_stop_and_cannot_be_replayed(tmp_path, monkeypatch):
    proof = tmp_path/'proof.json'; proof.write_text('{}')
    stop = tmp_path/'mcp_stop'; stop.write_text('reference_rough_skill_settled')
    monkeypatch.setattr(search_recovery, 'validate', lambda *args: (
        dict(task_id='original', state_sha256='a'*64), selection('window_new')))
    failure_path = search_recovery.claim(tmp_path, proof)
    assert failure_path.name == 'search_recovery_v1.chain_failure.json'
    assert (tmp_path/'search_recovery_v1.original_mcp_stop').read_text() == 'reference_rough_skill_settled'
    assert not stop.exists()
    with pytest.raises((FileExistsError, LibraryStopped)):
        search_recovery.claim(tmp_path, proof)


def recovery_fixture(tmp_path, monkeypatch):
    edit=tmp_path/'line/edit'; edit.mkdir(parents=True)
    folder=edit/'calls/c1'; folder.mkdir(parents=True)
    for name in ('request.json', 'response.json'):
        (folder/name).write_text('{}')
    for name, value in [('chain_started','reference_rough_skill_v1'),
                        ('mcp_stop','reference_rough_skill_settled')]:
        (edit/name).write_text(value)
    (edit/'chain_failure.json').write_text(json.dumps(dict(error='model_protocol_repair_exhausted:search_0')))
    snapshot=edit/'cards.json'; snapshot.write_text(json.dumps(dict(candidates=[])))
    saved=dict(task_id='original',request_count=1,calls=[dict(id='c1',status='received')],
        input_lock=dict(library_sources=[dict(source_id='film',sha256='a'*64)]),
        artifacts=dict(shared_visual_library=[dict(path=str(snapshot),sha256=json_sha(dict(candidates=[])))]))
    (edit/'library_state.json').write_text(json.dumps(saved))
    (edit/'catalog').mkdir();(edit/'catalog/inventory.json').write_text(json.dumps(CATALOG))
    proof=dict(policy=search_recovery.POLICY, output=str(edit),task_id='original',
        state_sha256=sha256_file(edit/'library_state.json'), search_name='search_0',
        failed_jobs=[dict(output=str(edit.parent),job_id='original_job')],
        protected_sha256={str(path):sha256_file(path) for path in [edit/'chain_started',edit/'chain_failure.json',edit/'mcp_stop',folder/'request.json',folder/'response.json']})
    proof_path=tmp_path/'proof.json';proof_path.write_text(json.dumps(proof))
    monkeypatch.setattr(search_recovery.server_jobs,'status',lambda *args: dict(job_id='original_job',state='failed',child_pid=0))
    monkeypatch.setattr(search_recovery.server_jobs,'_group_running',lambda *args: False)
    monkeypatch.setattr(pipeline,'_received_windows',lambda *args,**kw: [])
    monkeypatch.setattr(pipeline,'_received_library_search',lambda *args,**kw: selection())
    return edit,proof_path,saved,proof


@pytest.mark.parametrize('status',['submitted','uncertain','unknown'])
def test_explicit_recovery_rejects_every_unresolved_original_call(tmp_path, monkeypatch,status):
    edit,path,saved,proof=recovery_fixture(tmp_path,monkeypatch)
    saved['calls'][0]['status']=status
    (edit/'library_state.json').write_text(json.dumps(saved))
    proof['state_sha256']=sha256_file(edit/'library_state.json');path.write_text(json.dumps(proof))
    with pytest.raises(LibraryStopped,match='received_baseline_required'):
        search_recovery.validate(edit,path)


def test_explicit_recovery_rejects_user_stop_even_if_its_bytes_are_pinned(tmp_path,monkeypatch):
    edit,path,saved,proof=recovery_fixture(tmp_path,monkeypatch)
    (edit/'mcp_stop').write_text('user_stop')
    proof['protected_sha256'][str(edit/'mcp_stop')]=sha256_file(edit/'mcp_stop');path.write_text(json.dumps(proof))
    with pytest.raises(LibraryStopped,match='user_stop_cannot_be_cleared'):
        search_recovery.validate(edit,path)


def test_explicit_recovery_validates_before_claim_and_rejects_modified_original(tmp_path,monkeypatch):
    edit,path,saved,proof=recovery_fixture(tmp_path,monkeypatch)
    assert search_recovery.validate(edit,path)[0]==proof
    assert not (edit/'search_recovery_v1.claim.json').exists()
    (edit/'calls/c1/response.json').write_text('modified')
    with pytest.raises(LibraryStopped,match='original_changed'):
        search_recovery.validate(edit,path)


def test_claim_restores_stop_arriving_after_validation(tmp_path,monkeypatch):
    edit,path,saved,proof=recovery_fixture(tmp_path,monkeypatch)
    original=search_recovery.validate
    def stop_after_validation(*args):
        result=original(*args)
        (edit/'mcp_stop').write_text('user_stop')
        return result
    monkeypatch.setattr(search_recovery,'validate',stop_after_validation)
    with pytest.raises(LibraryStopped,match='concurrent_user_stop'):
        search_recovery.claim(edit,path)
    assert (edit/'mcp_stop').read_text()=='user_stop'


def test_completion_preserves_user_stop(tmp_path):
    (tmp_path/'mcp_stop').write_text('user_stop')
    search_recovery.mark_settled(tmp_path)
    assert (tmp_path/'mcp_stop').read_text()=='user_stop'


def test_plan_diagnostics_show_actual_entities_and_audio_array_without_rewriting_plan():
    from omni_story.library.contracts import plan_execution_diagnostics
    data=dict(cast=[dict(entity_id='invented')],segments=[])
    windows=[dict(window_id='known',source_id='film',source_start_s=0,source_end_s=10,
        observation=dict(roles=[dict(role_id='A',entity_id='actual_entity',identity_confirmed=True,
            identity_status='received',state='blue coat')],usable_ranges=[]))]
    result=plan_execution_diagnostics(data,windows)
    assert result['watched_window_table'][0]['observed_entities'][0]['entity_id']=='actual_entity'
    assert result['audio_relation_contract']['shape'][0]['segment_id']
    assert data['cast'][0]['entity_id']=='invented'


def test_one_bound_plan_correction_reuses_received_call_and_never_repairs_invalid_reply(tmp_path):
    from omni_story.library.state import LibraryState
    media=tmp_path/'navigation.jpg';media.write_bytes(b'original')
    state=LibraryState(tmp_path/'task',dict(reference='fixture'),max_requests=5)
    request=dict(tool='analyze_image',arguments=dict(image_source=str(media),prompt='original\nexact received entities'),media_sha256=sha256_file(media))
    snapshot=tmp_path/'baseline.json';snapshot.write_bytes(state.path.read_bytes())
    proof=tmp_path/'proof.json';proof.write_text(json.dumps(dict(plan_name='plan_1',baseline_snapshot=str(snapshot),state_sha256=sha256_file(snapshot),expected_request=request)))
    (state.output/'plan_cast_correction_v1.claim.json').write_text(json.dumps(dict(proof_path=str(proof),proof_sha256=sha256_file(proof))))
    class Model:
        sends=0
        def _submit(self,name,req):
            prior=next((row for row in state.data['calls'] if row['name']==name),None)
            if prior:return prior,json.loads((state.output/'calls'/prior['id']/'response.json').read_text())
            self.sends+=1
            call,_=state.begin_call(name,req)
            reply=dict(result=dict(content=[dict(type='text',text='{"valid":false}')]))
            state.complete_call(call,reply);state._reload()
            return call,reply  # Providers return the pre-completion record.
    model=Model()
    def reject(value):raise ValueError('actual_cast_mismatch')
    with pytest.raises(LibraryStopped,match='single_received_correction_invalid'):
        search_recovery.correct_plan(state,model,'plan_1',reject)
    assert model.sends==1 and state.data['request_count']==1
    assert not any(row['name'].endswith('_repair') for row in state.data['calls'])
    assert search_recovery.correct_plan(state,model,'plan_1',lambda value:None)=={'valid':False}
    assert model.sends==1
    # A received alias with a different digest cannot authorize another call.
    state.data['calls'][0]['request_sha256']='b'*64;state._save()
    with pytest.raises(LibraryStopped,match='unresolved_original_no_replay'):
        search_recovery.correct_plan(state,model,'plan_1',lambda value:None)
    assert model.sends==1
