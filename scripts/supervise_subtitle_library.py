#!/usr/bin/env python3
"""Continue unrelated windows after an authenticated provider content rejection.

Rejected media is quarantined, never retried, edited or resubmitted. All other
failures need attention. Original ledgers remain immutable and self-contained.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from omni_story.library.pipeline import _http_evidence
from omni_story.library.state import file_lock, json_sha, write_json


def read(path):
    return json.loads(Path(path).read_text())


def rejected_scope(run):
    state = read(run/'library_state.json')
    if any(c['status'] in {'submitted','uncertain'} for c in state['calls']):
        raise RuntimeError('unresolved_request_no_automatic_continuation')
    call = state['calls'][-1]
    if call['status'] != 'failed_known':
        raise RuntimeError('failure_requires_attention')
    request = read(run/'calls'/call['id']/'request.json')
    if json_sha(request) != call['request_sha256']:
        raise RuntimeError('rejected_request_changed')
    entries = _http_evidence(run, call['id'])
    sent = [e for e in entries if e.get('type') == 'request']
    replies = [e for e in entries if e.get('type') == 'response']
    if (len(sent) != 1 or len(replies) != 1 or sent[0]['seq'] != replies[0]['seq']
            or any(e.get('type') == 'unknown_result' for e in entries)
            or replies[0].get('status') != 400
            or str(json.loads(replies[0]['body']).get('error',{}).get('code')) != '1301'):
        raise RuntimeError('only_confirmed_400_1301_may_be_quarantined')
    scope = request.get('observation_scope', {})
    if scope.get('kind') not in {'continuous_window','sparse_contact_sheet'}:
        raise RuntimeError('rejected_source_interval_missing')
    return dict(scope=scope, reason='provider_content_rejection_quarantine_not_retried',
        evidence=dict(ledger_path=str(run/'library_state.json'), call_id=call['id'],
            request_sha256=call['request_sha256'], native_response_sha256=json_sha(replies[0]),
            http_status=400, provider_code='1301'))


def launch_worker(home, root, run, attempts, config, receipt=None):
    """Launch only a fresh, explicit run; a received/unknown ledger is never reset."""
    if (run/'library_state.json').exists() or (run/'worker.json').exists():
        raise RuntimeError('fresh_run_required_no_implicit_replay')
    config_path=run/'build_config.json'
    write_json(config_path,config)
    env=os.environ.copy()
    release=Path(__file__).resolve().parents[1]
    env['PYTHONPATH']=str(release/'src')+':'+str(release/'vendor')
    env['PATH']=str(home/'runtime/node/bin')+':'+env.get('PATH','')
    command=[sys.executable,'-u','-m','omni_story.library.visual_indexer','build',
        '--home',str(home),'--library',str(home/'shared/data/videos_library_02'),
        '--store',str(root),'--output',str(run),'--configuration',str(config_path),
        '--window-seconds',str(config.get('window_max_s',60))]
    with (run/'worker.log').open('ab') as log:
        process=subprocess.Popen(command,stdout=log,stderr=log,stdin=subprocess.DEVNULL,env=env)
        write_json(run/'launch.json',dict(pid=process.pid,run=str(run),started_at=time.time()))
        write_json(root/'active_build.json',dict(status='running',run=str(run),worker_pid=process.pid,
            supervisor_pid=os.getpid(),attempts=attempts,updated_at=time.time(),
            quarantined_windows=len(config.get('excluded_ranges',[])),
            quarantine_receipt=str(receipt) if receipt else None))
        write_json(root/'publication_status.json',dict(status='waiting_for_observations',
            active_run=str(run),updated_at=time.time()))
        process.wait()


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--home',required=True,type=Path)
    parser.add_argument('--root',required=True,type=Path)
    parser.add_argument('--initial-run',required=True,type=Path)
    args=parser.parse_args()
    home,root=args.home,args.root
    active=root/'active_build.json'
    with file_lock(root/'.supervisor.lock'):
        run=Path(read(active)['run']) if active.exists() else args.initial_run
        attempts=read(active).get('attempts',[]) if active.exists() else [str(run)]
        consecutive=0
        if not active.exists() or read(active).get('status') == 'prepared':
            launch_worker(home,root,run,attempts,read(run/'build_config.json'))
        while True:
            worker=read(run/'worker.json') if (run/'worker.json').exists() else {}
            if worker.get('status') == 'completed' and (run/'result.json').exists():
                write_json(active,dict(status='publishing',run=str(run),attempts=attempts,
                    supervisor_pid=os.getpid(),updated_at=time.time()))
                subprocess.run([sys.executable,str(Path(__file__).with_name('finalize_subtitle_library.py')),
                    '--home',str(home),'--root',str(root),'--run',str(run)],check=True)
                write_json(active,dict(status='completed',run=str(run),attempts=attempts,
                    supervisor_pid=os.getpid(),updated_at=time.time(),publication=read(root/'publication_status.json')))
                return
            if worker.get('status') != 'stopped':
                raise RuntimeError('worker_not_terminal_no_automatic_restart')
            if not str(worker.get('error','')).startswith('provider_content_rejected:'):
                raise RuntimeError('worker_failure_requires_attention:'+str(worker.get('error','')))
            quarantine=rejected_scope(run)
            accepted=read(run/'progress.json').get('new_observations_this_run',0)
            consecutive = 1 if accepted else consecutive+1
            if consecutive > 3:
                raise RuntimeError('repeated_provider_rejections_require_attention')
            config=dict(read(run/'library_state.json')['input_lock']['configuration'])
            excluded=list(config.get('excluded_ranges',[]))
            banned=quarantine['scope']
            plan=read(run/'window_plan.json')
            # Exclude context-overlap windows too; no portion of the rejected input
            # is deliberately sent again in a neighboring observation.
            for interval in plan['windows']:
                if interval['start_s'] < banned['source_end_s'] and banned['source_start_s'] < interval['end_s']:
                    entry=dict(quarantine,scope=dict(kind='continuous_window',source_sha256=plan['source_sha256'],
                        source_start_s=interval['start_s'],source_end_s=interval['end_s']))
                    if entry['scope'] != banned:
                        entry['reason']='context_overlap_with_provider_quarantine'
                    if not any(e['scope']==entry['scope'] for e in excluded):
                        excluded.append(entry)
            config['excluded_ranges']=excluded
            receipt=root/'quarantine'/('quarantine_'+json_sha(quarantine)[:16]+'.json')
            write_json(receipt,dict(rejection=quarantine,excluded_windows=excluded,at=time.time()))
            new_run=run.parent/f'build_20261111_continue_{len(attempts):03d}'
            if new_run.exists():
                raise RuntimeError('continuation_directory_already_exists')
            (new_run/'source_catalog').mkdir(parents=True)
            shutil.copy2(run/'source_catalog/inventory.json',new_run/'source_catalog/inventory.json')
            run=new_run
            attempts.append(str(run))
            launch_worker(home,root,run,attempts,config,receipt)


if __name__=='__main__':
    try:
        main()
    except BaseException as error:
        if '--root' in sys.argv:
            root=Path(sys.argv[sys.argv.index('--root')+1]);path=root/'active_build.json'
            old=read(path) if path.exists() else {}
            write_json(path,dict(old,status='needs_attention',error=str(error)[:1000],updated_at=time.time()))
        raise
