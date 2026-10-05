"""Goal authorizations use synthetic ledgers, never a real run or paid model."""
from copy import deepcopy
from types import SimpleNamespace
import pytest

from omni_story.library import extension_budget as extension, goal_budget as goal
from omni_story.library.state import LibraryStopped, json_sha, write_json
from test_library_extension_budget import base, request, new_call
from test_library_execute import _read


def setup(tmp_path):
    original=base(tmp_path)
    extension.authorize(original.output,'synthetic previous continuation')
    goal.authorize(original.output,'synthetic user requests Goal completion')
    return goal.stage_state(original.output)


def register(state,r=5):
    grant=goal.get_authorization(state)
    state.set_artifact(f'goal_round_{r}',{'round':r,'task_id':state.data['task_id'],
        'input_lock_sha256':json_sha(state.data['input_lock']),'goal_guide_sha256':grant['goal_guide_sha256'],
        'observation_compatibility':grant['observation_compatibility'],'new_unique_windows':0,'max_renders':1})


def test_goal_missing_authorization_rejects_without_writes(tmp_path):
    original=base(tmp_path)
    before={p:p.read_bytes() for p in original.output.rglob('*') if p.is_file()}
    with pytest.raises(LibraryStopped,match='authorization_required'):
        goal.stage_state(original.output)
    assert {p:p.read_bytes() for p in original.output.rglob('*') if p.is_file()}==before


def test_no_numeric_cap_and_old_calls_are_read_only(tmp_path):
    state=setup(tmp_path)
    grant=goal.get_authorization(SimpleNamespace(output=state.output))
    assert grant['additional_requests'] is grant['effective_request_limit'] is None
    assert state.usage()['max_requests'] is None and state.max_requests==float('inf')
    assert state.data['max_requests']==80
    with pytest.raises(LibraryStopped,match='historical_call_read_only'):
        state.fail_call(state.data['calls'][0],'cannot rewrite')
    before=state.path.read_bytes()
    with pytest.raises(LibraryStopped,match='historical_artifact_read_only'):
        state.set_artifact('active_finecut_preflight',{'new':True})
    assert state.path.read_bytes()==before


@pytest.mark.parametrize('name',['active_5_review','semantic_slice_5_aaaaaaaaaaaaaaaa','active_5_finecut'])
def test_stage_cannot_skip_paid_predecessors(tmp_path,name):
    state=setup(tmp_path)
    register(state)
    before=state.path.read_bytes()
    with pytest.raises(LibraryStopped,match='predecessor_unsettled'):
        state.begin_call(name,request(100))
    assert state.path.read_bytes()==before


def test_no_unknown_replay_or_late_round_first_call(tmp_path):
    state=setup(tmp_path)
    register(state,12)
    with pytest.raises(LibraryStopped,match='first_round_must_be_five'):
        state.begin_call('active_12_draft',request(99))
    register(state)
    call,_=state.begin_call('active_5_draft',request(100))
    state.fail_call(call,'lost synthetic reply',uncertain=True)
    before=state.path.read_bytes()
    with pytest.raises(LibraryStopped,match='new_outcome_unknown_no_replay'):
        state.begin_call('active_5_draft_repair',request(101),repair_of=call['id'])
    assert state.path.read_bytes()==before


@pytest.mark.parametrize('field,value',[('baseline_request_count',True),('base_request_limit',True),
    ('observation_compatibility',{'omitted_uncertainties_allowed':True})])
def test_goal_policy_tampering_rejects(tmp_path,field,value):
    state=setup(tmp_path)
    data=_read(state.path)
    entry=data['artifacts'][goal.AUTHORIZATION][0]
    grant=_read(entry['path']); grant[field]=value
    write_json(entry['path'],grant); entry['sha256']=json_sha(grant); write_json(state.path,data)
    with pytest.raises(LibraryStopped):
        goal.get_authorization(state)


def test_same_stage_duplicate_is_not_progress(tmp_path):
    state=setup(tmp_path); register(state)
    call,_=state.begin_call('active_5_draft',request(100))
    state.complete_call(call,{'known':True})
    before=state.path.read_bytes()
    with pytest.raises(LibraryStopped,match='duplicate_or_unknown_stage'):
        state.begin_call('active_5_draft',request(101))
    assert state.path.read_bytes()==before


def test_extra_history_protection_checks_files_without_changing_original_result(tmp_path):
    state=setup(tmp_path)
    path=state.output/'known_failure.json'
    write_json(path, {'failure': True})
    from omni_story.library.media import sha256_file
    state.set_artifact('goal_history_protection_5', {'completed_files': [{'path': str(path), 'sha256': sha256_file(path)}]})
    goal.get_authorization(state)
    write_json(path, {'failure': False})
    with pytest.raises(LibraryStopped, match='bound_file_changed'):
        goal.get_authorization(state)


def test_navigation_hash_and_input_lock_are_immutable(tmp_path):
    state=setup(tmp_path)
    value={'round':6,'input_lock_sha256':json_sha(state.data['input_lock'])}
    path=state.set_artifact('goal_navigation_6',value)
    goal.get_authorization(state)
    with pytest.raises(LibraryStopped, match='goal_stage_artifact_immutable'):
        state.set_artifact('goal_navigation_6',{**value,'round':7})
    write_json(path, {**value,'round':7})
    with pytest.raises(LibraryStopped, match='artifact_changed'):
        goal.get_authorization(state)


def test_full_snapshot_rejects_hand_entered_skipped_stage(tmp_path):
    state=setup(tmp_path); register(state)
    call,_=state.begin_call('active_5_draft',request(100))
    data=_read(state.path); data['calls'][-1]['name']='active_5_review'
    data['calls'][-1]['id']=f'glm_002_active_5_review'
    old=state.output/'calls'/call['id']; old.rename(state.output/'calls'/data['calls'][-1]['id'])
    write_json(state.path,data)
    with pytest.raises(LibraryStopped,match='predecessor_unsettled'):
        goal.get_authorization(state)


def test_renamed_id_cannot_make_a_third_known_exhausted_slice_observation(tmp_path):
    original=base(tmp_path)
    extension.authorize(original.output,'synthetic previous continuation')
    old=extension.stage_state(original.output)
    new_call(old,'active_4_draft',100)
    new_call(old,'active_4_finecut',101)
    fact=new_call(old,'semantic_slice_4_aaaaaaaaaaaaaaaa',102)
    new_call(old,'semantic_slice_4_aaaaaaaaaaaaaaaa_repair',103,repair_of=fact)
    goal.authorize(old.output,'synthetic Goal user authorization')
    state=goal.stage_state(old.output); register(state)
    for name,number in (('active_5_draft',200),('active_5_finecut',201)):
        call,folder=state.begin_call(name,request(number))
        value={'synthetic':'known model JSON'}
        state.complete_call(call,{'result':{'content':[{'type':'text','text':'{"synthetic":"known model JSON"}'}]}})
        write_json(folder/'parsed.json',value)
    before=state.path.read_bytes()
    with pytest.raises(LibraryStopped,match='known_exhausted_slice_lineage_no_third'):
        state.begin_call('semantic_slice_5_bbbbbbbbbbbbbbbb',request(202,scope=102))
    assert state.path.read_bytes()==before
