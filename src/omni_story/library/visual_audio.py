"""GLM-owned, picture-grounded TTS after an actual edit; no teacher screenplay."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
import math
from pathlib import Path
import subprocess

from .media import probe_media, sha256_file
from .state import LibraryState, LibraryStopped, json_sha, write_json, file_lock
from .opencode_provider import OpenCodeMCP, PROVIDER

POLICY = 'visual_audio_v1'
SUBTITLE_POLICY = 'single_localization_v1'
JSON_ONLY = '只输出一个完整JSON对象。禁止Markdown代码围栏、前言、总结或JSON外说明。'


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def content_ready(review, story_context):
    """Accept the reference's meaning without requiring speech in silent work."""
    from .contracts import reference_strategy
    from .story_context import core_missing
    speech = reference_strategy(story_context['reference'], creative_audio=True)['speech_mode']
    return (review['theme_alignment'] == 'matches' and
            (speech == 'none' or review['speech_clear'] is True) and
            not core_missing(review) and review['repair_at'] == 'none')


def revise_draft_timing(context, mcp, video, output_dir, *, stage='audio_plan_v1', scope=None):
    """One local draft repair using measured TTS; leave unrelated lines unchanged."""
    from .story_context import validate_story_context
    validate_story_context(context)
    problems = context.get('timing_problems', [])
    if not problems:
        return context
    previous = {line['id']: line for line in context['audio_draft']}
    editable = {row['line_id'] for row in problems}
    if not editable <= previous.keys():
        raise ValueError('audio_draft_timing_unknown_line')
    prompt = json.dumps(dict(story_context=context, editable_line_ids=sorted(editable)),
                        ensure_ascii=False) + (
        '\n承接同一参考主旨和现有完整文稿，只修正editable_line_ids中的实际TTS超时或重叠。'
        '依据实际画面缩短这些句子，或在同一组segment_ids可用窗口内调整时间；'
        '旁白可跨相邻镜头；对白保留说话者身份与已有身份锚，可覆盖反应、动作或细节画面。不要改写整套故事。'
        '可为新故事赋予身份、动机和原创对白；不要声称画面发生了实际没有的物理动作。'
        '其余句子逐字段保留，所有voice、mode和segment_ids保持；不得新增ID。'
        '无需逐音素对齐或消除轻微口型瑕疵。返回JSON {"audio_draft":[现有草稿字段]}。' + JSON_ONLY)

    def validate(value):
        if not isinstance(value, dict) or set(value) != {'audio_draft'} or not isinstance(value['audio_draft'], list):
            raise ValueError('audio_draft_timing_response_required')
        proposed = {line['id']: line for line in value['audio_draft']}
        if len(proposed) != len(value['audio_draft']) or not proposed.keys() <= previous.keys():
            raise ValueError('audio_draft_timing_line_identity_changed')
        for line_id, line in previous.items():
            if line_id not in editable:
                if proposed.get(line_id) != line:
                    raise ValueError('audio_draft_timing_unrelated_line_changed:' + line_id)
            elif line_id in proposed and any(proposed[line_id].get(key) != line.get(key)
                                             for key in ('voice', 'mode', 'segment_ids', 'speaker')):
                raise ValueError('audio_draft_timing_role_changed:' + line_id)
        candidate = dict(context, audio_draft=value['audio_draft'])
        validate_story_context(candidate)
        return value

    video = Path(video).resolve(strict=True)
    result = mcp.call(stage, prompt, video, validate, scope=scope or dict(kind='complete_file',
        source_sha256=sha256_file(video), source_start_s=0, source_end_s=context['duration_s']))
    revised = deepcopy(context)
    revised['audio_draft'] = result['audio_draft']
    revised['audio_timing'] = 'revised'
    revised.pop('timing_problems', None)
    write_json(Path(output_dir)/'draft_timing_revision.json', revised)
    return revised


def review_final_video(output, review_dir, story_context, *, av_reviewer=None, version=0):
    """Review one actual mixed video, with the shared reference goal and strategy."""
    from .audio_review import review_audio_video, INLINE_BASE64_LIMIT
    from .contracts import reference_strategy
    output, review_dir = Path(output), Path(review_dir)
    review_dir.mkdir(parents=True, exist_ok=True)
    duration = probe_media(output)['duration_s']
    review_media = output
    if 4*((output.stat().st_size+2)//3) >= INLINE_BASE64_LIMIT:
        review_media = review_dir/f'actual_av_proxy_v{version}.mp4'
        receipt = review_media.with_suffix('.binding.json')
        identity = dict(source_sha256=sha256_file(output), policy='audio_copy_picture_proxy_v1')
        if receipt.exists():
            saved = read(receipt)
            if saved['input'] != identity or sha256_file(review_media) != saved['sha256']:
                raise LibraryStopped('audio_av_proxy_changed')
        else:
            bitrate = max(32000, int(6_000_000*8/duration)-192000)
            temporary = review_media.with_name(review_media.stem+'.part.mp4')
            subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-n', '-i', str(output),
                '-map', '0:v:0', '-map', '0:a:0', '-c:v', 'libx264', '-preset', 'fast',
                '-b:v', str(bitrate), '-maxrate', str(bitrate), '-bufsize', str(bitrate*2),
                '-fps_mode', 'passthrough', '-pix_fmt', 'yuv420p', '-c:a', 'copy', str(temporary)], check=True)
            if abs(duration-probe_media(temporary)['duration_s']) > .075:
                raise LibraryStopped('audio_av_proxy_time_changed')
            temporary.replace(review_media)
            write_json(receipt, dict(input=identity, sha256=sha256_file(review_media)))
        if 4*((review_media.stat().st_size+2)//3) >= INLINE_BASE64_LIMIT:
            raise LibraryStopped('audio_whole_review_payload_too_large_no_paid_request')
    goal = {key: story_context['reference'][key] for key in ('theme', 'intended_takeaway')}
    if story_context['reference'].get('speech_form'):
        goal['speech_form'] = story_context['reference']['speech_form']
    if story_context['reference'].get('visual_form'):
        goal['visual_form'] = story_context['reference']['visual_form']
    visual_goals = list(dict.fromkeys(row['coverage']['visual_goal']
        for row in [*story_context.get('slots', []), *story_context['segments']]
        if isinstance(row.get('coverage'), dict) and isinstance(row['coverage'].get('visual_goal'), str)
        and row['coverage']['visual_goal'].strip()))
    if visual_goals:
        goal['visual_goals'] = visual_goals
    strategy = reference_strategy(story_context['reference'], creative_audio=True)
    prompt = json.dumps(dict(reference_goal=goal, strategy=strategy), ensure_ascii=False) + (
        '\n实际听看整片，先描述观众能理解的内容和感受，再判断是否实现参考的表达目标。'
        '没有提供生成台词；不得用参考目标替实际声音和画面填事实。'
        'reinterpret允许重设人物身份、动机、关系和对白；声音可为可见物件赋予外观与动作能承载的新用途，'
        '也可与画面共同建立隐喻。先依据本片实际声画和前后关系理解新故事，'
        '原电影的身份、物件用途和剧情因果不作为否决依据，画面无需为这段声音专门拍摄。'
        '判断参考主旨是否传达，不要求复刻参考的事件、人数或数额；人物选择可由对白和回应呈现。'
        'preserve应忠于已声明的事实。两种模式均核对当前角色的视觉身份与声音归属、物件状态、真实动作和新故事因果，'
        '不得用台词或隐喻补造缺失的关键物理动作。'
        '按speech_mode与timing_driver判断：旁白可跨镜头；无语音作品按画面、动作、音乐和可懂度判断，'
        '没有对白本身不是缺陷，speech_clear可为null。轻微口型、动作或节奏瑕疵可接受；'
        '若参考要求dialogue或mixed，按reference_goal.speech_form核对实际的角色视角、发言关系与旁白作用；'
        '问答、独白或混合形式都以该参考为准，不把所有对白强制改成问答。未提供speech_form时只判断角色发言归属。'
        '已建立说话者后，角色声音可继续覆盖听者反应、动作或细节画面；出画本身不属于第三人称旁白。'
        '不能把第三人称讲故事当成角色对白形式匹配；核心形式被替代时，repair_at应为audio并记录缺失。'
        '不审逐音素同步。只报告影响核心意思或可懂度的问题，以及严重遮挡声音的混音问题。'
        'issues可以保留小瑕疵，但仅需处理的问题才影响repair_at和missing_core_meaning。'
        '同次听看必须在visual_notes:string[]中具体说明实际动作、反应、物件、环境或持续表演'
        '怎样补充声音，并记录明显重复或视觉缺项；没有明显缺项也须说明实际画面贡献。'
        '无法确认时如实记录，不补造动作或精确时间。不要求固定镜头比例、频繁切镜或每种镜头齐全。'
        '人物说话画面可以承担表达；但若仅靠人物轮流说话替代了visual_form或visual_goals要求的'
        '核心可见变化、行动或结果，应在missing_core_meaning记录具体缺项并将repair_at设为selection。'
        '视觉目标只是待核对的创作意图，不能当成实际已发生的画面事实。轻微重复或节奏瑕疵可留在issues，'
        '不因这些小问题要求重选素材。'
        '不能一边报告妨碍核心理解的声画矛盾，一边将repair_at设为none；应在missing_core_meaning写明具体问题，'
        '并按实际需要给出audio或selection修复。'
        '只输出JSON {"apparent_story":"实际理解的内容或表达效果",'
        '"theme_alignment":"matches或unclear或different","speech_clear":true,'
        '"visual_notes":["实际画面贡献或缺项"],"missing_core_meaning":[],"repair_at":"none或audio或selection",'
        '"issues":[],"limitations":[]}。'
        '列表元素都是字符串。')
    received = (av_reviewer or review_audio_video)(review_media, review_dir/f'av_review_v{version}', prompt=prompt)
    if received.get('http_status') != 200 or received.get('parse_error'):
        raise LibraryStopped('audio_actual_listening_response_not_valid_received_200')
    verdict = received.get('parsed')
    # Preserve an explicit absent/unassessable voice as received evidence;
    # content_ready still requires True whenever the reference needs speech.
    if (not isinstance(verdict, dict) or verdict.get('theme_alignment') not in {'matches', 'unclear', 'different'} or
            (type(verdict.get('speech_clear')) is not bool and
             not ('speech_clear' in verdict and verdict['speech_clear'] is None)) or
            verdict.get('repair_at') not in {'none', 'audio', 'selection'} or
            not isinstance(verdict.get('apparent_story'), str) or not verdict['apparent_story'].strip()):
        raise LibraryStopped('audio_whole_content_review_invalid_received')
    for key in ('missing_core_meaning', 'issues', 'limitations'):
        if not isinstance(verdict.get(key), list) or any(not isinstance(value, str) for value in verdict[key]):
            raise LibraryStopped('audio_whole_content_review_invalid_received')
    if (not isinstance(verdict.get('visual_notes'), list) or not verdict['visual_notes'] or
            any(not isinstance(value, str) or not value.strip() for value in verdict['visual_notes'])):
        raise LibraryStopped('audio_whole_content_review_invalid_received')
    write_json(review_dir/f'review_v{version}.json', verdict)
    return verdict


def source(path):
    path = Path(path).resolve(strict=True)
    metadata = probe_media(path)
    return dict(path=str(path), sha256=sha256_file(path), duration_s=metadata['duration_s'],
                video_stream_index=metadata['video_stream_index'])


def revision_feedback(feedback):
    """Scope edits to measured overruns or located actual-media problems."""
    if not feedback or 'previous_plan' not in feedback:
        return feedback
    result = dict(feedback)
    if 'editable_line_ids' not in result and 'timing_problems' in result:
        result['editable_line_ids'] = list(dict.fromkeys(
            row['line_id'] for row in result['timing_problems']))
    elif 'editable_line_ids' not in result and 'actual_review' in result:
        lines = result['previous_plan']['lines']
        known = {row['id'] for row in lines}
        editable = set()
        def visit(review):
            if not isinstance(review, dict):
                return
            for issue in review.get('issues', []):
                if not isinstance(issue, dict) or issue.get('review_protocol_issue') is True:
                    continue
                start, end = issue.get('start_s'), issue.get('end_s')
                if (type(start) not in (int, float) or type(end) not in (int, float) or
                        not math.isfinite(start) or not math.isfinite(end) or not 0 <= start < end):
                    continue
                line_id = issue.get('line_id')
                if (isinstance(line_id, str) and line_id in known and any(
                        row['id'] == line_id and row['start_s'] < end and row['end_s'] > start
                        for row in lines)):
                    editable.add(line_id)
                    continue
                editable.update(row['id'] for row in lines
                                if row['start_s'] < end and row['end_s'] > start)
            for key in ('audiovisual', 'picture_comparison', 'whole_picture_review', 'performance_checks'):
                visit(review.get(key))
        visit(result['actual_review'])
        result['editable_line_ids'] = [row['id'] for row in lines if row['id'] in editable]
    return result


def plan_revision_check(value, feedback):
    """Protect performed data outside an explicitly scoped model revision."""
    if not feedback or 'previous_plan' not in feedback or 'editable_line_ids' not in feedback:
        return value
    previous = {row['id']: row for row in feedback['previous_plan']['lines']}
    editable = feedback['editable_line_ids']
    if (not isinstance(editable, list) or any(not isinstance(item, str) for item in editable) or
            not set(editable) <= set(previous)):
        raise ValueError('audio_plan_revision_editable_ids_invalid')
    current = {row['id']: row for row in value['lines']}
    if not set(current) <= set(previous):
        raise ValueError('audio_plan_revision_unrelated_new_id')
    fields = ('text', 'voice', 'speaker_id', 'mode', 'start_s', 'end_s')
    for line_id in set(previous) - set(editable):
        if line_id not in current or any(current[line_id][key] != previous[line_id][key] for key in fields):
            raise ValueError('audio_plan_revision_protected_line_changed:' + line_id)
    return value


def review_check(value, duration, *, audiovisual=False, lines=None):
    if not isinstance(value, dict) or value.get('status') not in {'pass', 'partial', 'fail'}:
        raise ValueError('audio_review_status_required')
    if not isinstance(value.get('issues'), list) or not isinstance(value.get('limitations'), list):
        raise ValueError('audio_review_issues_and_limits_required')
    if any(not isinstance(x, str) for x in value['limitations']):
        raise ValueError('audio_review_limitations_strings_required')
    notes = value.get('scope_notes', []) if audiovisual else []
    if (not isinstance(notes, list) or any(not isinstance(note, str) for note in notes) or
            len(notes) != len(set(notes)) or
            not set(notes) <= {'phoneme_viseme_lipsync', 'original_actor_voice_identity'}):
        raise ValueError('audio_review_scope_notes_invalid')
    for row in value['issues']:
        if (not isinstance(row, dict) or not isinstance(row.get('description'), str) or
                type(row.get('start_s')) not in (int, float) or type(row.get('end_s')) not in (int, float) or
                not 0 <= row['start_s'] < row['end_s'] <= duration):
            raise ValueError('audio_review_actual_interval_required')
    if value['status'] == 'pass' and (value['issues'] or value['limitations']):
        raise ValueError('audio_review_pass_cannot_have_issues_or_limits')
    if audiovisual:
        for key,field in [('heard','text'),('seen','description')]:
            rows = value.get(key)
            if not isinstance(rows,list):
                raise ValueError('audio_review_actual_' + key + '_required')
            for row in rows:
                if (not isinstance(row,dict) or not isinstance(row.get(field),str) or not row[field].strip() or
                        type(row.get('start_s')) not in (int,float) or type(row.get('end_s')) not in (int,float) or
                        not 0 <= row['start_s'] < row['end_s'] <= duration):
                    raise ValueError('audio_review_' + key + '_actual_interval_required')
            if value['status']=='pass' and not rows:
                raise ValueError('audio_review_pass_requires_' + key + '_coverage')
        if value['status']=='pass':
            for line in lines or []:
                start,end = line['start_s'],line['start_s']+line['actual_duration_s']
                tolerance = min(.35,(end-start)/4)
                for key in ('heard','seen'):
                    cursor,required_end = start+tolerance,end-tolerance
                    for row in sorted(value[key],key=lambda r:r['start_s']):
                        if row['end_s'] <= cursor:
                            continue
                        if row['start_s'] > cursor+1e-6:
                            break
                        cursor = max(cursor,row['end_s'])
                    if cursor < required_end-1e-6:
                        raise ValueError('audio_review_pass_missing_actual_' + key + ':' + line['id'])


def detail_review_check(value, panels, duration):
    """Validate only the supplied native-pixel panels, never a video interval."""
    if not isinstance(value, dict) or value.get('status') not in {'pass', 'partial', 'fail'}:
        raise ValueError('audio_mask_detail_status_required')
    if set(value) != {'status', 'issues', 'limitations', 'patches'}:
        raise ValueError('audio_mask_detail_fields_required')
    if (not isinstance(value['issues'], list) or not isinstance(value['limitations'], list) or
            not isinstance(value['patches'], list)):
        raise ValueError('audio_mask_detail_lists_required')
    if any(not isinstance(row, str) for row in value['limitations']):
        raise ValueError('audio_mask_detail_limitations_strings_required')
    known = {panel['panel_id']: panel['source_time_s'] for panel in panels}
    for issue in value['issues']:
        if not isinstance(issue, dict) or set(issue) != {'panel_id', 'at_s', 'description'}:
            raise ValueError('audio_mask_detail_point_fields_required')
        if not isinstance(issue['panel_id'], str) or issue['panel_id'] not in known:
            raise ValueError('audio_mask_detail_unknown_panel_id')
        at_s = issue['at_s']
        try:
            finite = type(at_s) in (int, float) and math.isfinite(at_s)
        except OverflowError:
            finite = False
        if (not finite or not 0 <= at_s <= duration or
                not math.isclose(at_s, known[issue['panel_id']], rel_tol=0, abs_tol=1e-6)):
            raise ValueError('audio_mask_detail_actual_panel_point_required')
        if not isinstance(issue['description'], str) or not issue['description'].strip():
            raise ValueError('audio_mask_detail_description_required')
    if value['status'] == 'pass' and (value['issues'] or value['limitations']):
        raise ValueError('audio_mask_detail_pass_cannot_have_issues_or_limits')
    if value['status'] == 'pass' and value['patches']:
        raise ValueError('audio_mask_detail_pass_cannot_request_edits')


class VisualAudio:
    def __init__(self, state, mcp, video, reference, *, music_path=None, tts=None,
                 av_reviewer=None, story_context=None, tts_cache_dir=None):
        self.state, self.mcp = state, mcp
        self.root = Path(state.output)
        self.video, self.reference = video, reference
        self.duration = video['duration_s']
        self.story_context = story_context or state.input_lock.get('story_context')
        if self.story_context is not None:
            from .story_context import validate_story_context
            from .contracts import validate_reference
            validate_story_context(self.story_context, reference_sha=reference['sha256'],
                                   video_sha=video['sha256'])
            validate_reference(self.story_context['reference'], reference['sha256'], reference['duration_s'])
            if (state.input_lock.get('story_context') is not None and
                    json_sha(self.story_context) != json_sha(state.input_lock['story_context'])):
                raise LibraryStopped('audio_story_context_changed')
        cache = tts_cache_dir or (self.story_context or {}).get('tts_cache_dir')
        self.tts_cache_dir = Path(cache).resolve() if cache else self.root/'tts'
        if (state.input_lock.get('tts_cache_dir') is not None and
                str(self.tts_cache_dir) != state.input_lock['tts_cache_dir']):
            raise LibraryStopped('audio_tts_cache_changed')
        self.music = Path(music_path).resolve(strict=True) if music_path else None
        self.music_sha256 = sha256_file(self.music) if self.music else None
        if self.music and state.input_lock.get('music') != dict(path=str(self.music),sha256=self.music_sha256):
            raise LibraryStopped('audio_music_source_changed')
        self.tts, self.av_reviewer = tts, av_reviewer
        card = Path(__file__).with_name('resources')/'visual_audio/decision-cards.md'
        self.knowledge = card.read_text(encoding='utf-8')
        snapshot = self.root/'decision-cards.md'
        if snapshot.exists() and snapshot.read_bytes() != card.read_bytes():
            raise LibraryStopped('audio_knowledge_snapshot_changed')
        if not snapshot.exists():
            snapshot.write_bytes(card.read_bytes())
        for item in (video, reference):
            if sha256_file(item['path']) != item['sha256']:
                raise LibraryStopped('audio_actual_source_changed')

    def status(self, stage, **details):
        write_json(self.root/'audio_progress.json',dict(stage=stage, **details))

    def call(self, name, prompt, media, validator, *, image=False, scope=None):
        path = Path(media).resolve(strict=True)
        full_prompt = self.knowledge+'\n\n'+prompt+'\n'+JSON_ONLY
        recovery = self.root/'audio_preflight_recovery.json'
        if recovery.exists():
            grants = read(recovery)
            grant = next((row for row in reversed(grants) if row['stage']==name),None)
            if grant:
                self.state._reload()
                failed = next(c for c in self.state.data['calls'] if c['id']==grant['failed_call_id'])
                evidence = self.root/'calls'/failed['id']/grant['evidence_relative_path']
                journal = self.root/'mcp_http.jsonl'
                if (failed['status']!='failed_known' or failed['request_sha256']!=grant['request_sha256'] or
                        failed['name']!=grant['failed_stage'] or
                        sha256_file(evidence)!=grant['evidence_sha256'] or
                        grant['failure_kind'] not in {'module_import','endpoint_guard'} or
                        ('ERR_MODULE_NOT_FOUND' if grant['failure_kind']=='module_import' else 'library_mcp_endpoint_blocked')
                            not in evidence.read_text(encoding='utf-8') or
                        journal.exists() and any(json.loads(line).get('job_id')==failed['id'] and
                            json.loads(line).get('type')=='request' for line in journal.read_text().splitlines())):
                    raise LibraryStopped('audio_preflight_recovery_not_proven_no_post')
                name = grant['alias']
                original = read(self.root/'calls'/failed['id']/'request.json')
                full_prompt = original['arguments']['prompt']+'\n'+grant['prompt_suffix']
        self.status(name)
        duration = self.duration if image else probe_media(path)['duration_s']
        return self.mcp.call(name, full_prompt, path, validator,
            scope=scope or dict(kind='complete_file',source_sha256=sha256_file(path),source_start_s=0,
                       source_end_s=duration),**({'image':True} if image else {}))

    def region_seed(self, raw):
        """Reuse a received localization, never substitute an author subtitle list."""
        from .audio_contracts import validate_regions
        from .contracts import parse_model_json
        seed = self.state.input_lock['configuration'].get('subtitle_region_seed')
        if not seed:
            return None
        ledger = Path(seed['path']).resolve(strict=True)
        if sha256_file(ledger)!=seed['sha256']:
            raise LibraryStopped('audio_subtitle_seed_ledger_changed')
        saved = read(ledger)
        if saved['input_lock']['video']['sha256']!=self.video['sha256']:
            raise LibraryStopped('audio_subtitle_seed_source_changed')
        call = next((row for row in saved['calls'] if row['id']==seed['call_id']),None)
        if not call or call['status']!='received' or call['name']!='audio_subtitle_regions':
            raise LibraryStopped('audio_subtitle_seed_not_received_localization')
        folder = ledger.parent/'calls'/call['id']
        request,reply = read(folder/'request.json'),read(folder/'response.json')
        if (json_sha(request)!=call['request_sha256'] or json_sha(reply)!=call['response_sha256'] or
                request['tool']!='analyze_video' or request['media_sha256']!=sha256_file(raw)):
            raise LibraryStopped('audio_subtitle_seed_request_or_reply_changed')
        value = parse_model_json('\n'.join(row['text'] for row in reply['result']['content'] if row.get('type')=='text'))
        validate_regions(value,self.duration)
        if read(folder/'parsed.json')!=value:
            raise LibraryStopped('audio_subtitle_seed_parsed_changed')
        self.state.set_artifact('audio_subtitle_seed',dict(seed=seed,request_sha256=call['request_sha256'],
            response_sha256=call['response_sha256'],new_vision_post=False))
        return value

    def picture_proxy(self, path, target):
        """Preserve time/canvas and mute only; actual local media binds the model."""
        path,target = Path(path).resolve(strict=True),Path(target)
        receipt = target.with_suffix('.binding.json')
        identity = dict(input_path=str(path),input_sha256=sha256_file(path),
            policy='original_canvas_time_mute_h264_crf27_fast_v1')
        if receipt.exists():
            saved = read(receipt)
            if saved['input'] != identity or not target.is_file() or sha256_file(target) != saved['output_sha256']:
                raise LibraryStopped('audio_picture_proxy_binding_changed')
        elif target.exists():
            raise LibraryStopped('audio_picture_proxy_unbound_existing')
        else:
            temporary = target.with_name(target.stem+'.part.mp4')
            subprocess.run(['ffmpeg','-nostdin','-y','-v','error','-i',str(path),'-map','0:v:0',
                '-an','-c:v','libx264','-crf','27','-preset','fast','-pix_fmt','yuv420p',
                '-movflags','+faststart',str(temporary)],check=True)
            if abs(probe_media(temporary)['duration_s']-self.duration) > .075:
                raise LibraryStopped('audio_picture_proxy_time_changed')
            temporary.replace(target)
            write_json(receipt,dict(input=identity,output_sha256=sha256_file(target)))
        if target.stat().st_size >= 8_000_000:
            raise LibraryStopped('audio_picture_requires_technical_chunking_over_8mb')
        if abs(probe_media(target)['duration_s']-self.duration) > .075:
            raise LibraryStopped('audio_picture_proxy_time_changed')
        return target

    def prepare_picture(self):
        from .audio_contracts import validate_regions, validate_observation
        from .audio_render import mask_video
        raw = self.picture_proxy(self.video['path'], self.root/'original_mute.mp4')
        regions = self.region_seed(raw) or self.call('audio_subtitle_regions',
            '只定位画外字幕、标题和说话人标签；保留人物、动作和场景内文字。'
            f'坐标为整帧归一化比例，时间0..{self.duration}秒。无叠字masks=[]。返回JSON '
            '{"masks":[{"x":比例,"y":比例,"w":比例,"h":比例,"start_s":秒,"end_s":秒,'
            '"color":"#000000","reason":"实际叠字区域"}],"protected_text":[],"limitations":[]}。',
            raw, lambda value: validate_regions(value,self.duration))
        write_json(self.root/'subtitle_regions.json',regions)
        masked, receipt = self.root/'picture_masked.mp4', self.root/'picture_mask_receipt.json'
        identity = dict(video_sha256=self.video['sha256'],masks=regions['masks'])
        if receipt.exists():
            saved = read(receipt)
            if saved['input'] != identity or sha256_file(masked) != saved['output']['sha256']:
                raise LibraryStopped('audio_mask_cache_changed')
        else:
            result = mask_video(self.video['path'],regions['masks'],masked)
            write_json(receipt,dict(input=identity,output=result))
        proxy = self.picture_proxy(masked,self.root/'picture_observation.mp4')
        observed = self.call('audio_observe',
            '独立记录实际画面人物和可见动作，不用配音稿补造事实。'
            '角色可见且适合发言才标dialogue，不确定用narration或none；表情不能证明说话。'
            f'时间0..{self.duration}秒。返回JSON '
            '{"actors":[{"id":"自行命名","appearance":"实际外观"}],'
            '"events":[{"id":"自行命名","start_s":秒,"end_s":秒,"visible":"可见变化",'
            '"actor_ids":[],"speech_mode":"dialogue或narration或none","speech_actor_id":null}],'
            '"limitations":[]}。',proxy,lambda value:validate_observation(value,self.duration))
        write_json(self.root/'picture_observed.json',observed)
        return masked,proxy,observed

    def plan(self, proxy, observed, version, feedback=None):
        from .audio_contracts import validate_plan
        if self.story_context is None:
            raise LibraryStopped('audio_story_context_required')
        feedback = revision_feedback(feedback)
        context = dict(observed=observed,actual_duration_s=self.duration,feedback=feedback,
            story_context=self.story_context,
            voices=['Ethan','Cherry','Serena','Moon'])
        prompt = json.dumps(context,ensure_ascii=False)+(
            '\n依据同一story_context中的reference主旨、观众应理解的内容、segments剪辑目的和audio_draft，'
            '为实际剪辑成片组织完整连贯的中文音频。沿用粗剪已建立的叙事和合适的草配句，'
            '只为真实画面变化或实测语音超时做必要调整，不根据局部动作另起一个故事。'
            'visible_facts和当前observed才是画面事实。reinterpret模式允许为新故事重设身份、动机、'
            '人物关系和原创对白，原片字幕/剧情不构成叙事约束；preserve模式才沿用已声明的来源语义。'
            '不可把unsupported_claims或画面没有的物理动作硬说成已经发生。'
            '对白需适合可见角色；不确定用口外旁白。旁白可以贯穿相邻镜头形成完整句子，'
            '不要为窄小嘴部窗口把完整意思切成无意义碎句，也不必逐秒填满。'
            '首版按每秒约3个汉字保守估计，给句首句尾留余量；这不是时长证明，仍以实际TTS测量为准。'
            '角色音色保持连续，适合的audio_draft文本和voice保留以复用已收到TTS。'
            '若参考策略speech_mode=none，返回空lines且不创作语音。'
            '字段硬约束：mode仅可为narration或dialogue。旁白mode=narration时speaker_id必须是JSON null，'
            '不可用空字符串或narrator。对白mode=dialogue时speaker_id必须是对应观察事件的speech_actor_id，'
            '且该事件speech_mode必须已经是dialogue；narration或none事件不能自改为角色对白。'
            '没有确切可读嘴形时只能检查发言窗口，不能声称逐音素唇同步。'
            '返回JSON {"lines":[{"id":"稳定语句ID","text":"你的原创短句","voice":"Ethan",'
            '"speaker_id":null,"mode":"narration或dialogue","start_s":秒,"end_s":容纳窗口末秒,'
            '"event_ids":["观察事件ID"],"segment_ids":["story_context段落ID"],'
            '"visual_basis":"这句话如何推动参考主旨、哪些画面或来源语义支持它"}],'
            '"limitations":[]}。end_s是可用窗口，音频真实长度在生成后才知道。'
            '各句不重叠，每句必须引用实际覆盖其窗口的segment_ids。'
            '旁白可由所引相邻event或segment连续覆盖，不可跨无证据时间缺口；旁白event_ids可为空。'
            '角色对白窗口仍必须被一个对应人物的dialogue事件完整容纳，不能借跨镜头旁白规则扩大对白。'
            '若feedback有实际问题，仅改相关句的文本/归属/时间；保留没有问题的句子及ID。'
            '若feedback同时给previous_plan与editable_line_ids，只能修改或删除这些ID的句子，禁止新增其他ID。'
            '其余ID必须保留，且text、voice、speaker_id、mode、start_s、end_s逐字段原样不变；'
            'event_ids和visual_basis可依据当前同源观察结构调整，但仍必须通过画面窗口与人物约束。')
        def validate_revision(value):
            validate_plan(value,observed,self.duration,story_context=self.story_context)
            return plan_revision_check(value,feedback)
        result = self.call(f'audio_plan_v{version}',prompt,proxy,
                           validate_revision)
        write_json(self.root/f'audio_plan_v{version}.json',result)
        return result

    def synthesize(self, plan):
        from .tts import ENDPOINT, MODEL, synthesize_line
        lines = []
        problems = []
        for line in plan['lines']:
            saved = next((row for row in (self.story_context or {}).get('received_tts', [])
                          if row['text'] == line['text'] and row['voice'] == line['voice']), None)
            if saved is not None:
                if (sha256_file(saved['audio_path']) != saved['audio_sha256'] or
                        not math.isfinite(saved['actual_duration_s']) or saved['actual_duration_s'] <= 0):
                    raise LibraryStopped('audio_received_tts_changed')
                result = dict(audio_path=saved['audio_path'], audio_sha256=saved['audio_sha256'],
                              duration_s=saved['actual_duration_s'])
            else:
                fingerprint = json_sha(dict(provider=ENDPOINT, model=MODEL, language='Chinese',
                    text=line['text'], voice=line['voice']))[:20]
                result = (self.tts or synthesize_line)(line['text'],line['voice'],self.tts_cache_dir,
                                                      line_id='utt_'+fingerprint)
            if result['duration_s'] > line['end_s']-line['start_s']+1e-6:
                problems.append(dict(line_id=line['id'],actual_duration_s=result['duration_s'],
                    available_s=line['end_s']-line['start_s'],description='实际TTS超过画面窗口，须缩短文本或依据画面另选窗口'))
            lines.append(dict(line,audio_path=result['audio_path'],actual_duration_s=result['duration_s'],
                              audio_sha256=result['audio_sha256']))
        return lines,problems

    def review(self, output, proxy, observed, lines, version):
        return review_final_video(output, self.root, self.story_context,
                                  av_reviewer=self.av_reviewer, version=version)

    def run(self):
        from .audio_render import mix_speech
        from .audio_usage import task_usage, usage_bindings
        final = self.root/'audio_result.json'
        if final.exists():
            saved = read(final)
            self.state._reload()
            records = self.state.data['artifacts'].get('visual_audio_delivery',[])
            if (not records or json_sha(read(records[-1]['path'])) != records[-1]['sha256'] or
                    read(records[-1]['path']) != dict(result_path=str(final),result_sha256=sha256_file(final)) or
                    saved.get('story_context_sha256') != json_sha(self.story_context) or
                    any(sha256_file(path) != digest for path,digest in saved['bound_files'].items())):
                raise LibraryStopped('audio_delivery_changed')
            return saved
        shared_draft = self.story_context.get('source_visual_policy') == 'silent_subtitle_masked'
        version = 0
        if shared_draft:
            from .story_context import synthesize_draft
            # Selection and finecut already saw these cleaned pixels. Carry their
            # common script forward instead of buying another observe/plan pass.
            masked = proxy = Path(self.video['path'])
            observed = dict(limitations=[])
            def draft_plan(context):
                return dict(lines=[dict(line, speaker_id=line.get('speaker_id'), event_ids=[])
                                   for line in context['audio_draft']], limitations=[])
            def perform(context):
                performed = synthesize_draft(context, self.tts_cache_dir, tts=self.tts)
                planned = draft_plan(performed)
                received = {row['id']: row for row in performed['received_tts']}
                spoken = [{**line, **received[line['id']]} for line in planned['lines']]
                return performed, planned, spoken, performed['timing_problems']
            performed, plan, lines, timing = perform(self.story_context)
            write_json(self.root/'audio_plan_v0.json', plan)
            write_json(self.root/'tts_binding_v0.json', dict(lines=lines, timing_problems=timing))
            if timing:
                from .visual_story_trial import proxy as prepare_proxy
                local = prepare_proxy(self.video, 0, self.duration, self.root/'timing_media', label='audio_timing')
                revised = revise_draft_timing(performed, self.mcp, local['path'], self.root,
                    scope={key: local[key] for key in ('kind', 'source_sha256', 'source_start_s', 'source_end_s')})
                performed, plan, lines, timing = perform(revised)
                write_json(self.root/'audio_plan_v1.json', plan)
                write_json(self.root/'tts_binding_v1.json', dict(lines=lines, timing_problems=timing))
                version = 1
        else:
            masked,proxy,observed = self.prepare_picture()
            plan = self.plan(proxy,observed,0)
            lines,timing = self.synthesize(plan)
            write_json(self.root/'tts_binding_v0.json',dict(lines=lines,timing_problems=timing))
            if timing:
                plan = self.plan(proxy,observed,1,dict(previous_plan=plan,timing_problems=timing))
                lines,timing = self.synthesize(plan)
                write_json(self.root/'tts_binding_v1.json',dict(lines=lines,timing_problems=timing))
                version = 1
        if timing:
            self.status('needs_audio_revision',problems=timing)
            raise LibraryStopped('audio_TTS_does_not_fit_no_clipping_or_speedup')
        if self.music and sha256_file(self.music) != self.music_sha256:
            raise LibraryStopped('audio_music_source_changed')
        output = self.root/f'candidate_v{version}.mp4'
        self.status('mixing')
        mixed = mix_speech(masked,lines,output,music_path=self.music)
        review = self.review(output,proxy,observed,lines,version)
        write_json(self.root/f'review_v{version}.json',review)
        ready = content_ready(review, self.story_context)
        status = 'delivered' if ready else ('needs_material_reselection' if review['repair_at']=='selection' else 'needs_audio_revision')
        for item in (self.video,self.reference):
            if sha256_file(item['path']) != item['sha256']:
                raise LibraryStopped('audio_actual_source_changed')
        usage = task_usage(self.state)
        paths = [output,masked,self.root/f'audio_plan_v{version}.json',
            self.root/f'tts_binding_v{version}.json',self.root/f'review_v{version}.json',Path(mixed['receipt_path']),
            *(Path(row['audio_path']) for row in lines),*(Path(row['path']) for row in mixed['audio_artifacts'].values())]
        if shared_draft and version:
            paths.append(self.root/'draft_timing_revision.json')
        elif not shared_draft:
            paths.extend([self.root/'picture_mask_receipt.json',self.root/'subtitle_regions.json',
                          self.root/'picture_observed.json'])
        av_proxy = self.root/f'actual_av_proxy_v{version}.mp4'
        if av_proxy.exists():
            paths.extend([av_proxy,av_proxy.with_suffix('.binding.json')])
        bound = {str(path):sha256_file(path) for path in paths}
        bound.update(usage_bindings(usage))
        result = dict(policy=POLICY,status=status,final_video=str(output),final_sha256=mixed['sha256'],
            input_video=self.video,reference=self.reference,story_context_sha256=json_sha(self.story_context),
            selected_version=version,review=review,content_delivery_ready=ready,
            joint_quality_gate=False,technical_delivery_verified=mixed['fully_decoded'],
            bound_files=bound,vision_usage=usage,mix_receipt=mixed['receipt_path'],
            lipsync='not_measured',limitations=[*observed['limitations'],*plan['limitations'],*review['limitations'],
                'One automated actual-media content review is not human audience verification.',
                ('Source subtitles were cleaned before selection; the shared draft was reused.' if shared_draft else
                 'Subtitle masking was localized once; no dense or repeated mouth audit was performed.')])
        if shared_draft:
            result['performed_story_context'] = performed
        write_json(final,result)
        self.state.set_artifact('visual_audio_delivery',dict(result_path=str(final),result_sha256=sha256_file(final)))
        self.status(status,video=str(output),content_delivery_ready=ready)
        return result


def execute(video,reference,output,*,model_factory,provider_config,music_path=None,tts=None,
            av_reviewer=None,story_context=None,tts_cache_dir=None):
    output = Path(output).resolve()
    video,reference = source(video),source(reference)
    if video['sha256'] == reference['sha256']:
        raise LibraryStopped('audio_input_must_be_edited_video_not_reference')
    if story_context is None:
        raise LibraryStopped('audio_story_context_required')
    from .story_context import validate_story_context
    from .contracts import validate_reference
    validate_story_context(story_context,reference_sha=reference['sha256'],video_sha=video['sha256'])
    validate_reference(story_context['reference'],reference['sha256'],reference['duration_s'])
    if abs(story_context['duration_s'] - video['duration_s']) > .075:
        raise LibraryStopped('audio_story_context_duration_changed')
    cache = tts_cache_dir or story_context.get('tts_cache_dir') or output/'tts'
    cache = Path(cache).resolve()
    for path in cache.glob('*/state.json'):
        if read(path).get('status') in {'submitted','uncertain'}:
            raise LibraryStopped('audio_tts_cache_request_outcome_unknown_no_replay')
    configuration = {**PROVIDER,**provider_config,'workflow':POLICY,'subtitle_policy':SUBTITLE_POLICY}
    identity = dict(video=video,reference=reference,configuration=configuration,
        story_context=story_context,tts_cache_dir=str(cache),
        music=(dict(path=str(Path(music_path).resolve(strict=True)),sha256=sha256_file(music_path)) if music_path else None))
    state = LibraryState(output,identity,max_requests=None)
    with file_lock(output/'.audio_worker.lock'):
        from .audio_usage import task_usage
        from .audio_new_work import validate_audio_new_work
        usage = task_usage(state)
        exclusions = validate_audio_new_work(state)
        if (usage.get('uncertain_calls') or (usage.get('prior_uncertain_calls') and exclusions is None) or
                any(service['uncertain_calls'] for service in usage.get('audio_services',{}).values())):
            raise LibraryStopped('audio_prior_request_outcome_unknown_no_new_evaluation')
        mcp = None if (output/'audio_result.json').exists() else model_factory(state)
        return VisualAudio(state,mcp,video,reference,music_path=music_path,
                           tts=tts,av_reviewer=av_reviewer,story_context=story_context,
                           tts_cache_dir=cache).run()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--video',type=Path,required=True)
    parser.add_argument('--reference',type=Path,required=True)
    parser.add_argument('--story-context',type=Path,required=True,
                        help='Source-bound reference reading, editing purpose and rough narration draft JSON.')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--music-bed',type=Path)
    parser.add_argument('--mcp-package-root',type=Path,required=True)
    parser.add_argument('--opencode',required=True)
    parser.add_argument('--vision-platform',choices=['ZAI','ZHIPU'],default='ZHIPU')
    parser.add_argument('--prior-audio-evaluation',type=Path,action='append',default=[],
                        help='Bind settled prior audio vision ledgers and retain cumulative usage.')
    parser.add_argument('--subtitle-region-seed',type=Path,
                        help='Reuse a received, source-bound GLM subtitle localization from this ledger.')
    parser.add_argument('--new-work-authorization',type=Path,
                        help='Bind explicit authorization for a fresh source while excluding prior unknown requests.')
    args = parser.parse_args(argv)
    from .server_cli import VISION_GENERATION
    configuration = dict(vision_platform=args.vision_platform,vision_generation=dict(VISION_GENERATION))
    if args.new_work_authorization:
        grant = args.new_work_authorization.resolve(strict=True)
        configuration['audio_new_work'] = dict(path=str(grant),sha256=sha256_file(grant))
    if args.subtitle_region_seed:
        ledger = args.subtitle_region_seed.resolve(strict=True)
        calls = [row for row in read(ledger)['calls'] if row['name']=='audio_subtitle_regions' and row['status']=='received'
                 and (ledger.parent/'calls'/row['id']/'parsed.json').is_file()]
        if len(calls)!=1:
            raise LibraryStopped('audio_subtitle_seed_requires_one_received_localization')
        configuration['subtitle_region_seed'] = dict(path=str(ledger),sha256=sha256_file(ledger),call_id=calls[0]['id'])
        if ledger not in [path.resolve() for path in args.prior_audio_evaluation]:
            args.prior_audio_evaluation.append(ledger)
    if args.prior_audio_evaluation:
        configuration['prior_audio_evaluations'] = [dict(path=str(path.resolve(strict=True)),sha256=sha256_file(path))
                                                   for path in args.prior_audio_evaluation]
    result = execute(args.video,args.reference,args.output,
        model_factory=lambda state:OpenCodeMCP(state,package_root=args.mcp_package_root,executable=args.opencode),
        provider_config=configuration,music_path=args.music_bed,story_context=read(args.story_context))
    print(json.dumps(result,ensure_ascii=False))


if __name__ == '__main__':
    main()
