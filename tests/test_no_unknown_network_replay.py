"""Exercise actual fetch guard with a fake transport; never contact a provider."""
import base64
import json
from pathlib import Path
import shutil
import subprocess
import os

import pytest

from omni_story.library.state import json_sha
from omni_story.library.media import sha256_file


@pytest.mark.parametrize('workflow,stage',[('reference_rough_skill_v1','reference'),('visual_library_v1','fine_'+'a'*16),('visual_audio_v1','audio_observe')])
@pytest.mark.parametrize('aggregate',[False,True])
def test_clean_library_and_audio_jobs_never_repost_unknown_even_with_legacy_retry_env(tmp_path,workflow,stage,aggregate):
    if not shutil.which('node'):pytest.skip('Node needed for actual fetch guard')
    package=tmp_path/'package';undici=package/'node_modules/undici';undici.mkdir(parents=True)
    (undici/'package.json').write_text('{"type":"module"}')
    (undici/'index.js').write_text('export class Agent {} export class ProxyAgent {}')
    root=tmp_path/'task';root.mkdir();media=root/'image.jpg';media.write_bytes(b'bound actual bytes')
    config=dict(provider='official_vision_mcp_in_opencode',workflow=workflow,progress_policy='finite_stages_no_retry_v1',transport='opencode_cli_official_mcp_v1',vision_model='glm-5.3-flash',agent_model='zhipuai-coding-plan/glm-5.3-flash',vision_platform='ZHIPU')
    request=dict(provider=config['provider'],policy_version='fixture',tool='analyze_image',media_sha256=sha256_file(media),arguments=dict(image_source=str(media),prompt='Original bound request'))
    call=dict(id='current',name=stage,status='submitted',request_sha256=json_sha(request))
    source=dict(path=str(media),sha256=sha256_file(media))
    state=dict(task_id='original',max_requests=None,request_count=1,calls=[call],policy_version='fixture',input_lock=dict(configuration=config,video=source,reference=source))
    (root/'library_state.json').write_text(json.dumps(state));(root/'mcp_current.json').write_text('{"job_id":"current"}')
    folder=root/'calls/current';folder.mkdir(parents=True);(folder/'request.json').write_text(json.dumps(request))
    body=dict(model=config['vision_model'],stream=False,messages=[dict(role='system',content='Fixture'),dict(role='user',content=[
        dict(type='image_url',image_url=dict(url='data:image/jpeg;base64,'+base64.b64encode(media.read_bytes()).decode())),dict(type='text',text=request['arguments']['prompt'])])])
    module=(Path(__file__).parents[1]/'src/omni_story/library/mcp_guard.mjs').as_uri()
    cause=dict(name='AggregateError',errors=[dict(code='ECONNREFUSED',message='fixture connection refused')]) if aggregate else dict(code='ECONNRESET')
    code='''let sends=0;globalThis.fetch=async()=>{sends++;const e=new Error('socket failed');e.cause=CAUSE;throw e;};
await import(MODULE);
const errors=[];for(let i=0;i<2;i++){try{await fetch('https://open.bigmodel.cn/api/paas/v4/chat/completions',{method:'POST',body:JSON.stringify(BODY)});}catch(e){errors.push(e.message);}}
console.log(JSON.stringify({sends,errors}));'''.replace('MODULE',json.dumps(module)).replace('BODY',json.dumps(body)).replace('CAUSE',json.dumps(cause))
    env={key:value for key,value in os.environ.items() if not key.startswith('OMNI_LIBRARY_')}
    env.update(OMNI_LIBRARY_MCP_ROOT=str(root),OMNI_LIBRARY_MCP_PACKAGE_ROOT=str(package),OMNI_LIBRARY_NETWORK_RETRY_MAX='2')
    result=subprocess.run(['node','--input-type=module','-e',code],capture_output=True,text=True,env=env,timeout=20)
    assert result.returncode==0,result.stderr
    value=json.loads(result.stdout.strip().splitlines()[-1])
    assert value['sends']==1 and value['errors'][1]=='library_mcp_retry_or_budget_blocked',value
    events=[json.loads(line) for line in (root/'mcp_http.jsonl').read_text().splitlines()]
    assert sum(row['type']=='request' for row in events)==1
    assert not any(row['type']=='network_retry_permit' for row in events)
    if aggregate:
        assert next(row for row in events if row['type']=='unknown_result')['cause']['errors']==cause['errors']
