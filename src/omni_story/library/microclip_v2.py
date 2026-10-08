"""One full-slot, multi-event finecut; old microclip results remain immutable."""
import argparse
import json
from pathlib import Path

from . import microclip_v2_contracts as contract
from .media import inventory_sources, sha256_file, verify_source
from .microclip import _read, _save, _frame_context
from .microclip_frames import extract_grid, verify_grid
from .pipeline import CodexMCP
from .render import render_library_video
from .slot_finecut import prepare_window
from .state import LibraryStopped


def _json(value):
    return json.dumps(value, ensure_ascii=False)


FRAME_FORMAT = {'frames': [{'frame_id': '<本表真实ID>', 'visible_action_or_state': '画面事实',
    'visible_text': ''}], 'visible_event': '可见变化', 'missing_information': [], 'limitations': []}
MOTION_FORMAT = {'visible_meaning': '从实际画面理解的含义', 'events': [{'start_s': 0,
    'end_s': 1, 'visible_content': '真正可见的状态/变化', 'text_evidence': ''}],
    'observation_status': 'complete', 'limitations': []}


def _image_scope(manifest):
    return {'kind': 'sparse_contact_sheet', 'source_sha256': manifest['source']['sha256'],
        'source_start_s': manifest['request']['start_s'], 'source_end_s': manifest['request']['end_s']}


def _video_scope(media):
    return {k: media[k] for k in ('kind', 'source_sha256', 'source_start_s', 'source_end_s')}


def _silent_window(source, start, end, cache):
    # A separate, SHA-bound analysis proxy; original media/audio stay unchanged.
    return prepare_window({**source, 'audio_stream_index': None}, start, end, cache, fps=30)


def _input(state, name, media, scope, lineage_path, **extra):
    state.set_artifact('mc2_input_' + name, {'policy': state.authorization['policy'], 'stage': name,
        'tool': 'analyze_image' if scope['kind'] == 'sparse_contact_sheet' else 'analyze_video',
        'media_path': str(Path(media).resolve()), 'media_sha256': sha256_file(media),
        'observation_scope': scope, 'lineage_path': str(Path(lineage_path).resolve()),
        'lineage_sha256': sha256_file(lineage_path), **extra})


def _image_call(glm, state, directory, name, manifest, prompt, validator, **extra):
    verify_grid(manifest)
    scope = _image_scope(manifest)
    _input(state, name, manifest['grid']['path'], scope, manifest['manifest_path'], **extra)
    value = glm.call(name, prompt + '\n当前附件帧映射：' + _json(_frame_context(manifest['frames'])),
        manifest['grid']['path'], validator, image=True, scope=scope)
    _save(directory / (name + '.json'), value)
    print(_json({'stage': name, 'frames': len(manifest['frames'])}), flush=True)
    return value


def _video_call(glm, state, directory, name, media, prompt, validator, **extra):
    scope = _video_scope(media)
    _input(state, name, media['path'], scope, Path(media['path']).parent / 'lineage.json', **extra)
    value = glm.call(name, prompt, media['path'], validator, scope=scope)
    _save(directory / (name + '.json'), value)
    print(_json({'stage': name}), flush=True)
    return value


def _semantic_ready(value, label):
    # This is an actual semantic stop, never a format-repair request.
    if value['status'] == 'blocked' or value['blocking_questions']:
        raise LibraryStopped(label + ':' + _json(value['blocking_questions']))


def execute(output):
    from .microclip_v2_media import anchor_grid, dense_pages
    from .microclip_v2_state import SlotMicroclipState
    state = SlotMicroclipState(output)
    auth = state.authorization
    directory = Path(auth['execution_directory'])
    result_path = directory / ('result_boundary_metadata_recovered.json' if auth.get('boundary_metadata_resume') else
                              'result_boundary_recovered.json' if auth.get('boundary_resume') else
                              'result_dispatch_recovered.json' if auth.get('known_failure_dispatch_resume') else
                              'result_network_recovered.json' if auth.get('known_failure_resume') else
                              'result_recovered.json' if auth.get('infrastructure_resume') else 'result.json')
    if result_path.exists():
        return _read(result_path)
    directory.mkdir(parents=True, exist_ok=True)
    saved = directory / 'parent_catalog' / 'inventory.json'
    source = (_read(saved) if saved.exists() else
        inventory_sources(auth['parent']['path'], directory / 'parent_catalog'))['sources'][0]
    verify_source(source)
    slot = auth['slot']
    knowledge = Path(auth['knowledge_path']).read_text(encoding='utf-8')
    catalog, observations, edges = {}, [], []
    rendered = None
    try:
        if auth.get('known_failure_resume'):
            from .microclip_v2_transport import KnownFailureMCP
            glm = KnownFailureMCP(state)
        else:
            glm = CodexMCP(state)
        overview = extract_grid(source['path'], slot['start_s'], slot['end_s'],
            directory / 'frames' / 'overview', count=6)
        catalog.update({f['frame_id']: f for f in overview['frames']})
        observations.append(_image_call(glm, state, directory, 'mc2_overview', overview,
            '静音独立观察完整段落的六张真实帧，不接收旧剧情或剪辑答案。逐格分开画面事实和字幕，'
            '描述状态变化和缺失过程；不把一处最剧烈动作当成整段含义。仅输出JSON：' + _json(FRAME_FORMAT),
            lambda v: contract.validate_frames(v, overview['frames'])))
        motion = _silent_window(source, slot['start_s'], slot['end_s'], state.output / 'media_cache')
        facts = _video_call(glm, state, directory, 'mc2_motion', motion,
            '静音独立观察正常速度的整个附件，不接收剧情或切点。附件局部从0开始，时长'
            + str(slot['end_s'] - slot['start_s']) + '秒。描述真实可见的过程与结果，字幕信息单列。'
            '若只看到首帧或过程不足，observation_status取limited，并说明限制；否则取complete。仅输出JSON：'
            + _json(MOTION_FORMAT), lambda v: contract.validate_motion(v, slot['end_s'] - slot['start_s']))
        intent_format = {'status': 'ready', 'original_claim_checks': [{'claim_id': '<原字段名>',
            'original_text': '<原模型目标原文>', 'verdict': 'supported', 'reason': '证据或缺项',
            'evidence_frame_ids': ['<已观察ID>']}], 'obligations': [{'obligation_id': 'o0',
            'description': '精剪必须保留的已成立信息', 'support': 'visual',
            'original_claim_ids': ['<对应原字段名>'], 'evidence_frame_ids': ['<真实ID>']}],
            'search_regions': [{'region_id': 'r0', 'start_frame_id': '<已观察ID>', 'end_frame_id': '<已观察ID>',
            'question': '需要确认什么具体过程或状态', 'obligation_ids': ['o0']}],
            'blocking_questions': [], 'limitations': []}
        intent = _image_call(glm, state, directory, 'mc2_intent', overview,
            '现在核验已有段落的表达要求，然后为完整段落精剪提出必须保留的信息。'
            '旧模型目标是可错的任务说明，不是画面事实。逐项原样引用所有非空字段'
            '(intended_takeaway/entry_state/exit_state/link_to_previous/link_to_next)，'
            '核验supported/partial/unsupported；不能默默删掉或换成一个动作。'
            '每项obligation添加original_claim_ids，映射它保留哪些原字段，全部原目标必须覆盖。'
            '若核心目标不受实际素材支持，status=blocked，记录缺项，不造新剧情。'
            '明确visual/text/mixed依据；partial不能称为纯画面成立。根据已观察帧自主选择1–6个区域，'
            '可位于整个段落任何位置，不必不断缩小同一区域。每项必要信息都必须有补看区域。'
            '选择会提供新过程证据的区域，不重复全段概览同一组六帧。'
            'region_id依次r0,r1等。仅输出JSON：' + _json(intent_format)
            + '\n原模型段落目标：' + _json(slot) + '\n独立概览：' + _json(observations)
            + '\n独立原速观察：' + _json(facts) + '\n准则：\n' + knowledge,
            lambda v: contract.validate_intent(v, slot, catalog))
        _semantic_ready(intent, 'slot_intent_not_supported')
        if any(c['claim_id'] in ('intended_takeaway', 'entry_state', 'exit_state')
                and c['verdict'] == 'unsupported' for c in intent['original_claim_checks']):
            raise LibraryStopped('original_core_slot_meaning_not_supported')
        for index, region in enumerate(intent['search_regions']):
            start = catalog[region['start_frame_id']]['source_time_s']
            end = min(slot['end_s'], catalog[region['end_frame_id']]['frame_end_s'])
            manifest = extract_grid(source['path'], start, end, directory / 'frames' / region['region_id'], count=6)
            if not {f['frame_id'] for f in manifest['frames']} - set(catalog):
                raise LibraryStopped('no_progress:region_adds_no_new_frame_evidence:' + region['region_id'])
            catalog.update({f['frame_id']: f for f in manifest['frames']})
            observations.append(_image_call(glm, state, directory, f'mc2_region_{index}', manifest,
                '观察你选择的区域。独立记录事实、文字和未确认信息，不为旧剧情辩护。'
                '当前问题：' + region['question'] + '。只描述附件，不提供裁剪方案。仅输出JSON：'
                + _json(FRAME_FORMAT), lambda v, rows=manifest['frames']: contract.validate_frames(v, rows),
                region_id=region['region_id']))
        anchor_format = {'status': 'ready', 'events': [{'event_id': 'e0', 'obligation_ids': ['o0'],
            'start_frame_id': '<已观察入点>', 'end_frame_id': '<已观察出点>', 'reason': '必须保留的具体过程/状态'}],
            'resolved_conflicts': [], 'blocking_questions': [], 'limitations': []}
        anchors = _image_call(glm, state, directory, 'mc2_anchors', overview,
            '根据独立观察与必要信息，为整个段落提名1–6个需要保留的事件及边界。'
            '可以保留多个不连续的瞬间，所有必要信息必须覆盖。不是只找一组最剧烈动作三锚点。'
            'start/end从已观察ID选择，事件按源时间顺序且不重叠。event_id依次e0,e1。'
            '检查原速观察与密帧对行动者、顺序、接触和结果是否冲突；有依据就记录如何消解，'
            '必要事实无法消解则status=blocked和blocking_questions非空，不编造一致结论。'
            '下一步会同时展示各候选边界本体与相邻帧。仅输出JSON：' + _json(anchor_format)
            + '\n必要信息：' + _json(intent) + '\n原速观察：' + _json(facts)
            + '\n独立帧观察：' + _json(observations) + '\n全部已观察帧：' + _json(_frame_context(list(catalog.values())))
            + '\n准则：\n' + knowledge, lambda v: contract.validate_anchors(v, catalog, intent))
        _semantic_ready(anchors, 'unresolved_source_evidence')
        boundary_ids = set()
        for event_index, event in enumerate(anchors['events']):
            for offset, key in enumerate(('start', 'end')):
                anchor_id = event[key + '_frame_id']
                manifest = anchor_grid(source['path'], slot, anchor_id,
                    directory / 'frames' / f'edge_{event_index * 2 + offset}')
                catalog.update({f['frame_id']: f for f in manifest['frames']})
                edge_format = {**FRAME_FORMAT, 'anchor_frame_id': anchor_id,
                    'confirmed_frame_id': anchor_id, 'status': 'confirmed', 'blocking_questions': [],
                    'reason': '为何此帧是合法且有用的边界', 'limitations': []}
                edge = _image_call(glm, state, directory, f'mc2_edge_{event_index * 2 + offset}', manifest,
                    '附件包含候选锚点本身和连续相邻帧。核验边界是否丢掉必要准备、变化、结果或反应。'
                    'confirmed_frame_id可选当前附件中更合适的相邻帧，不必坚持原提名。'
                    '存在阻塞性缺项时status=blocked，不能因为本步结束而假装confirmed。仅输出JSON：'
                    + _json(edge_format) + '\n候选事件：' + _json(event)
                    + '\n边界用途：' + key + '\n必要信息：' + _json(intent['obligations']),
                    lambda v, rows=manifest['frames'], aid=anchor_id: contract.validate_edge(v, rows, aid),
                    event_id=event['event_id'], anchor_key=key, anchor_frame_id=anchor_id)
                if edge['status'] != 'confirmed' or edge['blocking_questions']:
                    from .microclip_v2_boundary import resolve_boundary
                    edge = resolve_boundary(glm, state, directory, source, slot,
                        event_index * 2 + offset, event, key, manifest, edge, intent, catalog, observations)
                boundary_ids.add(edge['confirmed_frame_id'])
                edges.append(edge)
        # Confirming one local edge must not cross another preserved event.
        previous_end = slot['start_s']
        for i, event in enumerate(anchors['events']):
            a, b = (catalog[edges[2*i+j]['confirmed_frame_id']] for j in (0, 1))
            if (a['source_time_s'] < previous_end - 1e-6 or
                    a['source_time_s'] > b['source_time_s'] or b.get('frame_end_s') is None):
                raise LibraryStopped('confirmed_event_boundaries_overlap_or_reorder:' + event['event_id'])
            previous_end = b['frame_end_s']
        plan_format = {'shots': [{'start_frame_id': '<已确认ID>', 'end_frame_id': '<已确认ID>',
            'speed': 1, 'hold_s': 0, 'reason': '保留与剪辑作用', 'visible_change': '可见信息', 'obligation_ids': ['o0']}],
            'obligation_coverage': [{'obligation_id': 'o0', 'shot_indices': [0],
            'source_evidence_frame_ids': ['<选入范围内真实ID>'], 'exposure_s': 1,
            'necessary_exposure_s': 1, 'reason': '观众需要辨认什么及时间估计依据'}],
            'preserved_visible_meaning': '完整段落仍表达什么', 'omitted_content': [], 'limitations': []}
        plan_value = _video_call(glm, state, directory, 'mc2_plan', motion,
            '精剪完整段落，保留已核验的必要信息和前后联系。自主省略冗余、选择速度和停留。'
            '不得把段落目标缩成单动作。边界只能选已确认ID，end表示最后保留帧，程序以该帧下一帧时间作出点。'
            '1–12段，保持源时间顺序且不重叠，speed0.5–2，hold_s0–3。'
            '每项必要信息映射shot_indices与范围内真实证据ID；exposure_s为所列段源时长/速度之和，'
            '只有证据包含该段末帧时才可计入停留；necessary_exposure_s是你估计的需要时间，不能大于分配。'
            '它不是人类阈值测量；不要为了短而删因果，也不要用大量慢放/停留抵消删减。仅输出JSON：'
            + _json(plan_format) + '\n必要信息：' + _json(intent) + '\n事件：' + _json(anchors)
            + '\n已确认边界：' + _json(sorted(boundary_ids)) + '\n边界事实：' + _json(edges)
            + '\n独立观察：' + _json(observations) + '\n原速事实：' + _json(facts)
            + '\n帧映射：' + _json(_frame_context(list(catalog.values()))) + '\n准则：\n' + knowledge,
            lambda v: contract.validate_plan(source, slot, v, catalog, boundary_ids, intent))
        plan = contract.validate_plan(source, slot, plan_value, catalog, boundary_ids, intent)
        _save(directory / 'compiled_plan.json', plan)
        slice_facts = []
        for index, segment in enumerate(plan['segments']):
            selected = _silent_window(source, segment['source_in_s'], segment['source_out_s'],
                state.output / 'media_cache')
            value = _video_call(glm, state, directory, f'mc2_slice_{index}', selected,
                '独立观察实际附件短片，不接收剧情目标、切点理由或剪辑方案。'
                '仅描述画面中确实出现的身份/状态/行动/结果，文字单列；只见首帧或过程不足则limited。'
                '附件局部0开始，时长' + str(segment['source_out_s'] - segment['source_in_s'])
                + '秒。不要把原电影其它时间的事件套入这个短片。仅输出JSON：' + _json(MOTION_FORMAT),
                lambda v, length=segment['source_out_s'] - segment['source_in_s']: contract.validate_motion(v, length),
                slice_index=index, source_range={k: segment[k] for k in ('source_in_s', 'source_out_s')})
            slice_facts.append({'slice_index': index, 'source_in_s': segment['source_in_s'],
                'source_out_s': segment['source_out_s'], 'source_sha256': source['sha256'], 'facts': value})
        check_format = {'status': 'ready', 'obligation_checks': [{'obligation_id': 'o0', 'verdict': 'supported',
            'reason': '实际选入范围是否保留了必要信息'}], 'blocking_questions': [], 'limitations': []}
        source_check = _video_call(glm, state, directory, 'mc2_source_check', motion,
            '在渲染前比较已记录的独立事实、实际选择范围与所有必要信息。'
            '不能以计划理由证明画面事实，不把腾空自动当成落地，也不把字幕当成可见动作。'
            '逐项supported/partial/unsupported；关键含义缺失或关键事实矛盾必须blocked。仅输出JSON：'
            + _json(check_format) + '\n必要信息：' + _json(intent['obligations'])
            + '\n原目标及核验：' + _json(intent['original_claim_checks'])
            + '\n独立原速事实：' + _json(facts) + '\n独立帧事实：' + _json(observations)
            + '\n独立实际选入短片事实（优先，不能被全段描述覆盖）：' + _json(slice_facts)
            + '\n边界事实：' + _json(edges) + '\n实际源范围：' + _json(plan['segments'])
            + '\n信息覆盖：' + _json(plan_value['obligation_coverage']),
            lambda v: contract.validate_source_check(v, intent))
        _semantic_ready(source_check, 'selected_ranges_lose_slot_meaning')
        if any(c['verdict'] == 'unsupported' for c in source_check['obligation_checks']):
            raise LibraryStopped('selected_ranges_have_unsupported_obligation')
        segments = plan['segments']
        if (len(segments) == 1 and abs(segments[0]['source_in_s'] - slot['start_s']) < 1e-6
                and abs(segments[0]['source_out_s'] - slot['end_s']) < 1e-6
                and segments[0]['speed'] == 1 and segments[0]['freeze_tail_s'] == 0):
            raise LibraryStopped('no_progress:model_plan_has_no_actual_edit')
        destination = state.claim_render(plan, source)
        rendered = render_library_video({'sources': [source]}, plan, destination, fps=30, width=720, height=1280)
        actual = inventory_sources(rendered['rendered_path'], directory / 'output_catalog')['sources'][0]
        video = _silent_window(actual, 0, actual['duration_s'], state.output / 'media_cache')
        blind_video = _video_call(glm, state, directory, 'mc2_blind_video', video,
            '静音独立看实际输出，不接收剧情目标、原片或剪辑理由。先描述可见状态、行动、变化和结果。'
            '附件局部从0开始，时长' + str(actual['duration_s']) + '秒。只见首帧/过程不充分时取limited。仅输出JSON：'
            + _json(MOTION_FORMAT), lambda v: contract.validate_motion(v, actual['duration_s']))
        pages = dense_pages(actual['path'], 0, actual['duration_s'], directory / 'frames' / 'output')
        _save(directory / 'output_frame_coverage.json', pages['coverage'])
        coverage_path = directory / 'output_frame_coverage.json'
        state.set_artifact('mc2_blind_page_plan', {'policy': auth['policy'], 'final_sha256': rendered['sha256'],
            'coverage_path': str(coverage_path.resolve()), 'coverage_sha256': sha256_file(coverage_path),
            'pages': [{'stage': f'mc2_blind_page_{i}', 'start_s': manifest['request']['start_s'],
                       'end_s': manifest['request']['end_s']} for i, manifest in enumerate(pages['pages'])]})
        blind_pages = []
        for index, manifest in enumerate(pages['pages']):
            blind_pages.append(_image_call(glm, state, directory, f'mc2_blind_page_{index}', manifest,
                '静音独立观察实际输出的真实时间密帧，不接收剧情目标或剪辑答案。'
                '逐格描述看见的人物/动作/状态，区分文字；不能从一个姿态猜行动成功。'
                '帧图不是连续播放，明确能确认的变化与限制，人物/动作辨认不足则observation_status=limited。仅输出JSON：'
                + _json({**FRAME_FORMAT, 'observation_status': 'complete'}),
                lambda v, rows=manifest['frames']: contract.validate_blind_page(v, rows)))
        review_format = {'status': 'partial', 'key_moment_selection': 'partial', 'economy': 'partial',
            'readability': 'partial', 'obligation_checks': [{'obligation_id': 'o0', 'verdict': 'partial',
            'output_start_s': 0, 'output_end_s': 1, 'reason': '实际输出支持什么'}], 'reason': '实际效果及局限',
            'limitations': []}
        review = _video_call(glm, state, directory, 'mc2_review', video,
            '核验实际输出是否保留完整段落的必要信息、前后联系，并减少了冗余。'
            '先依据独立盲读与实际密帧；不因计划解释、变速或变短而pass。'
            '观察有限和成片有问题分别说明，不把盲读未观察到直接当作不存在。'
            '检查各项观看时间是否足够、停留是否有用；逐项给真实输出时间与证据。仅输出JSON：'
            + _json(review_format) + '\n必要信息：' + _json(intent['obligations'])
            + '\n原目标核验：' + _json(intent['original_claim_checks'])
            + '\n独立连续盲读：' + _json(blind_video) + '\n独立密帧盲读：' + _json(blind_pages)
            + '\n实际帧覆盖：' + _json(pages['coverage']) + '\n原独立事实：' + _json(facts)
            + '\n分配的观看时间：' + _json(plan_value['obligation_coverage'])
            + '\n原/新时长：' + _json([slot['end_s'] - slot['start_s'], actual['duration_s']]),
            lambda v: contract.validate_review(v, intent, actual['duration_s']))
        observation_complete = (facts['observation_status'] == 'complete'
            and all(s['facts']['observation_status'] == 'complete' for s in slice_facts)
            and pages['coverage']['target_gap_met'] and blind_video['observation_status'] == 'complete'
            and all(p['observation_status'] == 'complete' for p in blind_pages))
        semantic_complete = (all(c['verdict'] == 'supported' for c in intent['original_claim_checks'])
            and all(c['verdict'] == 'supported' for c in source_check['obligation_checks'])
            and all(o['support'] == 'visual' for o in intent['obligations'])
            and all(c['verdict'] == 'pass' for c in review['obligation_checks']))
        passed = observation_complete and semantic_complete and all(review[k] == 'pass'
            for k in ('status', 'key_moment_selection', 'economy', 'readability'))
        result = {'status': 'model_checked_candidate' if passed else 'candidate_with_limitations',
            'final_video': rendered['rendered_path'], 'final_sha256': rendered['sha256'],
            'original_duration_s': slot['end_s'] - slot['start_s'], 'measured_duration_s': rendered['measured_duration_s'],
            'model_quality_gate_passed': passed, 'observation_complete': observation_complete,
            'semantic_complete': semantic_complete, 'observed_source_frames': len(catalog),
            'events': len(anchors['events']), 'output_pages': len(blind_pages),
            'limitations': facts['limitations'] + intent['limitations'] + anchors['limitations']
                + [item for edge in edges for item in edge.get('limitations', [])]
                + plan_value['limitations']
                + [item for s in slice_facts for item in s['facts']['limitations']]
                + source_check['limitations'] + blind_video['limitations'] + pages['coverage']['limitations']
                + [item for page in blind_pages for item in page['limitations']]
                + review['limitations'] + ['Model exposure estimates and reviews are fallible; unfamiliar-viewer readability, '
                    'caption-free understanding, cloud sampling and music rhythm are unverified.']}
    except (LibraryStopped, ValueError, RuntimeError, OSError) as error:
        state._reload()
        if any(c['status'] == 'submitted' for c in state.data['calls'][auth['baseline_request_count']:]):
            print(_json({'status': 'waiting_existing_submission', 'reason': str(error)}), flush=True)
            raise
        result = {'status': 'stopped_with_actual_candidate' if rendered else 'stopped',
            'error': str(error), 'model_quality_gate_passed': False,
            'final_video': rendered['rendered_path'] if rendered else None,
            'final_sha256': rendered['sha256'] if rendered else None,
            'measured_duration_s': rendered['measured_duration_s'] if rendered else None,
            'observed_source_frames': len(catalog)}
    return _read(state.finish(result))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--prepare', action='store_true')
    action.add_argument('--resume-infrastructure', action='store_true')
    action.add_argument('--resume-known-failure', action='store_true')
    action.add_argument('--resume-undispatched-retry', action='store_true')
    action.add_argument('--resume-boundary-navigation', action='store_true')
    action.add_argument('--resume-boundary-metadata', action='store_true')
    action.add_argument('--run', action='store_true')
    args = parser.parse_args()
    if args.prepare:
        from .microclip_v2_state import record_authorization
        auth = record_authorization(args.output,
            '用户在2026-10-07端到端因果审计之后明确回复“继续”，授权修复段落目标、多事件补看、'
            '边界确认、时间分配与实际输出审核，并在原任务内对同一15秒段落做一次修正试验；'
            '仅一个新候选，不恢复Goal或重规划整片。')
        print(_json({'prepared': auth['execution_directory'], 'slot': auth['slot']}))
    elif args.resume_infrastructure:
        from .microclip_v2_state import record_infrastructure_resume
        auth = record_infrastructure_resume(args.output,
            '用户已明确继续已授权的完整slot精剪，修复同一真实帧在多个网格抽样目标时间不同而被误判冲突'
            '的CPU校验错误；222目标核验保持原样，从未提交的mc2_region_0继续，保留原停止结果，'
            '不增加模型回放、创作轮次或渲染授权。')
        print(_json({'infrastructure_resume': auth['execution_directory']}))
    elif args.resume_known_failure:
        from .microclip_v2_known500 import record_resume
        auth = record_resume(args.output,
            '用户在已知HTTP500停止后再次明确回复“继续”，授权对224训练动作区域做一次有界传输重试，'
            '保留原失败与全部已知观察，沿同一完整slot试验继续；不重放未知请求、'
            '不增加渲染授权、不恢复Goal，不自动开启另一轮重试。')
        print(_json({'known_failure_resume': auth['execution_directory']}))
    elif args.resume_undispatched_retry:
        from .microclip_v2_known500 import record_dispatch_resume
        auth = record_dispatch_resume(args.output,
            '用户已明确授权完成一次224已知HTTP500的实际重试；225在POST前被本地重复body守卫拦截，'
            '没有HTTP发送或模型内容。修复程序并继续尚未使用的唯一实际重试，保留225拦截及停止结果；'
            '不增加实际网络重试、渲染授权或创作轮次，不恢复Goal，不重放未知请求。')
        print(_json({'undispatched_retry_resume': auth['execution_directory']}))
    elif args.resume_boundary_navigation:
        from .microclip_v2_boundary_state import record_boundary_resume
        auth = record_boundary_resume(args.output,
            '用户在231有效语义阻塞后明确回复“继续”，授权补齐同一事件边界的局部缺项→'
            '模型指定新帧→边界重选回路，保留原已知观察与五个事件，在同一15秒slot试验内'
            '继续尚未使用的唯一渲染；不恢复Goal、不重规划整片、不自动新轮，未知请求不重放。',
            Path(__file__).with_name('craft_knowledge') / 'GLM_BOUNDARY_NAVIGATION_V1.md')
        print(_json({'boundary_navigation_resume': auth['execution_directory']}))
    elif args.resume_boundary_metadata:
        from .microclip_v2_boundary_metadata import record_metadata_resume
        auth = record_metadata_resume(args.output,
            '用户在238已知格式失败后再次明确继续。授权修复confirm动作历史观察范围字段的CPU解析，'
            '仅将等于232已完成观察范围的两字段绑定为非执行metadata，保留237/238原回复、'
            '失败、旧停止结果，不发第三次nav1；沿同一slot试验直接对模型自己选的候选做新邻帧'
            '确认，再继续原唯一未使用渲染。Goal暂停，不增加创作轮次或重放未知请求。')
        print(_json({'boundary_metadata_resume': auth['execution_directory']}))
    else:
        print(json.dumps(execute(args.output), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
