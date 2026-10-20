"""One frozen experiment, using the existing detached workers and resource guard."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import time
from types import SimpleNamespace

from ..library import server_jobs
from ..library.hot_batch import capacity, _unknown, unknown_guard_active
from ..library.media import sha256_file
from ..library.resource_guard import install_ffmpeg_wrapper
from ..library.server_cli import settings, load_history, credential
from ..library.state import file_lock, json_sha, write_json
from .budget import stop_new_requests
from .manifest import load_manifest


def now():
    return datetime.now(timezone.utc).isoformat()


def model_declined_selection(output):
    """A received empty selection is a failed case, not a broken batch runner."""
    output = Path(output)
    failure, ledger = output/'chain_failure.json', output/'library_state.json'
    if not failure.is_file() or not ledger.is_file():
        return False
    if json.loads(failure.read_text()).get('error') != 'no_watched_window_available_for_planning':
        return False
    state = json.loads(ledger.read_text())
    proofs = state.get('artifacts', {}).get('rough_no_observed_footage', [])
    if not proofs or not state.get('calls') or any(call['status'] != 'received' for call in state['calls']):
        return False
    entry = proofs[-1]
    proof = json.loads(Path(entry['path']).read_text())
    if json_sha(proof) != entry['sha256']:
        return False
    search_path = output/f"search_{proof['round']}.json"
    search = json.loads(search_path.read_text()) if search_path.exists() else {}
    return (all(proof.get(key) == 0 for key in ('requested_window_count', 'watched_window_count', 'new_observed_window_count'))
        and bool(search) and not search.get('windows') and not search.get('observation_ids')
        and not search.get('retrieval_queries') and not any(slot.get('observation_ids') for slot in search.get('slots', [])))


def model_plan_rejected(output):
    """Keep a proven search/plan proposal failure while admitting other cases."""
    from ..library.contracts import parse_model_json
    output = Path(output)
    try:
        failure = json.loads((output/'chain_failure.json').read_text())
        if (failure.get('type') != 'ModelProtocolRejected' or
                not re.fullmatch(r'model_protocol_repair_exhausted:(?:(?:search|plan)_[0-9]+|chain_[A-Za-z0-9_]+_fine_(?:plan|revise))',
                    failure.get('error', ''))):
            return False
        stage = failure['error'].split(':', 1)[1]
        calls = json.loads((output/'library_state.json').read_text())['calls']
        if len(calls) < 2 or any(call['status'] != 'received' for call in calls):
            return False
        pair = calls[-2:]
        if ([call['name'] for call in pair] != [stage, stage+'_repair'] or
                pair[1].get('repair_of') != pair[0]['id']):
            return False
        for attempt, call in enumerate(pair):
            folder = output/'calls'/call['id']
            request, response, proof = [json.loads((folder/name).read_text()) for name in
                ('request.json', 'response.json', 'protocol_failure.json')]
            text = '\n'.join(c['text'] for c in response['result']['content'] if c.get('type') == 'text')
            if (json_sha(request) != call['request_sha256'] or json_sha(response) != call['response_sha256'] or
                    proof['attempt'] != attempt or proof['model_text'] != text or not proof.get('error') or
                    not isinstance(parse_model_json(text), dict)):
                return False
        return True
    except (OSError, ValueError, TypeError, KeyError):
        return False


def reference_policy_refusal(output, reference_sha, *, role='user'):
    """Bind a known input/output refusal; only input refusal covers both methods."""
    from ..library.pipeline import _http_evidence, _outcome_unknown
    output = Path(output)
    try:
        failure = json.loads((output/'chain_failure.json').read_text())
        if failure.get('type') == 'ReferencePolicyRefused':
            proof = failure['provider_refusal']
            origin = Path(proof['source_output'])
            if origin != output and (origin/'library_state.json').is_file():
                return proof if proof == reference_policy_refusal(origin, reference_sha, role=role) else None
            return None
        state = json.loads((output/'library_state.json').read_text())
        if state['input_lock']['reference_sha256'] != reference_sha:
            return None
        for call in state['calls']:
            if call['name'] not in {'reference', 'reference_repair'} or call['status'] != 'failed_known':
                continue
            request = json.loads((output/'calls'/call['id']/'request.json').read_text())
            if json_sha(request) != call['request_sha256']:
                continue
            entries = _http_evidence(output, call['id'])
            sent = [entry for entry in entries if entry.get('type') == 'request']
            if len(sent) != 1 or _outcome_unknown(entries):
                continue
            for entry in entries:
                if entry.get('type') != 'response' or entry.get('status') != 400 or entry.get('seq') != sent[0]['seq']:
                    continue
                body = json.loads(entry['body'])
                if str(body.get('error', {}).get('code')) == '1301' and any(
                        item.get('role') == role for item in body.get('contentFilter', [])):
                    return dict(source_output=str(output), reference_sha256=reference_sha,
                        call_id=call['id'], request_sha256=call['request_sha256'],
                        response_seq=entry['seq'], response_body_sha256=hashlib.sha256(entry['body'].encode()).hexdigest(),
                        reason=('provider_input_refused_http400_code1301_no_resend' if role == 'user'
                                else 'provider_output_refused_http400_code1301_no_resend'))
    except (OSError, ValueError, TypeError, KeyError):
        return None
    return None


def reject_reference(output, proof):
    """Record an actual preflight failure, with zero model submissions."""
    if reference_policy_refusal(proof['source_output'], proof['reference_sha256']) != proof:
        raise RuntimeError('reference_refusal_evidence_changed')
    write_json(Path(output)/'chain_failure.json', dict(type='ReferencePolicyRefused',
        error='shared_reference_input_refused_http400_code1301', provider_refusal=proof))
    raise SystemExit(1)


def code_fingerprint():
    root = Path(__file__).resolve().parents[2]
    digest = hashlib.sha256()
    for path in sorted(root.rglob('*')):
        if path.is_file() and '__pycache__' not in path.parts:
            digest.update(str(path.relative_to(root)).encode()+b'\0'+path.read_bytes())
    return digest.hexdigest()


@contextmanager
def environment(values):
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


def validate_runtime(manifest, home, output):
    home, output = Path(home).resolve(strict=True), Path(output).resolve()
    if not output.is_relative_to((home/'shared/jobs').resolve()):
        raise ValueError('evaluation_outputs_require_shared_jobs')
    spec = manifest['output_spec']
    if (spec['width'], spec['height'], spec['fps']) != (1280, 720, 30):
        raise ValueError('evaluation_runtime_supports_1280x720_30fps')
    for key in ('media_root', 'visual_root'):
        if not Path(manifest['library'][key]).is_dir():
            raise ValueError('evaluation_library_directory_missing:'+key)
    if unknown_guard_active(home):
        raise RuntimeError('evaluation_unsettled_historical_guard_no_new_work')
    config = settings(home)
    load_history(config)
    credential(home)
    model_dir = Path(config.get('asr_model_dir', ''))
    if not model_dir.is_absolute() or not all((model_dir/name).is_file()
            for name in ('model.bin', 'config.json', 'tokenizer.json')):
        raise ValueError('evaluation_requires_configured_local_asr_model_no_download')
    return config


def worker_command(home, manifest, run, output, *, visual_root=None, asr_model_dir=None, refusal=None):
    if refusal:
        return [sys.executable, '-c',
            'import json,sys;from omni_story.evaluation.runner import reject_reference;reject_reference(sys.argv[1],json.loads(sys.argv[2]))',
            str(output), json.dumps(refusal)]
    case = next(row for row in manifest['cases'] if row['case_id'] == run['case_id'])
    command = [sys.executable, '-m', 'omni_story.library.server_cli', '--home', str(home),
        '_run', '--reference', case['reference']['path'], '--library', manifest['library']['media_root'],
        '--visual-library', str(visual_root or manifest['library']['visual_root']), '--creative-audio', '--output', str(output)]
    command.extend(['--output-spec', json.dumps({key: manifest['output_spec'][key] for key in ('width', 'height', 'fps')})])
    if asr_model_dir is not None:
        command.extend(['--asr-model-dir', str(asr_model_dir)])
    if run['method_id'] == 'baseline_single_pass':
        command.append('--stop-after-rough')
    elif run['method_id'] != 'full':
        raise ValueError('unsupported_evaluation_method')
    return command


def fork_library(source, target):
    """Each run gets the same initial cards; new observations cannot leak to peers."""
    source, target = Path(source), Path(target)
    shutil.copytree(source, target, ignore=shutil.ignore_patterns('media', 'provenance', '.write.lock'))
    for name in ('media', 'provenance'):
        if (source/name).exists():
            (target/name).symlink_to((source/name).resolve(), target_is_directory=True)
    return target


def frozen_asset_stats(manifest, *, verify_hashes=False):
    snapshot = Path(manifest['library']['manifest_path'])
    if sha256_file(snapshot) != manifest['library']['manifest_sha256']:
        raise ValueError('evaluation_library_snapshot_manifest_changed')
    saved = json.loads(snapshot.read_text())
    if saved.get('snapshot_version') != 1 or not saved.get('files'):
        raise ValueError('evaluation_requires_library_snapshot_version_1')
    roots = [Path(manifest['library'][key]).resolve() for key in ('media_root', 'visual_root')]
    current = {str(path.resolve()) for root in roots for path in root.rglob('*')
               if path.is_file() and not path.name.endswith(('.lock', '-journal', '-wal', '-shm'))}
    if current != {row['path'] for row in saved['files']}:
        raise ValueError('evaluation_library_file_set_changed')
    stats = []
    for row in saved['files']:
        path = Path(row['path']).resolve(strict=True)
        if verify_hashes and sha256_file(path) != row['sha256']:
            raise ValueError('evaluation_frozen_library_asset_changed:'+str(path))
        stat = path.stat()
        stats.append((str(path), stat.st_size, stat.st_mtime_ns, stat.st_ino))
    return stats


def run_content_keys(manifest, config):
    cases = {row['case_id']: row for row in manifest['cases']}
    snapshot = json.loads(Path(manifest['library']['manifest_path']).read_text())
    library_content = json_sha(sorted(row['sha256'] for row in snapshot['files']))
    return [json_sha(dict(reference_sha256=cases[run['case_id']]['reference']['sha256'],
        method=run['method_id'], library_content_sha256=library_content,
        output_spec=manifest['output_spec'], code_sha256=code_fingerprint(), runtime_sha256=json_sha(config)))
        for run in manifest['planned_runs']]


def registered_experiment(manifest, home, root):
    root = Path(root).resolve(strict=True)
    for _ in range(40):
        registered = json.loads((Path(home)/'shared/evaluation_registry.json').read_text())[manifest['experiment_id']]
        if registered.get('job_id'):
            break
        time.sleep(.05)  # parent publishes the durable ID just after detached launch.
    if (registered['manifest_sha256'] != manifest['source_manifest_sha256'] or
            registered['output'] != str(root) or
            registered['job_id'] != server_jobs.status(root)['job_id'] or
            registered.get('continuations', [registered])[-1]['code_sha256'] != code_fingerprint()):
        raise RuntimeError('evaluation_launch_binding_changed')
    return registered


def start(args):
    manifest = load_manifest(args.manifest)
    config = validate_runtime(manifest, args.home, args.output)
    budget = manifest['budget']
    if not budget.get('max_cost_cny') or not budget.get('max_model_calls'):
        raise ValueError('evaluation_requires_nonzero_authorized_budget')
    if not 1 <= args.concurrency <= 5:
        raise ValueError('evaluation_concurrency_must_be_1_to_5')
    cost = budget.get('cost_control')
    if cost is None:
        raise ValueError('evaluation_paid_execution_requires_verified_currency_profile')
    if (cost != dict(authorization_id='eval_20261019_cny100',
            spend_envelope_cny=90, per_operation_reserve_cny=1, safety_buffer_cny=10) or
            budget['max_cost_cny'] != 100):
        raise ValueError('evaluation_unverified_currency_profile')
    if cost is not None and os.environ.get('DASHSCOPE_BASE_URL',
            'https://dashscope.aliyuncs.com/compatible-mode/v1').rstrip('/') != 'https://dashscope.aliyuncs.com/compatible-mode/v1':
        raise ValueError('evaluation_currency_profile_requires_beijing_endpoint')
    # One durable experiment identity prevents a new directory from replaying it.
    registry = args.home/'shared/evaluation_registry.json'
    with file_lock(registry.with_suffix('.lock')):
        authorization = args.home/'shared/evaluation_authorization_20261019_cny100.json'
        if cost is not None and authorization.exists():
            raise RuntimeError('evaluation_currency_authorization_already_claimed_no_new_budget')
        saved = json.loads(registry.read_text()) if registry.exists() else {}
        content_keys = run_content_keys(manifest, config)
        if manifest['experiment_id'] in saved:
            raise RuntimeError('evaluation_identity_already_launched_no_replay')
        for prior in saved.values():
            if set(content_keys).intersection(prior.get('run_content_keys', [])):
                raise RuntimeError('evaluation_same_run_content_claimed_no_id_or_directory_replay')
            folder = Path(prior['output'])
            if not (folder/'.omni-server/job.json').exists():
                raise RuntimeError('evaluation_previous_launch_requires_reconciliation')
            state = server_jobs.status(folder)['state']
            if state not in server_jobs.TERMINAL_STATES or _unknown(folder, terminal=True):
                raise RuntimeError('evaluation_previous_active_or_unknown_no_new_work')
        command = [sys.executable, '-m', 'omni_story.evaluation.cli', '--home', str(args.home),
            '_batch', '--manifest', str(args.manifest.resolve()), '--output', str(args.output.resolve()),
            '--concurrency', str(args.concurrency)]
        saved[manifest['experiment_id']] = dict(output=str(args.output.resolve()),
            manifest_sha256=sha256_file(args.manifest), code_sha256=code_fingerprint(),
            run_content_keys=content_keys, claimed_at=now(), status='launch_claimed')
        write_json(registry, saved)
        if cost is not None:
            # One human authorization cannot become another ¥100 by changing IDs.
            with authorization.open('x', encoding='utf-8') as handle:
                json.dump(dict(output=str(args.output.resolve()), manifest_sha256=sha256_file(args.manifest),
                    cap_cny=100, reserved_buffer_cny=10, created_at=now()), handle, indent=2)
        job = server_jobs.start(command, args.output, Path(config['project_root']))
        saved[manifest['experiment_id']].update(job_id=job['job_id'], status='launched')
        write_json(registry, saved)
        return job


def continue_pending(args):
    """After diagnosis, admit only the original unstarted suffix; never retry a run.

    Keep the original authorization, budget, deadline, receipts and denominator.
    A new release is recorded as an experiment deviation, not a clean frozen run.
    """
    manifest = load_manifest(args.manifest)
    config = validate_runtime(manifest, args.home, args.output)
    root = args.output.resolve(strict=True)
    if not 1 <= args.concurrency <= 5 or not args.reason.strip():
        raise ValueError('evaluation_continuation_requires_reason_and_safe_concurrency')
    registry_path = args.home/'shared/evaluation_registry.json'
    with file_lock(registry_path.with_suffix('.lock')):
        registry = json.loads(registry_path.read_text())
        registered = registry[manifest['experiment_id']]
        original = server_jobs.status(root)
        if (registered['output'] != str(root) or registered['manifest_sha256'] != manifest['source_manifest_sha256'] or
                original['job_id'] != registered['job_id'] or original['state'] not in server_jobs.TERMINAL_STATES):
            raise RuntimeError('evaluation_continuation_requires_original_settled_experiment')
        for prior in registered.get('continuations', []):
            if server_jobs.status(prior['control'])['state'] not in server_jobs.TERMINAL_STATES:
                raise RuntimeError('evaluation_continuation_already_active')
        if _unknown(root, terminal=True):
            raise RuntimeError('evaluation_continuation_unknown_no_new_work')
        budget_path = root/'request_budget.json'
        budget = json.loads(budget_path.read_text())
        authorization = json.loads((args.home/'shared/evaluation_authorization_20261019_cny100.json').read_text())
        if (budget['batch_job_id'] != original['job_id'] or budget['experiment_id'] != manifest['experiment_id'] or
                budget['manifest_sha256'] != registered['manifest_sha256'] or authorization['output'] != str(root) or
                authorization['manifest_sha256'] != registered['manifest_sha256'] or
                budget.get('stop_reason') or budget.get('generation_stop_reason') or time.time() >= budget['deadline_unix']):
            raise RuntimeError('evaluation_continuation_original_budget_unavailable')
        jobs = json.loads((root/'batch_result.json').read_text())['jobs']
        if (len(jobs) >= len(manifest['planned_runs']) or
                [row['run_id'] for row in jobs] != [row['run_id'] for row in manifest['planned_runs'][:len(jobs)]]):
            raise RuntimeError('evaluation_continuation_requires_unstarted_planned_suffix')
        for row in jobs:
            expected = root/'runs'/row['case_id']/row['method_id']
            if Path(row['output']) != expected or server_jobs.status(expected)['state'] not in server_jobs.TERMINAL_STATES:
                raise RuntimeError('evaluation_continuation_child_not_settled')
        index = len(registered.get('continuations', []))+1
        control = root/'continuations'/f'{index:03d}'
        checkpoint = root/f'continuation_{index:03d}.json'
        claim = dict(control=str(control), code_sha256=code_fingerprint(), reason=args.reason,
            created_at=now(), jobs=jobs, budget_sha256_before=sha256_file(budget_path),
            previous_result_sha256=sha256_file(root/'batch_result.json'),
            previous_result=json.loads((root/'batch_result.json').read_text()),
            original_experiment=json.loads((root/'experiment.json').read_text()),
            manifest_sha256=registered['manifest_sha256'])
        if checkpoint.exists() or control.exists():
            raise RuntimeError('evaluation_continuation_claim_exists_no_replay')
        write_json(checkpoint, claim)
        registered.setdefault('continuations', []).append(dict(claim, checkpoint=str(checkpoint)))
        write_json(registry_path, registry)
        command = [sys.executable, '-m', 'omni_story.evaluation.cli', '--home', str(args.home), '_continue',
            '--manifest', str(args.manifest.resolve()), '--output', str(root), '--checkpoint', str(checkpoint),
            '--concurrency', str(args.concurrency)]
        return server_jobs.start(command, control, Path(config['project_root']))


def assess_settled_jobs(manifest, root, jobs, home, base_env):
    from .judge import evaluate
    errors = []
    for row in jobs:
        folder = Path(row['output'])
        if not (folder/'chain_result.json').is_file():
            continue
        progress_path = root/'progress.json'
        progress = json.loads(progress_path.read_text()) if progress_path.exists() else {}
        write_json(progress_path, dict(progress, phase='independent_evaluation',
            evaluating_run_id=row['run_id'], updated_at=now()))
        with environment({**base_env, 'OMNI_WORKER_OUTPUT': folder,
                'OMNI_WORKER_MAX_BYTES': 768*1024**2}):
            try:
                evaluate(manifest, root, row['run_id'], home=home)
            except Exception as error:
                errors.append(dict(run_id=row['run_id'], error_type=type(error).__name__))
        if _unknown(root, terminal=True):
            reason = 'unknown_independent_evaluation_no_new_work'
            stop_new_requests(root/'request_budget.json', reason)
            return errors, reason
    return errors, None


def run_batch(args):
    manifest = load_manifest(args.manifest)
    config = validate_runtime(manifest, args.home, args.output)
    root = args.output.resolve()
    budget = manifest['budget']
    fingerprint = code_fingerprint()
    checkpoint = getattr(args, 'checkpoint', None)
    resumed = json.loads(checkpoint.read_text()) if checkpoint else None
    control = Path(resumed['control']) if resumed else root
    # Serialize batch admissions with existing production batches, not workers.
    with file_lock(args.home/'shared/hot_batch.lock'):
        registered_experiment(manifest, args.home, root)
        if resumed:
            registered = json.loads((args.home/'shared/evaluation_registry.json').read_text())[manifest['experiment_id']]
            if (registered['continuations'][-1]['checkpoint'] != str(checkpoint) or
                    resumed['code_sha256'] != fingerprint or resumed['manifest_sha256'] != manifest['source_manifest_sha256']):
                raise RuntimeError('evaluation_continuation_binding_changed')
        # A crashed/accidentally invoked coordinator can never reset reservations.
        with (control/'.batch_once').open('x') as handle:
            handle.write(fingerprint)
        assets = frozen_asset_stats(manifest, verify_hashes=True)
        if not resumed:
            write_json(root/'manifest.json', manifest)
            write_json(root/'experiment.json', dict(code_sha256=fingerprint, started_at=now(),
            requested_concurrency=args.concurrency, automatic_restart=False,
            currency_cap_scope=('receipt_reconciled_conservative_CNY90_envelope_CNY10_buffer_not_invoice'
                if budget.get('cost_control') else 'planning_limit; not_provider_billing_cap'),
            request_count_scope='vision_stage_tts_av_operations; opencode_agent_transport_recorded_separately'))
        budget_path = root/'request_budget.json'
        if not resumed:
            write_json(budget_path, dict(experiment_id=manifest['experiment_id'],
            manifest_sha256=manifest['source_manifest_sha256'], batch_job_id=server_jobs.status(root)['job_id'],
            max_model_calls=budget['max_model_calls'],
            per_run_max_model_calls=budget.get('per_run_max_model_calls', budget['max_model_calls']),
            generation_call_limit=max(1, budget['max_model_calls']-len(manifest['planned_runs'])),
            deadline_unix=time.time()+budget['max_wall_time_s'], requests=[],
            **({'cost_control': dict(budget['cost_control'], experiment_root=str(root))}
               if budget.get('cost_control') else {})))
        resource_dir = args.home/'shared/hot_resources'
        wrapper = install_ffmpeg_wrapper(resource_dir)
        base_env = dict(PATH=str(wrapper)+os.pathsep+os.environ['PATH'],
            OMNI_RESOURCE_DIR=resource_dir, OMNI_FFMPEG_SLOTS=1, OMNI_FFMPEG_THREADS=2,
            OMNI_MIN_FREE_BYTES=2*1024**3, OMNI_ASR_THREADS=2, OMP_NUM_THREADS=2,
            OMNI_EVAL_BUDGET_FILE=budget_path, HF_HUB_OFFLINE='1')
        # capture is frozen, so reserve no video downloads; retain hot_batch's 64 MiB overhead.
        limits = SimpleNamespace(home=args.home, concurrency=args.concurrency, min_free_gb=2,
            worker_disk_mb=768, worker_memory_mb=1024, max_video_mb=0)
        jobs, reason = (resumed['jobs'] if resumed else []), None
        acknowledged_failures = {row['run_id'] for row in jobs if row['state'] in server_jobs.TERMINAL_STATES}
        # Resolve existing candidates before admitting another long generation
        # tranche. No worker is active here; in-flight reviews are never mistaken
        # for unknown calls of a terminal editor by the admission loop.
        evaluation_errors = []
        if resumed:
            evaluation_errors, reason = assess_settled_jobs(manifest, root, jobs, args.home, base_env)
        while True:
            active = []
            for row in jobs:
                current = server_jobs.status(row['output'])
                row['state'] = current['state']
                terminal = current['state'] in server_jobs.TERMINAL_STATES
                unknown = _unknown(row['output'], terminal)
                if unknown or current['state'] == 'unknown':
                    reason = 'unknown_child_no_new_work:'+str(unknown or row['output'])
                elif terminal and current['state'] != 'succeeded' and row['run_id'] not in acknowledged_failures:
                    case_sha = next(case['reference']['sha256'] for case in manifest['cases'] if case['case_id'] == row['case_id'])
                    if reference_policy_refusal(row['output'], case_sha):
                        row['failure_class'] = 'provider_input_refused_no_resend'
                    elif reference_policy_refusal(row['output'], case_sha, role='assistant'):
                        row['failure_class'] = 'provider_output_refused_no_resend'
                    elif model_declined_selection(row['output']):
                        row['failure_class'] = 'model_declined_selection'
                    elif model_plan_rejected(row['output']):
                        row['failure_class'] = 'received_model_plan_rejected'
                    else:
                        reason = reason or 'failed_child_needs_main_diagnosis:'+row['run_id']
                if not terminal and current['state'] != 'unknown':
                    active.append(row)
            if server_jobs.status(control).get('stop_requested'):
                reason = 'user_stop_no_new_work'
            budget_state = json.loads(budget_path.read_text())
            if time.time() >= budget_state['deadline_unix']:
                reason = reason or 'experiment_deadline_no_new_requests'
            if len(budget_state['requests']) >= budget_state['max_model_calls']:
                reason = reason or 'experiment_request_budget_exhausted'
            if sum(row.get('scope', 'generation') == 'generation' for row in budget_state['requests']) >= budget_state['generation_call_limit']:
                reason = reason or 'generation_budget_reserved_for_independent_judge'
            if code_fingerprint() != fingerprint:
                reason = 'code_changed_during_frozen_experiment'
            if frozen_asset_stats(manifest) != assets:
                reason = 'library_changed_during_frozen_experiment'
            if reason and not reason.startswith('failed_child_needs_main_diagnosis:'):
                stop_new_requests(budget_path, reason, generation_only=reason in {
                    'generation_budget_reserved_for_independent_judge', 'resource_capacity_exhausted_no_new_work'})
            measured = capacity(limits, active)
            write_json(root/'progress.json', dict(updated_at=now(), jobs=jobs,
                planned=len(manifest['planned_runs']), active=len(active), stop_reason=reason, capacity=measured))
            if len(jobs) == len(manifest['planned_runs']) or reason:
                if not active:
                    break
            elif measured['additional_workers']:
                run = manifest['planned_runs'][len(jobs)]
                case = next(row for row in manifest['cases'] if row['case_id'] == run['case_id'])
                if sha256_file(case['reference']['path']) != case['reference']['sha256']:
                    reason = 'frozen_reference_changed'
                    continue
                output = root/'runs'/run['case_id']/run['method_id']
                refusal = next((proof for prior in jobs if prior['case_id'] == run['case_id']
                    for proof in [reference_policy_refusal(prior['output'], case['reference']['sha256'])] if proof), None)
                visual_root = (None if refusal else fork_library(manifest['library']['visual_root'],
                    root/'library_snapshots'/run['case_id']/run['method_id']))
                with environment({**base_env, 'OMNI_WORKER_OUTPUT': output,
                        'OMNI_WORKER_MAX_BYTES': 768*1024**2, 'OMNI_EVAL_RUN_ID': run['run_id']}):
                    job = server_jobs.start(worker_command(args.home, manifest, run, output,
                        visual_root=visual_root, asr_model_dir=config['asr_model_dir'], refusal=refusal),
                                            output, Path(config['project_root']))
                jobs.append(dict(**run, job_id=job['job_id'], output=str(output), state=job['state']))
                continue
            elif not active:
                reason = 'resource_capacity_exhausted_no_new_work'
                continue
            time.sleep(2)
        if reason in (None, 'generation_budget_reserved_for_independent_judge',
                      'resource_capacity_exhausted_no_new_work') or (reason or '').startswith('failed_child_needs_main_diagnosis:'):
            errors, assessment_reason = assess_settled_jobs(manifest, root, jobs, args.home, base_env)
            evaluation_errors.extend(errors)
            reason = assessment_reason or reason
        result = dict(jobs=jobs, stop_reason=reason, evaluation_errors=evaluation_errors, finished_at=now(),
                      planned=len(manifest['planned_runs']), launched=len(jobs))
        write_json(root/'batch_result.json', result)
        from .collect import collect, execution_interval
        from .report import summarize, write_reports
        records = collect(manifest, root)
        result['finished_at'] = now()  # Include actual decoding/collection, not just worker time.
        write_json(root/'batch_result.json', result)
        write_reports(summarize(manifest, records, execution=execution_interval(root, records)), root/'reports')
        return result
