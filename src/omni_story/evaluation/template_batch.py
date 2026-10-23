"""Bounded template evaluations using the existing server admission and ledgers."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys
import time

from ..library import server_jobs
from ..library.hot_batch import capacity
from ..library.media import sha256_file
from ..library.resource_guard import install_ffmpeg_wrapper
from ..library.state import file_lock, json_sha, write_json
from ..library.worker_memory import WorkerGroups
from . import template_protocol as protocol


def code_identity():
    import hunter
    roots = [Path(__file__).parents[1], Path(hunter.__file__).parent]
    return json_sha([{str(p.relative_to(root)): sha256_file(p)
                      for p in sorted(root.rglob('*.py'))} for root in roots])


def failure_status(error):
    text = str(error).lower()
    if any(s in text for s in ['uncertain', 'unknown', 'no_replay', 'not_received']):
        return 'blocked_unknown'
    if any(s in text for s in ['budget', 'envelope', 'request_limit']):
        return 'blocked_budget'
    if 'resource_' in text:
        return 'blocked_resource'
    return 'failed'


def task(args):
    from .template_runner import understand, judge, verify_record
    started = time.time()
    manifest = protocol.load(args.manifest)
    directory = args.output / 'jobs' / args.template_id / args.method
    directory.mkdir(parents=True, exist_ok=True)
    with file_lock(directory / '.task.lock'):
        record = directory / 'record.json'
        if record.exists():
            verify_record(protocol.read(record))
            return
        row = dict(protocol_sha256=manifest['protocol_sha256'], template_id=args.template_id,
                   method=args.method, started_unix=started)
        options = argparse.Namespace(manifest=args.manifest, template_id=args.template_id,
            method=args.method, output=directory / 'understanding')
        try:
            if code_identity() != protocol.read(args.output / 'batch_state.json')['identity']['code_sha256']:
                raise RuntimeError('template_batch_code_changed')
            understand(options)  # Received requests are served from the original receipt.
            options.prediction = options.output / 'prediction.json'
            options.output, options.home = directory, args.home
            row.update(judge(options))
            verify_record(row)
        except Exception as error:
            row.pop('judgment', None)
            row.update(status=failure_status(error), error_type=type(error).__name__, error=str(error)[:400])
        row['elapsed_s'] = time.time() - started
        write_json(record, row)


def bind(root, manifest):
    identity = dict(protocol_sha256=manifest['protocol_sha256'], code_sha256=code_identity(),
        model='glm-5.3-flash', endpoint=os.environ.get('GLM_ANTHROPIC_BASE'),
        budget_path=str(Path(os.environ['OMNI_EVAL_BUDGET_FILE']).resolve()))
    registry = Path(os.environ['HUNTER_RECEIPTS_DIR']) / 'template_batch_registry.json'
    with file_lock(registry.with_suffix('.lock'), wait=True):
        saved = protocol.read(registry) if registry.exists() else {}
        previous = saved.get(manifest['experiment_id'])
        entry = dict(output=str(root), identity=identity)
        if previous and previous != entry:
            raise ValueError('template_batch_registered_identity_changed_use_original_version_and_output')
        saved[manifest['experiment_id']] = entry
        write_json(registry, saved)
    return identity


def run(args):
    args.manifest, args.output, args.home = args.manifest.resolve(), args.output.resolve(), args.home.resolve()
    manifest = protocol.load(args.manifest)
    if not 1 <= args.workers <= 5:
        raise ValueError('template_batch_workers_must_be_1_to_5')
    args.output.mkdir(parents=True, exist_ok=True)
    with file_lock(args.output / '.coordinator.lock'):
        groups = WorkerGroups()  # Require the existing 1024 MiB worker isolation.
        identity = bind(args.output, manifest)
        checkpoint = args.output / 'batch_state.json'
        state = protocol.read(checkpoint) if checkpoint.exists() else dict(
            identity=identity, started_unix=time.time(), active={}, settled={}, attempts={}, peak_workers=0,
            starting_reserved_calls=len(protocol.read(identity['budget_path'])['requests']))
        if state['identity'] != identity:
            raise ValueError('template_batch_locked_identity_changed')
        write_json(checkpoint, state)
        jobs = {t['template_id'] + '_' + m: (t['template_id'], m)
                for t in manifest['templates'] for m in manifest['methods']}
        limits = argparse.Namespace(home=args.home, concurrency=args.workers, min_free_gb=2,
            worker_disk_mb=512, worker_memory_mb=1024, max_video_mb=32)
        resources = args.home / 'shared/reliable_resources'
        wrapper = install_ffmpeg_wrapper(resources)
        children = {}
        while True:
            for key, saved in list(state['active'].items()):
                if server_jobs._identity(saved['pid']) == saved['pid_identity']:
                    if time.time() - saved['started_unix'] > 1200:
                        groups.kill(saved['cgroup'])
                    continue
                if key in children:
                    children.pop(key).wait()
                usage = groups.finish(saved['cgroup'])
                if usage is None:
                    continue
                groups.remove(saved['cgroup'])
                folder = Path(saved['output'])
                write_json(folder / ('resources_' + str(state['attempts'][key]) + '.json'), usage)
                if (folder / 'record.json').exists():
                    state['settled'][key] = protocol.read(folder / 'record.json')['status']
                elif state['attempts'][key] >= 2:
                    template_id, method = jobs[key]
                    row = dict(protocol_sha256=manifest['protocol_sha256'], template_id=template_id,
                        method=method, status='failed', error='worker_interrupted_twice', resources=usage)
                    write_json(folder / 'record.json', row)
                    state['settled'][key] = row['status']
                del state['active'][key]
            write_json(checkpoint, state)
            if len(state['settled']) == len(jobs):
                from .budget import liability
                budget = protocol.read(identity['budget_path'])
                write_json(args.output / 'execution.json', dict(identity=identity,
                    elapsed_s=time.time() - state['started_unix'], planned=len(jobs),
                    completed=sum(s == 'completed' for s in state['settled'].values()),
                    statuses=state['settled'], peak_workers=state['peak_workers'],
                    automatic_worker_recoveries=sum(max(n - 1, 0) for n in state['attempts'].values()),
                    runtime_manual_interventions=0,
                    new_reserved_calls=len(budget['requests']) - state['starting_reserved_calls'],
                    cumulative_cost_exposure=liability(budget)))
                from .template_cli import main
                main(['report', '--manifest', str(args.manifest), '--records', str(args.output / 'jobs'),
                      '--output', str(args.output / 'reports')])
                return 0 if all(s == 'completed' for s in state['settled'].values()) else 2
            pending = [k for k in jobs if k not in state['settled'] and k not in state['active']]
            measured = capacity(limits, list(state['active'].values()))
            if pending and measured['additional_workers']:
                key = pending[0]
                template_id, method = jobs[key]
                folder = args.output / 'jobs' / template_id / method
                folder.mkdir(parents=True, exist_ok=True)
                group = groups.create(key, state['attempts'].get(key, 0) + 1)
                command = [sys.executable, '-m', 'omni_story.library.worker_memory', str(group),
                    sys.executable, '-m', 'omni_story.evaluation.template_batch', 'task',
                    '--manifest', str(args.manifest), '--output', str(args.output), '--home', str(args.home),
                    '--template-id', template_id, '--method', method]
                env = dict(os.environ, PATH=str(wrapper) + os.pathsep + os.environ['PATH'],
                    OMNI_RESOURCE_DIR=str(resources), OMNI_FFMPEG_SLOTS='1', OMNI_FFMPEG_THREADS='2',
                    OMNI_WORKER_OUTPUT=str(folder), OMNI_WORKER_MAX_BYTES=str(512 * 1024**2))
                with (folder / 'worker.log').open('ab') as log:
                    child = subprocess.Popen(command, env=env, stdin=subprocess.DEVNULL,
                                             stdout=log, stderr=log, start_new_session=True)
                children[key] = child
                state['attempts'][key] = state['attempts'].get(key, 0) + 1
                state['active'][key] = dict(pid=child.pid, pid_identity=server_jobs._identity(child.pid),
                    started_unix=time.time(), cgroup=str(group), output=str(folder))
                state['peak_workers'] = max(state['peak_workers'], len(state['active']))
                write_json(checkpoint, state)
                continue
            if pending and not state['active']:
                state.setdefault('resource_wait_started_unix', time.time())
                if time.time() - state['resource_wait_started_unix'] > 600:
                    for key in pending:
                        template_id, method = jobs[key]
                        write_json(args.output / 'jobs' / template_id / method / 'record.json', dict(
                            protocol_sha256=manifest['protocol_sha256'], template_id=template_id,
                            method=method, status='blocked_resource', error='resource_wait_deadline'))
                        state['settled'][key] = 'blocked_resource'
            else:
                state.pop('resource_wait_started_unix', None)
            time.sleep(2)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['run', 'task'])
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--home', type=Path, required=True)
    parser.add_argument('--workers', type=int, default=2)
    parser.add_argument('--template-id')
    parser.add_argument('--method', choices=['single_reference', 'multi_reference'])
    args = parser.parse_args(argv)
    args.manifest, args.output, args.home = args.manifest.resolve(), args.output.resolve(), args.home.resolve()
    return run(args) if args.command == 'run' else task(args)


if __name__ == '__main__':
    raise SystemExit(main())
