"""Inspect the full final canvas after subtitles; no spatial crop is permitted."""
from pathlib import Path

from .contracts import normalize_intervals, interval_bounds
from .media import probe_media, sha256_file
from .state import LibraryStopped, write_json
from .visual_story_trial import proxy

CHECKS = ('framing_ok', 'subtitle_ok', 'no_credits', 'no_distortion', 'edit_coherence_ok')


def review_final_layout(video, folder, mcp, *, normalize=False):
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
        '"edit_coherence_ok":true,"issues":[{"start_s":0,"end_s":1,"description":"实际问题"}],'
        '"visual_notes":["实际构图、字幕及结尾的具体观察"],"limitations":[]}。'
        '只有全部五项为true且issues为空时status才可为pass。无法判断的项目设false并说明。')

    def validate(value):
        if (not isinstance(value, dict) or value.get('status') not in {'pass', 'needs_revision'} or
                any(type(value.get(k)) is not bool for k in CHECKS) or
                not isinstance(value.get('issues'), list)):
            raise ValueError('final_visual_review_invalid_contract')
        if normalize:
            normalize_intervals(value, value, 'issues', duration)
        for index, row in enumerate(value['issues']):
            if (not isinstance(row, dict) or type(row.get('start_s')) not in {int, float} or
                    type(row.get('end_s')) not in {int, float} or
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
        if (value['status'] == 'pass') != (all(value[k] for k in CHECKS) and not value['issues']):
            raise ValueError('final_visual_review_inconsistent_verdict')

    stage = 'final_layout_review_' + digest[:16]
    result = mcp.call(stage, prompt, media['path'], validate,
                      scope={k:media[k] for k in ('kind', 'source_sha256', 'source_start_s', 'source_end_s')})
    if sha256_file(video) != digest:
        raise LibraryStopped('final_visual_review_source_changed')
    mcp.state._reload()
    calls = [c for c in mcp.state.data['calls'] if c['name'] in {stage, stage+'_repair'}]
    if not calls or any(c['status'] != 'received' for c in calls):
        raise LibraryStopped('final_visual_review_received_call_required')
    lineage = Path(media['path']).parent/'lineage.json'
    evidence = {str(lineage): sha256_file(lineage)}
    for call in calls:
        for name in ('request.json', 'response.json'):
            path = mcp.state.output/'calls'/call['id']/name
            evidence[str(path)] = sha256_file(path)
    receipt = dict(result, final_video=str(video), final_sha256=digest,
                   reviewed_proxy=media['path'], reviewed_proxy_sha256=media['sha256'],
                   full_canvas_preserved=True, spatial_crop=None, review_stage=stage,
                   calls=calls, evidence_sha256=evidence, receipt_path=str(folder/'review.json'))
    write_json(folder/'review.json', receipt)
    return receipt
