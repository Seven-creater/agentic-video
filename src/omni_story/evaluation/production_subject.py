"""External observer for the frozen production reference stage.

Executed as a script so imports resolve to the pinned production release, not
the evaluation checkout. No production prompt or implementation is copied.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


RETRY_FIELDS = ('retry_of', 'attempt_id', 'adjudication')


def _adjudicate():
    if __package__:
        from . import adjudicate
        return adjudicate
    import importlib.util
    spec=importlib.util.spec_from_file_location('omni_story.evaluation.adjudicate',Path(__file__).with_name('adjudicate.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def _adjudications(ledger):
    return [row for row in _adjudicate().read_adjudications(ledger)
        if row.get('decided_by')=='user' and row.get('decision')=='abandoned_by_user']


class ReferenceCollected(BaseException):
    """A successful component boundary, never a completed video job."""


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify_tree(root, files):
    for relative, expected in files.items():
        if digest(Path(root) / relative) != expected:
            raise ValueError('production_freeze_changed:' + relative)


def _reference_outputs(run):
    paths=run.get('existing_reference_outputs',[])
    if isinstance(paths,dict):paths=paths.get(run.get('case_id'),[])
    return [paths] if isinstance(paths,str) else paths or []


def _guard_original_calls(run, paths):
    case_id=run.get('case_id')
    failure=run.get('subject_failures',{}).get(case_id,{})
    originals=[run['output'],*paths]
    for path in (run.get('subject_outputs',{}).get(case_id),failure.get('original_output')):
        if path:originals.append(path)
    retry=None;decisions=[];failure_adjudicated=False
    for original in originals:
        path=Path(original)
        if path.suffix=='.json':path=path.parent
        for ledger in (path/'production_stage/library_state.json',path/'library_state.json',
                path/'production_stage/state.json',path/'state.json'):
            if not ledger.is_file():continue
            state=json.loads(ledger.read_text())
            if state.get('input_lock',{}).get('reference_sha256',run['reference_sha256'])!=run['reference_sha256']:continue
            rows=_adjudications(ledger);decisions.extend(rows)
            calls=[call for call in state.get('calls',[]) if call['status'] in {'submitted','uncertain'}]
            if any(_adjudicate().is_blocked(call,rows) for call in calls):
                raise RuntimeError('subject_original_unknown_no_replay')
            if calls:
                if retry is None:retry=(ledger,calls[0])
                failure_adjudicated=True
    if ((failure.get('status') in {'blocked_unknown','submitted','uncertain'} and not failure_adjudicated) or
            any(_adjudicate().is_blocked(call,decisions) for call in failure.get('calls',[]))):
        raise RuntimeError('subject_original_unknown_no_replay')
    return retry


def _diagnose(diagnosis, source, error):
    entry=dict(source=str(source),error_type=type(error).__name__,reason=str(error))
    if entry not in diagnosis:diagnosis.append(entry)


def _subject_sources(path):
    path=Path(path)
    if path.name=='subject.json':return [path]
    paths=[path/'subject.json']
    if path.name=='production_stage':paths.append(path.parent/'subject.json')
    return paths


def _historical_subject(run, reading_path, binding):
    from omni_story.library.media import sha256_file
    from omni_story.library.state import json_sha
    reading=json.loads(reading_path.read_text())
    if reading.get('reference_sha256')!=run['reference_sha256']:return None
    state_path=reading_path.parent/'library_state.json'
    if not state_path.is_file():return None
    state=json.loads(state_path.read_text())
    if state['input_lock'].get('reference_sha256')!=run['reference_sha256']:return None
    for call in reversed(state['calls']):
        folder=state_path.parent/'calls'/call['id'];parsed=folder/'parsed.json'
        if call['name'] not in {'reference','reference_repair'} or call['status']!='received' or not parsed.exists():continue
        value=json.loads(parsed.read_text())
        request=json.loads((folder/'request.json').read_text());media=request['arguments']['video_source']
        saved=dict(schema_version='production_reference_subject_v1',binding=binding,
            understanding=value,understanding_sha256=json_sha(value),call_id=call['id'],
            request_sha256=call['request_sha256'],response_sha256=call['response_sha256'],
            artifacts={str(folder/name):sha256_file(folder/name) for name in ('request.json','response.json','parsed.json')},
            media_path=media,media_sha256=request['media_sha256'],archived_media_retired=not Path(media).is_file(),
            manual_correction=any(reading.get(k)!=v for k,v in value.items()),
            historical_reading_path=str(reading_path),status='reference_collected',
            note='Reused original received reference return; later manual changes are flagged separately.')
        verify_subject(saved)
        return saved


def _existing_subject(run, paths, diagnosis):
    binding={k:run[k] for k in ('reference','reference_sha256','production_root',
        'production_files','configuration','asr_model_dir')}
    for original in paths:
        path=Path(original)
        sources=[] if path.suffix=='.json' and path.name!='subject.json' else _subject_sources(path)
        for source in sources:
            try:
                saved=json.loads(source.read_text())
                if saved['binding']['reference_sha256']!=run['reference_sha256']:
                    raise ValueError('subject_reference_changed')
                if saved['binding']['production_files']!=run['production_files']:
                    raise ValueError('subject_production_changed')
                verify_subject(saved)
                return saved,str(original)
            except (OSError,ValueError,KeyError,TypeError,AttributeError,StopIteration) as error:
                _diagnose(diagnosis,source,error)
        reading=(path if path.suffix=='.json' else path/'reference_reading.json' if path.name=='production_stage'
            else path/'production_stage/reference_reading.json')
        if reading.name=='subject.json' or not reading.is_file():continue
        try:
            saved=_historical_subject(run,reading,binding)
            if saved is not None:return saved,str(original)
        except (OSError,ValueError,KeyError,TypeError,AttributeError,StopIteration) as error:
            _diagnose(diagnosis,reading,error)
    return None,None


def _registered_output(error):
    prefix='same_inputs_already_registered_use_original_output:'
    text=str(error)
    if not text.startswith(prefix):return None
    path=text[len(prefix):].strip()
    if not path or any(ord(c)<32 for c in path):return None
    original=Path(path)
    return original if original.is_absolute() and not original.is_file() else None


def _registry_pending(original):
    statuses=set()
    for name in ('library_state.json','state.json'):
        path=original/name
        if not path.is_file():continue
        state=json.loads(path.read_text())
        calls=state.get('calls',[])
        if not isinstance(calls,list):raise ValueError('subject_registry_calls_invalid')
        rows=_adjudications(path)
        statuses.update(call['status'] for call in calls
            if call['status'] not in {'submitted','uncertain'} or _adjudicate().is_blocked(call,rows))
    if statuses & {'submitted','uncertain'}:return True
    if statuses-{'received','failed_known'}:raise ValueError('subject_registry_call_status_invalid')
    return False


def _guard_retry_calls(stage):
    for name in ('library_state.json','state.json'):
        path=Path(stage)/name
        if path.is_file() and any(c['status'] in {'submitted','uncertain'}
                for c in json.loads(path.read_text()).get('calls',[])):
            raise RuntimeError('subject_retry_unknown_no_replay:'+str(stage))


def _adjudicated_retry(run, ledger, call, provider_factory, diagnosis, metadata):
    from omni_story.library.state import file_lock, write_json, json_sha, LibraryStopped
    ledger=Path(ledger);original=ledger.parent.resolve()
    root=Path(run['registry_path']).resolve().parent/'adjudicated_retries'
    if root.is_relative_to(original):raise ValueError('subject_retry_would_modify_original')
    key=json_sha(str(ledger.resolve()));reservations=root/'.attempts'
    plan=reservations/(key+'.retry.json')
    with file_lock(reservations/'attempts.lock',wait=True):
        if plan.exists():
            entry=json.loads(plan.read_text())
        else:
            rows=_adjudications(ledger)
            row=next(row for row in rows if row['call_id']==call['id'])
            # record_attempt appends beside its ledger; reserve against an immutable
            # snapshot so neither the original ledger nor its adjudications change.
            snapshot=reservations/(key+'.ledger.json')
            snapshot.write_bytes(ledger.read_bytes())
            log=reservations/'adjudications.jsonl'
            previous=_adjudicate().read_adjudications(snapshot)
            with log.open('a',encoding='utf-8') as handle:
                for decision in _adjudicate().read_adjudications(ledger):
                    if decision not in previous:handle.write(json.dumps(decision,ensure_ascii=False)+'\n')
            attempt=_adjudicate().record_attempt(snapshot,call['id'])
            stage=Path(run['output']).resolve()/('production_stage_'+attempt)
            entry=dict(retry_of=str(original),attempt_id=attempt,adjudication=row,
                output=str(stage),registry_path=str(root/(attempt+'.registry.json')),started=False)
            write_json(plan,entry)
    metadata.update({k:entry[k] for k in RETRY_FIELDS})
    result=Path(run['output']).resolve()/'subject.json'
    try:
        with file_lock(plan.with_suffix('.lock'),wait=True):
            entry=json.loads(plan.read_text());stage=Path(entry['output'])
            _guard_retry_calls(stage)
            saved,_=_existing_subject(run,[stage],diagnosis)
            if saved is None:
                if entry['started']:raise RuntimeError('subject_retry_unknown_no_replay:'+str(stage))
                entry['started']=True;write_json(plan,entry)
                retried=dict(run,output=str(stage),registry_path=entry['registry_path'],
                    allow_unregistered_reference=False,reuse_only=False,existing_reference_outputs=[],
                    **{k:entry[k] for k in RETRY_FIELDS})
                try:
                    saved=_capture_reference(retried,provider_factory=provider_factory,production_output=stage)
                except LibraryStopped as error:
                    if _registered_output(error) is not None:
                        raise RuntimeError('subject_retry_unknown_no_replay:'+str(stage)) from error
                    raise
            verify_subject(saved)
            saved=dict(saved,**{k:entry[k] for k in RETRY_FIELDS})
            write_json(stage/'subject.json',saved)
            write_json(result,saved)
            return saved
    except RuntimeError as error:
        if any(word in str(error) for word in ('unknown','uncertain','no_replay')):
            metadata['status']='blocked_unknown'
        raise
    finally:
        write_json(result.with_name('reuse.json'),metadata)


def capture_reference(run, *, provider_factory=None):
    if not run.get('subject_cache_root'):
        from omni_story.library.state import LibraryStopped
        try:
            return _capture_reference(run, provider_factory=provider_factory)
        except LibraryStopped as error:
            original=_registered_output(error)
            if original is None:raise
            retry=_guard_original_calls(run,[original])
            if retry is None:raise
            return _adjudicated_retry(run,*retry,provider_factory,[],{})
    if __package__:
        from .subject_cache import SubjectCache
    else:
        from subject_cache import SubjectCache
    from omni_story.library import pipeline
    from omni_story.library.media import sha256_file
    from omni_story.library.state import LibraryStopped, write_json, json_sha

    root = Path(run['production_root']).resolve(strict=True)
    verify_tree(root / 'src', run['production_files'])
    if not Path(pipeline.__file__).resolve().is_relative_to(root):
        raise ValueError('subject_imported_from_wrong_release')
    if sha256_file(run['reference']) != run['reference_sha256']:
        raise ValueError('subject_reference_changed')
    # Frozen source and prompt inputs identify the rendered production prompt
    # without copying or executing its reference stage on a cache lookup.
    cache = SubjectCache(run['subject_cache_root'],
        production_release=run.get('production_release') or json_sha(run['production_files']),
        prompt_sha256=run.get('prompt_sha256') or json_sha(dict(
            production_files=run['production_files'], configuration=run['configuration'],
            asr=run['asr'], asr_model_dir=run['asr_model_dir'])))
    fields = dict(media_sha256=run['reference_sha256'], condition=run.get('condition', 'full'),
                  production_release=cache.production_release, prompt_sha256=cache.prompt_sha256)
    result = Path(run['output']).resolve() / 'subject.json'
    binding={k:run[k] for k in ('reference','reference_sha256','production_root',
        'production_files','configuration','asr_model_dir')}
    diagnosis=[];metadata={};source=cache.root;saved=None
    try:
        value=cache.get(fields)
        if value is not None:
            source=next((p for p in cache.receipt_paths(fields) if Path(p).name=='subject.json'),None)
            if source is None:raise ValueError('subject_cache_corrupted')
            saved=json.loads(Path(source).read_text())
            if saved['understanding']!=value:raise ValueError('subject_cache_corrupted')
            verify_subject(saved)
            if Path(source).resolve()!=result:metadata['reused_from']=str(source)
        else:
            saved,source=_existing_subject(run,_reference_outputs(run),diagnosis)
            if saved is not None:metadata['reused_from']=source
            if saved is None:
                source=result
                try:
                    saved=_capture_reference(run,provider_factory=provider_factory)
                except LibraryStopped as error:
                    original=_registered_output(error)
                    if original is None:raise
                    metadata['registry_error']=str(error)
                    saved,_=_existing_subject(run,[original],diagnosis)
                    if saved is not None:
                        metadata['reused_from']=str(original)
                    else:
                        try:
                            pending=_registry_pending(original)
                        except (OSError,ValueError,KeyError,TypeError,AttributeError) as ledger_error:
                            _diagnose(diagnosis,original,ledger_error)
                            raise error
                        if pending:
                            metadata['status']='blocked_unknown'
                            raise RuntimeError('subject_original_unknown_no_replay:'+str(original)) from error
                        retry=_guard_original_calls(run,[original])
                        if retry is not None:
                            saved=_adjudicated_retry(run,*retry,provider_factory,diagnosis,metadata)
                        else:
                            if any(p.is_file() for p in [*_subject_sources(original),original/'reference_reading.json']):raise
                            resumed=_original_inputs(run,original)
                            metadata['resumed_in']=str(original)
                            metadata['resumed_reference']=resumed['reference']
                            saved=_capture_reference(resumed,provider_factory=provider_factory,production_output=original)
                            # The resumed subject is bound to the original catalogued reference path.
                            binding={k:resumed[k] for k in binding}
        source=source or result
        verify_subject(saved)
        saved=dict(saved,binding=binding)
        metadata.update({k:saved[k] for k in RETRY_FIELDS if k in saved})
        if result.exists():
            current=json.loads(result.read_text())
            if current['binding']!=binding:raise ValueError('subject_output_already_bound')
            verify_subject(current)
            if current['understanding']!=saved['understanding']:raise ValueError('subject_cache_conflict')
            saved=current
        else:write_json(result,saved)
        cache.put(fields,saved['understanding'],[result,*saved['artifacts']])
        return saved
    except RuntimeError as error:
        if any(word in str(error) for word in ('unknown','uncertain','no_replay')):
            metadata['status']='blocked_unknown'
            if isinstance(saved,dict):metadata.update({k:saved[k] for k in RETRY_FIELDS if k in saved})
        raise
    except (OSError,ValueError,KeyError,TypeError,AttributeError,StopIteration) as error:
        _diagnose(diagnosis,source or result,error)
        raise
    finally:
        reuse=result.with_name('reuse.json')
        if reuse.is_file():
            previous=json.loads(reuse.read_text())
            if 'attempt_id' in previous:
                for k in (*RETRY_FIELDS,'status'):
                    if k in previous:metadata.setdefault(k,previous[k])
        if diagnosis:metadata['reuse_diagnosis']=diagnosis
        if metadata:write_json(result.with_name('reuse.json'),metadata)


def _original_inputs(run, original):
    """Resume with the exact reference path the original folder catalogued; bytes must be identical."""
    catalog = Path(original)/'reference_catalog'/'inventory.json'
    if not catalog.is_file():
        return run
    sources = json.loads(catalog.read_text())['sources']
    if len(sources) != 1:
        raise ValueError('subject_resume_reference_catalog_invalid')
    path = Path(sources[0]['path'])
    if not path.is_file() or digest(path) != run['reference_sha256']:
        raise ValueError('subject_resume_original_reference_changed')
    return dict(run, reference=str(path))


def _capture_reference(run, *, provider_factory=None, production_output=None):
    from omni_story.library import pipeline, clean_chain
    from omni_story.library.media import sha256_file, verify_source
    from omni_story.library.state import write_json, json_sha

    root = Path(run['production_root']).resolve(strict=True)
    verify_tree(root / 'src', run['production_files'])
    if not Path(pipeline.__file__).resolve().is_relative_to(root):
        raise ValueError('subject_imported_from_wrong_release')
    output = Path(run['output']).resolve()
    stage = Path(production_output) if production_output is not None else output/'production_stage'
    output.mkdir(parents=True, exist_ok=True)
    result = output / 'subject.json'
    binding = {k: run[k] for k in ('reference', 'reference_sha256', 'production_root',
                                  'production_files', 'configuration', 'asr_model_dir')}
    if sha256_file(run['reference']) != run['reference_sha256']:
        raise ValueError('subject_reference_changed')
    if result.exists():
        saved = json.loads(result.read_text())
        if saved['binding'] != binding:
            raise ValueError('subject_output_already_bound')
        verify_subject(saved)
        return saved
    allow_new=run.get('allow_unregistered_reference',False)
    paths=_reference_outputs(run) if allow_new else run.get('existing_reference_outputs',[])
    registered=allow_new and isinstance(run.get('existing_reference_outputs'),dict) and bool(paths)
    subject_paths=set()
    if allow_new:
        for original in paths:
            path=Path(original)
            if path.suffix=='.json' and path.name!='subject.json':continue
            subject_paths.add(original)
            source=path if path.name=='subject.json' else path/'subject.json'
            try:
                saved=json.loads(source.read_text())
                if saved['binding']['reference_sha256']!=run['reference_sha256']:
                    if not isinstance(run.get('existing_reference_outputs'),dict):continue
                    raise ValueError('subject_reference_changed')
                registered=True
                if saved['binding']['production_files']!=run['production_files']:
                    raise ValueError('subject_production_changed')
                verify_subject(saved)
            except (OSError,ValueError,KeyError,TypeError,AttributeError,StopIteration):
                registered=True
                continue
            saved=dict(saved,binding=binding)
            write_json(result,saved)
            return saved
    for reading_path in paths:
        if reading_path in subject_paths:continue
        reading_path = Path(reading_path)
        if not reading_path.is_file():
            continue
        reading = json.loads(reading_path.read_text())
        if reading.get('reference_sha256') != run['reference_sha256']:
            continue  # The small reliable_v1 schema is a different subject.
        registered=True
        saved=_historical_subject(run,reading_path,binding)
        if saved is not None:
            write_json(result,saved)
            return saved
    if allow_new:
        retry=_guard_original_calls(run,paths)
        if retry is not None:
            return _adjudicated_retry(run,*retry,provider_factory,[],{})
    if production_output is None and run.get('reuse_only') and (not allow_new or registered):
        raise ValueError('subject_reuse_only_no_completed_result')
    cached=run.get('library_catalog')
    catalog_path=stage/'catalog/inventory.json'
    if cached and not catalog_path.exists():
        if digest(cached['path'])!=cached['sha256']:
            raise ValueError('subject_shared_library_catalog_changed')
        catalog=json.loads(Path(cached['path']).read_text())
        for source in catalog['sources']:
            verify_source(source)
        # Reuse production's native inventory checkpoint. Its original _catalog
        # still checks the complete source set; no prompt/input is rewritten.
        write_json(catalog_path,catalog)

    def factory(state):
        if run.get('external_budget_gate'):
            original_begin=state.begin_call
            def budgeted_begin(name,request,**kwargs):
                identity=dict(input_lock=state.input_lock,request=request)
                if run.get('attempt_id'):identity['attempt_id']=run['attempt_id']
                key=json_sha(identity)
                completed=subprocess.run([*run['external_budget_gate'],'--key',key,'--evidence',str(state.path),
                    '--provider','glm_coding_plan'],text=True,capture_output=True,timeout=60)
                if completed.returncode:
                    raise RuntimeError('subject_budget_gate_failed:'+completed.stderr[-1200:])
                return original_begin(name,request,**kwargs)
            state.begin_call=budgeted_begin
        elif provider_factory is None and (allow_new or run.get('attempt_id') or os.environ.get('OMNI_EVAL_BUDGET_FILE')):
            raise ValueError('old_production_requires_external_budget_gate')
        if provider_factory is None:
            from omni_story.library.opencode_provider import OpenCodeMCP
            delegate = OpenCodeMCP(state, package_root=run['package_root'],
                executable=run['executable'], exclusions=run['exclusions'])
        else:
            delegate = provider_factory(state)

        class Observer:
            exclusions = delegate.exclusions

            def call(self, name, prompt, media, validator, **kwargs):
                if name != 'reference':
                    raise ValueError('evaluation_attempted_post_reference_stage:' + name)
                value = delegate.call(name, prompt, media, validator, **kwargs)
                state._reload()
                matches = []
                for call in state.data['calls']:
                    folder = state.output / 'calls' / call['id']
                    parsed = folder / 'parsed.json'
                    if (call['name'] in {'reference', 'reference_repair'} and
                            call['status'] == 'received' and parsed.exists() and
                            json.loads(parsed.read_text()) == value):
                        matches.append((call, folder))
                if not matches:
                    raise ValueError('subject_original_received_reply_required')
                call, folder = matches[-1]
                artifacts = {str(folder / name): sha256_file(folder / name)
                             for name in ('request.json', 'response.json', 'parsed.json')}
                saved = dict(schema_version='production_reference_subject_v1', binding=binding,
                    understanding=value, understanding_sha256=json_sha(value),
                    call_id=call['id'], request_sha256=call['request_sha256'],
                    response_sha256=call['response_sha256'], artifacts=artifacts,
                    media_path=str(media), media_sha256=sha256_file(media),
                    manual_correction=False, status='reference_collected',
                    collection_runtime=dict(executable=run.get('executable','injected_provider_factory'),adapter=run.get('execution_adapter','original_opencode')),
                    note='Original validated reference return; no retrieval, plan, TTS or render executed.')
                saved.update({k:run[k] for k in RETRY_FIELDS if k in run})
                write_json(result, saved)
                raise ReferenceCollected()

        return Observer()

    try:
        if run.get('isolate_asr'):
            import importlib.util
            spec=importlib.util.spec_from_file_location('reference_isolated_asr',Path(__file__).with_name('isolated_asr.py'))
            module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
            module.install(output/'isolated_asr',root)
        pipeline.execute(run['reference'], run['library'], stage,
            frames=18, span_s=600, max_fine=None, max_requests=None, asr=run['asr'],
            model_factory=factory, provider_config=run['configuration'],
            registry_path=run['registry_path'], prompt_module=clean_chain.ROUGH_PROMPTS,
            asr_model_dir=run['asr_model_dir'])
    except ReferenceCollected:
        saved = json.loads(result.read_text())
        verify_subject(saved)
        return saved
    raise ValueError('production_reference_stage_not_collected')


def verify_subject(saved):
    from omni_story.library.state import json_sha
    if saved.get('status') != 'reference_collected':
        raise ValueError('subject_not_collected')
    if json_sha(saved['understanding']) != saved['understanding_sha256']:
        raise ValueError('subject_understanding_changed')
    for path, expected in saved['artifacts'].items():
        if digest(path) != expected:
            raise ValueError('subject_original_receipt_changed')
    request = next(Path(p) for p in saved['artifacts'] if p.endswith('/request.json'))
    if saved.get('attempt_id'):_guard_retry_calls(request.parents[2])
    response = request.with_name('response.json')
    parsed = json.loads(request.with_name('parsed.json').read_text())
    if (json_sha(json.loads(request.read_text())) != saved['request_sha256'] or
            json_sha(json.loads(response.read_text())) != saved['response_sha256'] or
            parsed != saved['understanding'] or
            (not saved.get('archived_media_retired') and digest(saved['media_path']) != saved['media_sha256'])):
        raise ValueError('subject_received_binding_changed')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    args = parser.parse_args()
    run = json.loads(args.run.read_text())
    # This script has not imported omni_story yet. Production owns those imports.
    sys.path.insert(0, str(Path(run['production_root']) / 'src'))
    capture_reference(run)


if __name__ == '__main__':
    main()
