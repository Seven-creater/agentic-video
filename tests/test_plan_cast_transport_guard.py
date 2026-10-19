"""Exercise the actual native POST guard with a fully local bound correction."""
import base64
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from omni_story.library.media import sha256_file
from omni_story.library.state import json_sha


def fixture(tmp_path):
    media=tmp_path/'media.jpg';media.write_bytes(b'local image bytes')
    request=dict(tool='analyze_image',arguments=dict(image_source=str(media),prompt='original\nverified cast constraints'),
        media_sha256=sha256_file(media),provider='official_vision_mcp_in_opencode',policy_version='fixture')
    paid=[dict(id='old_'+str(n),name='plan_1' if n==8 else 'plan_1_repair' if n==9 else 'old_'+str(n),status='received',repair_of='old_8' if n==9 else None) for n in range(10)]
    failed=dict(id='local_guard',name='plan_1_cast_correction_v1',status='failed_known',request_sha256=json_sha(request))
    folder=tmp_path/'calls'/failed['id'];folder.mkdir(parents=True)
    (folder/'request.json').write_text(json.dumps(request))
    failure=folder/'failure.json';failure.write_text(json.dumps(dict(uncertain=False,error='library_mcp_opencode_stage_duplicate_or_not_permitted')))
    previous=tmp_path/'previous.json';previous.write_text(json.dumps(dict(policy='received_plan_cast_correction_v1',expected_request=request)))
    configuration=dict(provider=request['provider'],workflow='reference_rough_skill_v1',progress_policy='finite_stages_no_retry_v1',
        transport='opencode_cli_official_mcp_v1',vision_model='glm-5.3-flash',agent_model='zhipuai-coding-plan/glm-5.3-flash')
    state=dict(task_id='original',input_lock=dict(configuration=configuration),max_requests=None,policy_version='fixture',calls=paid+[failed],request_count=11)
    snapshot=tmp_path/'baseline.json';snapshot.write_text(json.dumps(state))
    stop=tmp_path/'plan_cast_transport_v1.original_mcp_stop';stop.write_text('reference_rough_skill_settled')
    suffix='\nLocal stage grant fixed; same one bounded correction.'
    expected=deepcopy(request);expected['arguments']['prompt']+=suffix
    alias='plan_1_cast_correction_v1_preflight_v1'
    proof=dict(policy='received_plan_cast_transport_v1',output=str(tmp_path),plan_name='plan_1',max_new_correction_calls=1,
        baseline_snapshot=str(snapshot),state_sha256=sha256_file(snapshot),baseline_request_count=11,
        failed_preflight_id=failed['id'],previous_proof=str(previous),previous_proof_sha256=sha256_file(previous),
        expected_request=expected,transport_prompt_suffix=suffix,correction_alias=alias,
        protected_sha256={str(snapshot):sha256_file(snapshot),str(failure):sha256_file(failure),str(tmp_path/'mcp_stop'):sha256_file(stop)})
    current=dict(id='new_correction',name=alias,status='submitted',request_sha256=json_sha(expected),repair_of=None)
    folder=tmp_path/'calls'/current['id'];folder.mkdir()
    (folder/'request.json').write_text(json.dumps(expected))
    state['calls'].append(current);state['request_count']=12
    return state,proof,expected


def run_guard(root,state,proof,request):
    if not shutil.which('node'):pytest.skip('Node required for actual native POST guard')
    (root/'library_state.json').write_text(json.dumps(state))
    p=root/'proof.json';p.write_text(json.dumps(proof))
    (root/'plan_cast_transport_v1.claim.json').write_text(json.dumps(dict(proof_path=str(p),proof_sha256=sha256_file(p))))
    body=dict(model='glm-5.3-flash',stream=False,messages=[dict(role='system',content='fixture'),dict(role='user',content=[
        dict(type='image_url',image_url=dict(url='data:image/jpeg;base64,'+base64.b64encode(Path(request['arguments']['image_source']).read_bytes()).decode())),
        dict(type='text',text=request['arguments']['prompt'])])])
    module=(Path(__file__).parents[1]/'src/omni_story/library/mcp_opencode_guard.mjs').as_uri()
    code='import {opencodeRequestLimit} from '+json.dumps(module)+';try {console.log(opencodeRequestLimit('+json.dumps(str(root))+','+json.dumps(dict(job_id=state['calls'][-1]['id']))+','+json.dumps(body)+'));}catch(e){console.log(e.message);process.exitCode=2;}'
    return subprocess.run(['node','--input-type=module','-e',code],capture_output=True,text=True,timeout=15)


def test_exact_local_preflight_grant_and_later_regular_stage_pass(tmp_path):
    state,proof,request=fixture(tmp_path)
    result=run_guard(tmp_path,state,proof,request)
    assert result.returncode==0 and result.stdout.strip()=='Infinity',result.stdout+result.stderr
    state['calls'][-1]['status']='received'
    request=deepcopy(request);request['arguments']['prompt']='new ordinary blind stage'
    current=dict(id='normal_blind',name='blind_1',status='submitted',request_sha256=json_sha(request),repair_of=None)
    folder=tmp_path/'calls'/current['id'];folder.mkdir();(folder/'request.json').write_text(json.dumps(request))
    state['calls'].append(current);state['request_count']+=1
    result=run_guard(tmp_path,state,proof,request)
    assert result.returncode==0,result.stdout+result.stderr


@pytest.mark.parametrize('change',['wrong_digest','duplicate_alias','old_post','unknown_failure','other_alias','unbound_request','changed_original'])
def test_correction_guard_rejects_unbound_or_uncertain_work(tmp_path,change):
    state,proof,request=fixture(tmp_path)
    if change=='wrong_digest':state['calls'][-1]['request_sha256']='b'*64
    if change=='duplicate_alias':state['calls'].append(deepcopy(state['calls'][-1]));state['request_count']+=1
    if change=='old_post':(tmp_path/'mcp_http.jsonl').write_text(json.dumps(dict(job_id='local_guard',type='request'))+'\n')
    if change=='unknown_failure':state['calls'][10]['status']='uncertain'
    if change=='other_alias':state['calls'][-1]['name']='plan_1_cast_correction_v1_preflight_v2'
    if change=='unbound_request':proof['expected_request']['arguments']['prompt']+='invented'
    if change=='changed_original':state['calls'][0]['name']='changed'
    result=run_guard(tmp_path,state,proof,request)
    assert result.returncode==2,result.stdout+result.stderr
