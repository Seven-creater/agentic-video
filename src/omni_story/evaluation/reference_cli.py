"""Production-output evaluations from the four methods in the meeting transcript."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from ..library.media import sha256_file
from ..library.state import file_lock, json_sha, write_json
from . import reference_protocol as p, reference_exam as exam


def failure(error):
    text = str(error)
    if any(x in text.lower() for x in ('uncertain', 'unknown', 'no_replay', 'not_received')):
        status = 'blocked_unknown'
    elif any(x in text.lower() for x in ('budget', 'envelope', 'request_limit')):
        status = 'blocked_budget'
    else:
        status = 'failed'
    return dict(status=status, error_type=type(error).__name__, error=text[:600])


def prepare(args):
    inventory = p.read(args.inventory)
    selected = []
    for group in sorted(inventory['templates'], key=lambda g: json_sha(g['folder'])):
        # A declared, answer-independent duration rule bounds paired AV packets.
        assets = sorted(group['assets'], key=lambda a: (a['duration_s'], a['sha256']))
        if len(assets) >= 2 and assets[0]['duration_s'] + assets[1]['duration_s'] <= 180:
            selected.append(dict(id='t_' + json_sha(group['folder'])[:16], name=group['folder'],
                                 reference=assets[0], query=assets[1]))
        if len(selected) == 10:
            break
    if len(selected) != 10:
        raise ValueError('reference_eval_ten_eligible_templates_required')
    args.output.mkdir(parents=True, exist_ok=True)
    for case in selected:
        path = args.output / 'preparation' / case['id'] / 'labels.json'
        binding = dict(reference=case['reference'], query=case['query'],
                       prompt_sha256=json_sha(exam.LABEL_PROMPT))
        try:
            if path.exists():
                saved = p.read(path)
                if saved['binding'] != binding:
                    raise ValueError('reference_eval_preparation_changed')
                case['labels'] = saved['labels']
            else:
                labels = exam.prepare_labels(case, args.receipts / 'labels' / case['id'])
                write_json(path, dict(binding=binding, labels=labels))
                case['labels'] = labels
        except Exception as error:
            write_json(path.with_name('failure.json'), failure(error))
            case['labels'] = dict(provenance='model_assisted_unconfirmed', preparation_error=failure(error))
    manifest = dict(schema_version=p.SCHEMA, experiment_id=args.experiment_id,
        methods=p.METHODS, cases=selected, human_review_cases=[c['id'] for c in selected[:6]],
        production=p.read(args.runtime), receipts=str(args.receipts.resolve()),
        selection_policy='template_sha_order_shortest_two_eligible_av180_v1',
        preparation_code_sha256=code_identity(),
        input_condition='one_original_production_reference_per_template',
        study_limit='Development study; labels are model-assisted until explicit user review.')
    frozen = p.freeze(manifest, args.output / 'manifest.json')
    cards(frozen, args.output)
    print(json.dumps(dict(manifest=str(args.output / 'manifest.json'), cases=10, planned=40)))


def cards(manifest, output):
    text = ['# 人工核对卡片', '', '以下6条题目草案尚未人工确认。请核对原视频后确认或指出错误；没有预填人工通过。', '']
    review = dict(protocol_sha256=manifest['protocol_sha256'], confirmed_by='user', cases={})
    for case in manifest['cases']:
        if case['id'] not in manifest['human_review_cases']:
            continue
        q = case['labels']
        if 'preparation_error' in q:
            text += [f"## {case['name']}", '', '出题失败，尚无可确认答案：' + q['preparation_error']['error'], '']
            continue
        text += [f"## {case['name']}（{case['id']}）", '',
                 f"参考视频：[{Path(case['reference']['path']).name}]({case['reference']['path']})", '',
                 f"未见视频：[{Path(case['query']['path']).name}]({case['query']['path']})", '',
                 '要素：' + '；'.join(q['elements']), '',
                 f"预测截断点：{q['cut_s']}秒；隐藏后续：" + ' → '.join(q['future_events']), '',
                 '故事目标段落：' + (', '.join(sorted(p.story_gold(manifest, case['id'])))
                    if 'story_preparation_error' not in q else '出题失败，尚无有效答案'), '',
                 '音乐题草案：' + json.dumps(q['bgm_question'], ensure_ascii=False), '',
                 '核对：要素正确？隐藏后续正确？故事对应？音乐阶段明确？', '']
    text += ['完整合成长故事见 story.json；模型辅助标签与人工确认子集分别统计。', '']
    write_json(output / 'story.json', p.story_text(manifest))
    # Empty cases is intentional. Only a user response can supply confirmations.
    write_json(output / 'human-review-template.json', review)
    (output / 'human-review-cards.md').write_text('\n'.join(text))


def subject(case, manifest, folder):
    runtime = manifest['production']
    canonical=runtime.get('subject_outputs',{}).get(case['id'],str(folder/'subject'))
    job = dict(runtime, reference=case['reference']['path'], reference_sha256=case['reference']['sha256'],
               output=canonical)
    # Historical completed reference returns can be imported by the same frozen
    # observer; do not recreate them with the new standalone understanding prompt.
    write_json(folder / 'subject-run.json', job)
    script = Path(__file__).with_name('production_subject.py')
    completed = subprocess.run([sys.executable, str(script), '--run', str(folder / 'subject-run.json')],
        capture_output=True, text=True, timeout=1800)
    if completed.returncode:
        error=completed.stderr
        for key in ('Z_AI_API_KEY','ZHIPU_API_KEY','DASHSCOPE_API_KEY'):
            if os.environ.get(key):error=error.replace(os.environ[key],'[REDACTED]')
        (folder/'subject.stderr.log').write_text(error)
        raise RuntimeError('production_subject_failed:' + (error.strip().splitlines()[-1] if error.strip() else str(completed.returncode)))
    saved=p.read(Path(canonical)/'subject.json')
    return dict(saved,evidence_path=str(Path(canonical)/'subject.json'))


def _asr_prefix(media, folder, runtime):
    if not (runtime['asr'] or runtime['configuration'].get('creative_audio')):
        return dict(status='not_requested')
    from ..library.asr import transcribe_window
    from ..library.media import inventory_sources
    from faster_whisper import WhisperModel
    source = inventory_sources(media, folder / 'prefix-catalog')['sources'][0]
    if source['audio_stream_index'] is None:
        return dict(status='no_audio_track')
    model = WhisperModel(runtime['asr_model_dir'], device='cpu', compute_type='int8', cpu_threads=2)
    result = transcribe_window(source, 0, source['duration_s'], folder / 'prefix-asr', model=model)
    return dict(kind='automatic_unverified_prefix_transcription',
                segments=[dict(start_s=r['source_start_s'], end_s=r['source_end_s'], text=r['text'])
                          for r in result['segments']],
                limitation='Only prefix words; no music, identity or hidden-tail evidence.')


def task(args):
    manifest = p.load(args.manifest)
    case = next(c for c in manifest['cases'] if c['id'] == args.case)
    folder = args.output / 'cases' / case['id']
    folder.mkdir(parents=True, exist_ok=True)
    with file_lock(folder / '.case.lock', wait=True):
        identity = dict(protocol_sha256=manifest['protocol_sha256'],
                        code_sha256=code_identity(), case_id=case['id'])
        binding = folder / 'identity.json'
        if binding.exists() and p.read(binding) != identity:
            raise ValueError('reference_eval_locked_case_changed')
        write_json(binding, identity)
        collected = None
        try:
            collected = subject(case, manifest, folder)
        except Exception as error:
            subject_error = failure(error)
        canonical=Path(manifest['production'].get('subject_outputs',{}).get(case['id'],str(folder/'subject')))
        ledger=canonical/'production_stage/library_state.json'
        native_calls=p.read(ledger)['calls'] if ledger.exists() else []
        if collected is None:
            statuses={c['status'] for c in native_calls}
            if statuses.intersection({'submitted','uncertain'}):subject_error['status']='blocked_unknown'
            elif 'failed_known' in statuses:subject_error['status']='failed_known'
            elif statuses=={'received'}:subject_error['status']='failed_contract'
            elif manifest['production'].get('subject_failures',{}).get(case['id']):
                subject_error.update(manifest['production']['subject_failures'][case['id']])
        write_json(folder/'subject-status.json',dict(status='collected' if collected else subject_error['status'],
            original_output=str(canonical),calls=[dict(id=c['id'],status=c['status']) for c in native_calls],
            error=None if collected else subject_error['error']))
        receipt_root = Path(manifest['receipts']) / 'grading' / case['id']
        for name in p.TASKS:
            path = folder / (name + '.json')
            if path.exists():
                continue
            row = dict(**identity, task=name, label_source='model_assisted_unconfirmed')
            started = time.time()
            try:
                if 'preparation_error' in case['labels']:
                    row.update(status='blocked_question', reason=case['labels']['preparation_error'])
                elif name == 'bgm':
                    row.update(status='capability_missing',
                        reason='Frozen production reference path uses a silent visual proxy and optional ASR; it has no BGM-only exam input.',
                        question=case['labels']['bgm_question'], metrics=None,
                        question_preparation_note=case['labels'].get('bgm_preparation_error'))
                elif collected is None:
                    row.update(subject_error)
                elif name+'_preparation_error' in case['labels']:
                    row.update(status='blocked_question',reason=case['labels'][name+'_preparation_error'])
                elif not case['labels']['same_mechanism'] and name == 'forecast':
                    row.update(status='blocked_question', reason='Unseen sample was not verified as the same mechanism.')
                else:
                    if collected['manual_correction']:
                        raise ValueError('reference_eval_historical_manual_correction_excluded')
                    understanding = collected['understanding']
                    probes=Path(manifest['production'].get('probe_outputs',{}).get(case['id'],str(folder)))
                    row['subject_sha256'] = collected['understanding_sha256']
                    row['subject_path'] = collected['evidence_path']
                    if name == 'elements':
                        raw, receipt = exam.paid_review([case['reference']], {'reference':case['reference']['asset_id']},
                            exam.ELEMENT_IDS_PROMPT,dict(candidate_units=exam.candidate_units(understanding),
                            gold_rubric=[dict(id=f'g{i:03d}',text=text) for i,text in enumerate(case['labels']['elements'])]),
                            receipt_root / 'elements_ids_v1')
                        raw=exam.compile_element_ids(raw,understanding,len(case['labels']['elements']))
                        row['judgment_compiler']='candidate_unit_ids_v1'
                        metrics = exam.validate_elements(raw, case, understanding)
                    else:
                        question = p.public_question(case, name, understanding, p.story_text(manifest))
                        frames = []
                        if name == 'forecast':
                            from hunter.glm import extract_frames
                            media = p.prefix(case['query'], case['labels']['cut_s'], probes / 'prefix')
                            question['prefix_asr'] = _asr_prefix(media, probes, manifest['production'])
                            question['input_prefix_sha256'] = sha256_file(media)
                            frames = extract_frames(media, n=6, workdir=probes / 'prefix-frames')
                            if not frames:
                                raise ValueError('reference_eval_prefix_frames_missing')
                        answer = exam.ask_probe(question, probes / name, frames=frames)
                        row['answer'] = answer
                        if name == 'story':
                            metrics = exam.story_metrics(answer, manifest, case,compile_quotes=True)
                            row.update(story_extraction='paragraph_ids_to_source_v1',
                                compiled_excerpt=exam.compiled_story_excerpt(answer,manifest))
                            raw, receipt = None, None
                        else:
                            raw, receipt = exam.paid_review([case['query']], {'query':case['query']['asset_id']},
                                exam.FORECAST_IDS_PROMPT,
                                dict(predictions=[dict(id=f'p{i:03d}',text=text) for i,text in enumerate(answer['events'])],
                                     hidden_from_s=case['labels']['cut_s'],
                                     gold_events=[dict(id=f'g{i:03d}',text=text) for i,text in enumerate(case['labels']['future_events'])]),
                                receipt_root / 'forecast_ids_v1')
                            raw=exam.compile_forecast_ids(raw,answer,case,receipt['packet'])
                            row['judgment_compiler']='forecast_event_ids_v1'
                            metrics = exam.forecast_metrics(raw, answer, case)
                    row.update(status='scored_provisional', metrics=metrics, judgment=raw, receipt=receipt)
            except Exception as error:
                row.update(failure(error))
            row['elapsed_s'] = time.time() - started
            write_json(path, row)


def code_identity():
    root = Path(__file__).parent
    return json_sha({x.name:sha256_file(x) for x in sorted(root.glob('*.py'))})


def run(args):
    from ..library.worker_memory import WorkerGroups
    from ..library.resource_guard import install_ffmpeg_wrapper
    from ..library.hot_batch import capacity
    from types import SimpleNamespace
    manifest = p.load(args.manifest)
    args.output.mkdir(parents=True, exist_ok=True)
    with file_lock(args.output / '.coordinator.lock'):
        identity = dict(protocol_sha256=manifest['protocol_sha256'], code_sha256=code_identity())
        pin = args.output / 'run-identity.json'
        if pin.exists() and p.read(pin) != identity:
            raise ValueError('reference_eval_run_version_changed')
        write_json(pin, identity)
        groups = WorkerGroups()
        wrapper = install_ffmpeg_wrapper(args.output / 'resources')
        pending = [case for case in manifest['cases'] if not all(
            (args.output / 'cases' / case['id'] / (t + '.json')).exists() for t in p.TASKS)]
        active = {}
        peak, started = 0, time.time()
        limits = SimpleNamespace(home=Path(manifest['production']['home']), concurrency=args.workers,
            min_free_gb=2, worker_disk_mb=512, worker_memory_mb=1024, max_video_mb=32)
        resource_wait = time.time()
        while pending or active:
            for key, (process, group, stream) in list(active.items()):
                if process.poll() is None:
                    continue
                process.wait(); stream.close()
                usage = groups.finish(group)
                if usage is None:
                    continue
                groups.remove(group)
                folder = args.output / 'cases' / key
                write_json(folder / 'resources.json', usage)
                for name in p.TASKS:
                    path = folder / (name + '.json')
                    if not path.exists():
                        write_json(path, dict(case_id=key, task=name, status='worker_interrupted',
                            error='Worker exited before result; original paid receipts are retained.'))
                del active[key]
            available = capacity(limits, [dict(pid=v[0].pid, output=str(args.output/'cases'/k))
                                         for k,v in active.items()])['additional_workers']
            if pending and available:
                case = pending.pop(0); key = case['id']
                folder = args.output / 'cases' / key
                folder.mkdir(parents=True, exist_ok=True)
                group = groups.create('refeval_' + key, 1)
                env = dict(os.environ, PATH=str(wrapper)+os.pathsep+os.environ['PATH'],
                    OMNI_FFMPEG_SLOTS='1', OMNI_FFMPEG_THREADS='2', OMNI_ASR_THREADS='2',
                    OMNI_RESOURCE_DIR=str(args.output/'resources'), OMNI_WORKER_OUTPUT=str(folder),
                    OMNI_WORKER_MAX_BYTES=str(512*1024**2), HF_HUB_OFFLINE='1',
                    OMNI_EVAL_REQUEST_SCOPE='independent_evaluation')
                log = (folder / 'worker.log').open('ab')
                command = [sys.executable,'-m','omni_story.library.worker_memory',str(group),sys.executable,
                    '-m','omni_story.evaluation.reference_cli','task','--manifest',str(args.manifest),'--output',str(args.output),'--case',key]
                process = subprocess.Popen(command, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log)
                active[key] = (process, group, log); peak = max(peak,len(active)); resource_wait=time.time()
            elif pending and not active and time.time()-resource_wait > 600:
                for case in pending:
                    for name in p.TASKS:
                        write_json(args.output/'cases'/case['id']/(name+'.json'),
                            dict(case_id=case['id'],task=name,status='blocked_resource'))
                pending.clear()
            if pending or active:
                time.sleep(2)
        from .production_subject import verify_tree
        runtime=manifest['production']
        verify_tree(Path(runtime['production_root'])/'src',runtime['production_files'])
        unchanged=(Path(runtime['home'])/'current').resolve()==Path(runtime['production_root']).resolve()
        write_json(args.output/'execution.json', dict(identity=identity, elapsed_s=time.time()-started,
            peak_workers=peak, planned_tasks=40, production_changed=not unchanged))
    report(args)


def report(args):
    manifest = p.load(args.manifest, media=False)
    human = p.imported_human_reviews(args.human_reviews, manifest)
    rows = []
    for case in manifest['cases']:
        for name in p.TASKS:
            path = args.output/'cases'/case['id']/(name+'.json')
            row = p.read(path) if path.exists() else dict(case_id=case['id'],task=name,status='pending')
            row.update(template=case['name'], human_confirmed=human.get(case['id'], {}).get(name) == 'confirmed')
            rows.append(row)
    groups = {}
    def means(selected,keys):
        result={}
        for key in keys:
            values=[r['metrics'].get(key) for r in selected if type(r['metrics'].get(key)) in (int,float)]
            result[key]=sum(values)/len(values) if values else None
        return result
    for name in p.TASKS:
        chosen = [r for r in rows if r['task']==name]
        scored = [r for r in chosen if r['status']=='scored_provisional']
        keys = {k for r in scored for k,v in r['metrics'].items() if type(v) in (int,float)}
        groups[name] = dict(planned=10, scored=len(scored), coverage=len(scored)/10,
            statuses={s:sum(r['status']==s for r in chosen) for s in sorted({r['status'] for r in chosen})},
            means_scored=means(scored,keys),
            human_confirmed=sum(r['human_confirmed'] for r in chosen))
        for label,subset in [('human_confirmed',[r for r in scored if r['human_confirmed']]),
                             ('model_assisted',[r for r in scored if not r['human_confirmed']])]:
            groups[name]['means_'+label]=means(subset,keys)
        agreements=[review['judgment_agreement'][name] for review in human.values()
                    if name in review.get('judgment_agreement',{})]
        groups[name]['human_judge_agreement']=sum(agreements)/len(agreements) if agreements else None
        groups[name]['human_judge_comparisons']=len(agreements)
    for row in rows:
        if row['status']=='scored_provisional':
            case=next(c for c in manifest['cases'] if c['id']==row['case_id'])
            verify_scored_row(row,case,manifest,args.output/'cases'/case['id'])
    subjects=[]
    for case in manifest['cases']:
        path=args.output/'cases'/case['id']/'subject-status.json'
        value=p.read(path) if path.exists() else dict(status='not_collected',reason='No completed subject checkpoint; task status remains in the report.')
        subjects.append(dict(case_id=case['id'],template=case['name'],**value))
    result = dict(schema_version=p.SCHEMA, protocol_sha256=manifest['protocol_sha256'],
        methods=p.METHODS, groups=groups, rows=rows, human_review_pending=6-len(human),
        subjects=subjects,
        overall_score=None, note='Four transcript tasks reported separately. Model-assisted scores are provisional; no human confirmation is inferred.')
    write_json(args.output/'report.json',result)
    text=['# 原创作模块参考理解评测','',f"实验：{manifest['experiment_id']}；10模板、40个计划项。",'',
          '| 文稿题型 | 已评分／计划 | 状态 | 人工确认 |','|---|---:|---|---:|']
    for name,g in groups.items():
        text.append(f"| {name} | {g['scored']}/10 | {json.dumps(g['statuses'],ensure_ascii=False)} | {g['human_confirmed']}/10 |")
    text += ['', '各类分数：', '',json.dumps({k:v['means_scored'] for k,v in groups.items()},ensure_ascii=False,indent=2),
             '', '6份人工卡片尚需明确确认；模型辅助标签不当作人工标准。BGM能力缺失保留分母。',
             '原始理解、题目、答案、评审理由和回执引用见各 cases 子目录。旧独立理解实验成绩不计入本报告。','']
    (args.output/'report.md').write_text('\n'.join(text))
    print(json.dumps(dict(groups=groups,human_review_pending=result['human_review_pending']),ensure_ascii=False))


def verify_scored_row(row,case,manifest,folder):
    from .production_subject import verify_subject
    subject_value=p.read(row.get('subject_path',folder/'subject/subject.json'))
    verify_subject(subject_value)
    if (row['subject_sha256']!=subject_value['understanding_sha256'] or row['protocol_sha256']!=manifest['protocol_sha256'] or
            subject_value['binding']['reference_sha256']!=case['reference']['sha256']):
        raise ValueError('reference_eval_report_subject_changed')
    probes=Path(manifest['production'].get('probe_outputs',{}).get(case['id'],str(folder)))
    if row['task']=='story':
        saved=p.read(probes/'story/answer.json');exam.verify_probe(saved)
        if saved['answer']!=row['answer']:
            raise ValueError('reference_eval_report_story_answer_changed')
        compiled=row.get('story_extraction')=='paragraph_ids_to_source_v1'
        metrics=exam.story_metrics(saved['answer'],manifest,case,compile_quotes=compiled)
        if compiled and row['compiled_excerpt']!=exam.compiled_story_excerpt(saved['answer'],manifest):
            raise ValueError('reference_eval_report_story_extraction_changed')
    else:
        receipt=row['receipt']; state_path=Path(receipt['path']); state=p.read(state_path)
        response_path=state_path.with_name('response.json');response=p.read(response_path)
        if (state['status']!='received' or sha256_file(state_path)!=receipt['state_sha256'] or
                sha256_file(response_path)!=receipt['response_sha256'] or response['http_status']!=200):
            raise ValueError('reference_eval_report_judge_receipt_changed')
        raw=json.loads(json.loads(response['body_text'])['choices'][0]['message']['content'])
        if row.get('judgment_compiler')=='candidate_unit_ids_v1':
            raw=exam.compile_element_ids(raw,subject_value['understanding'],len(case['labels']['elements']))
        elif row.get('judgment_compiler')=='forecast_event_ids_v1':
            saved=p.read(probes/'forecast/answer.json');exam.verify_probe(saved)
            raw=exam.compile_forecast_ids(raw,saved['answer'],case,receipt['packet'])
        if raw!=row['judgment']:
            raise ValueError('reference_eval_report_judgment_not_received_response')
        if row['task']=='elements':
            metrics=exam.validate_elements(raw,case,subject_value['understanding'])
        else:
            saved=p.read(probes/'forecast/answer.json');exam.verify_probe(saved)
            if saved['answer']!=row['answer']:
                raise ValueError('reference_eval_report_forecast_answer_changed')
            metrics=exam.forecast_metrics(raw,saved['answer'],case)
    if metrics!=row['metrics']:
        raise ValueError('reference_eval_report_metrics_changed')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['prepare','run','task','report','cards'])
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--manifest',type=Path)
    parser.add_argument('--inventory',type=Path)
    parser.add_argument('--runtime',type=Path)
    parser.add_argument('--receipts',type=Path)
    parser.add_argument('--experiment-id',default='production_reference_20261024_v1')
    parser.add_argument('--case')
    parser.add_argument('--workers',type=int,choices=range(1,6),default=2)
    parser.add_argument('--human-reviews',type=Path)
    args=parser.parse_args(argv)
    args.output=args.output.resolve()
    if args.command=='prepare':prepare(args)
    elif args.command=='run':run(args)
    elif args.command=='task':task(args)
    elif args.command=='cards':cards(p.load(args.manifest),args.output)
    else:report(args)


if __name__=='__main__':
    main()
