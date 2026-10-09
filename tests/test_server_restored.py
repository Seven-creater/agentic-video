import argparse
import json
from pathlib import Path

import pytest

pytest.importorskip('av')
pytest.importorskip('PIL')
from omni_story.library import server_restored as restored


@pytest.fixture
def restoration(tmp_path, monkeypatch):
    lane = tmp_path / 'lane'; lane.mkdir()
    home = tmp_path / 'home'; home.mkdir()
    rough = lane / 'new_rough.mp4'; rough.write_bytes(b'new model rough')
    fine = lane / 'new_fine.mp4'; fine.write_bytes(b'new model fine')
    source = dict(path=str(rough), sha256=restored.sha256_file(rough), duration_s=81)
    reference = {'sha256':'fixed_ref','duration_s':21.933333}
    (lane / 'reference_catalog').mkdir()
    (lane / 'reference_catalog/inventory.json').write_text(json.dumps({'sources':[reference]}))
    seed = dict(source_call_id='glm_001_reference', full_response={'reference':{'theme':'original reference'}},
                evidence_limit='old interpretation limitations')
    proof = dict(reference_seed=seed, parent_requests=36, historical_requests=274)
    monkeypatch.setattr(restored.policy, 'load', lambda lane: proof)
    monkeypatch.setattr(restored.policy, 'configuration', lambda path: {'restoration_policy':'restored_original_method_v1'})
    monkeypatch.setattr(restored, 'settings', lambda home: {'mcp_package_root':'pkg','opencode_executable':'opencode'})
    monkeypatch.setattr(restored, 'load_history', lambda config: {'unknown_inputs':['preserved historical exclusions']})
    class State:
        def __init__(self):
            self.data = {'request_count':5,'calls':[{'id':'glm_005_review_0','name':'review_0','status':'received'}]}
            self.artifacts=[]
        def _reload(self): pass
        def set_artifact(self, name, value): self.artifacts.append((name,value))
    state = State()
    class Client:
        def __init__(self, given, **kwargs):
            assert given is state and kwargs['exclusions'] == ['preserved historical exclusions']
    monkeypatch.setattr(restored,'RestorationMCP',Client)
    result = dict(final_video=str(rough), final_sha256=source['sha256'], selected_round=0,
        selected_review_path=str(lane/'review_0.json'),
        review={'theme_status':'partial','continuity_status':'pass','editing_status':'partial'})
    events=[]
    def generate(reference_path, library, given_lane, **kwargs):
        events.append(('rough',kwargs))
        assert given_lane == lane and kwargs['reference_seed']['source_call_id'] == 'glm_001_reference'
        kwargs['model_factory'](state)
        return result
    monkeypatch.setattr(restored, 'run_rough', generate)
    monkeypatch.setattr(restored, 'inventory_sources', lambda *args: {'sources':[source]})
    def refine(given, mcp, context, parent, ref, output):
        events.append(('fine',context))
        assert given is state and parent == source and ref == reference
        handoff=json.loads((lane/'rough_handoff.json').read_text())
        assert handoff['rough_video_path'] == str(rough)
        assert handoff['rough_review_call_id'] == 'glm_005_review_0'
        assert state.artifacts[0][0] == 'restored_rough_handoff'
        return dict(final_video=str(fine), final_sha256=restored.sha256_file(fine), joint_quality_gate=True)
    monkeypatch.setattr(restored, 'execute_finecut', refine)
    args=argparse.Namespace(home=home,output=lane,reference=tmp_path/'ref.mp4',library=tmp_path/'movies',
                            asr_model_dir=tmp_path/'whisper')
    return args,result,events,state


def test_restored_generation_precedes_actual_handoff_and_skill(restoration):
    args,rough,events,state=restoration
    result=restored.execute(args)
    assert [event[0] for event in events] == ['rough','fine']
    assert result['rough_duration_s'] == 81
    assert result['joint_quality_gate'] is False
    assert result['usage']['lineage_cumulative_vision_requests'] == 315
    assert result['same77_output_is_not_guaranteed'] is True
    assert (args.output/'mcp_stop').read_text() == 'restoration_settled'


def test_rough_causal_or_identity_failure_does_not_enter_finecut(restoration):
    args,rough,events,state=restoration
    rough['review']['continuity_status']='fail'
    result=restored.execute(args)
    assert result['status'] == 'rough_content_not_established'
    assert [event[0] for event in events] == ['rough']


def test_settled_result_is_cache_only(restoration):
    args,rough,events,state=restoration
    original=restored.execute(args);events.clear()
    assert restored.execute(args) == original and events == []


def test_failed_stage_stops_and_preserves_scope_without_retry(restoration,monkeypatch):
    args,rough,events,state=restoration
    def fail(*args,**kwargs):raise ValueError('model_protocol_repair_exhausted:plan_0')
    monkeypatch.setattr(restored,'run_rough',fail)
    with pytest.raises(ValueError,match='repair_exhausted'):restored.execute(args)
    recorded=json.loads((args.output/'restoration_failure.json').read_text())
    assert recorded['automatic_restart'] is False
    with pytest.raises(restored.LibraryStopped,match='no_automatic_restart'):restored.execute(args)
