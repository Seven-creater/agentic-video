"""One authorized generic-skill trial appended to the existing GLM ledger."""
from copy import deepcopy
import json
import math
from pathlib import Path
import re

from .slot_finecut_file_proofs import verified_sha256 as sha256_file
from .state import LibraryState, LibraryStopped, file_lock, json_sha, scope_fingerprint, write_json

POLICY = 'visual_story_skill_trial_v1'
BASELINE = 250
RESUME_POLICY = 'vss_unknown_265_resume_v1'
RESUME_BASELINE = 265
RESUME_LOST_ID = 'glm_265_vss_detail_3_0'
RESUME_SKIP_STAGE = 'vss_detail_3_0'
STAGES = r'^vss_(observe|inspect_[0-9]+|detail_[0-9]+_[0-9]+|plan|selected_[0-9]+|blind|review|revise|selected_r_[0-9]+|blind_r|review_r)(?:_repair)?$'

def read(path):
    return json.loads(Path(path).read_text('utf-8'))

def require(test, reason):
    if not test:
        raise LibraryStopped('visual_story:' + reason)

def same_observation(left,right):
    """Continuous kinds and sub-microsecond rounding cannot disguise a replay."""
    a,b=scope_fingerprint(left),scope_fingerprint(right)
    continuous={'continuous_window','complete_file'}
    same_kind=a[0]==b[0] or a[0] in continuous and b[0] in continuous
    return same_kind and a[1]==b[1] and abs(a[2]-b[2])<=1e-6 and abs(a[3]-b[3])<=1e-6

def same_source_interval(left,right):
    """The skipped lost page cannot be replayed under another media kind."""
    a,b=scope_fingerprint(left),scope_fingerprint(right)
    return a[1]==b[1] and abs(a[2]-b[2])<=1e-6 and abs(a[3]-b[3])<=1e-6

def _get_unknown_resume(output,data,auth,*,force=False):
    rows=data['artifacts'].get(RESUME_POLICY,[])
    if not rows:
        return None
    require(len(rows)==1,'one_unknown_resume_required')
    path=Path(rows[0]['path']).resolve(strict=True)
    resume=read(path)
    require(path.is_relative_to(output/'artifacts') and json_sha(resume)==rows[0]['sha256'],
            'unknown_resume_changed')
    require(resume['policy']==RESUME_POLICY and resume['task_id']==data['task_id'] and
            resume['authorization_sha256']==data['artifacts'][POLICY][0]['sha256'] and
            resume['input_lock_sha256']==json_sha(data['input_lock']) and
            resume['baseline_request_count']==RESUME_BASELINE and
            resume['lost_call_id']==RESUME_LOST_ID and
            resume['skip_stages']==[RESUME_SKIP_STAGE] and resume['new_renders']==0 and
            resume['goal_resumed'] is False and resume['teacher_answers_forbidden'] is True and
            isinstance(resume['user_instruction'],str) and bool(resume['user_instruction'].strip()),
            'unknown_resume_scope_changed')
    snapshot=Path(resume['baseline_state_path']).resolve(strict=True)
    old=read(snapshot)
    require(snapshot.is_relative_to(output/'artifacts') and
            sha256_file(snapshot,force=force)==resume['baseline_state_sha256'] and
            old['request_count']==len(old['calls'])==RESUME_BASELINE and
            data['calls'][:RESUME_BASELINE]==old['calls'] and
            json_sha(old['calls'])==resume['prefix_calls_sha256'] and
            old['input_lock']==data['input_lock'] and old['max_requests']==data['max_requests'] and
            old['policy_version']==data['policy_version'] and
            old.get('continuation_policy')==data.get('continuation_policy')==
                'independent_media_no_unknown_replay_v1', 'unknown_resume_ledger_prefix_changed')
    for key,value in old['artifacts'].items():
        require(data['artifacts'].get(key)==value,'unknown_resume_artifact_prefix_changed:'+key)
    require(all(c['status']=='received' for c in old['calls'][BASELINE:-1]) and
            old['calls'][-1]['id']==RESUME_LOST_ID and old['calls'][-1]['name']==RESUME_SKIP_STAGE and
            old['calls'][-1]['status']=='uncertain','unknown_resume_lost_status_changed')
    request=read(output/'calls'/RESUME_LOST_ID/'request.json')
    require(resume['lost_request_sha256']==old['calls'][-1]['request_sha256']==json_sha(request) and
            resume['lost_media_sha256']==request['media_sha256'] and
            resume['lost_scope']==request['observation_scope'], 'unknown_resume_lost_input_changed')
    for name in ('response.json','parsed.json'):
        require(not(output/'calls'/RESUME_LOST_ID/name).exists(),'unknown_resume_reply_fabricated')
    require(set(resume['baseline_call_files'])=={c['id'] for c in old['calls']},
            'unknown_resume_call_file_inventory_changed')
    for call_id,files in resume['baseline_call_files'].items():
        folder=output/'calls'/call_id
        current=sorted(str(p.relative_to(folder)) for p in folder.rglob('*') if p.is_file())
        require(current==files,'unknown_resume_call_files_changed:'+call_id)
    mutable=Path(auth['execution_directory']).resolve()/'progress.json'
    require(all(Path(row['path']).resolve()!=mutable for row in resume['protected_files']),
            'unknown_resume_mutable_progress_protected')
    for row in resume['protected_files']:
        require(sha256_file(row['path'],force=force)==row['sha256'],
                'unknown_resume_protected_bytes_changed:'+row['path'])
    from .forward_slot_budget import _prefix_sha
    for row in resume['journal_prefixes']:
        require(_prefix_sha(row['path'],row['bytes'],force=force)==row['sha256'],
                'unknown_resume_journal_prefix_changed')
    return {**resume,'artifact_path':str(path),'artifact_sha256':sha256_file(path,force=force)}

def record_unknown_resume(output,user_instruction):
    """Append explicit permission to skip one lost page, never replay its POST."""
    output=Path(output).resolve(strict=True)
    auth=get_auth(output,force=True)
    if auth.get('unknown_resume'):
        return auth['unknown_resume']
    data=read(output/'library_state.json')
    require(isinstance(user_instruction,str) and bool(user_instruction.strip()),'resume_instruction_required')
    require(data['request_count']==len(data['calls'])==RESUME_BASELINE and
            data['calls'][-1]['id']==RESUME_LOST_ID and
            data['calls'][-1]['name']==RESUME_SKIP_STAGE and data['calls'][-1]['status']=='uncertain' and
            all(c['status']=='received' for c in data['calls'][BASELINE:-1]),'settled_265_resume_required')
    require(data.get('continuation_policy')=='independent_media_no_unknown_replay_v1',
            'base_independent_continuation_required')
    require(not(Path(auth['execution_directory'])/'result.json').exists(),'trial_finished_no_resume')
    request=read(output/'calls'/RESUME_LOST_ID/'request.json')
    base=output/'artifacts'/RESUME_POLICY
    base.mkdir(parents=True,exist_ok=True)
    snapshot=base/'baseline_state.json'
    raw=(output/'library_state.json').read_bytes()
    require(not snapshot.exists() or snapshot.read_bytes()==raw,'resume_snapshot_changed')
    snapshot.write_bytes(raw)
    from .forward_slot_budget import _history
    files,prefixes=_history(output)
    mutable=Path(auth['execution_directory']).resolve()/'progress.json'
    files=[row for row in files if Path(row['path']).resolve()!=mutable]
    call_files={c['id']:sorted(str(p.relative_to(output/'calls'/c['id'])) for p in
                              (output/'calls'/c['id']).rglob('*') if p.is_file()) for c in data['calls']}
    resume=dict(policy=RESUME_POLICY,task_id=data['task_id'],user_instruction=user_instruction,
        authorization_sha256=data['artifacts'][POLICY][0]['sha256'],
        input_lock_sha256=json_sha(data['input_lock']),baseline_request_count=RESUME_BASELINE,
        baseline_state_path=str(snapshot),baseline_state_sha256=sha256_file(snapshot),
        prefix_calls_sha256=json_sha(data['calls']),baseline_call_files=call_files,
        protected_files=files,journal_prefixes=prefixes,lost_call_id=RESUME_LOST_ID,
        lost_request_sha256=json_sha(request),lost_media_sha256=request['media_sha256'],
        lost_scope=request['observation_scope'],skip_stages=[RESUME_SKIP_STAGE],new_renders=0,
        goal_resumed=False,teacher_answers_forbidden=True,
        progress_policy='skip_one_lost_page_continue_original_trial_no_POST_replay_no_new_round')
    state=LibraryState(output,data['input_lock'],max_requests=data['max_requests'])
    state.set_artifact(RESUME_POLICY,resume)
    return get_auth(output,force=True)['unknown_resume']

def get_auth(output, *, force=False):
    output=Path(output).resolve(strict=True)
    data=read(output/'library_state.json')
    entries=data['artifacts'].get(POLICY, [])
    require(len(entries)==1,'one_authorization_required')
    path=Path(entries[0]['path']).resolve(strict=True)
    auth=read(path)
    require(path.is_relative_to(output/'artifacts') and json_sha(auth)==entries[0]['sha256'], 'authorization_changed')
    require(auth['policy']==POLICY and auth['task_id']==data['task_id'] and
            auth['input_lock_sha256']==json_sha(data['input_lock']) and
            auth['baseline_request_count']==BASELINE and auth['max_concurrency']==1 and
            auth['max_renders']==2 and auth['repairs_per_stage']==1 and
            auth['numeric_total_request_limit'] is None and auth['teacher_answers_forbidden'] is True,
            'authorization_scope_changed')
    old=read(auth['baseline_state_path'])
    require(sha256_file(auth['baseline_state_path'],force=force)==auth['baseline_state_sha256'] and
            old['request_count']==len(old['calls'])==BASELINE and
            data['calls'][:BASELINE]==old['calls'] and json_sha(old['calls'])==auth['prefix_calls_sha256'] and
            data['request_count']==len(data['calls']) and
            data['max_requests']==old['max_requests'] and data['policy_version']==old['policy_version'],
            'historical_ledger_changed')
    for key,value in old['artifacts'].items():
        require(data['artifacts'].get(key)==value,'historical_artifact_changed:'+key)
    for row in [*auth['protected_files'],*auth['knowledge_files']]:
        require(sha256_file(row['path'],force=force)==row['sha256'],'protected_bytes_changed:'+row['path'])
    from .forward_slot_budget import _prefix_sha
    for row in auth['journal_prefixes']:
        require(_prefix_sha(row['path'],row['bytes'],force=force)==row['sha256'],'historical_journal_prefix_changed')
    resume=_get_unknown_resume(output,data,auth,force=force)
    if resume:
        auth={**auth,'unknown_resume':resume}
    new=data['calls'][BASELINE:]
    require(all(re.fullmatch(STAGES,c['name']) for c in new) and
            len({c['name'] for c in new})==len(new),'new_stage_changed')
    require(not any(c['status']=='submitted' for c in old['calls']),'old_pending')
    for entry in new:
        request=read(output/'calls'/entry['id']/'request.json')
        require(json_sha(request)==entry['request_sha256'],'new_request_changed')
        check_input(output,data,auth,entry['name'],request,
                    recorded_lost_call_id=entry['id'] if resume and entry['id']==RESUME_LOST_ID else None)
        require(entry['status'] in {'submitted','received','uncertain','failed_known'},'new_status_changed')
        if entry['status']=='received':
            folder=output/'calls'/entry['id']
            response=read(folder/'response.json')
            require(json_sha(response)==entry['response_sha256'],'new_response_changed')
            if (folder/'parsed.json').exists():
                from .contracts import parse_model_json
                text='\n'.join(c['text'] for c in response['result']['content'] if c.get('type')=='text')
                require(parse_model_json(text)==read(folder/'parsed.json'),'new_parsed_cache_changed')
    return {**auth,'authorization_path':str(path),'authorization_sha256':sha256_file(path,force=force)}

def check_input(output,data,auth,name,request,*,recorded_lost_call_id=None):
    stem=name.removesuffix('_repair')
    require(re.fullmatch(STAGES,name),'stage_not_allowed')
    entries=data['artifacts'].get('vss_input_'+stem,[])
    require(len(entries)==1,'registered_input_required')
    descriptor=read(entries[0]['path'])
    require(json_sha(descriptor)==entries[0]['sha256'] and descriptor['stage']==stem and
            request['tool'] in {'analyze_image','analyze_video'} and
            request['tool']==descriptor['tool'] and request['media_sha256']==descriptor['media_sha256'] and
            scope_fingerprint(request['observation_scope'])==scope_fingerprint(descriptor['scope']),
            'registered_input_changed')
    field='image_source' if request['tool']=='analyze_image' else 'video_source'
    require(Path(request['arguments'][field]).resolve()==Path(descriptor['media_path']).resolve() and
            sha256_file(descriptor['media_path'])==descriptor['media_sha256'],'actual_media_changed')
    for unknown in auth['unknown_inputs']:
        require(request['media_sha256']!=unknown['media_sha256'] and
                json_sha(request)!=unknown['request_sha256'] and
                not same_observation(request['observation_scope'],unknown['scope']),
                'unknown_input_no_replay')
    resume=auth.get('unknown_resume')
    if recorded_lost_call_id is not None:
        require(resume and recorded_lost_call_id==resume['lost_call_id'] and
                name==RESUME_SKIP_STAGE and json_sha(request)==resume['lost_request_sha256'],
                'recorded_lost_input_binding_changed')
    if resume and recorded_lost_call_id is None:
        require(stem not in resume['skip_stages'] and
                request['media_sha256']!=resume['lost_media_sha256'] and
                json_sha(request)!=resume['lost_request_sha256'] and
                not same_source_interval(request['observation_scope'],resume['lost_scope']),
                'lost_265_input_no_replay')
    # The lost full-reference request cannot be disguised as another media kind.
    scope=request['observation_scope']
    require(not(scope['source_sha256']==auth['reference']['sha256'] and scope['source_start_s']==0 and
                abs(scope['source_end_s']-auth['reference']['duration_s'])<.001),
            'full_reference_unknown_do_not_reencode')

def authorize(output,user_instruction):
    output=Path(output).resolve(strict=True)
    data=read(output/'library_state.json')
    if data['artifacts'].get(POLICY):
        return get_auth(output)
    require(bool(user_instruction.strip()) and data['request_count']==len(data['calls'])==BASELINE and
            not any(c['status']=='submitted' for c in data['calls']),'settled_250_required')
    base=output/'artifacts'/POLICY
    base.mkdir(parents=True,exist_ok=True)
    snapshot=base/'baseline_state.json'
    raw=(output/'library_state.json').read_bytes()
    require(not snapshot.exists() or snapshot.read_bytes()==raw,'baseline_snapshot_changed')
    snapshot.write_bytes(raw)
    repo=Path(__file__).resolve().parents[2]
    knowledge=[]
    for relative in ('SKILL.md','references/decision-cards.md'):
        source=repo/'skills/visual-story-finecut'/relative
        destination=base/'knowledge'/relative
        destination.parent.mkdir(parents=True,exist_ok=True)
        require(not destination.exists() or destination.read_bytes()==source.read_bytes(),'knowledge_snapshot_changed')
        destination.write_bytes(source.read_bytes())
        knowledge.append({'path':str(destination),'sha256':sha256_file(destination)})
    from .slot_finecut_budget import _unknown_inputs
    from .forward_slot_budget import _history
    from .media import inventory_sources
    parent=inventory_sources([output/'render_0/final.mp4'],base/'parent_catalog')['sources'][0]
    reference=read(output/'reference_catalog/inventory.json')['sources'][0]
    require(sha256_file(reference['path'])==reference['sha256'],'reference_changed')
    files,prefixes=_history(output)
    # Bind reference outside run as well; whole-library discovery stays frozen.
    files.append({'path':reference['path'],'sha256':reference['sha256']})
    auth=dict(policy=POLICY,task_id=data['task_id'],input_lock_sha256=json_sha(data['input_lock']),
        baseline_request_count=BASELINE,baseline_state_path=str(snapshot),baseline_state_sha256=sha256_file(snapshot),
        prefix_calls_sha256=json_sha(data['calls']),execution_directory=str(base),
        unknown_inputs=_unknown_inputs(output,data['calls']),protected_files=files,journal_prefixes=prefixes,
        knowledge_files=knowledge,parent=parent,reference=reference,max_concurrency=1,max_renders=2,
        repairs_per_stage=1,numeric_total_request_limit=None,teacher_answers_forbidden=True,
        user_instruction=user_instruction,goal_resumed=False,
        progress_policy='one_candidate_plus_at_most_one_evidence_driven_revision_no_automatic_rounds',
        stage_pattern=STAGES,target_duration_s=reference['duration_s'],
        target_tolerance_s=2.0,reference_whole_video_resubmission=False)
    # Existing task/registry remain; this appends permission, never resets usage.
    state=LibraryState(output,data['input_lock'],max_requests=data['max_requests'])
    state.set_artifact(POLICY,auth)
    return get_auth(output,force=True)

class VisualStoryState(LibraryState):
    def __init__(self,output):
        self.output=Path(output).resolve(strict=True)
        self.path=self.output/'library_state.json';self.lock_path=self.output/'.state.lock'
        self.max_requests=math.inf
        self._reload();self.input_lock=deepcopy(self.data['input_lock'])
        get_auth(self.output)

    def begin_call(self,name,request,*,repair_of=None):
        self._reload();auth=get_auth(self.output)
        allowed_unknown=auth.get('unknown_resume',{}).get('lost_call_id')
        require(not any(c['status']=='submitted' or c['status']=='uncertain' and c['id']!=allowed_unknown
                        for c in self.data['calls'][BASELINE:]),'new_unknown_or_pending_no_replay')
        require(not (Path(auth['execution_directory'])/'result.json').exists(),'trial_finished_no_new_stage')
        require(not any(c['name']==name for c in self.data['calls'][BASELINE:]),'stage_already_recorded_reuse_cache')
        check_input(self.output,self.data,auth,name,request)
        if name.endswith('_repair'):
            original=next((c for c in self.data['calls'][BASELINE:] if c['name']==name[:-7]),None)
            require(original and original['status']=='received' and repair_of and
                    (repair_of.get('id') if isinstance(repair_of,dict) else repair_of)==original['id'],
                    'repair_parent_required')
        else:
            require(repair_of is None,'original_cannot_be_repair')
        return super().begin_call(name,request,repair_of=repair_of)

    def _new_call(self,call):
        self._reload()
        auth=get_auth(self.output)
        ident=call['id'] if isinstance(call,dict) else call
        require(any(c['id']==ident for c in self.data['calls'][BASELINE:]),'historical_call_read_only')
        require(not(auth.get('unknown_resume') and
                    any(c['id']==ident for c in self.data['calls'][:RESUME_BASELINE])),
                'unknown_resume_prefix_call_read_only')

    def complete_call(self,call,response,*,usage=None):
        self._new_call(call)
        return super().complete_call(call,response,usage=usage)

    def fail_call(self,call,error,*,uncertain=True):
        self._new_call(call)
        return super().fail_call(call,error,uncertain=uncertain)

    def reconcile_received(self,call,response,*,evidence,usage=None):
        self._new_call(call)
        return super().reconcile_received(call,response,evidence=evidence,usage=usage)

    def reclassify_uncertain(self,call,*,evidence):
        self._new_call(call)
        return super().reclassify_uncertain(call,evidence=evidence)

    def set_artifact(self,name,payload):
        require(name.startswith('vss_'),'new_artifacts_only')
        self._reload()
        get_auth(self.output)
        rows=self.data['artifacts'].get(name,[])
        if rows:
            require(len(rows)==1 and read(rows[0]['path'])==payload and json_sha(payload)==rows[0]['sha256'],
                    'immutable_trial_artifact_changed:'+name)
            return Path(rows[0]['path'])
        return super().set_artifact(name,payload)
