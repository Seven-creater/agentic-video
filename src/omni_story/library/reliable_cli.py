"""Durable server admission for the reliable production editing workflow."""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
import signal
import subprocess
import sys
import time

from . import server_cli, server_jobs
from .hot_batch import capacity
from .resource_guard import install_ffmpeg_wrapper
from .state import file_lock, json_sha, write_json
from .media import sha256_file
from .reliable import POLICY, SPEC, ReliableMCP, code_sha, execute, now, read
from .opencode_provider import PROVIDER
from ..evaluation.budget import liability


def load(path):
    manifest = read(path)
    if (manifest.get('execution_mode') != POLICY or manifest.get('output_spec') != SPEC or
            manifest.get('max_cost_cny') != 100 or not manifest.get('cases') or
            len({c['case_id'] for c in manifest['cases']}) != len(manifest['cases']) or
            any(c['input_kind'] not in {'reference','live_hotspot'} or
                not re.fullmatch(r'[A-Za-z0-9_-]+',c['case_id']) for c in manifest['cases'])):
        raise ValueError('reliable_manifest_invalid')
    if manifest.get('acceptance_profile') == 'frozen20_live2' and (
            len(manifest['cases']) != 22 or sum(c['input_kind'] == 'reference' for c in manifest['cases']) != 20):
        raise ValueError('reliable_acceptance_requires_frozen20_plus_live2')
    library = manifest['library']
    if sha256_file(library['manifest_path']) != library['manifest_sha256']:
        raise ValueError('reliable_frozen_library_manifest_changed')
    for c in manifest['cases']:
        if c['input_kind'] == 'reference' and sha256_file(c['reference']['path']) != c['reference']['sha256']:
            raise ValueError('reliable_frozen_reference_changed')
    for receipt in manifest.get('previous_captures', []):
        if sha256_file(receipt['path']) != receipt['sha256']:
            raise ValueError('reliable_previous_capture_changed')
    return manifest


def worker(args):
    m = load(args.manifest)
    case = next(c for c in m['cases'] if c['case_id'] == args.case)
    output = args.root / 'runs' / args.case / POLICY
    output.mkdir(parents=True, exist_ok=True)
    config = server_cli.settings(args.home)
    history = server_cli.load_history(config)
    credentials = read(args.home / 'credentials.json')
    os.environ['Z_AI_API_KEY'] = server_cli.credential(args.home)
    if credentials.get('DASHSCOPE_API_KEY'):
        os.environ['DASHSCOPE_API_KEY'] = credentials['DASHSCOPE_API_KEY']
    exclusions = list((history or {}).get('unknown_inputs', []))
    exclusions += m.get('unknown_exclusions', [])
    if case['input_kind'] == 'live_hotspot':
        from ..discovery.reliable_capture import capture_reference
        capture = output / 'capture'
        receipt = capture / 'capture_receipt.json'
        # A submitted capture can only resume by reading its own completed
        # receipt. Its paid discovery calls are never blindly replayed.
        with file_lock(args.root / '.capture_admission.lock',wait=True):
            if receipt.exists():
                captured = read(receipt)
            else:
                used = [read(p['path']) for p in m.get('previous_captures', [])]
                used += [read(p) for p in args.root.glob('runs/*/reliable_v1/capture/capture_receipt.json')]
                references = [c['reference']['sha256'] for c in m['cases'] if c['input_kind']=='reference']
                trends = [str(c['topic_id']) for c in m['cases'] if c.get('topic_id')]
                write_json(output / 'capture_started.json', dict(started_at=now()))
                captured = capture_reference(args.home, capture, max_candidates=10, freshness_hours=72,
                    visual_library=Path(m['library']['visual_root']),
                    exclude_trend_ids=[*trends,*[c['evidence']['trend']['sentence_id'] for c in used]],
                    exclude_reference_sha256s=[*references,*[c['sha256'] for c in used]])
        reference, title = Path(captured['path']), captured['evidence']['trend']['sentence']
        if sha256_file(reference) != captured['sha256']:
            raise RuntimeError('reliable_capture_binding_changed')
    else:
        reference, title = Path(case['reference']['path']), case.get('topic_title', '')
    configuration = dict(PROVIDER, workflow=POLICY, vision_generation=server_cli.VISION_GENERATION,
                         reliable_manifest_sha256=sha256_file(args.manifest))
    if case.get('received_sources'):
        configuration['received_sources'] = case['received_sources']
    def factory(state):
        return ReliableMCP(state, package_root=config['mcp_package_root'],
            executable=config['opencode_executable'], exclusions=exclusions)
    result = execute(reference, Path(m['library']['media_root']), Path(m['library']['visual_root']), output,
        model_factory=factory, configuration=configuration, title=title, exclusions=exclusions,
        registry_path=args.home / 'shared/reliable_model_registry.json',
        tts_registry_path=args.home / 'shared/reliable_tts_registry.json',
        unknown_tts_payloads=m.get('unknown_tts_payloads',[]))
    # Only local, reproducible render caches are reclaimed after immutable
    # delivery. Original model/voice receipts and all source assets are retained.
    removed = []
    for root in (output / 'render', output / 'source_sound'):
        if root.exists():
            for path in root.iterdir():
                if path.is_file() and path.suffix in {'.wav','.mp4'} and path.name != 'final.mp4':
                    removed.append(dict(path=str(path), sha256=sha256_file(path), bytes=path.stat().st_size))
                    path.unlink()
    for root in output.glob('*_audio'):
        if root.is_dir() and not root.is_symlink():
            for path in root.glob('*.wav'):
                if path.name not in {'voice.wav','mix.wav'}:
                    removed.append(dict(path=str(path), sha256=sha256_file(path), bytes=path.stat().st_size))
                    path.unlink()
    if removed:
        write_json(output / 'reclaimed_local_cache.json', dict(removed=removed, reclaimed_at=now()))
    print(json.dumps(dict(engineering_status=result['engineering_status'], final_video=result['final_video']), ensure_ascii=False))


def _environment(home, root, run_id, output):
    resources = home / 'shared/reliable_resources'
    wrapper = install_ffmpeg_wrapper(resources)
    node = home / 'runtime/node/bin'
    return dict(os.environ, PATH=str(wrapper) + os.pathsep + str(node) + os.pathsep + os.environ['PATH'],
        OMNI_RESOURCE_DIR=str(resources), OMNI_FFMPEG_SLOTS='1', OMNI_FFMPEG_THREADS='2',
        OMNI_MIN_FREE_BYTES=str(2 * 1024**3), OMNI_ASR_THREADS='2', OMP_NUM_THREADS='2',
        OMNI_EVAL_BUDGET_FILE=str(root / 'request_budget.json'), OMNI_EVAL_RUN_ID=run_id,
        OMNI_WORKER_OUTPUT=str(output), OMNI_WORKER_MAX_BYTES=str(768 * 1024**2), HF_HUB_OFFLINE='1')


def collect(root, manifest, state):
    rows = []
    for case in manifest['cases']:
        folder = root / 'runs' / case['case_id'] / POLICY
        result = folder / 'reliable_result.json'
        row = dict(case_id=case['case_id'], input_kind=case['input_kind'], engineering_status='pending',
            technical_complete=False, manual_interventions=0)
        if result.exists():
            saved = read(result)
            valid = Path(saved['final_video']).is_file() and sha256_file(saved['final_video']) == saved['final_sha256']
            row.update(engineering_status=saved['engineering_status'] if valid else 'failed',
                technical_complete=valid and saved['technical']['technical_complete'],
                final_video=saved['final_video'], final_sha256=saved['final_sha256'],
                degraded_reasons=saved['degraded_reasons'], quality_status=saved['quality_status'],
                automatic_recoveries=saved['automatic_recoveries'])
        elif case['case_id'] in state.get('settled', {}):
            row.update(state['settled'][case['case_id']])
        elif case['case_id'] in state.get('active', {}):
            row['engineering_status'] = 'running'
        rows.append(row)
    groups = {}
    for kind in ('reference','live_hotspot'):
        selected = [r for r in rows if r['input_kind'] == kind]
        groups[kind] = dict(total=len(selected), completed=sum(r['technical_complete'] for r in selected),
            normal=sum(r['engineering_status'] == 'completed' for r in selected),
            degraded=sum(r['engineering_status'] == 'completed_degraded' for r in selected),
            manual_interventions=sum(r['manual_interventions'] for r in selected))
    exposure = liability(read(root / 'request_budget.json'))
    report = dict(policy=POLICY, groups=groups, rows=rows, cost=exposure,
        started_at=state['started_at'], updated_at=now(), code_sha256=state['code_sha256'],
        batch_complete=all(r['technical_complete'] for r in rows),
        engineering_acceptance_pass=(manifest.get('acceptance_profile') == 'frozen20_live2' and
            groups['reference']['total'] == groups['reference']['completed'] == 20 and
            groups['live_hotspot']['total'] == groups['live_hotspot']['completed'] == 2 and
            sum(g['manual_interventions'] for g in groups.values()) == 0))
    write_json(root / 'report.json', report)
    return report


def batch(args):
    m = load(args.manifest)
    root = args.root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    with file_lock(args.home/'shared/reliable_batch_registry.lock',wait=True):
        registry_path = args.home/'shared/reliable_batch_registry.json'
        registry = read(registry_path) if registry_path.exists() else {}
        existing = registry.get(m['experiment_id'])
        registered = dict(output=str(root),manifest_sha256=sha256_file(args.manifest))
        if existing and existing != registered:
            raise RuntimeError('reliable_batch_already_registered_use_original_output:'+existing['output'])
        if not existing:
            registry[m['experiment_id']] = registered
            write_json(registry_path,registry)
    with file_lock(root / '.coordinator.lock'):
        checkpoint = root / 'batch_state.json'
        identity = dict(manifest_sha256=sha256_file(args.manifest), code_sha256=code_sha())
        if checkpoint.exists():
            state = read(checkpoint)
            if any(state[k] != v for k,v in identity.items()):
                raise RuntimeError('reliable_locked_batch_code_or_manifest_changed')
        else:
            prior = Path(m['prior_budget_file'])
            previous = read(prior)
            if sha256_file(prior) != m['prior_budget_sha256']:
                raise RuntimeError('reliable_prior_budget_binding_changed')
            budget = dict(experiment_id=m['experiment_id'], manifest_sha256=identity['manifest_sha256'],
                requests=[], max_model_calls=previous['max_model_calls']-len(previous['requests']), per_run_max_model_calls=20,
                generation_call_limit=previous['max_model_calls']-len(previous['requests']), deadline_unix=time.time()+86400,
                cost_control=dict(authorization_id='reliable_v1_with_original_cny100',
                    spend_envelope_cny=90, per_operation_reserve_cny=1, safety_buffer_cny=10,
                    experiment_root=str(root), prior_budget_file=str(prior), prior_budget_sha256=sha256_file(prior)))
            write_json(root / 'request_budget.json', budget)
            state = dict(**identity, started_at=now(), active={}, settled={}, attempts={})
            write_json(checkpoint,state)
        limits = argparse.Namespace(home=args.home, concurrency=5, min_free_gb=2,
            worker_disk_mb=768, worker_memory_mb=1024, max_video_mb=32)
        children = {}
        while True:
            for case_id, saved in list(state['active'].items()):
                folder = Path(saved['output'])
                if server_jobs._identity(saved['pid']) == saved['pid_identity']:
                    if time.time()-saved['started_unix'] > 2700:
                        os.killpg(saved['pid'],signal.SIGKILL if saved.get('deadline_exceeded_at') and
                            time.time()-saved['deadline_exceeded_at'] > 10 else signal.SIGTERM)
                        saved['deadline_exceeded'] = True
                        saved.setdefault('deadline_exceeded_at',time.time())
                    continue
                if case_id in children:
                    children.pop(case_id).wait()
                if (folder / 'reliable_result.json').exists():
                    state['settled'][case_id] = dict(engineering_status=read(folder/'reliable_result.json')['engineering_status'])
                elif saved.get('deadline_exceeded'):
                    state['settled'][case_id] = dict(engineering_status='failed',error='task_deadline_exceeded')
                elif (folder / 'reliable_failure.json').exists():
                    failure = read(folder / 'reliable_failure.json')
                    if not failure.get('retryable_local') or state['attempts'][case_id] >= 3:
                        state['settled'][case_id] = failure
                elif state['attempts'][case_id] >= 3:
                    state['settled'][case_id] = dict(engineering_status='failed', error='worker_interrupted_recovery_exhausted')
                # A lost worker with a recorded request is resumed through the
                # same checkpoints/ledger; unknown requests are never reposted.
                del state['active'][case_id]
            write_json(checkpoint,state)
            report = collect(root,m,state)
            if len(state['settled']) == len(m['cases']):
                return 0 if report['batch_complete'] else 2
            pending = [c for c in m['cases'] if c['case_id'] not in state['settled'] and c['case_id'] not in state['active']]
            measured = capacity(limits,list(state['active'].values()))
            if pending and not state['active'] and not measured['additional_workers']:
                state.setdefault('resource_wait_started_unix',time.time())
                if time.time()-state['resource_wait_started_unix'] > 1800:
                    for c in pending:
                        state['settled'][c['case_id']] = dict(engineering_status='blocked',error='resource_wait_deadline')
                    continue
            else:
                state.pop('resource_wait_started_unix',None)
            if pending and time.time() >= read(root/'request_budget.json')['deadline_unix']:
                for c in pending:
                    state['settled'][c['case_id']] = dict(engineering_status='blocked',error='batch_deadline')
                continue
            if pending and measured['additional_workers']:
                case = pending[0]
                case_id = case['case_id']
                folder = root / 'runs' / case_id / POLICY
                folder.mkdir(parents=True,exist_ok=True)
                command = [sys.executable,'-m','omni_story.library.reliable_cli','worker','--home',str(args.home),
                    '--manifest',str(args.manifest),'--root',str(root),'--case',case_id]
                with (folder / 'worker.log').open('ab') as log:
                    process = subprocess.Popen(command, cwd=Path(__file__).parents[3], stdin=subprocess.DEVNULL,
                        stdout=log,stderr=log,start_new_session=True,
                        env=_environment(args.home,root,m['experiment_id']+'/'+case_id+'/'+POLICY,folder))
                children[case_id] = process
                state['attempts'][case_id] = state['attempts'].get(case_id,0)+1
                state['active'][case_id] = dict(pid=process.pid,pid_identity=server_jobs._identity(process.pid),
                    output=str(folder),started_at=now(),started_unix=time.time())
                write_json(checkpoint,state)
                continue
            time.sleep(3)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['batch','worker','status'])
    parser.add_argument('--home',type=Path,default=server_cli._home())
    parser.add_argument('--manifest',type=Path,required=True)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--case')
    args = parser.parse_args(argv)
    args.home,args.root,args.manifest = args.home.resolve(),args.root.resolve(),args.manifest.resolve()
    if args.command == 'worker':
        try:
            output = args.root / 'runs' / args.case / POLICY
            output.mkdir(parents=True,exist_ok=True)
            with file_lock(output / '.worker.lock',wait=True):
                return worker(args) or 0
        except Exception as error:
            output = args.root / 'runs' / args.case / POLICY
            if not (output / 'reliable_failure.json').exists():
                write_json(output / 'reliable_failure.json',dict(engineering_status='blocked',error=str(error),failed_at=now()))
            raise
    if args.command == 'batch':
        return batch(args)
    print(json.dumps(read(args.root/'report.json'),ensure_ascii=False,indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
