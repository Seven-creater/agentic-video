"""One finite batch: serialized shared capture, independent parallel edit jobs."""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import sys
import time

from . import server_jobs
from .media import sha256_file
from .resource_guard import (ResourceLimit, directory_size, install_ffmpeg_wrapper,
                             memory_available_bytes)
from .state import file_lock, write_json

MIB = 1024 ** 2
GIB = 1024 ** 3
MAX_CONCURRENCY = 32
OPTIONS = ('library', 'visual_library', 'parent_task', 'audio_music_bed', 'asr_model_dir',
           'max_candidates', 'freshness_hours', 'count', 'concurrency', 'min_free_gb',
           'worker_disk_mb', 'worker_memory_mb', 'render_workers', 'max_video_mb', 'capture_source',
           'public_cache_root', 'prior_batch', 'prior_batch_sha256')


def capacity(args, active=()):
    """Reserve outstanding worker growth before admitting capture or another job."""
    reserve = int(args.min_free_gb * GIB)
    disk_budget, memory_budget = args.worker_disk_mb * MIB, args.worker_memory_mb * MIB
    free = shutil.disk_usage(args.home).free
    memory = memory_available_bytes()
    outstanding = sum(max(0, disk_budget - directory_size(row['output'])) for row in active)
    # Three source videos and their small comparison proxy share one capture cache.
    capture_budget = 3 * args.max_video_mb * MIB + 64 * MIB
    slots = max(0, min(args.concurrency - len(active),
        (free - reserve - outstanding - capture_budget) // disk_budget,
        (memory - 512 * MIB - len(active) * memory_budget) // memory_budget))
    return dict(additional_workers=slots, free_bytes=free, reserve_bytes=reserve,
        available_memory_bytes=memory, outstanding_worker_bytes=outstanding,
        capture_reserve_bytes=capture_budget, worker_disk_bytes=disk_budget,
        worker_memory_bytes=memory_budget)


def _validate(args):
    if (args.count < 1 or not 1 <= args.concurrency <= MAX_CONCURRENCY or
            not 1 <= args.render_workers <= 10 or args.worker_disk_mb < 32 or
            args.worker_memory_mb < 256 or args.max_video_mb < 1 or
            not 0 < args.min_free_gb < 100000):
        raise ValueError('batch requires positive budgets/count, concurrency in 1..32 and render-workers in 1..10')


def _command(args, action, output):
    command = [sys.executable, '-m', 'omni_story.library.hot_pipeline',
               '--home', str(args.home.resolve()), action, '--output', str(output)]
    for name in OPTIONS:
        if action == '_run' and name not in (*OPTIONS[:7], 'capture_source'):
            continue
        value = getattr(args, name)
        if value is not None:
            command.extend(['--' + name.replace('_', '-'), str(value)])
    return command


def start_batch(args, config):
    _validate(args)
    if (args.home / 'shared/hot_batch_unknown.json').is_file():
        raise ResourceLimit('batch_previous_unknown_no_automatic_new_batch')
    measured = capacity(args)
    if args.dry_run:
        print(json.dumps(dict(status='dry_run_no_requests', requested=args.count,
            concurrency=args.concurrency, capacity=measured), ensure_ascii=False, indent=2))
        return
    if not measured['additional_workers']:
        raise ResourceLimit('batch_no_capacity_no_requests:' + json.dumps(measured))
    print(json.dumps(server_jobs.start(_command(args, '_batch', args.output), args.output,
        Path(config['project_root'])), ensure_ascii=False, indent=2))


@contextmanager
def _environment(values):
    previous = {key: os.environ.get(key) for key in values}
    os.environ.update({key: str(value) for key, value in values.items()})
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _unknown(output, terminal):
    paths = [*Path(output).rglob('library_state.json'), *Path(output).rglob('state.json')]
    covered = set()
    for path in paths:
        saved = json.loads(path.read_text(encoding='utf-8'))
        if path.name == 'library_state.json' and (path.parent/'reference_replacement.json').is_file():
            from .reference_replacement import received_cache
            if received_cache(path.parent) is not None:
                covered.add(str(path.resolve()))
        if saved.get('authorized_retry_once'):
            from .tts_retry import validate_replacement
            validate_replacement(path, saved)
            covered.update(saved['authorized_retry_once'][k]['path']
                           for k in ('original','previous') if k in saved['authorized_retry_once'])
    for path in paths:
        if str(path.resolve()) in covered:
            continue
        value = json.loads(path.read_text(encoding='utf-8'))
        calls = value.get('calls', [value])
        if any(c.get('status') == 'uncertain' or
               (terminal and c.get('status') == 'submitted') for c in calls):
            return str(path)
    return None


def unknown_guard_active(home):
    """Keep historical guard bytes; only verified replacement audio settles it."""
    guard = Path(home)/'shared/hot_batch_unknown.json'
    if not guard.exists():
        return False
    saved = json.loads(guard.read_text())
    ledger = Path(saved.get('ledger', ''))
    if not ledger.is_file():
        return True
    from .tts_retry import validate_replacement
    try:
        validate_replacement(ledger)
        return _unknown(Path(saved['source_edit']), terminal=True) is not None
    except (ValueError, KeyError, OSError, RuntimeError):
        return True


def _inherited_jobs(args):
    """Continue a settled partial batch without launching its completed work again."""
    prior = getattr(args, 'prior_batch', None)
    digest = getattr(args, 'prior_batch_sha256', None)
    if prior is None:
        if digest is not None:
            raise ResourceLimit('batch_prior_path_required')
        return []
    prior = Path(prior).resolve(strict=True)
    if (not prior.is_relative_to((args.home/'shared/jobs').resolve()) or prior == args.output.resolve() or
            not digest or sha256_file(prior/'batch_result.json') != digest):
        raise ResourceLimit('batch_prior_binding_changed')
    parent = server_jobs.status(prior)
    if parent['state'] not in server_jobs.TERMINAL_STATES or parent.get('supervisor_alive'):
        raise ResourceLimit('batch_prior_still_active')
    old = json.loads((prior/'batch_result.json').read_text())
    jobs = old['jobs']
    if (old['status'] != 'stopped' or old['requested'] != args.count or
            not 0 < len(jobs) == old['launched'] < args.count or
            old['counts']['content_ready'] != len(jobs) or old['counts']['failed_or_unresolved'] != 0 or
            _unknown(prior, terminal=True)):
        raise ResourceLimit('batch_prior_not_settled_ready_no_replay')
    for row in jobs:
        line = Path(row['output']).resolve(strict=True)
        if not line.is_relative_to((args.home/'shared/jobs').resolve()):
            raise ResourceLimit('batch_prior_child_outside_jobs')
        child = server_jobs.status(line)
        if child['state'] != 'succeeded' or child.get('supervisor_alive') or _unknown(line, terminal=True):
            raise ResourceLimit('batch_prior_child_not_settled')
        result = json.loads((line/'hot_result.json').read_text())
        if (child['job_id'] != row['job_id'] or result.get('content_delivery_ready') is not True or
                result.get('joint_quality_gate') is not True or result['delivery_kind'] != 'fine_candidate' or
                sha256_file(result['final_video']) != result['final_sha256'] or
                sha256_file(result['chain_result_path']) != result['chain_result_sha256'] or
                result['capture']['sha256'] != row['reference_sha256'] or
                sha256_file(result['capture']['path']) != row['reference_sha256'] or
                str(result['capture']['evidence']['trend']['sentence_id']) != row['trend_id']):
            raise ResourceLimit('batch_prior_delivery_binding_changed')
    if len({row['trend_id'] for row in jobs}) != len(jobs) or len({row['reference_sha256'] for row in jobs}) != len(jobs):
        raise ResourceLimit('batch_prior_references_not_distinct')
    return jobs


def stop_children(output):
    output = Path(output).resolve(strict=True)
    for path in (output / 'lines').glob('*/.omni-server/job.json'):
        child = path.parent.parent.resolve(strict=True)
        if not child.is_relative_to(output / 'lines'):
            raise ValueError('batch child outside lines directory')
        job = server_jobs.status(child)
        command = job.get('command', [])
        if ('omni_story.library.hot_pipeline' not in command or '_run' not in command or
                '--output' not in command or Path(command[command.index('--output')+1]).resolve() != child):
            raise ValueError('batch child command owner changed')
        server_jobs.stop(child)


@contextmanager
def _admission_lock(output):
    import fcntl
    with (Path(output) / '.admission.lock').open('a+b') as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def stop_batch(output):
    with _admission_lock(output):
        status = server_jobs.stop(output)
        stop_children(output)
        return status


def _refresh_jobs(args, root, jobs):
    active, reason = [], None
    for row in jobs:
        job = server_jobs.status(Path(row['output']))
        terminal = job['state'] in server_jobs.TERMINAL_STATES
        row['state'] = job['state']
        unknown = _unknown(row['output'], terminal)
        if unknown or job['state'] == 'unknown':
            reason = 'unknown_child_no_new_work:' + (unknown or row['output'])
            write_json(args.home / 'shared/hot_batch_unknown.json',
                dict(batch=str(root), job_id=row['job_id'], evidence=reason))
        if not terminal and job['state'] != 'unknown':
            active.append(row)
        result = Path(row['output']) / 'hot_result.json'
        if result.is_file():
            value = json.loads(result.read_text(encoding='utf-8'))
            row.update(result_path=str(result), delivery_kind=value['delivery_kind'],
                content_status=value['status'], final_video=value['final_video'],
                joint_quality_gate=value.get('joint_quality_gate', False),
                content_delivery_ready=value.get('content_delivery_ready', False))
    return active, reason


def run_batch(args):
    from .hot_pipeline import _inputs, _now
    from ..discovery.hot_capture import capture_reference
    if args.capture_source == 'public_browser':
        from ..discovery.public_capture import capture_reference
    _validate(args)
    config = _inputs(args)
    if (args.home/'shared/hot_batch_unknown.json').is_file():
        raise ResourceLimit('batch_previous_unknown_no_automatic_new_batch')
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=True)
    # One batch owns shared capture and reservations; workers still run in parallel.
    with file_lock(args.home / 'shared/hot_batch.lock'):
        jobs = _inherited_jobs(args)
        with (root / 'batch_input.json').open('x', encoding='utf-8') as handle:
            json.dump({key: str(getattr(args, key)) for key in OPTIONS}, handle, indent=2)
        if jobs:
            write_json(root/'inherited_batch.json', dict(prior_batch=str(args.prior_batch.resolve()),
                prior_batch_sha256=args.prior_batch_sha256, completed_jobs=jobs,
                target_total=args.count, remaining_new_jobs=args.count-len(jobs)))
        resource_dir = args.home / 'shared/hot_resources'
        bin_dir = install_ffmpeg_wrapper(resource_dir)
        environment = dict(OMNI_RESOURCE_DIR=resource_dir, OMNI_FFMPEG_SLOTS=args.render_workers,
            OMNI_MIN_FREE_BYTES=int(args.min_free_gb * GIB), OMNI_FFMPEG_THREADS=2,
            PATH=str(bin_dir) + os.pathsep + os.environ['PATH'], OMP_NUM_THREADS=2,
            OMNI_ASR_THREADS=2)
        selected_trends = {row['trend_id'] for row in jobs}
        selected_sha = {row['reference_sha256'] for row in jobs}
        stop_reason = None
        limit = min(args.concurrency, max(1,
            (memory_available_bytes() - 512 * MIB) // (args.worker_memory_mb * MIB)))
        while True:
            if server_jobs.status(root).get('stop_requested'):
                stop_reason = 'user_stop_no_new_work'
                stop_children(root)
            active, unknown_reason = _refresh_jobs(args, root, jobs)
            stop_reason = unknown_reason or stop_reason
            measured = capacity(args, active)
            write_json(root / 'batch_progress.json', dict(updated_at=_now(), requested=args.count,
                stage='draining' if stop_reason else 'running', stop_reason=stop_reason,
                concurrency=args.concurrency, active=len(active), jobs=jobs, capacity=measured))
            if len(jobs) >= args.count or stop_reason:
                if not active:
                    break
            elif len(active) < limit and measured['additional_workers']:
                capture = root / 'captures' / f'{len(jobs)+1:03d}'
                try:
                    # Capture is shared and serialized; an already launched edit can
                    # wait on GLM while we acquire the next distinct reference.
                    with _environment({**environment, 'OMNI_WORKER_OUTPUT': capture,
                            'OMNI_WORKER_MAX_BYTES': measured['capture_reserve_bytes']}):
                        reference = capture_reference(args.home, capture,
                            max_candidates=args.max_candidates, freshness_hours=args.freshness_hours,
                            visual_library=args.visual_library, exclude_trend_ids=selected_trends,
                            exclude_reference_sha256s=selected_sha,
                            max_video_bytes=args.max_video_mb * MIB,
                            min_free_bytes=measured['reserve_bytes'] + measured['outstanding_worker_bytes'] +
                                           measured['worker_disk_bytes'],
                            **({'cache_root': args.public_cache_root} if args.capture_source == 'public_browser' else {}))
                    selected_trends.add(str(reference['evidence']['trend']['sentence_id']))
                    selected_sha.add(reference['sha256'])
                    active, unknown_reason = _refresh_jobs(args, root, jobs)
                    if unknown_reason:
                        raise ResourceLimit(unknown_reason)
                    # Cache writes may have consumed more than expected. Recheck
                    # before submitting any editing request, not after it starts.
                    available = capacity(args, active)
                    if (available['free_bytes'] - available['reserve_bytes'] -
                            available['outstanding_worker_bytes'] < available['worker_disk_bytes']):
                        raise ResourceLimit('batch_disk_changed_before_edit_no_request')
                    if available['available_memory_bytes'] < (512 * MIB +
                            (len(active) + 1) * available['worker_memory_bytes']):
                        raise ResourceLimit('batch_memory_changed_before_edit_no_request')
                    output = root / 'lines' / f'{len(jobs)+1:03d}'
                    command = _command(args, '_run', output) + ['--reference-receipt',
                                                               str(capture / 'capture_receipt.json')]
                    with _admission_lock(root):
                        if server_jobs.status(root).get('stop_requested'):
                            raise ResourceLimit('user_stop_before_edit_no_request')
                        with _environment({**environment, 'OMNI_WORKER_OUTPUT': output,
                                'OMNI_WORKER_MAX_BYTES': args.worker_disk_mb * MIB}):
                            job = server_jobs.start(command, output, Path(config['project_root']))
                        jobs.append(dict(job_id=job['job_id'], output=str(output), state=job['state'],
                            reference_sha256=reference['sha256'], trend_id=str(reference['evidence']['trend']['sentence_id'])))
                    continue
                except Exception as error:
                    stop_reason = f'{type(error).__name__}:{error}'
            elif not active:
                stop_reason = 'resource_capacity_exhausted_no_new_requests'
                break
            time.sleep(2)
        ready = sum(row['state'] == 'succeeded' and bool(row.get('result_path')) and
            row.get('content_delivery_ready') is True and row.get('joint_quality_gate') is True and
            row.get('delivery_kind') == 'fine_candidate' for row in jobs)
        failed = len(jobs) - ready
        counts = dict(succeeded=len(jobs)-failed, failed_or_unresolved=failed,
            content_ready=ready)
        status = ('stopped' if stop_reason or len(jobs) != args.count else
                  'completed_with_failures' if failed else 'completed')
        result = dict(status=status, counts=counts,
            requested=args.count, launched=len(jobs), stop_reason=stop_reason, jobs=jobs,
            completed_at=_now(), automatic_restart=False)
        write_json(root / 'batch_result.json', result)
        write_json(root / 'batch_progress.json', {**result, 'stage': 'settled', 'active': 0})
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return result
