"""One local GLM microcut experiment using explicit temporal observation."""
import argparse
import json
from pathlib import Path

from . import microclip_contracts as contract
from .media import inventory_sources, verify_source
from .pipeline import CodexMCP
from .render import render_library_video
from .slot_finecut import prepare_window
from .state import LibraryStopped, write_json


def _read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _save(path, value):
    path = Path(path)
    if path.exists():
        if _read(path) != value:
            raise LibraryStopped('microclip_record_changed:' + str(path))
        return
    write_json(path, value)


def _frame_context(frames):
    return [{k: f[k] for k in ('frame_id', 'source_time_s', 'frame_end_s')} for f in frames]


def observation_prompt(manifest, memory, catalog, knowledge, first):
    return ('你是局部视频精剪的视觉决策模型。附件是同一原片的真实时序帧网格，格号/帧ID与时间见下表。'
            '逐格描述看得见的动作/状态；字幕原文单列，不把文字推成画面事实。'
            '找值得精看的动作变化、关键形态、结果/反应，说明当前还缺什么。'
            '从已观察帧ID选择更小范围补看，给具体问题；不要只重写剧情。'
            'zoom只能在当前附件范围内继续缩小，不重复当前完整范围。'
            + ('首次必须zoom进一步确认，或stop说明没有可观察依据。' if first else
               '证据足够可ready；需要补看则zoom；仍无依据可stop。') +
            '只输出JSON。frames须恰好覆盖当前附件每个ID一次。时间由程序从帧ID映射，不自由猜切点。\n'
            + json.dumps({'frames': [{'frame_id': manifest['frames'][0]['frame_id'],
                 'visible_action_or_state': '实际可见动作或状态', 'visible_text': ''}],
                 'visible_event': '目前可见事件', 'missing_information': ['明确缺项'],
                 'next_observation': {'action': 'zoom', 'start_frame_id': '<选择本表入点帧ID>',
                    'end_frame_id': '<选择本表出点帧ID>', 'question': '下一观察要确认什么',
                    'reason': '为什么查看这个范围'}, 'limitations': []}, ensure_ascii=False)
            + '\n当前附件帧映射：' + json.dumps(_frame_context(manifest['frames']), ensure_ascii=False)
            + '\n此前帧映射：' + json.dumps(_frame_context(list(catalog.values())), ensure_ascii=False)
            + '\n此前模型观察：' + json.dumps(memory, ensure_ascii=False)
            + '\nSkill观察流程：\n' + knowledge)


def _input(state, name, media, scope, lineage_path, **extra):
    from .media import sha256_file
    descriptor = {'policy': state.authorization['policy'], 'stage': name,
        'tool': 'analyze_image' if scope['kind'] == 'sparse_contact_sheet' else 'analyze_video',
        'media_path': str(Path(media).resolve()), 'media_sha256': sha256_file(media),
        'observation_scope': scope, 'lineage_path': str(Path(lineage_path).resolve()),
        'lineage_sha256': sha256_file(lineage_path), **extra}
    state.set_artifact('mc_input_' + name, descriptor)


def execute(output):
    from .microclip_frames import extract_grid, verify_grid, frame_catalog
    from .microclip_state import MicroclipState
    state = MicroclipState(output)
    auth = state.authorization
    directory = Path(auth['execution_directory'])
    result_path = directory / ('result_recovered.json' if auth.get('infrastructure_resume') else 'result.json')
    if result_path.exists():
        return _read(result_path)
    directory.mkdir(parents=True, exist_ok=True)
    saved_inventory = directory / 'parent_catalog' / 'inventory.json'
    source = (_read(saved_inventory) if saved_inventory.exists() else
              inventory_sources(auth['parent']['path'], directory / 'parent_catalog'))['sources'][0]
    verify_source(source)
    if source['sha256'] != auth['parent']['sha256']:
        raise LibraryStopped('microclip_parent_changed')
    slot = auth['slot']
    knowledge = Path(auth['knowledge_path']).read_text(encoding='utf-8')
    glm = CodexMCP(state)
    catalog, memory = {}, []
    start, end = slot['start_s'], slot['end_s']
    reason = '在既有粗剪段落内识别值得精看的可见变化。'
    rendered = None
    try:
        for index in range(4):
            manifest = extract_grid(source['path'], start, end, directory / 'frames' / f'view_{index}',
                                    count=6, exclude_frame_ids=tuple(catalog))
            verify_grid(manifest, source_path=source['path'])
            unseen = {f['frame_id'] for f in manifest['frames']} - set(catalog)
            if not unseen:
                raise LibraryStopped('no_progress:no_new_decoded_frames')
            catalog.update({f['frame_id']: f for f in manifest['frames']})
            name = f'mc_observe_{index}'
            scope = {'kind': 'sparse_contact_sheet', 'source_sha256': source['sha256'],
                     'source_start_s': start, 'source_end_s': end}
            _input(state, name, manifest['grid']['path'], scope, manifest['manifest_path'])
            prompt = observation_prompt(manifest, memory, catalog, knowledge, index == 0)
            prompt += '\n本次具体观察问题：' + reason
            value = glm.call(name, prompt, manifest['grid']['path'],
                lambda v: contract.validate_observation(v, manifest['frames'], catalog, first=index == 0),
                image=True, scope=scope)
            memory.append(value)
            _save(directory / f'observation_{index}.json', value)
            print(json.dumps({'stage': name, 'new_frames': len(unseen),
                              'next_action': value['next_observation']['action']}, ensure_ascii=False), flush=True)
            next_view = value['next_observation']
            if next_view['action'] == 'stop':
                raise LibraryStopped('model_stopped:' + next_view['reason'])
            if next_view['action'] == 'ready':
                break
            a, b = catalog[next_view['start_frame_id']], catalog[next_view['end_frame_id']]
            new_start, new_end = a['source_time_s'], min(b['frame_end_s'], slot['end_s'])
            if new_start < start or new_end > end + .000001 or new_end - new_start >= end - start - .000001:
                raise LibraryStopped('no_progress:observation_did_not_focus')
            start, end, reason = new_start, new_end, next_view['question']
        else:
            raise LibraryStopped('local_observation_levels_exhausted_without_ready')
        motion = prepare_window(source, slot['start_s'], slot['end_s'], state.output / 'media_cache', fps=30)
        scope = {k: motion[k] for k in ('kind', 'source_sha256', 'source_start_s', 'source_end_s')}
        duration = slot['end_s'] - slot['start_s']
        prompt = ('静音独立观察附件原速短片；不接收预设剧情或裁剪方案。'
                  '说明真正可见的动作变化与结果，字幕提供的含义单列为text_evidence。'
                  '这是附件局部时间0开始，仅输出JSON：'
                  + json.dumps({'visible_meaning': '从画面理解到什么', 'events': [
                      {'start_s': 0, 'end_s': duration, 'visible_content': '实际动作/状态变化',
                       'text_evidence': ''}], 'limitations': []}, ensure_ascii=False))
        _input(state, 'mc_motion', motion['path'], scope, Path(motion['path']).parent / 'lineage.json')
        facts = glm.call('mc_motion', prompt, motion['path'], lambda v: contract.validate_motion(v, duration), scope=scope)
        _save(directory / 'motion_facts.json', facts)
        anchors_prompt = ('结合附件实际原速过程、已有真实帧观察，为本次局部精剪提名一组入点、关键瞬间、出点。'
            '三个ID均从已观察帧中选择，时间严格start<peak<end，保留必要动作与可见结果。'
            '这只是候选锚点；下一步程序会展示原始相邻帧让你确认/细化边界。'
            '例子中的尖括号是字段说明，不可原样输出；只输出JSON：'
            + json.dumps({'start_frame_id': '<已观察入点ID>', 'peak_frame_id': '<已观察关键变化ID>',
                'end_frame_id': '<已观察结果ID>', 'reason': '为何围绕这些时刻',
                'expected_visible_change': '实际希望保留的变化', 'limitations': []}, ensure_ascii=False)
            + '\n帧映射：' + json.dumps(_frame_context(list(catalog.values())), ensure_ascii=False)
            + '\n帧观察：' + json.dumps(memory, ensure_ascii=False)
            + '\n独立原速事实：' + json.dumps(facts, ensure_ascii=False))
        _input(state, 'mc_anchors', motion['path'], scope, Path(motion['path']).parent / 'lineage.json')
        anchors = glm.call('mc_anchors', anchors_prompt, motion['path'],
                           lambda v: contract.validate_anchors(v, catalog), scope=scope)
        _save(directory / 'anchors.json', anchors)
        raw_frames = frame_catalog(source['path'], start_s=slot['start_s'], end_s=slot['end_s'])['frames']
        boundary_ids = {anchors[k + '_frame_id'] for k in ('start', 'peak', 'end')}
        for index, key in enumerate(('start', 'peak', 'end')):
            anchor_id = anchors[key + '_frame_id']
            position = next(i for i, row in enumerate(raw_frames) if row['frame_id'] == anchor_id)
            # Seven consecutive presentation frames contain the old anchor and
            # up to six genuinely new adjacent frames; no fps-derived times.
            left = max(0, min(position - 3, len(raw_frames) - 7))
            rows = raw_frames[left:left + 7]
            edge_start = rows[0]['source_time_s']
            edge_end = min(rows[-1]['frame_end_s'], slot['end_s'])
            manifest = extract_grid(source['path'], edge_start, edge_end,
                directory / 'frames' / f'edge_{index}', count=6, exclude_frame_ids=tuple(catalog))
            verify_grid(manifest, source_path=source['path'])
            catalog.update({f['frame_id']: f for f in manifest['frames']})
            boundary_ids.update(f['frame_id'] for f in manifest['frames'])
            edge_scope = {'kind': 'sparse_contact_sheet', 'source_sha256': source['sha256'],
                          'source_start_s': edge_start, 'source_end_s': edge_end}
            name = f'mc_edges_{index}'
            _input(state, name, manifest['grid']['path'], edge_scope, manifest['manifest_path'],
                   anchor_frame_id=anchor_id, anchor_key=key)
            edge_prompt = ('这是你提名的' + key + '候选锚点附近真实相邻原帧。'
                '核对动作是否开始/到达关键变化/有可见结果，说明切点过早或过晚的证据。'
                '本阶段只记录帧事实，不再发起zoom，next_observation只取ready或stop。'
                '逐格覆盖当前所有帧ID，visible_text单列。只输出与此前观察相同字段的JSON。'
                + '\n锚点：' + json.dumps(anchors, ensure_ascii=False)
                + '\n当前帧映射：' + json.dumps(_frame_context(manifest['frames']), ensure_ascii=False)
                + '\n输出格式：' + json.dumps({'frames': [{'frame_id': '<本表帧ID>',
                   'visible_action_or_state': '实际可见事实', 'visible_text': ''}],
                   'visible_event': '边界附近变化', 'missing_information': [],
                   'next_observation': {'action': 'ready', 'reason': '确认情况'}, 'limitations': []}, ensure_ascii=False))
            edge_value = glm.call(name, edge_prompt, manifest['grid']['path'],
                lambda v: contract.validate_observation(v, manifest['frames'], catalog), image=True, scope=edge_scope)
            _save(directory / f'edge_observation_{index}.json', edge_value)
            memory.append(edge_value)
            if edge_value['next_observation']['action'] != 'ready':
                raise LibraryStopped('boundary_not_confirmed:' + edge_value['next_observation']['reason'])
        prompt = ('根据已记录的帧事实与独立原速观察，精剪附件这一段。你自主选关键瞬间/结果、删除冗余，'
                  '必要时局部慢放、加速或尾帧停留；保留段落原有可见含义。'
                  '边界只能使用候选锚点及已经相邻帧确认的ID；start是入点帧、end是最后保留帧'
                  '(程序使用其下一帧时间作排他出点)。尖括号字段是说明，不可原样输出。'
                  '不增加字幕、不虚构动作、保持顺序，不必把每一类动作都保留。'
                  'speed为0.5–2，hold_s为0–3秒，shots为1–8段。只输出JSON：\n'
                  + json.dumps({'shots': [{'start_frame_id': '<已确认入点帧ID>',
                     'end_frame_id': '<已确认出点帧ID>', 'speed': 1, 'hold_s': 0,
                     'reason': '保留与剪辑作用', 'visible_change': '这一范围支持什么变化'}],
                     'preserved_visible_meaning': '剪后仍能看懂什么',
                     'omitted_content': ['哪些实际过程可省略'], 'limitations': []}, ensure_ascii=False)
                  + '\n帧映射：' + json.dumps(_frame_context(list(catalog.values())), ensure_ascii=False)
                  + '\n可用边界ID：' + json.dumps(sorted(boundary_ids), ensure_ascii=False)
                  + '\n帧观察：' + json.dumps(memory, ensure_ascii=False)
                  + '\n独立原速事实：' + json.dumps(facts, ensure_ascii=False)
                  + '\nSkill：\n' + knowledge)
        _input(state, 'mc_plan', motion['path'], scope, Path(motion['path']).parent / 'lineage.json')
        plan_value = glm.call('mc_plan', prompt, motion['path'],
                             lambda v: contract.validate_plan(source, slot, v, catalog, boundary_ids), scope=scope)
        plan = contract.validate_plan(source, slot, plan_value, catalog, boundary_ids)
        _save(directory / 'model_plan.json', plan_value)
        _save(directory / 'compiled_plan.json', plan)
        segments = plan['segments']
        unchanged = (len(segments) == 1 and abs(segments[0]['source_in_s'] - slot['start_s']) < .000001
                     and abs(segments[0]['source_out_s'] - slot['end_s']) < .000001
                     and segments[0]['speed'] == 1 and segments[0]['freeze_tail_s'] == 0)
        if unchanged:
            raise LibraryStopped('no_progress:model_plan_has_no_actual_edit')
        destination = state.claim_render(plan, source)
        rendered = render_library_video({'sources': [source]}, plan, destination, fps=30, width=720, height=1280)
        print(json.dumps({'actual_microclip': rendered['rendered_path'], 'duration_s': rendered['measured_duration_s']}, ensure_ascii=False), flush=True)
        actual = inventory_sources(rendered['rendered_path'], directory / 'output_catalog')['sources'][0]
        review_media = prepare_window(actual, 0, actual['duration_s'], state.output / 'media_cache', fps=30)
        review_scope = {k: review_media[k] for k in ('kind', 'source_sha256', 'source_start_s', 'source_end_s')}
        _input(state, 'mc_blind', review_media['path'], review_scope, Path(review_media['path']).parent / 'lineage.json')
        blind = glm.call('mc_blind', '静音独立观察实际剪后附件。隐藏预设剧情、原片说明、切点理由，'
            '先描述看得见的内容与动作结果，文字证据单列。附件0开始，只输出JSON：'
            + json.dumps({'visible_meaning': '看懂的含义', 'events': [{'start_s': 0,
                'end_s': actual['duration_s'], 'visible_content': '实际可见内容', 'text_evidence': ''}],
                'limitations': []}, ensure_ascii=False), review_media['path'],
            lambda v: contract.validate_motion(v, actual['duration_s']), scope=review_scope)
        _save(directory / 'blind_reading.json', blind)
        _input(state, 'mc_review', review_media['path'], review_scope, Path(review_media['path']).parent / 'lineage.json')
        target_prompt = ('核验实际附件与独立原速事实、逐帧事实及独立剪后盲读。'
            '关键瞬间是否真实且有用，结果/身份是否看清，删掉的过程是否冗余，是否实际精炼。'
            '不能用计划说明补出附件缺失内容，不因有变速或时长变短就pass。'
            '每项取pass/partial/fail，只输出JSON：'
            + json.dumps({'status': 'partial', 'key_moment_selection': 'partial', 'economy': 'partial',
                'readability': 'partial', 'reason': '依据实际输出时间和可见画面说明', 'limitations': []}, ensure_ascii=False)
            + '\n原速事实：' + json.dumps(facts, ensure_ascii=False)
            + '\n帧事实：' + json.dumps(memory, ensure_ascii=False)
            + '\n模型微剪：' + json.dumps(plan_value, ensure_ascii=False)
            + '\n实际剪后盲读：' + json.dumps(blind, ensure_ascii=False)
            + '\n实际时长：' + str(actual['duration_s']))
        review = glm.call('mc_review', target_prompt, review_media['path'], contract.validate_review, scope=review_scope)
        _save(directory / 'review.json', review)
        passed = all(review[k] == 'pass' for k in ('status', 'key_moment_selection', 'economy', 'readability'))
        result = {'status': 'model_checked_candidate' if passed else 'candidate_with_limitations',
            'final_video': rendered['rendered_path'], 'final_sha256': rendered['sha256'],
            'parent_slot_s': [slot['start_s'], slot['end_s']], 'original_duration_s': duration,
            'measured_duration_s': rendered['measured_duration_s'], 'model_quality_gate_passed': passed,
            'observed_frames': len(catalog), 'observation_rounds': len(memory),
            'limitations': review['limitations'] + blind['limitations'] + [
                'Model review is fallible; subtitles remain. Cloud video sampling and music rhythm are unverified.']}
    except (LibraryStopped, ValueError, RuntimeError, OSError) as error:
        state._reload()
        if any(c['status'] == 'submitted' for c in state.data['calls'][207:]):
            print(json.dumps({'status': 'waiting_existing_submission', 'reason': str(error)}, ensure_ascii=False), flush=True)
            raise
        result = {'status': 'stopped_with_actual_candidate' if rendered else 'stopped',
            'error': str(error), 'model_quality_gate_passed': False,
            'final_video': rendered['rendered_path'] if rendered else None,
            'final_sha256': rendered['sha256'] if rendered else None,
            'measured_duration_s': rendered['measured_duration_s'] if rendered else None,
            'observed_frames': len(catalog), 'observation_rounds': len(memory)}
    path = state.finish(result)
    return _read(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--prepare', action='store_true')
    action.add_argument('--resume-infrastructure', action='store_true')
    action.add_argument('--run', action='store_true')
    args = parser.parse_args()
    if args.prepare:
        from .microclip_state import record_authorization
        value = record_authorization(args.output, '用户在2026-10-07局部精剪skill调研后明确回复“下一步”，'
            '授权实现skill并对一段已有GLM粗剪slot做一次实际局部精剪验证；无整片重规划或Goal恢复。')
        print(json.dumps({'prepared': value['execution_directory'], 'slot': value['slot']}, ensure_ascii=False))
    elif args.resume_infrastructure:
        from .microclip_state import record_infrastructure_resume
        value = record_infrastructure_resume(args.output,
            '用户明确要求继续已授权局部精剪；修复第三组未提交帧图的Windows目录发布故障，'
            '复用208/209已收到回复，保留停止结果，不开启新语义轮次或额外渲染。')
        print(json.dumps({'infrastructure_resume': value['execution_directory']}, ensure_ascii=False))
    else:
        print(json.dumps(execute(args.output), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
