"""Core-meaning conditions over original, frozen production reference returns."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time

from . import reference_protocol as p, reference_exam as exam
from .production_subject import verify_subject, verify_tree
from ..library.media import sha256_file
from ..library.state import json_sha, write_json, file_lock

PREFIX_CONTEXT=dict(input_scope='actual_reference_prefix_only',
    instruction='当前媒体只是视频开头，后续画面和声音未提供。根据目前线索推测整片大致主旨、态度或核心价值；'
                '将推测与已见事实分开，信息不足时保留多种可能。不要声称已经看到了后续。')


def prefix_task(case,manifest,folder,receipts):
    from .reference_cli import subject
    labels=case['labels']
    if 'forecast_preparation_error' in labels:
        return dict(status='blocked_question',reason=labels['forecast_preparation_error'])
    full_status=folder/'subject-status.json'
    if full_status.exists() and p.read(full_status)['status']=='blocked_unknown':
        raise RuntimeError('core_prefix_original_source_unknown_no_replay')
    registry=Path(os.environ['HUNTER_RECEIPTS_DIR'])/'core_prefix_registry.json'
    with file_lock(registry.with_suffix('.lock'),wait=True):
        records=p.read(registry) if registry.exists() else {}
        for entry in records.values():
            if entry['source_sha256']!=case['reference']['sha256']:continue
            ledger=Path(entry['output'])/'production_stage/library_state.json'
            if (ledger.exists() and any(c['status'] in {'submitted','uncertain'} for c in p.read(ledger)['calls'])) or (
                    not ledger.exists() and entry['output']!=str(folder/'prefix-condition/subject')):
                raise RuntimeError('core_prefix_source_unknown_no_replay')
        canonical=manifest['production'].get('subject_outputs',{}).get(case['id']+'_prefix',str(folder/'prefix-condition/subject'))
        canonical_ledger=Path(canonical)/'production_stage/library_state.json'
        if canonical_ledger.exists() and any(c['status'] in {'submitted','uncertain'} for c in p.read(canonical_ledger)['calls']):
            raise RuntimeError('core_prefix_source_unknown_no_replay')
        if manifest['production'].get('reuse_only') and not (Path(canonical)/'subject.json').exists():
            return dict(status='blocked_subject',reason='No saved original prefix result for reuse-only regrading; no request sent.')
        records[str(folder)]=dict(source_sha256=case['reference']['sha256'],output=canonical)
        write_json(registry,records)
    prefix_folder=Path(manifest['production'].get('prefix_media_outputs',{}).get(case['id'],str(folder/'prefix-media')))
    media=p.prefix(case['reference'],labels['prefix_cut_s'],prefix_folder)
    asset=dict(case['reference'],path=str(media),sha256=sha256_file(media),
               asset_id=case['reference']['asset_id']+'_prefix',duration_s=labels['prefix_cut_s'])
    runtime=dict(manifest['production'],configuration=dict(manifest['production']['configuration'],
        reference_context=PREFIX_CONTEXT))
    prefix_case=dict(case,id=case['id']+'_prefix',reference=asset)
    collected=subject(prefix_case,dict(manifest,production=runtime),folder/'prefix-condition')
    if collected['manual_correction']:raise ValueError('core_prefix_manual_correction_not_allowed')
    reading=collected['understanding']
    raw,receipt=exam.paid_review([case['reference']],{'reference':case['reference']['asset_id']},
        exam.PREFIX_CORE_PROMPT,dict(candidate_units=exam.candidate_units(reading),
            gold_rubric=[dict(id=f'g{i:03d}',text=text) for i,text in enumerate(labels['elements'])],
            visible_prefix_s=labels['prefix_cut_s'],prefix_information=labels['prefix_information'],
            acceptable_prefix_themes=labels['prefix_acceptable_themes']),receipts/'prefix_core_v3')
    judgment=exam.compile_element_ids(raw,reading,len(labels['elements']),require_inference=True)
    metrics=exam.validate_elements(judgment,case,reading)
    metrics.update(prefix_information=labels['prefix_information'],inference_validity=judgment.get('inference_validity'))
    lineage=prefix_folder/'prefix.json'
    return dict(status='scored_provisional',subject_path=collected['evidence_path'],
        subject_sha256=collected['understanding_sha256'],subject_condition='reference_prefix_only',
        prefix_path=str(media),prefix_sha256=asset['sha256'],prefix_lineage_path=str(lineage),
        prefix_lineage_sha256=sha256_file(lineage),metrics=metrics,judgment=judgment,receipt=receipt)


def _full_subject(case,manifest,folder):
    path=folder/'subject-status.json'
    if not path.exists() or p.read(path)['status']!='collected':return None
    source=Path(p.read(path)['original_output'])/'subject.json'
    saved=p.read(source);verify_subject(saved)
    if saved['manual_correction'] or saved['binding']['reference_sha256']!=case['reference']['sha256']:
        raise ValueError('core_gallery_original_subject_required')
    return dict(case_id=case['id'],asset=case['reference'],subject_path=str(source),
        understanding=saved['understanding'],understanding_sha256=saved['understanding_sha256'],
        production_version=json_sha(manifest['production']['production_files']))


def music_metrics(result,case_id,original_score=None):
    ids=[entry['case_id'] for entry in result.get('candidates',[])]
    return dict(music_template_hit=float(case_id in ids),unique_template_hit=float(ids==[case_id]),
        candidate_count=len(ids),shared_music=len(ids)>1,
        matched_original_core_full_match_rate=original_score if ids==[case_id] else None)


def music_tasks(manifest,output):
    from .reference_cli import failure,code_identity
    gallery=[]
    for case in manifest['cases']:
        entry=_full_subject(case,manifest,output/'cases'/case['id'])
        if entry:gallery.append(entry)
    candidate=manifest['production'].get('music_candidate')
    for case in manifest['cases']:
        folder=output/'cases'/case['id'];path=folder/'bgm.json'
        if path.exists():continue
        row=dict(case_id=case['id'],task='bgm',protocol_sha256=manifest['protocol_sha256'],
            code_sha256=code_identity(),label_source='model_assisted_unconfirmed')
        started=time.time();question=case['labels'].get('bgm_question')
        if not question:
            question=dict(start_s=0,end_s=min(8,case['query']['duration_s']),transform='stereo_difference',
                provenance='fixed_first_eight_seconds_stereo_difference_not_verified_music_only')
        own=next((e for e in gallery if e['case_id']==case['id']),None)
        try:
            if 'preparation_error' in case['labels']:
                row.update(status='blocked_question',reason=case['labels']['preparation_error'])
            elif not candidate:
                row.update(status='capability_missing',reason='Pinned production music candidate not configured.')
            elif not own:
                row.update(status='blocked_subject',reason='Original full reference understanding unavailable; no invented music meaning.')
            elif case['labels'].get('same_mechanism') is not True:
                row.update(status='blocked_question',reason='Query core meaning was not verified as consistent with reference.')
            elif question['end_s']-question['start_s']<2:
                row.update(status='ineligible_audio',reason='No verified candidate music-only window of at least two seconds.',
                    preparation_note=case['labels'].get('bgm_preparation_error'))
            else:
                verify_tree(Path(candidate['root'])/'src',candidate['files'])
                music_output=Path(manifest['production'].get('music_outputs',{}).get(case['id'],str(folder/'music-subject')))
                job=dict(candidate=candidate,gallery=gallery,query=case['query'],window=question,
                    output=str(music_output),receipts=str(Path(manifest['receipts'])/'music'))
                write_json(folder/'music-run.json',job)
                completed=subprocess.run([sys.executable,str(Path(__file__).with_name('reference_music_subject.py')),
                    '--run',str(folder/'music-run.json')],capture_output=True,text=True,timeout=900)
                if completed.returncode:
                    detail=completed.stderr.strip().splitlines()[-1]
                    if any(s in detail for s in ('below_signal_floor','requires_two_channels')):
                        row.update(status='ineligible_audio',reason=detail)
                        row['elapsed_s']=time.time()-started;write_json(path,row);continue
                    raise RuntimeError('core_music_subject_failed:'+detail)
                answer_path=music_output/'answer.json';answer=p.read(answer_path)
                if answer['status']=='ineligible_query_not_music_only':
                    row.update(status='ineligible_audio',reason='Actual cropped query contains speech or no audible music.',answer=answer)
                else:
                    meaning=p.read(folder/'elements.json')
                    original_score=meaning.get('metrics',{}).get('full_match_rate')
                    row.update(status='scored_provisional',metrics=music_metrics(answer,case['id'],original_score),answer=answer,
                        answer_path=str(answer_path),answer_sha256=sha256_file(answer_path),
                        subject_path=own['subject_path'],subject_sha256=own['understanding_sha256'],
                        original_meaning_score_path=str(folder/'elements.json'),gallery_size=len(gallery))
        except Exception as error:row.update(failure(error))
        row['elapsed_s']=time.time()-started;write_json(path,row)


def verify_row(row,case,manifest,folder):
    subject=p.read(row['subject_path']);verify_subject(subject)
    if (subject['understanding_sha256']!=row['subject_sha256'] or
            row['protocol_sha256']!=manifest['protocol_sha256']):raise ValueError('core_report_subject_changed')
    if row['task']=='bgm':
        answer=p.read(row['answer_path'])
        job=p.read(folder/'music-run.json')
        comparison=answer['comparison'];state_path=Path(comparison['receipt_path'])
        if (sha256_file(row['answer_path'])!=row['answer_sha256'] or answer!=row['answer'] or
                sha256_file(state_path)!=comparison['receipt_sha256'] or
                sha256_file(state_path.with_name('response.json'))!=comparison['response_sha256'] or
                p.read(state_path)['status']!='received' or
                subject['binding']['reference_sha256']!=case['reference']['sha256']):
            raise ValueError('core_music_receipt_changed')
        raw=json.loads(json.loads(p.read(state_path.with_name('response.json'))['body_text'])['choices'][0]['message']['content'])
        gallery_ids=[e['case_id'] for e in job['gallery']]
        query_id=f'm{len(gallery_ids):03d}'
        expected=[gallery_ids[int(i[1:])] for i in raw['query_matches']]
        query_row=next(s for s in raw['samples'] if s['sample_id']==query_id)
        if (sha256_file(folder/'music-run.json')!=answer['run_sha256'] or raw!=comparison['observations'] or
                answer['gallery_case_ids']!=gallery_ids or
                [e['case_id'] for e in answer['candidates']]!=expected or
                query_row['spoken_speech_present'] is not False or query_row['music_audible'] is not True):
            raise ValueError('core_music_answer_not_received_music_only_response')
        for entry in answer['candidates']:
            original=p.read(entry['subject_path']);verify_subject(original)
            if original['understanding']!=entry['original_understanding']:
                raise ValueError('core_music_did_not_retrieve_original_understanding')
        meaning=p.read(folder/'elements.json')
        metrics=music_metrics(answer,case['id'],meaning.get('metrics',{}).get('full_match_rate'))
    else:
        lineage=p.read(row['prefix_lineage_path'])
        if (sha256_file(row['prefix_lineage_path'])!=row['prefix_lineage_sha256'] or
                lineage['binding']['source_sha256']!=case['reference']['sha256'] or
                lineage['binding']['end_s']!=case['labels']['prefix_cut_s'] or
                sha256_file(row['prefix_path'])!=row['prefix_sha256'] or
                subject['binding']['reference_sha256']!=row['prefix_sha256'] or
                subject['binding']['configuration'].get('reference_context')!=PREFIX_CONTEXT):
            raise ValueError('core_prefix_lineage_or_input_scope_changed')
        receipt=row['receipt'];state_path=Path(receipt['path'])
        if (sha256_file(state_path)!=receipt['state_sha256'] or p.read(state_path)['status']!='received' or
                sha256_file(state_path.with_name('response.json'))!=receipt['response_sha256']):
            raise ValueError('core_prefix_judge_receipt_changed')
        raw=json.loads(json.loads(p.read(state_path.with_name('response.json'))['body_text'])['choices'][0]['message']['content'])
        compiled=exam.compile_element_ids(raw,subject['understanding'],len(case['labels']['elements']),require_inference=True)
        if compiled!=row['judgment']:raise ValueError('core_prefix_judgment_changed')
        metrics=exam.validate_elements(compiled,case,subject['understanding'])
        metrics.update(prefix_information=case['labels']['prefix_information'],inference_validity=compiled.get('inference_validity'))
    if metrics!=row['metrics']:raise ValueError('core_report_metrics_changed')


def report_text(manifest,result):
    names=dict(elements='完整主旨',forecast='前缀推测主旨',story='长故事主旨匹配',bgm='BGM关联')
    groups=result['groups'];rows=result['rows']
    def percent(value):return f'{value:.1%}' if type(value) in (int,float) else '—'
    text=['# 参考视频核心主旨评测记录','',
        '10个模板、每个4类，共40个计划项。结果与不适用原因全部保留；本轮标签未经用户人工确认，分数为模型辅助评测。','',
        '| 题型 | 有效／计划 | 有效项成绩 |','|---|---:|---|']
    for task,g in groups.items():
        m=g['means_scored']
        score=(f"首选 {percent(m.get('hit_at_1'))}；范围 {percent(m.get('exact_range'))}" if task=='story' else
            f"关联 {percent(m.get('music_template_hit'))}；唯一 {percent(m.get('unique_template_hit'))}" if task=='bgm' else
            f"主旨完整命中 {percent(m.get('full_match_rate'))}")
        text.append(f"| {names[task]} | {g['scored']}/10 | {score} |")
    text+=['','主旨完整命中率＝明确表达的有效要素数／核验有效的主旨要素数；部分、遗漏、矛盾另计。前缀只看实际裁切后的开头，通过原模块独立处理，不接收整片理解。故事按所选段落ID核验首选及范围。音乐只在实际无对白且有音乐的查询上计分；共曲返回多解，原GLM主旨分数单独报告。','',
        '| 模板 | 完整主旨 | 前缀主旨 | 长故事首选／范围 | BGM关联／唯一 |',
        '|---|---|---|---|---|']
    for case in manifest['cases']:
        values=[]
        for task in p.TASKS:
            row=next(r for r in rows if r['case_id']==case['id'] and r['task']==task)
            m=row.get('metrics',{})
            values.append((f"{percent(m.get('hit_at_1'))}／{percent(m.get('exact_range'))}" if task=='story' else
                f"{percent(m.get('music_template_hit'))}／{percent(m.get('unique_template_hit'))}" if task=='bgm' else
                percent(m.get('full_match_rate'))) if row['status']=='scored_provisional' else row['status'])
        text.append('| '+case['name']+' | '+' | '.join(values)+' |')
    text+=['','未得到有效分数的项目：','']
    for row in rows:
        if row['status']!='scored_provisional':
            text.append(f"- {row['template']}／{names[row['task']]}：{row['status']}；{row.get('reason',row.get('error','无有效结果'))}")
    text+=['','主旨判断计数：','']
    for task in ('elements','forecast'):
        text.append(f"- {names[task]}：{json.dumps(groups[task]['element_counts'],ensure_ascii=False)}")
    text.append('- 前缀信息条件：'+json.dumps(groups['forecast'].get('prefix_information',{}),ensure_ascii=False))
    text.append('- 前缀推测合理性：'+json.dumps(groups['forecast'].get('inference_validity',{}),ensure_ascii=False))
    if 'cost' in result:text+=['',f"累计费用与未确认预占的保守上界：{result['cost']['upper_cny']}元，沿用原100元总上限；不是实际账单。"]
    if 'execution' in result:text.append(f"本轮执行耗时 {result['execution']['elapsed_s']:.1f} 秒，峰值 {result['execution']['peak_workers']} 路；生产版本是否改变：{result['execution']['production_changed']}。")
    text+=['','标注与局限：','',
        '- 预标注、检索资料和原片核验在正式运行前固定。修订过的标注有单独理由与原始回执；不伪装成人工金标准。',
        '- 前缀信息不足的合理多解不等同于编造；完整主旨吻合与推断合理性分别保留。',
        '- 声道差分只是候选提取，不能保证清除对白；未通过实际声音核验的结果不算纯BGM成绩。',
        '- 同曲不保证反向改编具有相同价值；此类关联不能证明模型理解了任意同曲视频。',
        '- 逐题原理解、候选引用、裁切来源、模型回执和检查结果见 report.json 与 cases/。无四类加权总分。','',
        '冻结版本：','',f"- 原理解生产目录：{manifest['production']['production_root']}",
        f"- 音乐候选目录：{manifest['production'].get('music_candidate',{}).get('root','未配置')}",
        f"- 协议哈希：{manifest['protocol_sha256']}",'']
    return text
