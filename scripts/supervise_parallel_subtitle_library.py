#!/usr/bin/env python3
"""Bounded independent ledgers sharing an immutable visual library."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from omni_story.library.state import file_lock, json_sha, write_json
from omni_story.library.visual_library import VisualLibrary
from omni_story.library.resource_guard import install_ffmpeg_wrapper, memory_available_bytes
from supervise_subtitle_library import rejected_scope


def read(path):
    return json.loads(Path(path).read_text())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--home', type=Path, required=True)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--run', type=Path, required=True)
    args = parser.parse_args()
    home, root, coordinator = args.home, args.root, args.run
    release = Path(__file__).resolve().parents[1]
    manifest = read(coordinator / 'assignment.json')
    plan, base_config = manifest['plan'], manifest['configuration']
    partitions = manifest['partitions']
    assigned = [i for p in partitions for i in p['indices']]
    if (not 1 <= len(partitions) <= 4 or len(set(assigned)) != len(assigned)
            or sorted(assigned) != manifest['remaining_plan_indices']
            or json_sha(plan) != manifest['plan_sha256']):
        raise RuntimeError('parallel_assignment_not_disjoint_and_complete')
    control_path = coordinator / 'control.json'
    pool_path = coordinator / 'pool_state.json'
    with file_lock(root / '.supervisor.lock'):
        if pool_path.exists():
            raise RuntimeError('existing_pool_no_implicit_restart')
        control = dict(schema_version='subtitle_parallel_control_v1', stop_new_work=False,
                       excluded_ranges=base_config.get('excluded_ranges', []))
        write_json(control_path, control)
        resources = coordinator / 'resources'
        wrapper = install_ffmpeg_wrapper(resources)
        processes, rows = {}, []
        library = VisualLibrary(root)
        catalog = read(coordinator / 'source_catalog/inventory.json')
        started_at = time.time()

        def launch(row):
            number = len(row['attempts']) + 1
            run = coordinator / f"{row['id']}_{number:03d}"
            if run.exists():
                raise RuntimeError('parallel_worker_run_already_exists')
            (run / 'source_catalog').mkdir(parents=True)
            shutil.copy2(coordinator / 'source_catalog/inventory.json', run / 'source_catalog/inventory.json')
            config = dict(base_config, parallel_partition=dict(worker_id=row['id'],
                plan_sha256=manifest['plan_sha256'], indices=row['indices']),
                parallel_control_path=str(control_path), parallel_attempt=number,
                excluded_ranges=control['excluded_ranges'])
            config.pop('received_observation_seeds', None)
            write_json(run / 'build_config.json', config)
            env = os.environ.copy()
            env.update(PYTHONPATH=str(release/'src')+':'+str(release/'vendor'),
                PATH=str(wrapper)+':'+str(home/'runtime/node/bin')+':'+env.get('PATH',''),
                OMNI_RESOURCE_DIR=str(resources), OMNI_WORKER_OUTPUT=str(run),
                OMNI_MIN_FREE_BYTES=str(5*2**30), OMNI_FFMPEG_SLOTS='1', OMNI_FFMPEG_THREADS='2')
            command = [sys.executable, '-u', '-m', 'omni_story.library.visual_indexer', 'build',
                '--home', str(home), '--library', str(home/'shared/data/videos_library_02'),
                '--store', str(root), '--output', str(run), '--configuration', str(run/'build_config.json'),
                '--window-seconds', str(config['window_max_s'])]
            with (run/'worker.log').open('ab') as log:
                process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=log, env=env)
            processes[row['id']] = process
            row.update(run=str(run), worker_pid=process.pid, status='running', handled=False)
            row['attempts'].append(str(run))
            write_json(run/'launch.json', dict(pid=process.pid, run=str(run), started_at=time.time()))

        def snapshot(status='running', error=None):
            sources = [dict(source_id=s['source_id'], **library.coverage(s['sha256'], s['duration_s']))
                       for s in catalog['sources']]
            attempts = [*manifest['prior_attempts'], *(a for row in rows for a in row['attempts'])]
            workers = [{k: row[k] for k in ('id','run','worker_pid','status','attempts') if k in row} for row in rows]
            value = dict(mode='parallel', status=status, run=str(coordinator), supervisor_pid=os.getpid(),
                workers=workers, attempts=attempts, updated_at=time.time(), started_at=started_at,
                parallelism=len(partitions), error=error)
            write_json(root/'active_build.json', value)
            write_json(pool_path, value)
            write_json(coordinator/'progress.json', dict(status=status, planned_windows=len(plan['windows']),
                assigned_remaining_windows=len(assigned), sources=sources, workers=workers, updated_at=time.time()))
            return sources

        def halt(reason):
            control.update(stop_new_work=True, reason=reason, updated_at=time.time())
            write_json(control_path, control)

        attention = None
        try:
            for partition in partitions:
                row = dict(partition, attempts=[], consecutive_rejections=0)
                rows.append(row)
                launch(row)
                snapshot()
                time.sleep(1)
            write_json(root/'publication_status.json', dict(status='waiting_for_observations',
                active_run=str(coordinator), parallelism=len(partitions), updated_at=time.time()))
            while True:
                requested = read(control_path)
                if requested.get('stop_new_work') and not control['stop_new_work']:
                    control = requested
                    attention = str(requested.get('reason', 'external_drain_requested'))
                for row in rows:
                    process = processes[row['id']]
                    if process.poll() is None or row.get('handled'):
                        continue
                    row['handled'] = True
                    run = Path(row['run'])
                    worker = read(run/'worker.json') if (run/'worker.json').exists() else {}
                    if worker.get('status') == 'completed' and (run/'result.json').exists():
                        row['status'] = 'completed'
                        continue
                    if control['stop_new_work']:
                        row['status'] = 'stopped_for_attention'
                        continue
                    error = str(worker.get('error', 'worker_exited_without_terminal_record'))
                    if error.startswith('provider_content_rejected:'):
                        try:
                            rejection = rejected_scope(run)
                            accepted = read(run/'progress.json').get('new_observations_this_run',0)
                            row['consecutive_rejections'] = 1 if accepted else row['consecutive_rejections']+1
                            if row['consecutive_rejections'] > 3:
                                raise RuntimeError('repeated_provider_rejections_require_attention')
                            banned = rejection['scope']
                            for interval in plan['windows']:
                                if interval['start_s'] < banned['source_end_s'] and banned['source_start_s'] < interval['end_s']:
                                    entry = dict(rejection, scope=dict(kind='continuous_window',
                                        source_sha256=plan['source_sha256'], source_start_s=interval['start_s'],
                                        source_end_s=interval['end_s']))
                                    if entry['scope'] != banned:
                                        entry['reason'] = 'context_overlap_with_provider_quarantine'
                                    if not any(e['scope']==entry['scope'] for e in control['excluded_ranges']):
                                        control['excluded_ranges'].append(entry)
                            write_json(control_path, control)
                            write_json(root/'quarantine'/('quarantine_'+json_sha(rejection)[:16]+'.json'),
                                dict(rejection=rejection, excluded_windows=control['excluded_ranges'], at=time.time()))
                            launch(row)
                            continue
                        except Exception as failure:
                            error = str(failure)
                    row.update(status='needs_attention', error=error)
                    attention = row['id']+':'+error
                    halt(attention)
                if memory_available_bytes() < 256*2**20:
                    attention = attention or 'parallel_memory_reserve_below_256mib'
                    halt(attention)
                sources = snapshot('draining_for_attention' if attention else 'running', attention)
                if all(p.poll() is not None for p in processes.values()):
                    if attention:
                        snapshot('needs_attention', attention)
                        return 1
                    if not all(row['status']=='completed' for row in rows):
                        raise RuntimeError('parallel_workers_not_all_completed')
                    break
                time.sleep(2)
            excluded = [dict(**e['scope'], reason=e['reason'], evidence=e.get('evidence'))
                        for e in control['excluded_ranges']]
            write_json(coordinator/'result.json', dict(status='completed_with_gaps' if excluded else 'completed',
                sources=sources, excluded_ranges=excluded, parallelism=len(partitions)))
            write_json(coordinator/'worker.json', dict(status='completed', finished_at=time.time()))
            snapshot('publishing')
            subprocess.run([sys.executable, str(release/'scripts/finalize_subtitle_library.py'),
                '--home',str(home),'--root',str(root),'--run',str(coordinator)], check=True)
            snapshot('completed')
            return 0
        except BaseException as error:
            halt(str(error)[:1000])
            snapshot('draining_for_attention',str(error)[:1000])
            # In-flight windows may finish; never terminate or replay their requests.
            for process in processes.values():
                process.wait()
            snapshot('needs_attention',str(error)[:1000])
            raise


if __name__ == '__main__':
    raise SystemExit(main())
