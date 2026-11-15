#!/usr/bin/env python3
"""Serial scene_v1 batch with recorded ancestry and restartable progress.

Delivered references use --parent-task automatically. After a general code fix,
rerunning the same batch resumes its stopped jobs; unchanged failed code is not
retried in a paid loop. Existing server admission checks still settle receipts.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import time

from omni_story.library.state import write_json
from omni_story.library import server_jobs


def read(path):
    return json.loads(path.read_text()) if path.is_file() else {}


def code_sha():
    digest = hashlib.sha256()
    source = Path(__file__).resolve().parents[1]/'src/omni_story/library'
    for path in sorted(p for p in source.iterdir() if p.suffix in ('.py', '.mjs') and not p.name.startswith('._')):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def delivered_parent(jobs, reference_sha, visual_library_root=None):
    candidates = []
    for path in jobs.rglob('reference_reading.json'):
        if visual_library_root is not None:
            config = read(path.parent/'library_state.json').get('input_lock', {}).get('configuration', {})
            if config.get('visual_library_root') != visual_library_root:
                continue
        if read(path).get('reference_sha256') == reference_sha and read(path.parent/'run_outcome.json').get('outcome') == 'delivered':
            candidates.append(path.parent)
    return max(candidates, key=lambda p: (p/'run_outcome.json').stat().st_mtime, default=None)


def received_attempt_parent(attempts):
    """An early startup failure must not hide the last usable task ledger."""
    return next((Path(a['output']) for a in reversed(attempts)
                 if (Path(a['output'])/'library_state.json').is_file()), None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home', type=Path, required=True)
    parser.add_argument('--server', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True, help='Batch journal directory; videos stay under parent evaluations.')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--material-profile', type=Path)
    parser.add_argument('--revision-of', type=Path, help='Explicitly revise this unready delivered task, preserving its original output.')
    parser.add_argument('--revision-reason')
    parser.add_argument('references', type=Path, nargs='+')
    args = parser.parse_args()
    if bool(args.revision_of) != bool(args.revision_reason) or (args.revision_of and len(args.references) != 1):
        parser.error('--revision-of and --revision-reason require exactly one reference')
    home, output = args.home.resolve(), args.output.resolve()
    journal = output/'batch.json'
    batch = read(journal) or dict(references={}, created_at=time.time())
    from omni_story.library.material_profile import default_path, load
    profile, binding = None, None
    selected = args.material_profile or batch.get('material_profile') or default_path(home)
    if selected:
        profile, binding = load(selected)
        if batch['references'] and batch.get('material_profile') != binding['path']:
            raise ValueError('material_profile:use_new_batch_for_collection_change')
        batch['material_profile'] = binding['path']
    library_root = profile['visual_library_root'] if profile else None
    version = code_sha()
    references = [p.resolve(strict=True) for p in args.references]
    if not args.dry_run:
        batch.pop('summary', None)
        write_json(journal, batch)
    for reference in references:
        key = hashlib.sha256(reference.read_bytes()).hexdigest()
        row = batch['references'].setdefault(key, dict(reference=str(reference), attempts=[]))
        latest = row['attempts'][-1] if row['attempts'] else None
        if latest:
            target = Path(latest['output'])
            job = server_jobs.status(target) if (target/'.omni-server/job.json').is_file() else {}
            outcome = read(target/'run_outcome.json')
            if str(outcome.get('error') or '').startswith(('provider_content_rejected:', 'opencode_vision_job_failed_no_retry:')):
                from omni_story.library.opencode_provider import official_rejection
                from omni_story.library.pipeline import _http_evidence
                call_id = outcome.get('last_call_id')
                rejection = official_rejection(_http_evidence(target, call_id)) if call_id else None
                if rejection and rejection['category'] == 'provider_content_rejected':
                    row.update(outcome='provider_rejected', error='provider_content_rejected:' + rejection['provider_code'],
                               provider_rejection=rejection)
                    write_json(journal, batch)
                    continue
            if (outcome.get('outcome') in ('delivered', 'delivered_with_review_notes') and
                    Path(outcome.get('final_video') or target/'final_subtitled.mp4').is_file()):
                row['outcome'] = outcome['outcome']
                continue
            active = job.get('state') in ('starting', 'running')
            if job.get('state') == 'unknown':
                row['outcome'] = 'unknown_supervisor_state'
                continue
            if not active and latest['code_sha256'] == version and (target/'library_state.json').is_file():
                row['outcome'] = outcome.get('outcome', 'start_failed')
                continue
            received_parent = received_attempt_parent(row['attempts'])
            parent = received_parent or delivered_parent(home/'shared/jobs', key, library_root)
            if received_parent:
                # Resume admission refuses a ledger with unsettled uncertain
                # calls (no implicit replay), which would deadlock this reference.
                # A fresh run in a new ledger is the only way forward then.
                ledger = read(parent/'library_state.json')
                if any(call.get('status') == 'uncertain' for call in ledger.get('calls', [])):
                    parent = delivered_parent(home/'shared/jobs', key, library_root)
            option = '--resume-from' if received_parent and parent == received_parent else '--parent-task'
        else:
            active = False
            parent = delivered_parent(home/'shared/jobs', key, library_root)
            option = '--parent-task'
        if args.revision_of and not active:
            parent, option = args.revision_of.resolve(strict=True), '--parent-task'
        if not active:
            # Attempt numbers are unique per journal, but directories live in the
            # parent's shared namespace: a fresh journal counting from 1 can collide
            # with a settled attempt of an earlier batch, which omni-server refuses.
            # Skip existing directories unless this journal owns them.
            attempt = len(row['attempts'])+1
            while True:
                target = ((parent/'evaluations') if parent else output)/f'{output.name}_{reference.parent.name}_{key[:12]}_{attempt}'
                if not target.exists() or any(a.get('output') == str(target) for a in row['attempts']):
                    break
                attempt += 1
            command = [str(args.server.resolve()), 'start', '--reference', str(reference), '--output', str(target),
                '--library', str(home/'shared/data/videos'), '--asr', '--creative-audio', '--creative-style', 'picture_aware',
                '--material-flow', 'scene_v1', '--asr-model-dir', str(home/'shared/library_models/faster-whisper-small-536b066/from-local'),
                '--visual-library', str(home/'shared/data/visual_library/three_films_v2')]
            if profile:
                command[command.index('--library')+1] = profile['library']
                command[command.index('--visual-library')+1] = profile['visual_library_root']
                command += ['--material-index', profile['material_index_root'], '--material-profile', binding['path']]
            if parent:
                command += [option, str(parent)]
            if args.revision_of:
                command += ['--revision-reason', args.revision_reason]
            print(json.dumps(dict(reference=str(reference), command=command), ensure_ascii=False), flush=True)
            if args.dry_run:
                continue
            latest = dict(output=str(target), code_sha256=version, command=command)
            for field in ('outcome', 'status', 'error', 'duration_s', 'content_delivery_ready',
                          'fallback_level', 'quality_status'):
                row.pop(field, None)
            row['attempts'].append(latest)
            write_json(journal, batch)
            started = subprocess.run(command, capture_output=True, text=True)
            latest['start_returncode'] = started.returncode
            latest['start_result'] = (started.stdout+started.stderr)[-4000:]
            write_json(journal, batch)
            if started.returncode:
                # The start never ran, so this code version has not actually been
                # tried; drop the attempt or a rerun would mistake the stale
                # directory outcome for "unchanged code already failed here".
                row['attempts'].remove(latest)
                row['outcome'] = 'start_failed'
                write_json(journal, batch)
                continue
        if args.dry_run:
            print(json.dumps(dict(reference=str(reference), status='already_running'), ensure_ascii=False))
            continue
        while server_jobs.status(target).get('state') in ('starting', 'running'):
            time.sleep(15)
        outcome = read(target/'run_outcome.json')
        row['outcome'] = outcome.get('outcome', 'missing_outcome')
        row['status'] = outcome.get('status')
        row['error'] = outcome.get('error')
        result = read(target/'dialogue_result.json')
        if result:
            row['duration_s'] = result['timeline'][-1]['end_s']
            row['content_delivery_ready'] = result['content_delivery_ready']
            row['fallback_level'] = result.get('fallback_level', 0)
            row['quality_status'] = result.get('quality_status')
        write_json(journal, batch)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    if args.dry_run:
        return 0
    rows = list(batch['references'].values())
    batch['summary'] = dict(total=len(rows), delivered=sum(r.get('outcome') == 'delivered' for r in rows),
        with_review_notes=sum(r.get('outcome') == 'delivered_with_review_notes' for r in rows),
        without_resume=sum(r.get('outcome') == 'delivered' and len(r['attempts']) == 1 for r in rows),
        degraded=sum(r.get('outcome') == 'delivered' and (r.get('fallback_level') or 0) > 0 for r in rows))
    write_json(journal, batch)
    print(json.dumps(batch['summary'], ensure_ascii=False), flush=True)
    return int(batch['summary']['delivered']+batch['summary']['with_review_notes'] != len(rows))


if __name__ == '__main__':
    raise SystemExit(main())
