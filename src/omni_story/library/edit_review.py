"""Review actual rendered cuts and constrain repairs to the reported cuts."""
import json
import subprocess

ISSUES = ('continuity', 'speaker', 'framing', 'timing', 'subtitle', 'missing_material')


def cut_spans(cuts, total, fps):
    starts = [round(c['at'] * fps) for c in cuts]
    return [dict(id=str(i + 1), scene=c['scene'], shot=c['shot'], start=start, end=end)
            for i, (c, start, end) in enumerate(zip(cuts, starts, starts[1:] + [total]))]


def audit_render(out, stage, picture, cuts, lines, total, fps, reference, ask, ff):
    """Present every rendered frame; cut labels bind observations to editable rows."""
    spans = cut_spans(cuts, total, fps)
    findings = {}
    page_size = 36 * fps
    for start in range(0, total, page_size):
        end = min(start + page_size, total)
        page = [s for s in spans if s['start'] < end and s['end'] > start]
        labels = []
        for row in page:
            a, b = (max(start, row['start']) - start) / fps, (min(end, row['end']) - start) / fps
            labels.append(f"drawtext=text={row['id']}:x=10:y=10:fontsize=24:fontcolor=white:"
                          f"box=1:boxcolor=black:enable='gte(t,{a:.6f})*lt(t,{b:.6f})'")
        media = out / f'{stage}_{start}.mp4'
        ff('-ss', start / fps, '-i', picture, '-t', (end - start) / fps, '-an',
           '-vf', ','.join(['scale=768:432', 'fps=20', *labels]), '-threads', 2,
           '-c:v', 'libx264', '-crf', 25, '-maxrate', '900k', '-bufsize', '1800k', media)
        intervals = {row['id']: row for row in page}

        def validate(reply):
            issues = reply.get('issues')
            if not isinstance(issues, list):
                raise ValueError('return issues, an empty list when no defect is seen')
            for item in issues:
                if not isinstance(item, dict) or str(item.get('cut')) not in intervals:
                    raise ValueError('report only the visible cut numbers in this attachment')
                if item.get('kind') not in ISSUES or not str(item.get('seen', '')).strip():
                    raise ValueError('each issue needs a valid kind and concrete visible evidence')
                item['cut'] = str(item['cut'])
            return reply

        dialogue = [{'role': l['role'], 'text': l['text'], 'start': round(l['start_s'] * fps),
                     'end': round(l['end_s'] * fps)} for l in lines
                    if round(l['start_s'] * fps) < end and round(l['end_s'] * fps) > start]
        data = dict(fps=fps, cuts=page, excerpt_start_frame=start, dialogue=dialogue, scene_intent=reference)
        report = ask(f'{stage}_{start}',
                     '你在审实际导出的静音视频，左上编号为剪辑表的刀号。对照每刀前后，'
                     '独立查同一行动中人物位置或方向突然断开、重复起身或脚步、跟拍特写把移动当静止、'
                     '切到说话者时构图实际突出别人、前后情绪动作错位，以及遮罩或源字幕的明显问题。'
                     '有意跟走、转场、听者反应、道具插镜只要符合场景，都可保留。'
                     '只报告确实看到的缺陷，给出具体可见证据，不凭台词声称看到物件。'
                     '素材缺证据时写missing_material，别要求编造画面。'
                     '返回 {"issues":[{"cut":"画面编号","kind":"'
                     + '/'.join(ISSUES) + '","seen":"直观看到的证据以及为何不合这场"}]}，'
                     '流畅时issues为空。数据的起止均为全片帧数。' + json.dumps(data, ensure_ascii=False),
                     validate, media)
        for item in report['issues']:
            key = (item['cut'], item['kind'])
            findings.setdefault(key, dict(item, interval=intervals[item['cut']], reviewed_from_frame=start))
    return list(findings.values())


def apply_cut_replacements(cuts, actionable, response):
    """A picture repair may replace the flagged source/crop; it cannot retime dialogue."""
    replacements = response.get('replacements', {})
    deferred = response.get('deferred', {})
    if (not isinstance(replacements, dict) or not isinstance(deferred, dict)
            or set(replacements) & set(deferred)
            or set(replacements) | set(deferred) != set(actionable)
            or any(not isinstance(reason, str) or not reason.strip() for reason in deferred.values())):
        raise ValueError('each flagged cut must be replaced or deferred once; no unrelated cuts')
    result = [dict(cut) for cut in cuts]
    fields = {'shot', 'offset', 'speed', 'size', 'center', 'center_y', 'tail', 'shows_speaker', 'reason'}
    for key, edit in replacements.items():
        if not isinstance(edit, dict) or set(edit) - fields or not edit.get('reason'):
            raise ValueError('repair fields are limited to picture and must give a reason')
        row = result[int(key) - 1]
        if edit.get('shot', row['shot']) != row['shot'] and 'offset' not in edit:
            raise ValueError('a different source shot needs its own source offset')
        row.update(edit)
    return result


def rendered_frames(path):
    result = subprocess.check_output(['ffmpeg', '-v', 'error', '-xerror', '-i', str(path),
                                      '-map', '0:v:0', '-an', '-f', 'framemd5', 'pipe:1'], text=True)
    return [line.rsplit(',', 1)[1].strip() for line in result.splitlines() if line and not line.startswith('#')]


def picture_steps(path):
    """Mean luma change at each adjacent frame, used only for claims about an abrupt edit."""
    pixels = 96 * 54
    data = subprocess.check_output(['ffmpeg', '-v', 'error', '-xerror', '-i', str(path),
                                    '-map', '0:v:0', '-an', '-vf', 'scale=96:54,format=gray',
                                    '-f', 'rawvideo', 'pipe:1'])
    return [0.0] + [round(sum(abs(a - b) for a, b in zip(data[i-pixels:i], data[i:i+pixels])) / pixels, 2)
                    for i in range(pixels, len(data), pixels)]


def verify_claim(claim, section, changed, steps_before, steps_after):
    """Bind an introduced/resolved defect to observed, changed frames of its actual cut."""
    claim = {key: claim[key] for key in ('verdict','reason','frame','position','evidence_type','before_seen','after_seen')
             if key in claim}
    verdict = claim.get('verdict')
    if verdict in ('preexisting', 'not_observed', 'uncertain'):
        return dict(claim)
    if verdict not in ('introduced', 'resolved'):
        raise ValueError('enter a causal or noncausal verdict')
    frame = claim.get('frame')
    position = claim.get('position')
    start, end = section['start'], section['end']
    if type(frame) is not int or position not in ('inside', 'start', 'end'):
        raise ValueError('provide a zero-based integer frame and its position in the cut')
    in_position = {'inside': start < frame < end, 'start': frame == start,
                   'end': frame == end and end < len(changed)}
    if not in_position[position]:
        return dict(claim, verdict='wrong_location', rejection='frame does not match the claimed cut position')
    if not (changed[frame] or (frame > 0 and changed[frame-1])):
        return dict(claim, verdict='unchanged', rejection='the cited transition has identical input frames in both renders')
    if claim.get('evidence_type') == 'hard_cut':
        before, after = steps_before[frame], steps_after[frame]
        if verdict == 'introduced' and after < 12:
            return dict(claim, verdict='not_observed', rejection='no abrupt transition at the cited frame', steps=[before, after])
        if verdict == 'introduced' and before >= 12 and after <= before + 4:
            return dict(claim, verdict='preexisting', rejection='the abrupt transition already occurs at this frame', steps=[before, after])
        if verdict == 'resolved' and (before < 12 or after >= before - 4):
            return dict(claim, verdict='uncertain', rejection='the original abrupt transition is not measurably reduced', steps=[before, after])
    return dict(claim)


def _paired_page(out, before, after, spans, start, end, fps, ff):
    labels = []
    for row in spans:
        a = (max(start, row['start']) - start) / fps
        b = (min(end, row['end']) - start) / fps
        labels.append(f"drawtext=text='CUT {row['id']}':x=12:y=12:fontsize=24:fontcolor=white:"
                      f"box=1:boxcolor=black:enable='gte(t,{a:.6f})*lt(t,{b:.6f})'")
    frame_number = f"drawtext=text='FRAME %{{eif\\:n+{start}\\:d}}':x=145:y=12:fontsize=24:fontcolor=white:box=1:boxcolor=black"
    size = f'trim=start_frame={start}:end_frame={end},setpts=PTS-STARTPTS,scale=512:288,setsar=1'
    header = ("drawtext=text=BEFORE:x=8:y=298:fontsize=20:fontcolor=white,"
              "drawtext=text=AFTER:x=520:y=298:fontsize=20:fontcolor=white")
    graph = ';'.join([f'[0:v]{size}[before]', f'[1:v]{size}[after]',
                      '[before][after]hstack,pad=1024:324:0:0,' + ','.join([header, frame_number, *labels]) + '[view]'])
    media = out / f'causal_picture_pair_{start}.mp4'
    ff('-i', before, '-i', after, '-an', '-filter_complex_threads', 1, '-filter_complex', graph,
       '-map', '[view]', '-threads', 2, '-c:v', 'libx264', '-crf', 25, '-maxrate', '1100k',
       '-bufsize', '2200k', media)
    return media


def adjudicate_repairs(out, before, after, before_cuts, after_cuts, original, candidate, total, fps, ask, ff):
    """Rejudge both passes against aligned renders, independently of their original report counts."""
    old_spans, spans = cut_spans(before_cuts, total, fps), cut_spans(after_cuts, total, fps)
    if [{k: v for k, v in row.items() if k != 'shot'} for row in old_spans] != [
            {k: v for k, v in row.items() if k != 'shot'} for row in spans]:
        raise ValueError('paired evidence requires the same cut clock and scene boundaries')
    by_id = {row['id']: row for row in spans}
    old_by_id = {row['id']: row for row in old_spans}
    first, second = rendered_frames(before), rendered_frames(after)
    if len(first) != total or len(second) != total:
        raise ValueError('paired evidence requires exactly the locked number of rendered frames')
    changed = [a != b for a, b in zip(first, second)]
    findings = {}
    pages = {}
    for side, issues in (('before', original), ('after', candidate)):
        for i, issue in enumerate(issues):
            key = f'{side}_{i+1}'
            section = by_id[issue['cut']]
            previous_span = old_by_id[issue['cut']]
            record = dict(key=key, side=side, finding=issue, interval=section, before_shot=previous_span['shot'])
            findings[key] = record
            start, end = section['start'], section['end']
            if issue['kind'] == 'missing_material':
                record.update(verdict='uncertain', reason='source material absence is not a visual regression')
            elif not any(changed[max(0, start-fps//2):min(total, end+fps//2)]):
                record.update(verdict='unchanged', reason='identical rendered frames at the cut and both neighboring half-seconds')
            else:
                examined = max(start, issue.get('reviewed_from_frame', start))
                pages.setdefault(examined//(fps*30), []).append(record)
    if pages:
        steps_before, steps_after = picture_steps(before), picture_steps(after)
        if len(steps_before) != total or len(steps_after) != total:
            raise ValueError('paired frame measurements disagree with the render clock')
        for page, reports in pages.items():
            start = max(0, page*30*fps-fps)
            end = min(total, (page+1)*30*fps+fps)
            section_spans = [s for s in spans if s['start'] < end and s['end'] > start]
            media = _paired_page(out, before, after, section_spans, start, end, fps, ff)
            expected = {r['key']: r for r in reports}
            for item in reports:
                sid = item['interval']
                first_interior = max(sid['start']+1,start)
                end_interior = min(sid['end'],end)
                item['luma_step_evidence'] = {
                    'strongest_inside_after': max(
                        ((steps_after[f], f) for f in range(first_interior,end_interior)), default=None),
                    'end_boundary_before_after': [steps_before[sid['end']],steps_after[sid['end']]]
                        if sid['end'] < total else None}

            def validate(reply):
                rows = reply.get('reviews')
                if not isinstance(rows, dict) or set(rows) != set(expected):
                    raise ValueError('return exactly one review under every supplied report key')
                for key, row in rows.items():
                    item = expected[key]
                    allowable = ('preexisting', 'not_observed', 'uncertain',
                                 'resolved' if item['side']=='before' else 'introduced')
                    if not isinstance(row, dict) or row.get('verdict') not in allowable or not str(row.get('reason', '')).strip():
                        raise ValueError('use the allowed verdict for this earlier or later report and give a reason')
                    if row['verdict'] in ('introduced', 'resolved'):
                        if (row.get('evidence_type') not in ('hard_cut', 'visible_content')
                                or type(row.get('frame')) is not int or not start <= row['frame'] < end
                                or not all(isinstance(row.get(f), str) and row[f].strip() for f in ('before_seen','after_seen'))):
                            raise ValueError('a causal report needs the visible frame number and actual evidence from both images')
                        verify_claim(row,item['interval'],changed,steps_before,steps_after)
                return reply

            reply = ask(f'causal_picture_comparison_{start}',
                        '同步对看同一帧：左改剪前，右改剪后。左上显示成片帧号和刀号；时间以标出的绝对零基帧为准。'
                        '逐条验证这些已有报告。新增只有在右侧能亲眼看到同一问题、左侧同一帧及切点前后没有、'
                        '且真正发生在该刀的位置时才选introduced。原版已经有同样的切换或问题选preexisting；'
                        '说错刀内和刀口、没有看到描述的动作，选not_observed；无法证实选uncertain。'
                        '对于改剪前原有问题，只有左侧能看到问题并且确认右侧解决了才选resolved。'
                        '禁止用镜头号、台词或问题列表代替图像。突变或意外剪切用hard_cut，其余物件和表演误接用visible_content。'
                        '刀口查前一帧和后一帧；刀尾第一帧等于下一刀开头。亮度变化量仅用于核对是否真有画面突变及位置。'
                        '返回 {"reviews":{"报告键":{"verdict":"introduced/resolved/preexisting/not_observed/uncertain",'
                        '"frame":实际成片帧号,"position":"inside/start/end","evidence_type":"hard_cut/visible_content",'
                        '"before_seen":"左画面","after_seen":"右画面","reason":"结论依据"}}}。'
                        '非因果结论只需verdict与reason。原报告及实际刀号区间：'
                        +json.dumps(reports,ensure_ascii=False),validate,media)
            for key, claim in reply['reviews'].items():
                item = expected[key]
                item.update(verify_claim(claim,item['interval'],changed,steps_before,steps_after))
    checks = list(findings.values())
    blockers = [r for r in checks if r['verdict'] == 'introduced']
    resolved = [r for r in checks if r['verdict'] == 'resolved']
    accepted = bool(resolved) and not blockers
    return dict(checks=checks, new_defects=blockers, resolved=resolved, accepted=accepted,
                changed_frames=sum(changed),
                remaining=[r['finding'] for r in checks if r['side']=='before' and (not accepted or r['verdict']!='resolved')])
