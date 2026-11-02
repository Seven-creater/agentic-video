"""Inspect the full final canvas; verify measurable claims on the final bytes."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess

from .contracts import normalize_intervals, interval_bounds, finite_number, numeric_error
from .media import probe_media, probe_luma_window, sha256_file
from .state import LibraryStopped, write_json
from .visual_story_trial import proxy

CHECKS = ('framing_ok', 'subtitle_ok', 'no_credits', 'no_distortion', 'edit_coherence_ok')
ISSUE_KINDS = ('black_frame', 'subtitle_overlap', 'other')


def _subtitle_overlap_measurement(video, digest, window, source_subtitles):
    """Only a SHA-bound burn receipt plus recorded source policy can disprove overlap."""
    measurement = dict(method='recorded_subtitle_cues', window_s=window, outcome='unverified')
    receipt_path = video.with_suffix(video.suffix + '.receipt.json')
    if not receipt_path.is_file():
        measurement['reason'] = 'subtitle_burn_receipt_missing'
        return measurement
    record = json.loads(receipt_path.read_text(encoding='utf-8'))
    if (not isinstance(record, dict) or record.get('operation') != 'final_subtitles'
            or record.get('sha256') != digest):
        measurement['reason'] = 'subtitle_burn_receipt_not_bound_to_final_video'
        return measurement
    # Use the actual ASS event times (centisecond rounding), not speech boundaries
    # or SRT's millisecond times, which can differ from the burned cue list.
    ass = Path(record['subtitle_ass_path'])
    if sha256_file(ass) != record['subtitle_ass_sha256']:
        measurement['reason'] = 'burned_cue_file_changed'
        return measurement
    def seconds(stamp):
        hours, minutes, seconds = stamp.split(':')
        return int(hours)*3600 + int(minutes)*60 + float(seconds)
    lines = ass.read_text(encoding='utf-8-sig').splitlines()
    cues = []
    for index, line in enumerate(lines):
        if line.startswith('Dialogue:'):
            fields = line.split(',', 9)
            if len(fields) != 10:
                raise ValueError('invalid_burned_subtitle_cue')
            start, end = seconds(fields[1]), seconds(fields[2])
            if not all(finite_number(v) for v in (start, end)) or start >= end:
                raise ValueError('invalid_burned_subtitle_cue')
            if start < window[1] and end > window[0]:
                cues.append(dict(line=index+1, start_s=start, end_s=end))
    if not cues and not any(line.startswith('Dialogue:') for line in lines):
        raise ValueError('burned_subtitle_cues_required')
    overlapping = [[a['line'], b['line']] for i, a in enumerate(cues) for b in cues[i+1:]
                   if max(a['start_s'], b['start_s'], window[0]) <
                      min(a['end_s'], b['end_s'], window[1])]
    measurement.update(cues=cues, overlapping_pairs=overlapping,
        evidence_sha256={str(receipt_path): sha256_file(receipt_path), str(ass): sha256_file(ass)})
    # The policy must come from the final performed story, never the reviewer.
    # Without this record, non-overlapping new cues cannot rule out old subtitles.
    measurement['source_subtitles'] = source_subtitles
    if (not isinstance(source_subtitles, dict) or not source_subtitles.get('story_context_sha256')
            or source_subtitles.get('policy') not in {'silent_subtitle_masked', 'absent'}):
        measurement['reason'] = 'source_subtitles_not_recorded_masked_or_absent'
    else:
        measurement['outcome'] = 'retained' if overlapping else 'refuted'
    return measurement


def _verify_measured_issues(result, video, digest, source_subtitles):
    result = deepcopy(result)
    result.pop('refuted_issues', None)
    kept, refuted = [], []
    for issue in result['issues']:
        issue.pop('measurement', None)
        if issue.get('kind', 'other') == 'other':
            kept.append(issue)
            continue
        window = [issue['start_s'], issue['end_s']]
        try:
            if issue.get('kind', 'other') == 'black_frame':
                measurement = probe_luma_window(video, *window)
                measurement['outcome'] = 'retained' if measurement['black_frame_count'] else 'refuted'
            else:
                measurement = _subtitle_overlap_measurement(video, digest, window, source_subtitles)
        except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError) as error:
            measurement = dict(window_s=window, outcome='unverified', reason=str(error))
        issue['measurement'] = measurement
        (refuted if measurement['outcome'] == 'refuted' else kept).append(issue)
    if refuted:
        result['refuted_issues'] = refuted
        for check in CHECKS:
            if (not result[check] and any(row.get('check', check) == check for row in refuted)
                    and not any(row.get('check', check) == check for row in kept)):
                result[check] = True
    result['issues'] = kept
    result['status'] = 'pass' if all(result[k] for k in CHECKS) and not kept else 'needs_revision'
    return result


def review_final_layout(video, folder, mcp, *, normalize=False, source_subtitles=None):
    video, folder = Path(video).resolve(strict=True), Path(folder)
    digest = sha256_file(video)
    folder = folder/digest[:16]
    folder.mkdir(parents=True, exist_ok=True)
    duration = probe_media(video)['duration_s']
    media = proxy(dict(path=str(video), sha256=digest, video_stream_index=0,
                       duration_s=duration), 0, duration, folder, label='final_full_canvas')
    if media['spec']['roi'] is not None or media['source_sha256'] != digest:
        raise LibraryStopped('final_visual_review_must_preserve_full_canvas')
    prompt = (
        '直接查看这份最终交付视频的完整画布和已经烧录的字幕。没有提供创作计划，'
        '不要依据预期替画面补事实。只检查实际输出的视觉质量：'
        '主体是否异常缩小、偏置或套了多层黑边；正常电影上下窄黑边可以接受。'
        '是否有拉伸变形、黑色遮块切掉内容；新旧字幕是否重叠、残留无关字幕、截断或难读；'
        '是否混入演职员名单、片头片尾或THE END卡；镜头转换是否明显乱序、重复或失去连贯性。'
        '必须报告实际看到的异常及输出时间，不因故事大致可懂就判画面合格。'
        '返回一个JSON：{"status":"pass或needs_revision",'
        '"framing_ok":true,"subtitle_ok":true,"no_credits":true,"no_distortion":true,'
        '"edit_coherence_ok":true,"issues":[{"start_s":0,"end_s":1,"kind":"other",'
        '"check":"framing_ok","description":"实际问题"}],'
        '"visual_notes":["实际构图、字幕及结尾的具体观察"],"limitations":[]}。'
        'kind必填：黑屏或黑闪用black_frame，字幕重叠、重复或新旧叠字用subtitle_overlap，其余other；'
        'check必填且为上述五项之一，表示该问题使哪项为false。每个false须有对应问题和时间，'
        '同一问题影响多项时分别列出。只有五项全true且issues为空时status才为pass。')

    def validate(value, **context):
        if (not isinstance(value, dict) or value.get('status') not in {'pass', 'needs_revision'} or
                any(type(value.get(k)) is not bool for k in CHECKS) or
                not isinstance(value.get('issues'), list)):
            raise ValueError('final_visual_review_invalid_contract')
        if 'normalization' in value and not isinstance(value['normalization'], list):
            raise ValueError('final_visual_review_invalid_contract')
        # Require evidence per false check only for replies carrying kind, or an
        # originally empty issues list answering the new prompt; legacy checks stay unchanged.
        new_contract = (any(isinstance(row, dict) and 'kind' in row for row in value['issues'])
                        or (not value['issues'] and context.get('new_prompt', False)))
        # Check present enum values before normalization can drop an invalid interval.
        for index, row in enumerate(value['issues']):
            if (not isinstance(row, dict) or
                    ('kind' in row and (not isinstance(row['kind'], str) or row['kind'] not in ISSUE_KINDS)) or
                    ('check' in row and (not isinstance(row['check'], str) or row['check'] not in CHECKS))):
                raise numeric_error('final_visual_review_invalid_contract', f'$.issues[{index}]', row,
                    dict(kind=list(ISSUE_KINDS), check=list(CHECKS),
                         instruction='kind和check若提供须符合枚举；check是该问题使之为false的检查项'))
            start, end = row.get('start_s'), row.get('end_s')
            # Widen point times for any reply to the new prompt, even one omitting kind.
            if (('kind' in row or context.get('new_prompt', False)) and
                    finite_number(start) and finite_number(end) and start == end):
                if not 0 <= start <= duration:
                    raise ValueError('final_visual_review_issue_evidence_required')
                widened = [max(0, start-.5), min(duration, end+.5)]
                value.setdefault('normalization', []).append(dict(path=f'$.issues[{index}]',
                    original=deepcopy(row), normalized=dict(row, start_s=widened[0], end_s=widened[1]),
                    reason='point_time_widened', half_window_s=.5))
                row['start_s'], row['end_s'] = widened
        if normalize:
            normalize_intervals(value, value, 'issues', duration)
        for index, row in enumerate(value['issues']):
            if (not finite_number(row.get('start_s')) or not finite_number(row.get('end_s')) or
                    not 0 <= row['start_s'] <= row['end_s'] <= duration + .075 or
                    not isinstance(row.get('description'), str) or not row['description'].strip()):
                if isinstance(row, dict):
                    interval_bounds(row.get('start_s'), row.get('end_s'), 0, duration,
                        f'issues[{index}]', 'final_visual_review_issue_evidence_required')
                raise ValueError('final_visual_review_issue_evidence_required')
        for name in ('visual_notes', 'limitations'):
            if (not isinstance(value.get(name), list) or
                    any(not isinstance(x, str) or not x.strip() for x in value[name])):
                raise ValueError('final_visual_review_notes_required')
        if not value['visual_notes']:
            raise ValueError('final_visual_review_actual_observations_required')
        if (new_contract and (
                any(not value[k] and not any(row.get('check', k) == k for row in value['issues']) for k in CHECKS)
                or any(value[row['check']] for row in value['issues'] if 'check' in row)) or
                (value['status'] == 'pass') != (all(value[k] for k in CHECKS) and not value['issues'])):
            raise ValueError('final_visual_review_inconsistent_verdict')

    stage = 'final_layout_review_' + digest[:16]
    # Same-task retries retain their paid prompt; --resume-from uses a new
    # ledger, and final reviews are not reusable parent observation stages.
    mcp.state._reload()
    prior = next((c for c in mcp.state.data['calls'] if c['name'] == stage and not c.get('repair_of')), None)
    actual_prompt = (json.loads((mcp.state.output/'calls'/prior['id']/'request.json').read_text(encoding='utf-8'))
                     ['arguments']['prompt'] if prior else prompt)
    new_prompt = 'kind必填' in actual_prompt
    contract_defaults = []
    def validate_current(value):
        defaults = []
        if isinstance(value, dict) and isinstance(value.get('issues'), list):
            for index, row in enumerate(value['issues']):
                if isinstance(row, dict):
                    if 'kind' not in row:
                        defaults.append(dict(path=f'$.issues[{index}].kind', default='other'))
                    if 'check' not in row:
                        defaults.append(dict(path=f'$.issues[{index}].check',
                            default=[k for k in CHECKS if value.get(k) is False]))
        validate(value, new_prompt=new_prompt)
        if not contract_defaults:
            contract_defaults.extend(defaults)
    result = mcp.call(stage, prompt, media['path'], validate_current,
        scope={k: media[k] for k in ('kind', 'source_sha256', 'source_start_s', 'source_end_s')})
    result = _verify_measured_issues(result, video, digest, source_subtitles)
    if sha256_file(video) != digest:
        raise LibraryStopped('final_visual_review_source_changed')
    mcp.state._reload()
    calls = [c for c in mcp.state.data['calls'] if c['name'] in {stage, stage+'_repair', stage+'_repair2'}]
    if not calls or any(c['status'] != 'received' for c in calls):
        raise LibraryStopped('final_visual_review_received_call_required')
    lineage = Path(media['path']).parent/'lineage.json'
    evidence = {str(lineage): sha256_file(lineage)}
    for call in calls:
        for name in ('request.json', 'response.json'):
            path = mcp.state.output/'calls'/call['id']/name
            evidence[str(path)] = sha256_file(path)
    for issue in [*result['issues'], *result.get('refuted_issues', [])]:
        evidence.update(issue.get('measurement', {}).get('evidence_sha256', {}))
    receipt = dict(result, contract_defaults=contract_defaults, final_video=str(video), final_sha256=digest,
                   reviewed_proxy=media['path'], reviewed_proxy_sha256=media['sha256'],
                   full_canvas_preserved=True, spatial_crop=None, review_stage=stage,
                   calls=calls, evidence_sha256=evidence, receipt_path=str(folder/'review.json'))
    write_json(folder/'review.json', receipt)
    return receipt
