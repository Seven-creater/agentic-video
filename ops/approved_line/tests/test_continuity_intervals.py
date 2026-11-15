from copy import deepcopy
import json
from pathlib import Path

import pytest

from omni_story.library.material_browser import validate_continuity_groups,continuity_request_key


def mapping(wid='window', start=0, duration=17):
    return [dict(window_id=wid,start_s=start,end_s=start+duration,media_duration_s=duration)]


def payload(segments):
    return dict(groups=[dict(setting='visible place',participants='visible people',
        evidence='observed continuity',limitations='unknown audio',segments=segments)],issues=[])


@pytest.mark.parametrize('index,retained',[(0,3),(1,3),(2,2)])
def test_paid_zero_duration_replies_keep_only_observed_positive_ranges(index,retained):
    raw=json.loads(Path(__file__).with_name('continuity_zero_duration_fixtures.json').read_text())[index]
    original=deepcopy(raw)
    segments=[r for g in raw['groups'] for r in g['segments']]
    wid=segments[0]['window_id']
    accepted=validate_continuity_groups(deepcopy(raw),mapping(wid))
    assert raw==original
    assert [r for g in accepted['groups'] for r in g['segments']]==[
        r for r in segments if r['end_s']>r['start_s']]
    assert len(accepted['groups'])==retained
    assert all(r['segment']['start_s']==r['segment']['end_s']
        for r in accepted['excluded_unusable_segments'])
    assert validate_continuity_groups(deepcopy(accepted),mapping(wid))==accepted


def test_all_point_observations_produce_no_usable_scene():
    value=payload([dict(window_id='window',start_s=5,end_s=5)])
    result=validate_continuity_groups(value,mapping())
    assert result['groups']==[] and result['issues']
    assert result['excluded_unusable_segments'][0]['segment']==dict(window_id='window',start_s=5,end_s=5)


@pytest.mark.parametrize('a,b,message',[
    (5,4,'greater than'),(-1,4,'outside clip'),(1,18,'outside clip'),
    (100,100,'greater than'),(float('nan'),3,'finite'),(0,float('inf'),'finite'),
    (True,3,'finite'),
])
def test_invalid_intervals_remain_rejected_with_exact_path(a,b,message):
    value=payload([dict(window_id='window',start_s=a,end_s=b)])
    with pytest.raises(ValueError,match=message) as caught:
        validate_continuity_groups(value,mapping())
    assert 'groups[0].segments[0] window' in str(caught.value)


def test_unknown_window_is_not_silently_dropped_even_for_a_point():
    with pytest.raises(ValueError,match='unknown continuity window'):
        validate_continuity_groups(payload([dict(window_id='missing',start_s=2,end_s=2)]),mapping())


def test_unique_montage_coordinates_preserve_duration_and_record_conversion():
    value=payload([dict(window_id='window',start_s=21,end_s=24)])
    accepted=validate_continuity_groups(value,mapping(start=20))
    assert accepted['groups'][0]['segments']==[dict(window_id='window',start_s=1,end_s=4)]
    assert len(accepted['coordinate_normalizations'])==1


def test_new_interval_policy_has_distinct_stable_request_identity():
    from omni_story.library.state import json_sha
    ids=['vl_0123456789abcdef']
    assert continuity_request_key(ids)!=json_sha(ids)[:16]
    assert continuity_request_key(list(ids))==continuity_request_key(ids)


@pytest.mark.parametrize('compatible',[True,False])
def test_actual_mcp_guard_accepts_versioned_key_without_loosening_stage_allowlist(tmp_path,compatible):
    import base64,hashlib,subprocess
    from omni_story.library import material_browser,prompts
    from omni_story.library.state import LibraryState
    provider='official_vision_mcp_in_opencode'
    config=dict(provider=provider,transport='opencode_cli_official_mcp_v1',
        progress_policy='finite_stages_no_retry_v1',vision_model='glm-5.3-flash',
        agent_model='zhipuai-coding-plan/glm-5.3-flash',workflow='reference_rough_skill_v1',
        material_flow='scene_v1',creative_audio=True,creative_style='picture_aware',
        visual_library_root=str(tmp_path/'library'))
    root=tmp_path/'task';state=LibraryState(root,dict(configuration=config),max_requests=None)
    media=root/'clip.mp4';media.write_bytes(b'exact official transport fixture bytes')
    key=continuity_request_key(['vl_0123456789abcdef'])
    stage='material_scene_browser_continuity_'+('' if compatible else 'ranges_v2_')+key
    prompt='Independently observe actual scene continuity.'
    request=dict(tool='analyze_video',arguments=dict(video_source=str(media),prompt=prompt),
        media_sha256=hashlib.sha256(media.read_bytes()).hexdigest(),provider=provider,
        policy_version=prompts.POLICY_VERSION)
    call,_=state.begin_call(stage,request)
    body=dict(model='glm-5.3-flash',stream=False,messages=[dict(role='user',content=[
        dict(type='video_url',video_url=dict(url='data:video/mp4;base64,'+base64.b64encode(media.read_bytes()).decode())),
        dict(type='text',text=prompt)])])
    (root/'guard_payload.json').write_text(json.dumps(dict(job=dict(job_id=call['id']),body=body)))
    guard=Path(material_browser.__file__).with_name('mcp_opencode_guard.mjs')
    script="""
import fs from 'node:fs';
const {opencodeRequestLimit}=await import(process.argv[1]);
const root=process.argv[2],p=JSON.parse(fs.readFileSync(root+'/guard_payload.json','utf8'));
try {console.log(String(opencodeRequestLimit(root,p.job,p.body)));}
catch(e) {console.log(e.message);}
"""
    result=subprocess.run(['node','--input-type=module','-e',script,guard.as_uri(),str(root)],
        capture_output=True,text=True,timeout=15)
    assert result.returncode==0,result.stderr
    assert result.stdout.strip()==('Infinity' if compatible else 'library_mcp_opencode_stage_duplicate_or_not_permitted')
