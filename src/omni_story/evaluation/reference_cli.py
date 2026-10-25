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

V4_SUBJECT_BOOTSTRAP='''import json, sys
from pathlib import Path
run=json.loads(Path(sys.argv[1]).read_text())
sys.path.insert(0,sys.argv[2])
sys.path.insert(0,str(Path(run['production_root'])/'src'))
import isolated_asr
isolated_asr.install(Path(run['output'])/'isolated_asr',run['production_root'],profile=run.pop('asr_profile',None))
run['isolate_asr']=False
from production_subject import capture_reference
capture_reference(run)
'''


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
    if getattr(args,'prefix_policy',None) and getattr(args,'protocol_version',None)!='v4':
        raise ValueError('reference_eval_prefix_policy_requires_v4')
    inventory = p.read(args.inventory)
    selected = []
    curated=p.read(args.curation) if args.curation else None
    if curated:
        for selection in curated['selections']:
            group=next(g for g in inventory['templates'] if g['folder']==selection['folder'])
            assets={Path(a['path']).name:a for a in group['assets']}
            reference,query=assets[selection['reference_file']],assets[selection['query_file']]
            if reference['sha256']==query['sha256']:raise ValueError('reference_eval_curated_support_query_overlap')
            selected.append(dict(id='t_'+json_sha(group['folder'])[:16],name=group['folder'],reference=reference,query=query,
                evaluation_target='core_meaning_v2',curation=selection,prefix_cut_s=min(5,reference['duration_s']/3)))
    for group in sorted(inventory['templates'], key=lambda g: json_sha(g['folder'])):
        if curated:break
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
                       prompt_sha256=json_sha(exam.CORE_LABEL_PROMPT if curated else exam.LABEL_PROMPT),
                       **(dict(curation=case['curation'],prefix_cut_s=case['prefix_cut_s']) if curated else {}))
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
        selection_policy='public_source_and_actual_core_curation_v2' if curated else 'template_sha_order_shortest_two_eligible_av180_v1',
        preparation_code_sha256=code_identity(),
        input_condition='one_original_production_reference_per_template',
        study_limit='Development study; labels are model-assisted until explicit user review.',
        **(dict(evaluation_target='core_meaning_v2',curation_sha256=sha256_file(args.curation)) if curated else {}))
    if getattr(args,'protocol_version',None)=='v4':
        measurements=(p.read(args.bgm_measurements) if args.bgm_measurements else inventory.get('bgm_measurements'))
        if measurements is None:
            items=inventory.get('bgm_items')
            if not items:raise ValueError('reference_eval_v4_bgm_measurements_required')
            measurements=p.v4_module('bgm_measure').measure_all(items)
        manifest.update(protocol_version='v4',story_seed=args.story_seed,bgm_measurements=measurements,
            asr_profile=p.read(args.asr_profile) if args.asr_profile else None)
        if getattr(args,'prefix_policy',None):manifest['prefix_policy']=args.prefix_policy
        manifest['bgm_eligibility']=p.bgm_plan(manifest)
        for case in selected:
            if 'theme_reveal_s' in case.get('curation',{}):
                case['labels']['theme_reveal_s']=case['curation']['theme_reveal_s']
        if getattr(args,'confirmations',None):
            # User confirmations rewrite theme elements before freeze; preparation/labels.json keeps the model draft.
            confirmed=p.load_confirmations(args.confirmations)
            p.apply_confirmations(selected,confirmed)
            manifest.update(confirmations_sha256=sha256_file(args.confirmations),scoring_rule=confirmed['rule'])
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
        if manifest.get('protocol_version')=='v4' and manifest.get('prefix_policy')=='before_last_element':
            point=next(point for task,point in p.task_points(manifest,case) if task=='forecast')
            forecast=('前缀题不适用：'+point['skipped_reason'] if point.get('skipped_reason') else
                f"只看本参考前{point['cut_s']}秒；被藏要素："+'；'.join(q['elements'][i] for i in point['hidden_element_indices']))
        else:
            forecast=(f"只看本参考前{q['prefix_cut_s']}秒；合理主旨推测："+'；'.join(q['prefix_acceptable_themes'])
                if case.get('evaluation_target')=='core_meaning_v2' else f"预测截断点：{q['cut_s']}秒；隐藏后续："+' → '.join(q['future_events']))
        text += [f"## {case['name']}（{case['id']}）", '',
                 f"参考视频：[{Path(case['reference']['path']).name}]({case['reference']['path']})", '',
                 f"未见视频：[{Path(case['query']['path']).name}]({case['query']['path']})", '',
                 '要素：' + '；'.join(q['elements']), '',
                 forecast, '',
                 '故事目标段落：' + (', '.join(sorted(p.story_gold(manifest, case['id'])))
                    if 'story_preparation_error' not in q else '出题失败，尚无有效答案'), '',
                 '音乐题草案：' + json.dumps(q['bgm_question'], ensure_ascii=False), '',
                 ('核对：主旨与态度准确？前缀推测合理？故事主旨对应？音乐片段有无对白？'
                    if case.get('evaluation_target')=='core_meaning_v2' else '核对：要素正确？隐藏后续正确？故事对应？音乐阶段明确？'), '']
    text += ['完整合成长故事见 story.json；模型辅助标签与人工确认子集分别统计。', '']
    write_json(output / 'story.json', p.story_text(manifest))
    # Empty cases is intentional. Only a user response can supply confirmations.
    write_json(output / 'human-review-template.json', review)
    (output / 'human-review-cards.md').write_text('\n'.join(text))


def subject(case, manifest, folder, *, condition='full', cache_root=None):
    runtime = manifest['production']
    v4=manifest.get('protocol_version')=='v4' or 'support_k' in manifest
    canonical=str(folder/'subject') if v4 else runtime.get('subject_outputs',{}).get(case['id'],str(folder/'subject'))
    job = dict(runtime, reference=case['reference']['path'], reference_sha256=case['reference']['sha256'],
               output=canonical)
    if v4:
        job.update(subject_cache_root=str(cache_root or folder.parents[1]/'subject_cache'),condition=condition,
            asr_profile=manifest.get('asr_profile'),case_id=case['id'],
            allow_unregistered_reference=condition=='full')
        if exam.text_judge(manifest) and condition=='full' and 'support_k' not in manifest:job['reuse_only']=True
        else:job.pop('reuse_only',None)
    # Historical completed reference returns can be imported by the same frozen
    # observer; do not recreate them with the new standalone understanding prompt.
    write_json(folder / 'subject-run.json', job)
    script = Path(__file__).with_name('production_subject.py')
    command=([sys.executable,'-c',V4_SUBJECT_BOOTSTRAP,str(folder/'subject-run.json'),str(script.parent)]
        if v4 else [sys.executable,str(script),'--run',str(folder/'subject-run.json')])
    completed = subprocess.run(command,
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


def reuse_elements(case,manifest):
    if not exam.text_judge(manifest):return None
    label_sha=json_sha(case['labels']['elements'])
    for source in manifest.get('reuse_rows_from',[]):
        path=Path(source)
        rows=[r for r in p.read(path)['rows'] if r.get('case_id')==case['id'] and
            r.get('task')=='elements' and r.get('status')=='scored_provisional']
        if not rows:continue
        old=p.read(path.with_name('manifest.json'))
        gold=next((c for c in old['cases'] if c['id']==case['id']),None)
        if gold is not None and 'elements' in gold['labels'] and json_sha(gold['labels']['elements'])==label_sha:
            return dict(rows[0],reused_from=str(path),reused_label_sha256=label_sha,
                judge_backend=rows[0].get('judge_backend','qwen_video'))
    return None


def _rerun_binding(manifest):
    omitted={'protocol_sha256','rerun_failed_from','receipts','experiment_id','production','reuse_rows_from','cases'}
    runtime=manifest.get('production',{})
    return dict(manifest={k:v for k,v in manifest.items() if k not in omitted},
        production={k:runtime[k] for k in ('production_root','production_files','configuration','asr',
            'asr_model_dir','music_candidate') if k in runtime})


def _verify_reused_files(value):
    if isinstance(value,list):
        for item in value:_verify_reused_files(item)
    elif isinstance(value,dict):
        for key,path in value.items():
            if key in {'subject_path','support_template_path'} or (key=='path' and 'stage' in value):continue
            sha_key=key[:-5]+'_sha256' if key.endswith('_path') else 'sha256' if key=='path' else None
            expected=value.get(sha_key) if sha_key else None
            if key=='path' and expected is None:expected=value.get('state_sha256')
            if expected and sha256_file(path)!=expected:raise ValueError('reference_eval_reused_file_changed')
        for item in value.values():_verify_reused_files(item)


def _rerun_source(source,row,case,manifest):
    path=Path(source);old=p.read(path.with_name('manifest.json'))
    if _rerun_binding(old)!=_rerun_binding(manifest):raise ValueError('reference_eval_rerun_manifest_changed')
    gold=next((c for c in old['cases'] if c['id']==case['id']),None)
    points={(name,point.get('condition') if point else None):point for name,point in p.task_points(manifest,case)}
    key=(row.get('task'),row.get('condition'))
    if gold!=case or row.get('case_id')!=case['id'] or key not in points:
        raise ValueError('reference_eval_rerun_row_identity_changed')
    point=points[key]
    if point and row.get('prefix_cut_s')!=point.get('cut_s'):
        raise ValueError('reference_eval_rerun_condition_changed')
    if row['task']=='story' and p.story_text(old)!=p.story_text(manifest):
        raise ValueError('reference_eval_rerun_story_changed')
    _verify_reused_files(row)
    if row['status']=='scored_provisional':verify_scored_row(row,gold,old,path.parent/'cases'/case['id'])


def reuse_failed_rows(manifest,output,*,case=None):
    sources=manifest.get('rerun_failed_from',[])
    if not isinstance(sources,list) or any(not isinstance(source,str) for source in sources):
        raise ValueError('reference_eval_rerun_failed_from_invalid')
    cases=[case] if case else manifest['cases']
    for source in sources:
        for row in p.read(source)['rows']:
            if row.get('status')!='scored_provisional' and not str(row.get('status','')).startswith('not_applicable'):continue
            current=next((c for c in cases if c['id']==row.get('case_id')),None)
            if current is None:continue
            points=[(name,point) for name,point in p.task_points(manifest,current)
                if (name,point.get('condition') if point else None)==(row.get('task'),row.get('condition'))]
            if not points:continue
            name,point=points[0];path=p.row_path(output/'cases'/current['id'],name,point)
            if path.exists():continue
            try:_rerun_source(source,row,current,manifest)
            except (OSError,ValueError,KeyError,TypeError,StopIteration):continue
            write_json(path,dict(row,reused_from_report=source))


def _verify_reused_row(row,case,manifest):
    source=row['reused_from_report']
    if source not in manifest.get('rerun_failed_from',[]):raise ValueError('reference_eval_reused_row_changed')
    original=next((old for old in p.read(source)['rows'] if old.get('case_id')==row['case_id'] and
        old.get('task')==row['task'] and old.get('condition')==row.get('condition')),None)
    if original is None or row!=dict(original,reused_from_report=source):raise ValueError('reference_eval_reused_row_changed')
    _rerun_source(source,original,case,manifest)


def task(args):
    manifest = p.load(args.manifest)
    case = next(c for c in manifest['cases'] if c['id'] == args.case)
    if 'support_k' in manifest:
        from .multi_reference import task as multi_task
        return multi_task(case,manifest,args.output)
    folder = args.output / 'cases' / case['id']
    folder.mkdir(parents=True, exist_ok=True)
    with file_lock(folder / '.case.lock', wait=True):
        identity = dict(protocol_sha256=manifest['protocol_sha256'],
                        code_sha256=code_identity(), case_id=case['id'])
        binding = folder / 'identity.json'
        if binding.exists() and p.read(binding) != identity:
            raise ValueError('reference_eval_locked_case_changed')
        write_json(binding, identity)
        reuse_failed_rows(manifest,args.output,case=case)
        text=exam.text_judge(manifest)
        reused=reuse_elements(case,manifest) if not (folder/'elements.json').exists() else None
        needs_full=not text or any(name in {'elements','story'} and not p.row_path(folder,name,point).exists() and
            name+'_preparation_error' not in case['labels'] and not (name=='elements' and reused)
            for name,point in p.task_points(manifest,case))
        if manifest.get('rerun_failed_from') and all(p.row_path(folder,name,point).exists()
                for name,point in p.task_points(manifest,case) if name!='bgm'):needs_full=False
        collected = None
        subject_error=dict(status='not_collected',error='No full understanding required for pending tasks.')
        try:
            if (manifest.get('protocol_version')=='v4' or case.get('evaluation_target')=='core_meaning_v2') and 'preparation_error' in case['labels']:
                raise ValueError('reference_eval_uncurated_input_not_submitted')
            if needs_full:
                collected = (subject(case,manifest,folder,cache_root=args.output/'subject_cache')
                    if manifest.get('protocol_version')=='v4' else subject(case,manifest,folder))
        except Exception as error:
            subject_error = failure(error)
        canonical=(folder/'subject' if manifest.get('protocol_version')=='v4' else
            Path(manifest['production'].get('subject_outputs',{}).get(case['id'],str(folder/'subject'))))
        ledger=canonical/'production_stage/library_state.json'
        native_calls=p.read(ledger)['calls'] if ledger.exists() else []
        if collected is None:
            statuses={c['status'] for c in native_calls}
            if ledger.exists() and p.blocked(ledger):subject_error['status']='blocked_unknown'
            elif 'failed_known' in statuses:subject_error['status']='failed_known'
            elif statuses=={'received'}:subject_error['status']='failed_contract'
            elif manifest['production'].get('subject_failures',{}).get(case['id']):
                subject_error.update(manifest['production']['subject_failures'][case['id']])
        if needs_full or not (folder/'subject-status.json').exists():
            write_json(folder/'subject-status.json',dict(status='collected' if collected else subject_error['status'],
                original_output=str(canonical),calls=[dict(id=c['id'],status=c['status']) for c in native_calls],
                error=None if collected else subject_error['error']))
        receipt_root = Path(manifest['receipts']) / 'grading' / case['id']
        for name,point in p.task_points(manifest,case):
            v4=manifest.get('protocol_version')=='v4'
            core=v4 or case.get('evaluation_target')=='core_meaning_v2'
            if core and name=='bgm':continue  # All independent reference returns form the music gallery first.
            path = p.row_path(folder,name,point)
            if path.exists():
                continue
            row = dict(**identity, task=name, label_source=case['labels'].get('provenance','model_assisted_unconfirmed') if v4 else 'model_assisted_unconfirmed')
            if text:row['judge_backend']='glm_text'
            if point:
                row.update(condition=point['condition'],prefix_cut_s=point.get('cut_s'))
                if point.get('reason'):row['reason']=point['reason']
            started = time.time()
            try:
                if name=='elements' and reused:
                    row.update(reused)
                elif point and point.get('skipped_reason'):
                    row.update(status='not_applicable',skipped_reason=point['skipped_reason'])
                elif 'preparation_error' in case['labels']:
                    row.update(status='blocked_question', reason=case['labels']['preparation_error'])
                elif core and name=='forecast':
                    from .reference_core import prefix_task
                    row.update(prefix_task(case,manifest,folder,receipt_root,point=point) if v4 else
                        prefix_task(case,manifest,folder,receipt_root))
                elif name == 'bgm':
                    row.update(status='capability_missing',
                        reason='Frozen production reference path uses a silent visual proxy and optional ASR; it has no BGM-only exam input.',
                        question=case['labels']['bgm_question'], metrics=None,
                        question_preparation_note=case['labels'].get('bgm_preparation_error'))
                elif text and name+'_preparation_error' in case['labels']:
                    row.update(status='blocked_question',reason=case['labels'][name+'_preparation_error'])
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
                        reviewer=exam.checked_review if v4 else exam.paid_review
                        raw, receipt = reviewer([case['reference']], {'reference':case['reference']['asset_id']},
                            exam.CORE_ELEMENT_PROMPT if core else exam.ELEMENT_IDS_PROMPT,dict(candidate_units=exam.candidate_units(understanding),
                            gold_rubric=[dict(id=f'g{i:03d}',text=value) for i,value in enumerate(case['labels']['elements'])],
                            **(dict(scoring_rule=manifest['scoring_rule']) if text else {})),
                            receipt_root / 'elements_ids_v1',**(dict(folder=folder,count=len(case['labels']['elements'])) if v4 else {}),
                            **(dict(judge_backend='glm_text') if text else {}))
                        raw=exam.compile_element_ids(raw,understanding,len(case['labels']['elements']))
                        row['judgment_compiler']='candidate_unit_ids_v1'
                        metrics = exam.validate_elements(raw, case, understanding,**(dict(judge_backend='glm_text') if text else {}))
                    else:
                        question = p.public_question(dict(case,evaluation_target='core_meaning_v2') if v4 else case, name, understanding,
                            p.story_inputs(manifest,case['id']) if core else p.story_text(manifest))
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
    files=sorted([*root.glob('*.py'),*root.glob('*.mjs')])
    return json_sha({x.name:sha256_file(x) for x in files})


def run(args):
    from ..library.worker_memory import WorkerGroups
    from ..library.resource_guard import install_ffmpeg_wrapper
    from ..library.hot_batch import capacity
    from types import SimpleNamespace
    manifest = p.load(args.manifest)
    core=manifest.get('protocol_version')=='v4' or manifest.get('evaluation_target')=='core_meaning_v2' or 'support_k' in manifest
    if 'support_k' in manifest:
        from .multi_reference import row_metadata
    work_tasks=p.TASKS[:3] if core else p.TASKS
    args.output.mkdir(parents=True, exist_ok=True)
    with file_lock(args.output / '.coordinator.lock'):
        identity = dict(protocol_sha256=manifest['protocol_sha256'], code_sha256=code_identity())
        pin = args.output / 'run-identity.json'
        if pin.exists() and p.read(pin) != identity:
            raise ValueError('reference_eval_run_version_changed')
        write_json(pin, identity)
        reuse_failed_rows(manifest,args.output)
        groups = WorkerGroups()
        wrapper = install_ffmpeg_wrapper(args.output / 'resources')
        pending = [case for case in manifest['cases'] if not all(
            p.row_path(args.output/'cases'/case['id'],name,point).exists()
            for name,point in p.task_points(manifest,case) if name in work_tasks)]
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
                case=next(c for c in manifest['cases'] if c['id']==key)
                for name,point in p.task_points(manifest,case):
                    if name not in work_tasks:continue
                    path = p.row_path(folder,name,point)
                    if not path.exists():
                        write_json(path, dict(case_id=key, task=name, status='worker_interrupted',
                            **(row_metadata(case,manifest,folder,condition=point['condition'] if point else None) if 'support_k' in manifest else {}),
                            **(dict(condition=point['condition'],prefix_cut_s=point.get('cut_s')) if point else {}),
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
                    for name,point in p.task_points(manifest,case):
                        if name not in work_tasks:continue
                        write_json(p.row_path(args.output/'cases'/case['id'],name,point),
                            dict(case_id=case['id'],task=name,status='blocked_resource',
                                **(row_metadata(case,manifest,args.output/'cases'/case['id'],condition=point['condition'] if point else None) if 'support_k' in manifest else {}),
                                **(dict(condition=point['condition'],prefix_cut_s=point.get('cut_s')) if point else {})))
                pending.clear()
            if pending or active:
                time.sleep(2)
        from .production_subject import verify_tree
        runtime=manifest['production']
        verify_tree(Path(runtime['production_root'])/'src',runtime['production_files'])
        unchanged=(Path(runtime['home'])/'current').resolve()==Path(runtime['production_root']).resolve()
        write_json(args.output/'execution.json', dict(identity=identity, elapsed_s=time.time()-started,
            peak_workers=peak, planned_tasks=(sum(1 for c in manifest['cases'] for _ in p.task_points(manifest,c))
                if manifest.get('protocol_version')=='v4' or 'support_k' in manifest else 40), production_changed=not unchanged))
        if core:
            from .reference_core import music_tasks
            music_tasks(manifest,args.output)
            execution=p.read(args.output/'execution.json')
            execution['elapsed_s']=time.time()-started
            write_json(args.output/'execution.json',execution)
    report(args)


def report(args):
    manifest = p.load(args.manifest, media=False)
    v4=manifest.get('protocol_version')=='v4'
    human = p.imported_human_reviews(args.human_reviews, manifest)
    rows = []
    for case in manifest['cases']:
        for name,point in p.task_points(manifest,case):
            path = p.row_path(args.output/'cases'/case['id'],name,point)
            row = p.read(path) if path.exists() else dict(case_id=case['id'],task=name,status='pending')
            reused='reused_from_report' in row
            if reused:
                _verify_reused_row(row,case,manifest);rows.append(row);continue
            confirmed=human.get(case['id'],{}).get(name)
            row.update(template=case['name'], human_confirmed=(confirmed=='confirmed' or
                (v4 and confirmed is None and case['labels'].get('provenance')=='human_confirmed')))
            if 'support_k' in manifest:
                from .multi_reference import row_metadata
                metadata=row_metadata(case,manifest,args.output/'cases'/case['id'],condition=point['condition'] if point else None)
                if path.exists() and any(row.get(key)!=value for key,value in metadata.items()):
                    raise ValueError('multi_reference_row_binding_changed')
                row.update(metadata)
                if point:
                    if path.exists() and (row.get('condition')!=point['condition'] or row.get('prefix_cut_s')!=point.get('cut_s')):
                        raise ValueError('reference_eval_v4_prefix_condition_changed')
                    row.update(condition=point['condition'],prefix_cut_s=point.get('cut_s'),forecast_group=point['forecast_group'])
            if exam.text_judge(manifest):
                row.setdefault('judge_backend','fingerprint' if name=='bgm' and manifest.get('bgm_mode')=='fingerprint' else 'glm_text')
            if v4:
                if point:
                    if path.exists() and (row.get('condition')!=point['condition'] or row.get('prefix_cut_s')!=point.get('cut_s')):
                        raise ValueError('reference_eval_v4_prefix_condition_changed')
                    row.update(condition=point['condition'],prefix_cut_s=point.get('cut_s'))
                row['label_source']='human_confirmed' if row['human_confirmed'] else 'model_assisted_unconfirmed'
                if name=='elements' and manifest.get('confirmations_sha256'):
                    row['element_label_source']=case['labels'].get('element_label_source','model_assisted_unconfirmed')
            rows.append(row)
    groups = {}
    def means(selected,keys):
        result={}
        for key in keys:
            values=[r['metrics'].get(key) for r in selected if type(r['metrics'].get(key)) in ((int,float,bool) if v4 else (int,float))]
            result[key]=sum(values)/len(values) if values else None
        return result
    for name in p.TASKS:
        chosen = [r for r in rows if r['task']==name]
        scored = [r for r in chosen if r['status']=='scored_provisional']
        keys = {k for r in scored for k,v in r['metrics'].items() if type(v) in ((int,float,bool) if v4 else (int,float))}
        if v4 and name=='forecast' and manifest.get('prefix_policy')=='before_last_element':keys.add('visible_fraction')
        planned=len(chosen) if v4 or 'support_k' in manifest else 10
        groups[name] = dict(planned=planned, scored=len(scored), coverage=len(scored)/planned if planned else 0,
            statuses={s:sum(r['status']==s for r in chosen) for s in sorted({r['status'] for r in chosen})},
            means_scored=means(scored,keys),
            human_confirmed=sum(r['human_confirmed'] for r in chosen))
        for label,subset in [('human_confirmed',[r for r in scored if r['human_confirmed']]),
                             ('model_assisted',[r for r in scored if not r['human_confirmed']])]:
            groups[name]['means_'+label]=means(subset,keys)
        if v4:
            groups[name]['label_sources']={source:dict(planned=sum(r['label_source']==source for r in chosen),
                scored=sum(r['label_source']==source for r in scored),
                element_counts={s:sum(r['metrics'].get('counts',{}).get(s,0) for r in scored if r['label_source']==source)
                    for s in ('matched','partial','missing','contradicted','label_disputed')})
                for source in ('human_confirmed','model_assisted_unconfirmed')}
            if name=='elements' and manifest.get('confirmations_sha256'):
                groups[name]['element_label_sources']={source:dict(
                    planned=sum(r.get('element_label_source')==source for r in chosen),
                    scored=sum(r.get('element_label_source')==source and r['status']=='scored_provisional' for r in chosen),
                    means_scored=means([r for r in scored if r.get('element_label_source')==source],keys))
                    for source in ('human_confirmed','model_assisted_unconfirmed')}
        agreements=[review['judgment_agreement'][name] for review in human.values()
                    if name in review.get('judgment_agreement',{})]
        groups[name]['human_judge_agreement']=sum(agreements)/len(agreements) if agreements else None
        groups[name]['human_judge_comparisons']=len(agreements)
        if v4 or manifest.get('evaluation_target')=='core_meaning_v2':
            groups[name]['element_counts']={s:sum(r['metrics'].get('counts',{}).get(s,0) for r in scored)
                for s in ('matched','partial','missing','contradicted','label_disputed')}
            if name=='forecast':
                groups[name]['prefix_information']={s:sum(r['metrics'].get('prefix_information')==s for r in scored)
                    for s in ('explicit','implied','ambiguous')}
                groups[name]['inference_validity']={s:sum(r['metrics'].get('inference_validity')==s for r in scored)
                    for s in ('supported','plausible','insufficient','contradicted')}
                if v4:
                    groups[name]['cut_s']=[dict(case_id=r['case_id'],condition=r['condition'],cut_s=r['prefix_cut_s'])
                        for r in chosen]
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
    if v4:
        from .prefix_subset import summarize
        from .story_leakage import audit
        result.update(protocol_version='v4',prefix_subset=summarize(manifest,result),story_leakage=audit(manifest)['summary'],
            report_scope='single_video',note=result['note']+' 按单条视频报告，不代表模板级结论。',
            label_sources={source:dict(planned=sum(r['label_source']==source for r in rows),
                scored=sum(r['label_source']==source and r['status']=='scored_provisional' for r in rows))
                for source in ('human_confirmed','model_assisted_unconfirmed')},
            **(dict(scoring_rule=manifest['scoring_rule'],confirmations_sha256=manifest['confirmations_sha256'])
                if manifest.get('confirmations_sha256') else {}))
    if exam.text_judge(manifest):
        result.update(judge_backend='glm_text',note=result['note']+' 评审与被测同属 GLM 家族，标签为人工确认。')
    if 'support_k' in manifest:
        from .multi_reference import update_report
        update_report(manifest,result)
    budget_file=os.environ.get('OMNI_EVAL_BUDGET_FILE') or manifest['production'].get('budget_file')
    if budget_file:
        from .budget import liability
        costs=liability(p.read(budget_file))
        result['cost']={k:v for k,v in costs.items() if k!='evidence'}
    if (args.output/'execution.json').exists():result['execution']=p.read(args.output/'execution.json')
    write_json(args.output/'report.json',result)
    text=['# 原创作模块参考理解评测','',f"实验：{manifest['experiment_id']}；10模板、40个计划项。",'',
          '| 文稿题型 | 已评分／计划 | 状态 | 人工确认 |','|---|---:|---|---:|']
    for name,g in groups.items():
        text.append(f"| {name} | {g['scored']}/10 | {json.dumps(g['statuses'],ensure_ascii=False)} | {g['human_confirmed']}/10 |")
    text += ['', '各类分数：', '',json.dumps({k:v['means_scored'] for k,v in groups.items()},ensure_ascii=False,indent=2),
             '', '6份人工卡片尚需明确确认；模型辅助标签不当作人工标准。失败或不适用项保留分母。',
             '原始理解、题目、答案、评审理由和回执引用见各 cases 子目录。旧独立理解实验成绩不计入本报告。','']
    if 'support_k' not in manifest and (v4 or manifest.get('evaluation_target')=='core_meaning_v2'):
        from .reference_core import report_text
        text=report_text(manifest,result)
    if manifest.get('confirmations_sha256'):
        text+=['','人工确认评分规则：'+manifest['scoring_rule'],
            '人工确认文件 SHA256：'+manifest['confirmations_sha256'],
            '要素题按 element_label_source 分列人工确认与模型辅助计数及均分（groups.elements.element_label_sources）。','']
    if 'support_k' in manifest:
        from .multi_reference import report_text as multi_report_text
        text=multi_report_text(result)
    (args.output/'report.md').write_text('\n'.join(text))
    print(json.dumps(dict(groups=groups,human_review_pending=result['human_review_pending']),ensure_ascii=False))


def verify_scored_row(row,case,manifest,folder):
    if 'reused_from_report' in row:return _verify_reused_row(row,case,manifest)
    if 'support_k' in manifest and row['task'] in p.TASKS[:3]:
        from .multi_reference import verify_row
        return verify_row(row,case,manifest,folder)
    if exam.text_judge(manifest) and row.get('reused_from') and row['task']=='elements':
        if row['reused_from'] not in manifest.get('reuse_rows_from',[]):raise ValueError('reference_eval_reused_row_changed')
        original=reuse_elements(case,dict(manifest,reuse_rows_from=[row['reused_from']]))
        metadata={'template','human_confirmed','label_source','element_label_source','elapsed_s'}
        if original is None or any(row.get(key)!=value for key,value in original.items() if key not in metadata):
            raise ValueError('reference_eval_reused_row_changed')
        return
    if (manifest.get('protocol_version')=='v4' or manifest.get('evaluation_target')=='core_meaning_v2') and row['task'] in {'forecast','bgm'}:
        from .reference_core import verify_row
        return verify_row(row,case,manifest,folder)
    from .production_subject import verify_subject
    subject_value=p.read(row.get('subject_path',folder/'subject/subject.json'))
    verify_subject(subject_value)
    if (row['subject_sha256']!=subject_value['understanding_sha256'] or row['protocol_sha256']!=manifest['protocol_sha256'] or
            subject_value['binding']['reference_sha256']!=case['reference']['sha256']):
        raise ValueError('reference_eval_report_subject_changed')
    probes=Path(manifest['production'].get('probe_outputs',{}).get(case['id'],str(folder)))
    if row['task']=='story':
        saved=exam.read_probe(probes/'story')
        if saved['answer']!=row['answer']:
            raise ValueError('reference_eval_report_story_answer_changed')
        compiled=row.get('story_extraction')=='paragraph_ids_to_source_v1'
        metrics=exam.story_metrics(saved['answer'],manifest,case,compile_quotes=compiled)
        if compiled and row['compiled_excerpt']!=exam.compiled_story_excerpt(saved['answer'],manifest):
            raise ValueError('reference_eval_report_story_extraction_changed')
    else:
        receipt=row['receipt']
        if exam.text_judge(manifest):raw=exam.received_judgment(receipt)
        else:
            state_path=Path(receipt['path']); state=p.read(state_path)
            response_path=state_path.with_name('response.json');response=p.read(response_path)
            if (state['status']!='received' or sha256_file(state_path)!=receipt['state_sha256'] or
                    sha256_file(response_path)!=receipt['response_sha256'] or response['http_status']!=200):
                raise ValueError('reference_eval_report_judge_receipt_changed')
            raw=(exam.received_judgment(receipt) if manifest.get('protocol_version')=='v4' else
                json.loads(json.loads(response['body_text'])['choices'][0]['message']['content']))
        if row.get('judgment_compiler')=='candidate_unit_ids_v1':
            raw=exam.compile_element_ids(raw,subject_value['understanding'],len(case['labels']['elements']))
        elif row.get('judgment_compiler')=='forecast_event_ids_v1':
            saved=p.read(probes/'forecast/answer.json');exam.verify_probe(saved)
            raw=exam.compile_forecast_ids(raw,saved['answer'],case,receipt['packet'])
        if raw!=row['judgment']:
            raise ValueError('reference_eval_report_judgment_not_received_response')
        if row['task']=='elements':
            metrics=exam.validate_elements(raw,case,subject_value['understanding'],
                **(dict(judge_backend='glm_text') if exam.text_judge(manifest) else {}))
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
    parser.add_argument('--curation',type=Path)
    parser.add_argument('--receipts',type=Path)
    parser.add_argument('--experiment-id',default='production_reference_20261024_v1')
    parser.add_argument('--protocol-version',choices=['v4'])
    parser.add_argument('--prefix-policy',choices=['before_last_element'])
    parser.add_argument('--story-seed',type=int,default=0)
    parser.add_argument('--bgm-measurements',type=Path)
    parser.add_argument('--asr-profile',type=Path)
    parser.add_argument('--confirmations',type=Path)
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
