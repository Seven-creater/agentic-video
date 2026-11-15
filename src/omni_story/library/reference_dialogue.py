"""Read ASR-aligned reference frames before locking dialogue ownership."""
from copy import deepcopy
import json
import math
from pathlib import Path

from .state import json_sha, write_json


def reference_proxy(route, start, end, label, stage):
    """Reuse the exact received proxy bytes; x264 re-encodes are not byte-stable."""
    from .pipeline import _material_ledgers, _revision_ledgers
    from .media import sha256_file
    from .visual_story_trial import proxy
    expected = dict(source_sha256=route.reference_source['sha256'], source_start_s=float(start),
                    source_end_s=float(end), roi=None, fps=30, audio=False, label=label)
    # A revision reuses its parent's exact proxy too, so identical turn requests can
    # reuse replies; any doubt about the parent's files only means a fresh proxy.
    # The label only names a purpose (27 r2: the parent's split used the full clean
    # reference encode, the revision would have made a 'scene_reference_dialogue' one
    # of the same range); every content parameter must still match.
    content = {k: v for k, v in expected.items() if k != 'label'}
    for root, saved in _revision_ledgers(route.state):
        for call in reversed(saved.get('calls', [])):
            if call.get('status') != 'received' or call.get('name', '').split('_repair')[0] != stage:
                continue
            try:
                request = json.loads((root/'calls'/call['id']/'request.json').read_text())
                media = request['arguments'].get('video_source')
                lineage = json.loads((Path(media).parent/'lineage.json').read_text()) if media else None
                spec = (lineage or {}).get('spec') or {}
                if (json_sha(request) == call.get('request_sha256') and set(spec) == set(expected) and
                        {k: v for k, v in spec.items() if k != 'label'} == content and
                        lineage.get('sha256') == request.get('media_sha256') == sha256_file(media)):
                    return lineage
            except (OSError, ValueError, KeyError, TypeError):
                continue
    for root, saved in list(_material_ledgers(route.state))[1:]:
        for call in reversed(saved['calls']):
            if call['status'] != 'received' or call['name'].split('_repair')[0] != stage:
                continue
            request = json.loads((root/'calls'/call['id']/'request.json').read_text())
            if json_sha(request) != call['request_sha256']:
                raise ValueError('reference_proxy:recorded_request_changed')
            media = request['arguments'].get('video_source')
            if not media:
                continue
            record = Path(media).parent/'lineage.json'
            if not record.exists():
                continue
            lineage = json.loads(record.read_text())
            if lineage.get('spec') == expected:
                if lineage['sha256'] != request['media_sha256'] or sha256_file(media) != request['media_sha256']:
                    raise ValueError('reference_proxy:received_media_changed')
                return lineage
    return proxy(route.reference_source, start, end, route.cache, label=label)


def line_sheets(source, lines, cache):
    from PIL import Image, ImageDraw
    from .microclip_frames import _decode, _sha

    targets = sorted((line['start_s']+(line['end_s']-line['start_s'])*part/3, line['line_id'])
                     for line in lines for part in (1, 2))
    frames, cursor = [], 0
    for _, row, frame in _decode(source['path'], source['sha256']):
        if cursor == len(targets):
            break
        while cursor < len(targets) and row['source_time_s'] >= targets[cursor][0]:
            full = frame.to_image()
            full.thumbnail((640, 640), Image.Resampling.LANCZOS)
            frames.append((dict(row, line_id=targets[cursor][1], frame_id=f'f{cursor+1}'), full))
            cursor += 1
    if cursor != len(targets):
        raise ValueError('reference_dialogue:missing_reference_frames')
    pages = []
    for offset in range(0, len(frames), 8):
        page = frames[offset:offset+8]
        width = min(640, page[0][1].width)
        height = round(page[0][1].height*width/page[0][1].width)
        sheet = Image.new('RGB', (width*2, (height+28)*math.ceil(len(page)/2)), 'white')
        for i, (row, full) in enumerate(page):
            x, y = i % 2*width, i//2*(height+28)
            sheet.paste(full.resize((width, height), Image.Resampling.LANCZOS), (x, y+28))
            ImageDraw.Draw(sheet).text((x+8, y+6),
                f"frame {row['frame_id']} | line {row['line_id']} | source {row['source_time_s']:.3f}s", fill='black')
        rows = [row for row, _ in page]
        folder = Path(cache)/'reference_dialogue'/json_sha(rows)[:20]
        folder.mkdir(parents=True, exist_ok=True)
        path = folder/'lines.jpg'
        sheet.save(path, quality=95)
        ids = list(dict.fromkeys(row['line_id'] for row in rows))
        intervals = [line for line in lines if line['line_id'] in ids]
        write_json(folder/'lineage.json', dict(kind='sparse_contact_sheet', path=str(path),
            sha256=_sha(path), source_sha256=source['sha256'], frames=rows,
            source_start_s=min(line['start_s'] for line in intervals),
            source_end_s=max(line['end_s'] for line in intervals)))
        pages.append((path, ids))
    return pages


def transcribe(route):
    """Correct text before interpreting speakers; still frames do not identify voices."""
    result = deepcopy(route.lines)
    by_id = {row['line_id']: row for row in result}
    for number, (path, ids) in enumerate(line_sheets(route.reference_source, route.lines, route.cache), 1):
        frames = json.loads((Path(path).parent/'lineage.json').read_text())['frames']
        frame_ids = [f['frame_id'] for f in frames]
        def validate(value):
            rows = value.get('frames') if isinstance(value, dict) else None
            if not isinstance(rows, list) or [r.get('frame_id') for r in rows] != frame_ids:
                raise ValueError('reference_dialogue:逐帧保留本页frame_id及顺序；同一line的两帧分开记录')
            for row in rows:
                if not isinstance(row.get('subtitle_text'), str):
                    raise ValueError('reference_dialogue:填写subtitle_text（看不清则空串）')
            return value
        prompt = ('逐格读本页参考视频原帧。每张图标有唯一frame、来源line及原片时间，字幕未遮挡。'
            '先独立抄写实际可见字幕subtitle_text；不能把ASR复制成字幕证据，看不清或无字幕填空串。'
            '字幕横跨多个ASR行时，subtitle_text可重复抄整条字幕，文字与ASR的分句对齐在后续统一处理。'
            '同一line的两张帧字幕可能不同，必须按frame分别抄录，不能合并或去重。'
            '本步只抄原帧文字，不纠正或拼接ASR，不判断说话人或语义，不得改写剧情。'
            '一个字也不要省略，尤其是否定、反问及末尾接话的字。'
            '只返回JSON {"frames":[{"frame_id":"f1","subtitle_text":"看见的原字幕"}]}。\n'+
            json.dumps(dict(frames=[{k:f[k] for k in ('frame_id','line_id','source_time_s')} for f in frames]), ensure_ascii=False))
        reply = route._call(f'material_scene_transcript_frames_{number}', prompt, validate, path, image=True)
        for frame, row in zip(frames, validate(reply)['frames']):
            line = by_id[frame['line_id']]
            line.setdefault('subtitle_observations', []).append(dict(frame_id=frame['frame_id'],
                source_time_s=frame['source_time_s'], subtitle_text=row['subtitle_text']))
        for ident in ids:
            line = by_id[ident]
            texts = list(dict.fromkeys(r['subtitle_text'] for r in line['subtitle_observations']))
            line.update(asr_text=line.get('asr_text', line['text']), subtitle_text=texts[0] if len(texts)==1 else '')
    align_shared_subtitles(route, result)
    route.state.set_artifact('reference_transcript_verified', result)
    return result


def align_shared_subtitles(route, rows):
    """Align the whole transcript once, after independent frame text extraction."""
    if not rows:
        return
    ids = [row['line_id'] for row in rows]
    def validate(value):
        aligned = value.get('lines') if isinstance(value, dict) else None
        if (not isinstance(aligned, list) or [r.get('line_id') for r in aligned] != ids
                or any(not isinstance(r.get('text'), str) or not r['text'].strip() for r in aligned)):
            raise ValueError('reference_transcript_align:保留全部行号，每行填写实际对应的非空text')
        return value
    prompt = ('按完整时间顺序将ASR与逐帧抄录的字幕对齐，纠正每行错字、漏字。'
        'subtitle_observations按帧保留实际字幕及时间；同一ASR行可能采样到前后两人的不同字幕。'
        '不能把两张帧的字幕拼成一句；依据ASR实际文字和时间选择对应文字，无法确认则保留ASR并说明。'
        'asr_text给出本行实际发声内容及分句边界；subtitle_text只是该时刻显示的字幕，'
        '可能包括上一句、下一句或整段话，不能把其全文直接当成本行台词。'
        '同一条字幕横跨多行时，必须根据各行原ASR分工分配，避免重复。'
        '例如ASR分别是“放在柜台”和“明天来取”，字幕是“放在柜台，明天来取”，'
        '两行分别保留放置句和领取句，不能任一行再把另一行也说一遍。'
        '原ASR确实重复的发言必须保留；没有字幕的行保留ASR原文，不能猜造原片台词。'
        '不能把相邻行ASR拼到本行，不能改写剧情或新增反复强调；不增删或交换行号。'
        '只返回JSON {"lines":[{"line_id":1,"text":"本行实际原句"}],"reason":"分配依据"}。\n'+
        json.dumps(dict(lines=[{k:v for k,v in r.items() if k != 'source_words'} for r in rows]), ensure_ascii=False))
    aligned = validate(route._call('material_scene_transcript_align_v2_1', prompt, validate))
    for original, revised in zip(rows, aligned['lines']):
        original['text'] = revised['text']
    route.state.set_artifact('reference_transcript_alignment', dict(before_ids=ids, reply=aligned))


MINOR_ROLE = '其他人'
TURNS_STAGE = 'material_scene_turns_whole_1'


def read_turns(route):
    """Watch the whole reference once: key characters and who says each line (user 11-10).

    References are short (24 templates, 12-161 s). The earlier route read 20 s units and
    then re-judged them over the whole film; 03 (8876dabb) got two names for one person,
    and 03 (b3922c13) had the last unit overwrite the buyer's appearance with a different
    man, so the whole-film review dropped all 19 of her lines. The model decides how many
    roles the content needs (user 11-10: the number of people varies across thousands of
    references; a fixed cap of 3 let 03 keep a 4-line passer-by and 21 list 5 people).
    Anyone outside the roster speaks as MINOR_ROLE and is not imitated. Supporting people
    with the same function may be merged into one role (user 11-10: 21 listed four
    co-workers who each only get mistaken for the target and try to stop her)."""
    if not route.lines:
        # No usable reference speech (a silent subtitled edit, or an ASR alignment
        # that supplied no lines): there are no turns to attribute.
        route.state.set_artifact('reference_turns_verified', dict(characters={}, lines=[]))
        route.state.set_artifact('reference_non_speech', [])
        return []
    media = reference_proxy(route, 0, route.duration, 'reference_turns_full', TURNS_STAGE)
    expected = [row['line_id'] for row in route.lines]

    def validate(value):
        problems = []
        people = value.get('characters') if isinstance(value, dict) else None
        rows = value.get('lines') if isinstance(value, dict) else None
        if not isinstance(people, list) or not people or any(not isinstance(p, dict) or not all(
                isinstance(p.get(k), str) and p[k].strip() for k in ('role', 'appearance')) for p in people):
            problems.append('characters须为非空数组，每个关键人物给泛化role与全片可辨认appearance')
            roles = set()
        else:
            names = [p['role'].strip() for p in people]
            roles = set(names)
            repeated = sorted({r for r in names if names.count(r) > 1})
            if repeated:
                problems.append(f'同一role只能出现一次：{repeated}；同一人全片只用一个role')
            if MINOR_ROLE in roles:
                problems.append(f'characters只列关键人物，不要列"{MINOR_ROLE}"')
            for person in people:
                merged = person.get('merged_from')
                if merged is not None and (not isinstance(merged, list) or len(merged) < 2 or any(
                        not isinstance(m, str) or not m.strip() for m in merged)):
                    problems.append(f'{person["role"]}的merged_from须列出至少两个被合并人物的外观；未合并则省略该字段')
        if not isinstance(value, dict) or not isinstance(value.get('roster_reason'), str) or not value['roster_reason'].strip():
            problems.append('roster_reason须说明参考要表达的内容，以及为什么这几个角色就足够、其余人物去掉不影响内容')
        if not isinstance(value, dict) or not isinstance(value.get('relationships'), str) or not value['relationships'].strip():
            problems.append('relationships须写人物外观、相互关系及时间依据')
        if not isinstance(rows, list):
            rows = []
        got = [r.get('line_id') if isinstance(r, dict) else None for r in rows]
        if got != expected:
            missing = [i for i in expected if i not in got]
            extra = [i for i in got if i not in expected]
            problems.append('lines须逐行保留全部line_id及顺序' + (f'；缺少{missing}' if missing else '') +
                            (f'；多出{extra}' if extra else '') +
                            ('；顺序须与给定lines一致' if not missing and not extra else ''))
        allowed = roles | {MINOR_ROLE}
        for row in rows:
            if not isinstance(row, dict):
                continue
            ident, targets = row.get('line_id'), row.get('addressee_roles')
            if row.get('kind') not in ('speech', 'non_speech'):
                problems.append(f'第{ident}行kind须为speech或non_speech')
            elif row['kind'] == 'speech':
                if row.get('role') not in allowed:
                    problems.append(f'第{ident}行role={row.get("role")!r}须为{sorted(roles)}之一，名单外的人填"{MINOR_ROLE}"')
                if not isinstance(row.get('function'), str) or not row['function'].strip():
                    problems.append(f'第{ident}行须填function本句作用')
            if not isinstance(targets, list) or set(targets) - allowed:
                problems.append(f'第{ident}行addressee_roles须为数组且只含{sorted(allowed)}，无对象填[]')
            elif row.get('role') in targets and row.get('role') != MINOR_ROLE:
                problems.append(f'第{ident}行对话对象不能是说话者本人')
            if not isinstance(row.get('speaker_evidence'), str) or not row['speaker_evidence'].strip():
                problems.append(f'第{ident}行须填speaker_evidence可见依据')
            if type(row.get('on_screen')) is not bool:
                problems.append(f'第{ident}行on_screen须为true/false')
        if problems:
            raise ValueError('须同时修复以下各项：\n' + '\n'.join(problems))
        return value

    prompt = ('从头到尾看完整参考视频，一次确定关键人物和每句台词的实际说话者。视频无声，不声称听到声音。'
        '我们只需仿拍出参考的内容。先想清楚参考要表达什么（谁和谁、因为什么、结果如何），'
        '再判断最少需要几个角色就能把这个内容表达出来，需要几个就列几个，人数由内容决定；'
        '去掉后不影响内容的人（路人、围观者、只喊一声、附和、插一两句的配角）不列。'
        '作用相同的几个配角（如先后被误认、出面阻拦或附和的几个人）合并成一个角色：把他们的台词都归给这个role，'
        'appearance写其中一人的外观作为该角色外观，merged_from列出被合并的各人外观；主要角色不合并，'
        '合并后该角色的台词前后要说得通，对话对象不能是自己。roster_reason写这些判断（要表达的内容、留几个、合并了谁）。'
        '每人一个泛化role（顾客/店员/摊主等，不用素材电影人物名），appearance写全片主要出场时的衣服、发型、配饰等外观。'
        '同一个人全片只用一个role：换机位、转身、远近景、被不同称呼时仍是同一role；'
        '若同一角色在个别镜头里外观有变化，按其台词和剧情位置判断是否同一角色，不另立人物。'
        '再逐行判断说话者：依靠连续动嘴、人物外观、字幕与前后问答关系，不能把在场听者、称呼对象当成说话者；'
        '低头看物品仍可能在说话，另一人抬头、微笑或做手势不等于他在说话。'
        f'说话者是关键人物时填其role，不是关键人物（路人、围观者等）时role填"{MINOR_ROLE}"。'
        'addressee_roles填本句实际交谈对象的role（名单外的人填' + f'"{MINOR_ROLE}"' + '），旁白、对观众或无法确定时填[]。'
        'on_screen：实际露脸讲话填true，明确画外发言填false，不确定填true。'
        '每行kind填speech或non_speech：ASR有时把音乐或制作署名误识别成对白，明确不是实际发言时填non_speech、role=null并说明依据；'
        '不能仅因看不到动嘴就排除画外音、旁白或接话，无法确认非发言时保留speech。'
        '不改文字、不增删行，不用已有剧情常识替代本视频。relationships写人物外观、相互关系及时间依据。'
        '只返回JSON {"roster_reason":"参考要表达的内容，及为何这几个角色足够","characters":[{"role":"顾客","appearance":"实际外观"},'
        '{"role":"同事","appearance":"合并后采用的外观","merged_from":["被合并者1外观","被合并者2外观"]}],'
        '"relationships":"人物关系及时间证据",'
        '"lines":[{"line_id":1,"kind":"speech","role":"顾客","addressee_roles":["摊主"],"on_screen":true,'
        '"speaker_evidence":"时间及可见依据","function":"本句作用"}]}。\n' +
        json.dumps(dict(duration_s=route.duration, lines=[dict(line_id=r['line_id'], start_s=r['start_s'],
            end_s=r['end_s'], text=r['text']) for r in route.lines]), ensure_ascii=False))
    reply = validate(route._call(TURNS_STAGE, prompt, validate, media['path']))
    characters = {p['role'].strip(): p['appearance'].strip() for p in reply['characters']}
    route.state.set_artifact('reference_turns_roster', dict(status='fixed', characters=characters))
    route.state.set_artifact('reference_relationship_review', reply)
    result = []
    for original, row in zip(route.lines, reply['lines']):
        result.append(dict(original, source_asr_line_id=original['line_id'],
                           **{k: row.get(k) for k in ('kind', 'role', 'addressee_roles', 'on_screen',
                                                       'speaker_evidence', 'function')}))
    route.state.set_artifact('reference_turns_verified', dict(characters=characters, lines=result))
    route.state.set_artifact('reference_non_speech', [row for row in result if row['kind'] == 'non_speech'])
    minor = [row for row in result if row['kind'] == 'speech' and row['role'] == MINOR_ROLE]
    if minor:
        route.state.set_artifact('reference_minor_lines', dict(policy='key_roster_only_v1', lines=minor))
    kept = []
    for row in result:
        if row['kind'] != 'speech' or row['role'] == MINOR_ROLE:
            continue
        row = {k: v for k, v in row.items() if k not in ('source_words', 'subtitle_observations')}
        row['addressee_roles'] = [t for t in row['addressee_roles'] if t != MINOR_ROLE]
        kept.append(row)
    return kept
