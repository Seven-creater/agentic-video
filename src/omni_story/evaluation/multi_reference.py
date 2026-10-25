"""Opt-in template understanding over independently collected support readings."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import time

from . import reference_protocol as p, reference_exam as exam
from .production_subject import verify_subject
from .subject_cache import SubjectCache
from ..library.media import sha256_file
from ..library.state import file_lock, json_sha, write_json


def support_assets(case,manifest):
    k=manifest['support_k']
    if type(k) is not int or k not in (1,3,5):raise ValueError('multi_reference_support_k_invalid')
    assets=case.get('support_assets')
    if not isinstance(assets,list) or len(assets)<k or assets[0]!=case['reference']:
        raise ValueError('multi_reference_support_assets_invalid')
    selected=assets[:k]
    for asset in selected:
        if (not isinstance(asset,dict) or any(not isinstance(asset.get(key),str) or not asset[key]
                for key in ('asset_id','sha256','path')) or asset['asset_id'] in ('.','..') or
                '/' in asset['asset_id'] or '\\' in asset['asset_id'] or
                type(asset.get('duration_s')) not in (int,float) or not 0<asset['duration_s']<float('inf')):
            raise ValueError('multi_reference_support_asset_invalid')
    if any(len({a[key] for a in selected})!=k for key in ('asset_id','sha256')):
        raise ValueError('multi_reference_duplicate_support')
    return selected


def validate_manifest(manifest,*,media=True):
    assets=[a for case in manifest['cases'] for a in support_assets(case,manifest)]
    if {a['sha256'] for a in assets}&{c['query']['sha256'] for c in manifest['cases']}:
        raise ValueError('reference_eval_unseen_video_leakage')
    if media and any(sha256_file(a['path'])!=a['sha256'] for a in assets):
        raise ValueError('reference_eval_original_media_changed')


def task_points(manifest,case):
    from .prefix_policy import before_last_element
    yield 'elements',None
    point=before_last_element(case['labels'],case['reference']['duration_s'])
    if point['cut_s'] is not None and not point['hidden_element_indices']:
        point=dict(point,skipped_reason='no_hidden_elements')
    yield 'forecast',dict(point,condition='forecast@before_last_element')
    extra=dict(point,condition='forecast@support_plus_prefix')
    if manifest['support_k']==1:extra['skipped_reason']='support_k_one'
    yield 'forecast',extra
    yield 'story',None
    yield 'bgm',None


def row_metadata(case,manifest,folder):
    return dict(support_k=manifest['support_k'],
        support_asset_ids=[a['asset_id'] for a in support_assets(case,manifest)],
        template_path=str(folder/'template/template.json'))


def text_manifest(manifest):
    return dict(manifest,protocol_version='v4',judge_backend='glm_text',prefix_policy='before_last_element',
        scoring_rule=manifest.get('scoring_rule','只按候选明确表达的主旨与价值方向逐项比较，不补写候选含义。'))


def production_module(manifest):
    path=Path(manifest['production']['production_root'])/'src/omni_story/library/reference_template.py'
    spec=importlib.util.spec_from_file_location('_evaluation_reference_template_'+sha256_file(path),path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module,sha256_file(path)


def collect_support(case,manifest,folder):
    from .reference_cli import subject,failure
    collected=[];errors=[]
    for index,asset in enumerate(support_assets(case,manifest)):
        directory=folder/'support'/asset['asset_id']
        directory.mkdir(parents=True,exist_ok=True)
        try:
            source_case=dict(case,id=case['id'] if index==0 else case['id']+'__'+asset['asset_id'],reference=asset)
            saved=subject(source_case,manifest,directory,cache_root=folder.parents[1]/'subject_cache')
            verify_subject(saved)
            if saved['manual_correction'] or saved['binding']['reference_sha256']!=asset['sha256']:
                raise ValueError('multi_reference_original_reading_required')
            collected.append(dict(asset_id=asset['asset_id'],asset_sha256=asset['sha256'],
                subject_path=saved['evidence_path'],subject_sha256=saved['understanding_sha256'],
                reading=saved['understanding']))
            status=dict(status='collected',original_output=str(Path(saved['evidence_path']).parent))
        except Exception as error:
            status=failure(error)
            ledger=directory/'subject/production_stage/library_state.json'
            if ledger.exists() and p.blocked(ledger):status['status']='blocked_unknown'
            status=dict(status,status='blocked_unknown' if status['status']=='blocked_unknown' else 'failed')
            errors.append(dict(asset_id=asset['asset_id'],**status))
        write_json(directory/'subject-status.json',status)
    if errors:
        return None,dict(status='blocked_unknown' if any(e['status']=='blocked_unknown' for e in errors) else 'failed',
            error=errors[0]['error'],support_errors=errors)
    return collected,None


def _source_receipts(sources):
    for source in sources:
        saved=p.read(source['subject_path']);verify_subject(saved)
        if (saved['manual_correction'] or saved['binding']['reference_sha256']!=source['asset_sha256'] or
                saved['understanding_sha256']!=source['subject_sha256'] or saved['understanding']!=source['reading']):
            raise ValueError('multi_reference_source_changed')


def _probe(question,directory):
    answer=exam.ask_probe(question,directory,allow_invalid=True)
    saved=p.read(directory/'answer.json');exam.verify_probe(saved)
    if saved['answer']!=answer or saved['identity']['question']!=question:
        raise ValueError('multi_reference_probe_binding_changed')
    return answer,saved


def _template_reply(module,prompt,sources,directory):
    ids=[s['asset_id'] for s in sources]
    question=dict(prompt_kind='template',instruction=prompt)
    raw,saved=_probe(question,directory/'summary')
    probes=[str(directory/'summary/answer.json')]
    try:
        return module.validate_template(raw,reading_ids=ids),probes,None
    except (ValueError,TypeError,KeyError) as error:
        defects=[str(error)]
    raw_text=saved.get('raw_text',json.dumps(raw,ensure_ascii=False))
    record=dict(original_sha256=exam._sha_text(raw_text),defects_before=defects,calls=0)
    if saved.get('stop_reason')=='max_tokens' and not raw_text.strip():
        write_json(directory/'repair.json',dict(record,defects_after=['empty_body_truncated']))
        raise ValueError('multi_reference_empty_body_truncated')
    record['calls']=1
    try:
        repaired,reply=_probe(dict(prompt_kind='template_repair',instruction=exam.REPAIR_PROMPT.format(
            defects='、'.join(defects),reply=raw_text),template_contract=prompt),directory/'summary_repair1')
        probes.append(str(directory/'summary_repair1/answer.json'))
        value=module.validate_template(repaired,reading_ids=ids)
        record.update(repaired_sha256=exam._sha_text(reply.get('raw_text',json.dumps(repaired,ensure_ascii=False))),
            defects_after=[])
    except Exception as error:
        write_json(directory/'repair.json',dict(record,defects_after=[str(error)]))
        raise
    write_json(directory/'repair.json',record)
    return value,probes,record


def summarize(case,manifest,folder,sources,*,other_only=False):
    module,module_sha=production_module(manifest)
    directory=folder/('support-template' if other_only else 'template')
    directory.mkdir(parents=True,exist_ok=True)
    readings=[s['reading'] for s in sources];ids=[s['asset_id'] for s in sources]
    prompt=module.template_prompt(readings,reading_ids=ids) if len(sources)>1 else None
    binding=dict(support_k=manifest['support_k'],support_sha256s=[s['asset_sha256'] for s in sources],
        reading_sha256s=[s['subject_sha256'] for s in sources],reading_ids=ids,
        production_release=json_sha(manifest['production']['production_files']),module_sha256=module_sha,
        prompt_sha256=json_sha(prompt),condition='support_without_reference' if other_only else 'support_full')
    fields=dict(media_sha256=json_sha(dict(k=binding['support_k'],support_sha256s=binding['support_sha256s'])),
        condition=binding['condition'],production_release=binding['production_release'],prompt_sha256=json_sha(binding))
    cache=SubjectCache(folder.parents[1]/'subject_cache')
    with file_lock(directory/'.template.lock',wait=True):
        path=directory/'template-record.json'
        record=p.read(path) if path.exists() else cache.get(fields)
        if record is not None:
            if record['binding']!=binding:raise ValueError('multi_reference_template_binding_changed')
            verify_template(record,manifest)
            if path.exists() and p.read(directory/'template.json')!=record['template']:
                raise ValueError('multi_reference_template_changed')
        else:
            if len(sources)==1:
                value=module.single_reading_as_template(readings[0]);probes=[];repair=None
                if not isinstance(value,dict):raise ValueError('multi_reference_single_template_invalid')
            else:value,probes,repair=_template_reply(module,prompt,sources,directory)
            record=dict(binding=binding,template=value,template_sha256=json_sha(value),sources=sources,
                probe_paths=probes,repair=repair)
            verify_template(record,manifest)
            cache.put(fields,record,[s['subject_path'] for s in sources]+probes)
        write_json(directory/'template.json',record['template'])
        write_json(path,record)
    return record


def verify_template(record,manifest):
    module,module_sha=production_module(manifest)
    sources=record['sources'];binding=record['binding'];probes=record['probe_paths']
    other=binding['condition']=='support_without_reference'
    if (binding['condition'] not in ('support_full','support_without_reference') or
            binding['support_k']!=manifest['support_k'] or len(sources)!=manifest['support_k']-int(other) or
            binding['support_sha256s']!=[s['asset_sha256'] for s in sources] or
            binding['reading_sha256s']!=[s['subject_sha256'] for s in sources] or
            binding['reading_ids']!=[s['asset_id'] for s in sources] or
            (len(sources)==1 and probes) or (len(sources)>1 and len(probes) not in (1,2))):
        raise ValueError('multi_reference_template_binding_changed')
    if (record['binding']['module_sha256']!=module_sha or
            record['binding']['production_release']!=json_sha(manifest['production']['production_files']) or
            record['template_sha256']!=json_sha(record['template'])):
        raise ValueError('multi_reference_template_changed')
    _source_receipts(sources)
    prompt=module.template_prompt([s['reading'] for s in sources],reading_ids=[s['asset_id'] for s in sources]) if len(sources)>1 else None
    if binding['prompt_sha256']!=json_sha(prompt):raise ValueError('multi_reference_template_prompt_changed')
    if not probes:
        expected=module.single_reading_as_template(sources[0]['reading'])
    else:
        original=p.read(probes[0]);exam.verify_probe(original)
        if original['identity']['question']!=dict(prompt_kind='template',instruction=prompt):
            raise ValueError('multi_reference_template_prompt_changed')
        received=original
        if len(probes)==2:
            received=p.read(probes[1]);exam.verify_probe(received)
            instruction=exam.REPAIR_PROMPT.format(defects='、'.join(record['repair']['defects_before']),
                reply=original.get('raw_text',json.dumps(original['answer'],ensure_ascii=False)))
            if received['identity']['question']!=dict(prompt_kind='template_repair',instruction=instruction,template_contract=prompt):
                raise ValueError('multi_reference_template_repair_changed')
        expected=module.validate_template(received['answer'],reading_ids=[s['asset_id'] for s in sources])
    if expected!=record['template']:raise ValueError('multi_reference_template_not_received')


def _prefix_question(template,reading,cut):
    return dict(prompt_kind='support_plus_prefix',template=template,prefix_understanding=reading,
        visible_prefix_s=cut,question='推测这段视频后面要表达的价值/结局。',
        instruction='只依据其他参考的模板与本视频已见前缀；区分推测和事实，保留不确定性，不声称见过后续。',
        response_contract=dict(prediction='价值或结局推测',uncertainties=[]))


def normalize_prefix_answer(answer):
    if not isinstance(answer,dict):raise ValueError('multi_reference_prefix_answer_invalid')
    prediction=answer.get('prediction')
    if isinstance(prediction,(dict,list)):
        answer=dict(answer,prediction_raw=prediction,
            prediction='\n'.join(exam.strings(prediction)))
    if not isinstance(answer.get('prediction'),str) or not answer['prediction'].strip():
        raise ValueError('multi_reference_prefix_answer_invalid')
    return answer


def prefix_reading(case,manifest,folder,point,reading):
    if point['condition']!='forecast@support_plus_prefix':return reading,{}
    record=p.read(folder/'template/template-record.json')
    other=summarize(case,manifest,folder,record['sources'][1:],other_only=True)
    question=_prefix_question(other['template'],reading,point['cut_s'])
    answer,_=_probe(question,folder/'support-plus-prefix')
    answer=normalize_prefix_answer(answer)
    return answer,dict(answer=answer,answer_path=str(folder/'support-plus-prefix/answer.json'),
        support_template_path=str(folder/'support-template/template.json'),
        support_template_sha256=other['template_sha256'])


def verify_prefix_answer(row,case,manifest,folder,reading):
    if row['condition']!='forecast@support_plus_prefix':return reading
    record=p.read(folder/'support-template/template-record.json');verify_template(record,manifest)
    if ([s['asset_sha256'] for s in record['sources']]!=[a['sha256'] for a in support_assets(case,manifest)[1:]] or
            row['support_template_path']!=str(folder/'support-template/template.json') or
            p.read(row['support_template_path'])!=record['template'] or
            row['support_template_sha256']!=record['template_sha256'] or
            row['answer_path']!=str(folder/'support-plus-prefix/answer.json')):
        raise ValueError('multi_reference_prefix_support_changed')
    saved=p.read(row['answer_path']);exam.verify_probe(saved)
    if (saved['identity']['question']!=_prefix_question(record['template'],reading,row['prefix_cut_s']) or
            normalize_prefix_answer(saved['answer'])!=row['answer']):
        raise ValueError('multi_reference_prefix_answer_changed')
    return normalize_prefix_answer(saved['answer'])


def task(case,manifest,output):
    from .reference_cli import code_identity,failure,reuse_failed_rows
    from .reference_core import prefix_task
    folder=output/'cases'/case['id'];folder.mkdir(parents=True,exist_ok=True)
    with file_lock(folder/'.case.lock',wait=True):
        identity=dict(protocol_sha256=manifest['protocol_sha256'],code_sha256=code_identity(),case_id=case['id'])
        pin=folder/'identity.json'
        if pin.exists() and p.read(pin)!=identity:raise ValueError('reference_eval_locked_case_changed')
        write_json(pin,identity)
        reuse_failed_rows(manifest,output,case=case)
        pending=[(name,point) for name,point in task_points(manifest,case)
            if name!='bgm' and not p.row_path(folder,name,point).exists()]
        if not pending:return
        sources=None;record=None;error=None
        if 'preparation_error' not in case['labels']:
            sources,error=collect_support(case,manifest,folder)
            if sources is not None:
                try:record=summarize(case,manifest,folder,sources)
                except Exception as exception:error=failure(exception)
        original=folder/'support'/support_assets(case,manifest)[0]['asset_id']/'subject'
        write_json(folder/'subject-status.json',dict(status='collected' if sources else (error or {}).get('status','blocked_question'),
            original_output=str(original),error=(error or {}).get('error')))
        runtime=text_manifest(manifest)
        receipts=Path(manifest['receipts'])/'grading'/case['id']
        for name,point in pending:
            row=dict(identity,task=name,label_source=case['labels'].get('provenance','model_assisted_unconfirmed'),
                judge_backend='glm_text',**row_metadata(case,manifest,folder))
            if point:row.update(condition=point['condition'],prefix_cut_s=point['cut_s'])
            started=time.time()
            try:
                if point and point.get('skipped_reason'):
                    row.update(status='not_applicable',skipped_reason=point['skipped_reason'])
                elif 'preparation_error' in case['labels'] or name+'_preparation_error' in case['labels']:
                    row.update(status='blocked_question',reason=case['labels'].get('preparation_error',case['labels'].get(name+'_preparation_error')))
                elif error:row.update(error)
                elif name=='forecast':row.update(prefix_task(case,runtime,folder,receipts,point=point))
                else:
                    reading=record['template']
                    row.update(subject_path=str(folder/'template/template.json'),subject_sha256=record['template_sha256'])
                    if name=='elements':
                        raw,receipt=exam.checked_review([],{},exam.CORE_ELEMENT_PROMPT,
                            dict(candidate_units=exam.candidate_units(reading),scoring_rule=runtime['scoring_rule'],
                                gold_rubric=[dict(id=f'g{i:03d}',text=t) for i,t in enumerate(case['labels']['elements'])]),
                            receipts/'elements_ids_v1',folder,count=len(case['labels']['elements']),judge_backend='glm_text')
                        judgment=exam.compile_element_ids(raw,reading,len(case['labels']['elements']))
                        row.update(judgment=judgment,receipt=receipt,
                            metrics=exam.validate_elements(judgment,case,reading,judge_backend='glm_text'))
                    else:
                        question=p.public_question(dict(case,evaluation_target='core_meaning_v2'),name,reading,p.story_inputs(manifest,case['id']))
                        answer=exam.ask_probe(question,folder/'story')
                        row.update(answer=answer,judgment=None,receipt=None,
                            metrics=exam.story_metrics(answer,manifest,case,compile_quotes=True),
                            story_extraction='paragraph_ids_to_source_v1',compiled_excerpt=exam.compiled_story_excerpt(answer,manifest))
                    row['status']='scored_provisional'
            except Exception as exception:row.update(failure(exception))
            row['elapsed_s']=time.time()-started
            write_json(p.row_path(folder,name,point),row)


def verify_row(row,case,manifest,folder):
    if any(row.get(key)!=value for key,value in row_metadata(case,manifest,folder).items()):
        raise ValueError('multi_reference_row_binding_changed')
    record=p.read(folder/'template/template-record.json');verify_template(record,manifest)
    if (p.read(row['template_path'])!=record['template'] or
            [s['asset_sha256'] for s in record['sources']]!=[a['sha256'] for a in support_assets(case,manifest)] or
            row['protocol_sha256']!=manifest['protocol_sha256'] or row.get('judge_backend')!='glm_text'):
        raise ValueError('multi_reference_row_template_changed')
    if row['task']=='forecast':
        from .reference_core import verify_row as verify_prefix
        return verify_prefix(row,case,text_manifest(manifest),folder)
    reading=record['template']
    if row['subject_path']!=row['template_path'] or row['subject_sha256']!=record['template_sha256']:
        raise ValueError('multi_reference_subject_changed')
    if row['task']=='story':
        saved=exam.read_probe(folder/'story')
        question=p.public_question(dict(case,evaluation_target='core_meaning_v2'),'story',reading,p.story_inputs(manifest,case['id']))
        if saved['answer']!=row['answer'] or saved['identity']['question']!=question:
            raise ValueError('multi_reference_story_answer_changed')
        metrics=exam.story_metrics(saved['answer'],manifest,case,compile_quotes=True)
        if row['compiled_excerpt']!=exam.compiled_story_excerpt(saved['answer'],manifest):
            raise ValueError('reference_eval_report_story_extraction_changed')
    else:
        raw=exam.received_judgment(row['receipt'])
        judgment=exam.compile_element_ids(raw,reading,len(case['labels']['elements']))
        if judgment!=row['judgment']:raise ValueError('multi_reference_judgment_changed')
        metrics=exam.validate_elements(judgment,case,reading,judge_backend='glm_text')
    if metrics!=row['metrics']:raise ValueError('reference_eval_report_metrics_changed')


def summarize_report(report):
    groups={}
    for k in sorted({r['support_k'] for r in report['rows'] if 'support_k' in r}):
        tasks={}
        for name in p.TASKS[:3]:
            chosen=[r for r in report['rows'] if r.get('support_k')==k and r['task']==name]
            def summary(rows):
                scored=[r for r in rows if r['status']=='scored_provisional']
                keys={key for r in scored for key,value in r['metrics'].items() if type(value) in (int,float,bool)}
                means={key:sum(values)/len(values) for key in keys
                    if (values:=[r['metrics'][key] for r in scored if type(r['metrics'].get(key)) in (int,float,bool)])}
                return dict(planned=len(rows),scored=len(scored),means_scored=means,
                    statuses={s:sum(r['status']==s for r in rows) for s in sorted({r['status'] for r in rows})})
            tasks[name]=summary(chosen)
            if name=='forecast':tasks[name]['conditions']={condition:summary([r for r in chosen if r.get('condition')==condition])
                for condition in sorted({r['condition'] for r in chosen if 'condition' in r})}
        groups[str(k)]=tasks
    return groups


def update_report(manifest,result):
    result.update(support_k=manifest['support_k'],support_k_groups=summarize_report(result),report_scope='template',
        judge_backend='glm_text',note='按 support_k 分组报告模板级理解；预测分别保留单视频前缀与其他参考加前缀条件。')


def report_text(result):
    text=['# 多参考模板理解评测','','| k | 题型／条件 | 有效／计划 | 有效项均值 |','|---:|---|---:|---|']
    for k,tasks in result['support_k_groups'].items():
        for task,group in tasks.items():
            for name,values in (group.get('conditions') or {task:group}).items():
                text.append(f"| {k} | {name} | {values['scored']}/{values['planned']} | {json.dumps(values['means_scored'],ensure_ascii=False)} |")
    text+=['','逐题状态与原因：','']
    for row in result['rows']:
        text.append(f"- {row['template']}／{row.get('condition',row['task'])}：{row['status']}；"+
            json.dumps(row.get('metrics',row.get('support_errors',row.get('reason',row.get('error',row.get('skipped_reason',''))))),ensure_ascii=False))
    return text+['']
