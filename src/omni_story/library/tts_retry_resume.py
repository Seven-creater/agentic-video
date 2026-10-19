"""Admit one continuation after a human-authorized TTS replacement succeeded."""
import json
from pathlib import Path

from . import server_jobs
from .media import sha256_file
from .state import LibraryStopped
from .tts_retry import validate_replacement

POLICY = 'human_authorized_tts_audio_resume_v1'


def validate(output, proof_path):
    output, proof_path = Path(output).resolve(strict=True), Path(proof_path).resolve(strict=True)
    proof = json.loads(proof_path.read_text())
    if proof.get('policy') != POLICY or proof.get('output') != str(output):
        raise LibraryStopped('tts_resume:scope_changed')
    saved = json.loads((output/'library_state.json').read_text())
    baseline_path = Path(proof['baseline_snapshot'])
    baseline = json.loads(baseline_path.read_text())
    if (sha256_file(baseline_path) != proof['state_sha256'] or saved != baseline
            or saved.get('task_id') != proof['task_id']
            or len(saved['calls']) != 13 or saved['request_count'] != 13
            or any(c['status'] != 'received' for c in saved['calls'][:10]+saved['calls'][11:])
            or saved['calls'][10]['status'] != 'failed_known'
            or saved['calls'][-1]['name'] != 'rough_audio_timing_1'):
        raise LibraryStopped('tts_resume:exact_received_prefix_required')
    parent = json.loads(Path(proof['caption_proof']).read_text())
    original = json.loads(Path(parent['baseline_snapshot']).read_text())
    if (parent['policy'] != 'main_plan_caption_reflow_v1' or parent['output'] != str(output)
            or saved['calls'][:12] != original['calls'] or saved['input_lock'] != original['input_lock']):
        raise LibraryStopped('tts_resume:original_caption_prefix_changed')
    failure_path = output/'caption_reflow_repair_v1.chain_failure.json'
    failure = json.loads(failure_path.read_text())
    if failure.get('type') != 'TTSUnknown' or failure.get('error') != 'tts_request_outcome_unknown:API_transport_failed:28':
        raise LibraryStopped('tts_resume:wrong_failure')
    tts_path = Path(proof['tts_ledger']).resolve(strict=True)
    if not tts_path.is_relative_to(output/'rough_audio/tts'):
        raise LibraryStopped('tts_resume:tts_outside_original_edit')
    replacement = json.loads(tts_path.read_text())
    validate_replacement(tts_path, replacement)
    required = {str(output/'library_state.json'), str(baseline_path), str(output/'mcp_stop'),
        str(failure_path), str(output/'result.json'), str(output/'plan_1.json'),
        str(output/'render_1/render_result.json'), str(output/'render_1/final.mp4'),
        str(output/'caption_reflow_repair_v1.claim.json'), proof['caption_proof'], str(tts_path)}
    for record in replacement['authorized_retry_once'].values():
        if isinstance(record, dict):
            required.add(record['path'])
    for call in saved['calls']:
        folder = output/'calls'/call['id']
        required.add(str(folder/'request.json'))
        required.add(str(folder/('failure.json' if call['status']=='failed_known' else 'response.json')))
    required.update(parent['protected_sha256'])
    if not required <= set(proof['protected_sha256']):
        raise LibraryStopped('tts_resume:complete_original_pins_required')
    for name, digest in proof['protected_sha256'].items():
        if sha256_file(name) != digest:
            raise LibraryStopped('tts_resume:original_file_changed:' + name)
    if (not proof.get('failed_jobs') or
            not any(Path(row['output']).resolve() == output.parent for row in proof['failed_jobs'])):
        raise LibraryStopped('tts_resume:original_worker_binding_required')
    for row in proof['failed_jobs']:
        job = server_jobs.status(row['output'])
        if (job['job_id'] != row['job_id'] or job['state'] != 'failed'
                or job.get('supervisor_alive') or job.get('stop_requested')
                or server_jobs._group_running(job.get('child_pid'))):
            raise LibraryStopped('tts_resume:old_job_not_settled')
    if (output/'mcp_stop').read_text() != 'reference_rough_skill_settled' or (output/'chain_result.json').exists():
        raise LibraryStopped('tts_resume:stopped_or_already_completed')
    return proof, None
