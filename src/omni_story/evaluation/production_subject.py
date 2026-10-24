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


class ReferenceCollected(BaseException):
    """A successful component boundary, never a completed video job."""


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify_tree(root, files):
    for relative, expected in files.items():
        if digest(Path(root) / relative) != expected:
            raise ValueError('production_freeze_changed:' + relative)


def capture_reference(run, *, provider_factory=None):
    from omni_story.library import pipeline, clean_chain
    from omni_story.library.media import sha256_file, verify_source
    from omni_story.library.state import write_json, json_sha

    root = Path(run['production_root']).resolve(strict=True)
    verify_tree(root / 'src', run['production_files'])
    if not Path(pipeline.__file__).resolve().is_relative_to(root):
        raise ValueError('subject_imported_from_wrong_release')
    output = Path(run['output']).resolve()
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
    for reading_path in run.get('existing_reference_outputs', []):
        reading_path = Path(reading_path)
        if not reading_path.is_file():
            continue
        reading = json.loads(reading_path.read_text())
        if reading.get('reference_sha256') != run['reference_sha256']:
            continue  # The small reliable_v1 schema is a different subject.
        state_path = reading_path.parent / 'library_state.json'
        if not state_path.is_file():
            continue
        state = json.loads(state_path.read_text())
        if state['input_lock'].get('reference_sha256') != run['reference_sha256']:
            continue
        for call in reversed(state['calls']):
            folder = state_path.parent / 'calls' / call['id']
            parsed = folder / 'parsed.json'
            if call['name'] not in {'reference','reference_repair'} or call['status'] != 'received' or not parsed.exists():
                continue
            value = json.loads(parsed.read_text())
            modified = any(reading.get(k) != v for k,v in value.items())
            request = json.loads((folder/'request.json').read_text())
            media = request['arguments']['video_source']
            saved = dict(schema_version='production_reference_subject_v1', binding=binding,
                understanding=value, understanding_sha256=json_sha(value), call_id=call['id'],
                request_sha256=call['request_sha256'], response_sha256=call['response_sha256'],
                artifacts={str(folder/name):sha256_file(folder/name) for name in ('request.json','response.json','parsed.json')},
                media_path=media, media_sha256=request['media_sha256'],
                archived_media_retired=not Path(media).is_file(), manual_correction=modified,
                historical_reading_path=str(reading_path), status='reference_collected',
                note='Reused original received reference return; later manual changes are flagged separately.')
            verify_subject(saved)
            write_json(result,saved)
            return saved
    if run.get('reuse_only'):
        raise ValueError('subject_reuse_only_no_completed_result')
    cached=run.get('library_catalog')
    catalog_path=output/'production_stage/catalog/inventory.json'
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
                key=json_sha(dict(input_lock=state.input_lock,request=request))
                completed=subprocess.run([*run['external_budget_gate'],'--key',key,'--evidence',str(state.path)],
                    text=True,capture_output=True,timeout=60)
                if completed.returncode:
                    raise RuntimeError('subject_budget_gate_failed:'+completed.stderr[-1200:])
                return original_begin(name,request,**kwargs)
            state.begin_call=budgeted_begin
        elif provider_factory is None and os.environ.get('OMNI_EVAL_BUDGET_FILE'):
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
                write_json(result, saved)
                raise ReferenceCollected()

        return Observer()

    try:
        if run.get('isolate_asr'):
            import importlib.util
            spec=importlib.util.spec_from_file_location('reference_isolated_asr',Path(__file__).with_name('isolated_asr.py'))
            module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
            module.install(output/'isolated_asr',root)
        pipeline.execute(run['reference'], run['library'], output / 'production_stage',
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
