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
            self.corrected_review=dict(reference_sha256=reference['sha256'], theme_status='partial',
                editing_status='partial',continuity_status='pass', evidence=['Actual output 1–2s.'],
                limitations=['Native sampling remains unknown.'], revision_requests=[])
        def _reload(self): pass
        def set_artifact(self, name, value): self.artifacts.append((name,value))
    state = State()
    events=[]
    class Client:
        def __init__(self, given, **kwargs):
            assert given is state and kwargs['exclusions'] == ['preserved historical exclusions']
        def call(self, name, prompt, media, validator):
            events.append(('selected_review', dict(name=name,prompt=prompt,media=str(media))))
            assert json.loads((lane/'restoration_progress.json').read_text())['stage'] == 'selected_review_actual_rough'
            validator(state.corrected_review)
            call=dict(id='glm_006_'+name, name=name, status='received')
            state.data['calls'].append(call)
            state.data['request_count']+=1
            restored.write_json(lane/'calls'/call['id']/'parsed.json', state.corrected_review)
            return dict(state.corrected_review)
    monkeypatch.setattr(restored,'RestorationMCP',Client)
    result = dict(final_video=str(rough), final_sha256=source['sha256'], selected_round=0,
        selected_review_path=str(lane/'review_0.json'),
        review={'theme_status':'partial','continuity_status':'pass','editing_status':'partial'})
    restored.write_json(lane/'result.json',result)
    restored.write_json(lane/'review_0.json',result['review'])
    restored.write_json(lane/'plan_0.json',{'segments':[{'segment_id':'current_model_choice'}]})
    restored.write_json(lane/'blind_reading_0.json',{'apparent_story':'Current actual rough sequence.'})
    restored.write_json(lane/'render_0/render_result.json',dict(sha256=source['sha256'],
        measured_duration_s=source['duration_s'],provenance=[{'segment_id':'current_model_choice'}]))
    proxy=lane/'full_rough_proxy.mp4';proxy.write_bytes(b'whole current rough proxy')
    def prepare(given,start,end,cache,*,fps):
        assert given == source and start == 0 and end == 81 and cache == lane/'cache' and fps == 12
        return {'path':str(proxy)}
    monkeypatch.setattr(restored,'prepare_window',prepare)
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
        assert handoff['rough_review_call_id'] == 'glm_006_selected_review_v2_0'
        assert handoff['rough_review'] == state.corrected_review
        assert context['prior_rough_review'] == state.corrected_review
        assert context['actual_rough_handoff'] == handoff
        assert state.artifacts[0][0] == 'restored_rough_handoff'
        return dict(final_video=str(fine), final_sha256=restored.sha256_file(fine), joint_quality_gate=True)
    monkeypatch.setattr(restored, 'execute_finecut', refine)
    args=argparse.Namespace(home=home,output=lane,reference=tmp_path/'ref.mp4',library=tmp_path/'movies',
                            asr_model_dir=tmp_path/'whisper')
    return args,result,events,state


def test_restored_generation_precedes_actual_handoff_and_skill(restoration):
    args,rough,events,state=restoration
    result=restored.execute(args)
    assert [event[0] for event in events] == ['rough','selected_review','fine']
    _,observed=events[1]
    prefix=restored.selected_review.review_prompt({})[:-2]
    assert observed['prompt'].startswith(prefix)
    context=json.loads(observed['prompt'][len(prefix):])
    assert context['reference']['theme'] == 'original reference'
    assert context['reference_protocol_limit'] == 'old interpretation limitations'
    assert context['plan']['segments'][0]['segment_id'] == 'current_model_choice'
    assert context['blind_reading']['apparent_story'] == 'Current actual rough sequence.'
    assert context['actual_render_sha256'] == rough['final_sha256']
    assert result['rough']['selected_review_path'] == str(args.output/'selected_review_v2_0.json')
    assert result['rough_duration_s'] == 81
    assert result['joint_quality_gate'] is False
    assert result['usage']['lineage_cumulative_vision_requests'] == 316
    assert result['same77_output_is_not_guaranteed'] is True
    assert (args.output/'mcp_stop').read_text() == 'restoration_settled'


@pytest.mark.parametrize('field', ['theme_status', 'continuity_status'])
def test_rough_causal_or_identity_failure_does_not_enter_finecut(restoration, field):
    args,rough,events,state=restoration
    state.corrected_review[field]='fail'
    result=restored.execute(args)
    assert result['status'] == 'rough_content_not_established'
    assert [event[0] for event in events] == ['rough','selected_review']


def test_original_identity_failure_can_be_corrected_without_overwriting_prior_result(restoration):
    args,rough,events,state=restoration
    rough['review']['continuity_status']='fail'
    restored.write_json(args.output/'result.json',rough)
    restored.write_json(args.output/'review_0.json',rough['review'])
    original_result=(args.output/'result.json').read_bytes()
    original_review=(args.output/'review_0.json').read_bytes()
    outcome=restored.execute(args)
    assert outcome['status'] == 'restored_rough_to_fine_completed'
    assert outcome['rough']['review']['continuity_status'] == 'pass'
    assert rough['review']['continuity_status'] == 'fail'
    assert (args.output/'result.json').read_bytes() == original_result
    assert (args.output/'review_0.json').read_bytes() == original_review
    reviewed=json.loads((args.output/'restored_reviewed_rough.json').read_text())
    assert reviewed == outcome['rough']


def test_existing_selected_review_is_verified_and_reused_without_another_call(restoration):
    args,rough,events,state=restoration
    rough.update(selected_review_path=str(args.output/'selected_review_v2_0.json'),
                 review=dict(state.corrected_review))
    restored.write_json(args.output/'selected_review_v2_0.json',rough['review'])
    call=dict(id='glm_006_selected_review_v2_0',name='selected_review_v2_0',status='received')
    state.data['calls'].append(call);state.data['request_count']+=1
    restored.write_json(args.output/'calls'/call['id']/'parsed.json',rough['review'])
    restored.write_json(args.output/'calls'/call['id']/'request.json',
                        {'arguments':{'prompt':restored.selected_review.review_prompt({})}})
    outcome=restored.execute(args)
    assert [event[0] for event in events] == ['rough','fine']
    assert outcome['rough']['review'] == rough['review']
    assert state.data['request_count'] == 6


def test_old027_prefix_under_selected_alias_is_rejected_without_another_request(restoration):
    from omni_story.library.resources import original_rough_v1
    args,rough,events,state=restoration
    rough.update(selected_review_path=str(args.output/'selected_review_v2_0.json'),
                 review=dict(state.corrected_review))
    restored.write_json(args.output/'selected_review_v2_0.json',rough['review'])
    call=dict(id='glm_006_selected_review_v2_0',name='selected_review_v2_0',status='received')
    state.data['calls'].append(call);state.data['request_count']+=1
    restored.write_json(args.output/'calls'/call['id']/'parsed.json',rough['review'])
    restored.write_json(args.output/'calls'/call['id']/'request.json',
                        {'arguments':{'prompt':original_rough_v1.review_prompt({})}})
    with pytest.raises(restored.LibraryStopped,match='selected_review_cache_changed'):
        restored.execute(args)
    assert [event[0] for event in events] == ['rough']
    assert state.data['request_count'] == 6


def test_mismatched_selected_review_cache_stops_without_a_new_request(restoration):
    args,rough,events,state=restoration
    rough.update(selected_review_path=str(args.output/'selected_review_v2_0.json'),
                 review=dict(state.corrected_review))
    restored.write_json(args.output/'selected_review_v2_0.json',{'changed':True})
    with pytest.raises(restored.LibraryStopped,match='selected_review_cache_changed'):
        restored.execute(args)
    assert [event[0] for event in events] == ['rough']
    assert state.data['request_count'] == 5


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


def test_new_registration_binds_exact043_resource_provenance(restoration,monkeypatch):
    from omni_story.library.resources import original_rough_v1
    args,rough,events,state=restoration
    args.reference.write_bytes(b'reference input')
    args.library.mkdir()
    args.asr_model_dir.mkdir()
    seed_path=args.output/'seed.json'
    restored.write_json(seed_path,{'source_call_id':'glm_001_reference'})
    registered={}
    def register(*values,**options):
        registered.update(options)
    monkeypatch.setattr(restored.policy,'register',register)
    monkeypatch.setattr(restored,'credential',lambda home:'unused offline test credential')
    monkeypatch.setattr(restored,'settings',lambda home:{'project_root':str(args.home)})
    monkeypatch.setattr(restored.server_jobs,'_launch_arguments',lambda command,cwd:cwd)
    monkeypatch.setattr(restored.server_jobs,'_launch',lambda command,output,cwd:{'state':'offline test only'})
    restored.main(['--home',str(args.home),'start','--output',str(args.output),
        '--reference',str(args.reference),'--library',str(args.library),'--asr-model-dir',str(args.asr_model_dir),
        '--parent-output',str(args.output.parent),'--reference-seed',str(seed_path),'--user-instruction','那你恢复'])
    provenance=registered['method_provenance']
    assert provenance['selected_actual_video_review'] == restored.selected_review.provenance()
    assert {key:value for key,value in provenance.items() if key != 'selected_actual_video_review'} == original_rough_v1.provenance()
    assert events == []
