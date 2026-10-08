"""Finishing over encoded parent timelines; no movie-library reconstruction."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json

from . import parent_timeline_cut as cut
from .media import inventory_sources
from .pipeline import CodexMCP
from .slot_finecut import prepare_window
from .render import render_library_video
from .state import LibraryStopped, json_sha, write_json


def _read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _finish(state, parent, values):
    payload = {'baseline_id':f'render_{parent}',**values}
    return _read(state.finish(parent,payload))


def _slots_rendered(plan, rendered):
    ranges = {}
    for segment,actual in zip(plan['segments'],rendered['provenance'],strict=True):
        sid = segment['slot_id']
        ranges.setdefault(sid,{'slot_id':sid,'start_s':actual['output_in_s'],'end_s':actual['output_out_s']})
        ranges[sid]['end_s'] = actual['output_out_s']
    return list(ranges.values())


def _parent(state,glm,parent):
    p = parent['round']
    folder = Path(state.authorization['execution_directory'])/f'render_{p}'
    if (folder/'result.json').exists():
        return _finish(state,p,_read(folder/'result.json'))
    folder.mkdir(parents=True,exist_ok=True)
    outline = _read(state.authorization['outline_paths'][str(p)])
    source = inventory_sources(parent['path'],folder/'parent_catalog')['sources'][0]
    if source['sha256']!=parent['sha256']:
        raise ValueError('parent_cut:actual_parent_changed')
    knowledge = Path(state.authorization['knowledge_path']).read_text(encoding='utf-8')
    decisions = []
    for slot in outline['slots']:
        media = prepare_window(source,slot['start_s'],slot['end_s'],state.output/'media_cache',fps=30)
        key = json_sha(slot)[:16]
        decision = glm.call(f'pc_{p}_slot_{key}',cut.slot_prompt(slot,knowledge),media['path'],
            lambda v,s=slot:cut.validate_slot_cut(v,s),
            scope={k:media[k] for k in ('kind','source_sha256','source_start_s','source_end_s')})
        write_json(folder/'slots'/f'{slot["slot_id"]}.json',decision)
        decisions.append(decision)
    plan = cut.build_plan(source,outline,decisions)
    write_json(folder/'plan.json',plan)
    directory = state.claim_render(p,plan)
    rendered = render_library_video({'sources':[source]},plan,directory,
        fps=plan['fps'],width=plan['width'],height=plan['height'])
    # Deliver the real candidate path as soon as it exists. Reviews never
    # change its cuts or turn a limited reading into a fabricated quality pass.
    print(json.dumps({'actual_candidate':rendered['rendered_path'],
        'duration_s':rendered['measured_duration_s'],'folder':str(Path(rendered['rendered_path']).parent)},ensure_ascii=False),flush=True)
    actual = inventory_sources(rendered['rendered_path'],folder/'output_catalog')['sources'][0]
    media = prepare_window(actual,0,rendered['measured_duration_s'],state.output/'media_cache',fps=30)
    scopes = {k:media[k] for k in ('kind','source_sha256','source_start_s','source_end_s')}
    intervals = _slots_rendered(plan,rendered)
    slot_ids = [s['slot_id'] for s in outline['slots']]
    blind = glm.call(f'pc_{p}_blind',cut.review_prompt(rendered['sha256'],rendered['measured_duration_s'],intervals),
        media['path'],lambda v:cut.validate_review(v,rendered['sha256'],rendered['measured_duration_s'],slot_ids),scope=scopes)
    write_json(folder/'blind_review.json',blind)
    prompt = cut.review_prompt(rendered['sha256'],rendered['measured_duration_s'],intervals) + '\n'+json.dumps({
        'original_model_slot_navigation':outline['slots'],'independent_actual_blind_review':blind,
        'instruction':'比较实际视频与原段落含义；画面没交代的内容不要靠导航补出。重要身份/结果是否看清，'
            '删去哪些冗余、慢放和停留是否有作用。不能因变短就判通过。音乐节拍未核验。'},ensure_ascii=False)
    review = glm.call(f'pc_{p}_review',prompt,media['path'],
        lambda v:cut.validate_review(v,rendered['sha256'],rendered['measured_duration_s'],slot_ids),scope=scopes)
    write_json(folder/'target_review.json',review)
    passed = all(value['status']=='pass' and all(s['status']=='pass' for s in value['slots'])
                 for value in (blind,review))
    return _finish(state,p,{'status':'model_checked_candidate' if passed else 'candidate_with_limitations',
        'final_video':rendered['rendered_path'],'final_sha256':rendered['sha256'],
        'measured_duration_s':rendered['measured_duration_s'],'model_quality_gate_passed':passed,
        'limitations':blind['limitations']+review['limitations']+[
            'Model review is fallible. Parent baked captions remain; source audio is retimed and tail holds are silent.',
            'Speed changes use ordinary frame resampling, not high-frame-rate slow motion.']})


def execute(output):
    from .parent_cut_state import ParentCutState
    initial = ParentCutState(output)
    directory = Path(initial.authorization['execution_directory'])
    # Construct clients before starting either thread; never reconcile another
    # lane's reply while it is being submitted.
    lanes = {}
    for parent in initial.authorization['parents']:
        state = ParentCutState(output)
        lanes[parent['round']] = (state,None if(directory/f'render_{parent["round"]}'/'result.json').exists()else CodexMCP(state))
    def run(parent):
        state,glm = lanes[parent['round']]
        try:
            return _parent(state,glm,parent)
        except (ValueError,RuntimeError,OSError) as error:
            state._reload()
            if any(c['status']=='submitted' and c['name'].startswith(f'pc_{parent["round"]}_') for c in state.data['calls']):
                raise
            values = {'status':'stopped','error':str(error),'model_quality_gate_passed':False,
                      'limitations':['Known failure retained; no automatic new planning round.']}
            path = directory/f'render_{parent["round"]}'/'render/render_result.json'
            if path.exists():
                actual = _read(path)
                values.update(final_video=actual['rendered_path'],final_sha256=actual['sha256'],review_status='incomplete')
            return _finish(state,parent['round'],values)
    with ThreadPoolExecutor(max_workers=2,thread_name_prefix='parent_cut') as pool:
        results = list(pool.map(run,initial.authorization['parents']))
    initial.assert_protected()
    result = {'policy':'sf_parent_timeline_finecut_v1','results':results,'requests_total':len(initial.data['calls'])}
    path = directory/'comparison.json'
    if path.exists():
        old = _read(path)
        if old['results']!=results:
            raise ValueError('parent_cut:completed_comparison_changed')
        return old
    write_json(path,result)
    return result
