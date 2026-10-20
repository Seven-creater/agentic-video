"""Collect real artifacts, terminal jobs and immutable evaluation receipts."""
from __future__ import annotations

from fractions import Fraction
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess

from ..library import server_jobs
from ..library.hot_batch import _unknown
from ..library.media import probe_media, sha256_file
from ..library.state import write_json


def execution_interval(root, records):
    root = Path(root)
    experiment = json.loads((root/'experiment.json').read_text()) if (root/'experiment.json').exists() else {}
    batch = json.loads((root/'batch_result.json').read_text()) if (root/'batch_result.json').exists() else {}
    progress = json.loads((root/'progress.json').read_text()) if (root/'progress.json').exists() else {}
    final = (not any(row['status'] == 'running' for row in records) and
             [row['run_id'] for row in batch.get('jobs', [])] ==
             [row['run_id'] for row in progress.get('jobs', [])] and
             (batch.get('finished_at') or '') >= (progress.get('updated_at') or ''))
    finish = batch.get('finished_at') if final else None
    if finish:
        # Actual receipt/check times survive copying and repeated report generation.
        times = [row.get(key) for row in records for key in
                 ('finished_at', 'technical_checked_at', 'quality_received_at')]
        latest = max(datetime.fromisoformat(value).timestamp() for value in [finish]+times if value)
        finish = datetime.fromtimestamp(latest, timezone.utc).isoformat()
    return dict(started_at=experiment.get('started_at'), finished_at=finish,
        endpoint_scope='latest_settled_coordinator_or_recorded_check_receipt; report regeneration time excluded')


def operation_usage(folder, reservations):
    counts = dict(vision_stage=0, tts=0, qwen_av=0)
    for path in Path(folder).rglob('library_state.json'):
        counts['vision_stage'] += len(json.loads(path.read_text())['calls'])
    for path in Path(folder).rglob('state.json'):
        saved = json.loads(path.read_text())
        if saved.get('schema_version') == 'tts_line_v1':
            counts['tts'] += saved['request_count']
        elif saved.get('policy') == 'actual-audio-video-review-v1':
            counts['qwen_av'] += 1
    return dict(**counts, total=sum(counts.values()), reserved=len(reservations),
        independent_evaluation_reserved=sum(r.get('scope') == 'independent_evaluation' for r in reservations),
        scope='business_ledger_operations; opencode_agent_transport_not_individually_counted')


def production_delivered(result, method):
    """A blind score cannot convert a production rejection into a delivery."""
    visual = result.get('final_visual_review', {})
    if (not result.get('final_sha256') or visual.get('status') != 'pass' or
            visual.get('final_sha256') != result['final_sha256']):
        return False
    if method == 'baseline_single_pass':
        # stop_after_rough deliberately leaves the finecut joint gate false.
        return result.get('status') == 'rough_audio_ready' and result.get('content_delivery_ready') is True
    audio = (result.get('finecut', {}).get('creative_audio', {}) or {})
    return (method == 'full' and result.get('status') == 'rough_to_skill_completed' and
            result.get('joint_quality_gate') is True and audio.get('status') == 'delivered' and
            result.get('content_delivery_ready', audio.get('content_delivery_ready')) is True and
            audio.get('joint_quality_gate') is True and audio.get('technical_delivery_verified') is True)


def technical_check(video, spec):
    measured = probe_media(video)
    stream = next(row for row in measured['streams'] if row['index'] == measured['video_stream_index'])
    checks = dict(width=stream['width'] == spec['width'], height=stream['height'] == spec['height'],
        fps=abs(float(Fraction(stream['avg_frame_rate']))-spec['fps']) < .01,
        duration=spec['duration_min_s'] <= measured['duration_s'] <= spec['duration_max_s'],
        audio=any(row.get('codec_type') == 'audio' for row in measured['streams']))
    decoded = subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-xerror', '-i', str(video),
        '-map', '0:v:0', '-map', '0:a:0', '-f', 'null', '-'], capture_output=True, timeout=600)
    checks['fully_decoded'] = decoded.returncode == 0 and not decoded.stderr.strip()
    return dict(sha256=sha256_file(video), duration_s=measured['duration_s'], checks=checks,
                technical_complete=all(checks.values()))


def collect(manifest, root):
    root = Path(root).resolve()
    experiment_path = root/'experiment.json'
    experiment = json.loads(experiment_path.read_text()) if experiment_path.exists() else {}
    from .runner import code_fingerprint
    original_code = experiment.get('code_sha256')
    continuations = [json.loads(path.read_text()) for path in sorted(root.glob('continuation_*.json'))]
    changed = (original_code not in (None, code_fingerprint()) or
        any(claim.get('code_sha256') not in (None, original_code) for claim in continuations))
    budget_path = root/'request_budget.json'
    reservations = json.loads(budget_path.read_text())['requests'] if budget_path.exists() else []
    rows = []
    for index, run in enumerate(manifest['planned_runs']):
        folder = root/'runs'/run['case_id']/run['method_id']
        if not (folder/'.omni-server/job.json').is_file():
            continue  # report retains this frozen run as pending.
        job = server_jobs.status(folder)
        terminal = job['state'] in server_jobs.TERMINAL_STATES
        row = dict(**run, status='running', technical_complete=False,
            generation_code_sha256=original_code, source_continuation=None,
            manual_intervention=None if changed else False, protocol_deviation=changed,
            production_delivered=False,
            started_at=job.get('started_at') or job.get('created_at'), finished_at=job.get('finished_at'),
            cost=dict(amount=None, currency='CNY', complete=False), evidence_paths=[str(folder/'.omni-server/job.json')])
        for claim in continuations:
            if index >= len(claim['jobs']):
                row.update(generation_code_sha256=claim['code_sha256'], source_continuation=claim['control'])
        row['operations'] = operation_usage(folder, [r for r in reservations if r['run_id'] == run['run_id']])
        if terminal:
            row['status'] = 'failed' if job['state'] != 'succeeded' else 'candidate'
            failure = folder/'chain_failure.json'
            if job['state'] != 'succeeded' and failure.is_file():
                saved_failure = json.loads(failure.read_text())
                row['failure_reason'] = saved_failure.get('type', 'Error')+':'+saved_failure.get('error', 'unspecified')
                row['evidence_paths'].append(str(failure))
        if job['state'] == 'unknown' or _unknown(folder, terminal):
            row.update(status='unknown', failure_reason='unsettled_request_no_replay')
        result_path = folder/'chain_result.json'
        if terminal and result_path.is_file():
            result = json.loads(result_path.read_text())
            row['evidence_paths'].append(str(result_path))
            row['internal_quality_gate'] = result.get('joint_quality_gate')
            row['production_delivered'] = job['state'] == 'succeeded' and production_delivered(result, run['method_id'])
            row['production_status'] = result.get('status')
            video = result.get('final_video') or result.get('candidate_video')
            expected = result.get('final_sha256') or result.get('candidate_sha256')
            if video and expected and Path(video).is_file() and sha256_file(video) == expected:
                proof_path = folder/'technical_check.json'
                identity = dict(video_sha256=expected, output_spec=manifest['output_spec'])
                prior = json.loads(proof_path.read_text()) if proof_path.exists() else None
                proof = technical_check(video, manifest['output_spec'])
                if prior and prior.get('input') == identity:
                    if prior['result'] != proof:
                        raise ValueError('evaluation_technical_proof_modified_or_incomplete')
                else:
                    prior = dict(input=identity, result=proof, checked_at=datetime.now(timezone.utc).isoformat())
                    write_json(proof_path, prior)
                row.update(artifact_path=video, artifact_sha256=expected,
                           technical_complete=proof['technical_complete'], technical_checked_at=prior.get('checked_at'))
                row['evidence_paths'].append(str(proof_path))
                judgment_path = folder/'external_evaluation/judgment.json'
                if judgment_path.exists():
                    from .judge import load_judgment
                    case = next(case for case in manifest['cases'] if case['case_id'] == run['case_id'])
                    judgment = load_judgment(judgment_path, case['reference']['sha256'], expected)
                    row.update(quality_evaluator='independent',
                        quality_scope=('final_full_video' if row['production_delivered'] else 'candidate_full_video'),
                        reviewer_model=judgment['reviewer_model'], shares_model_with_internal=judgment['shares_model_with_internal'],
                        quality_received_at=json.loads(Path(judgment['receipt_path']).read_text()).get('received_at'),
                        quality_scores=judgment['scores'], severe_defects=judgment['severe_defects'],
                        quality_pass=judgment['quality_pass'])
                    row['evidence_paths'].append(str(judgment_path))
                    if job['state'] == 'succeeded' and row['status'] != 'unknown' and row['technical_complete']:
                        row['status'] = ('completed' if row['production_delivered'] else 'candidate') if judgment['quality_pass'] else 'rejected'
            else:
                row.update(status='failed', failure_reason='result_artifact_missing_or_hash_changed')
        rows.append(row)
    write_json(root/'run_records.json', rows)
    return rows
