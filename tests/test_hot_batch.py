"""Offline checks for the batch's costly boundaries, without model requests."""
from argparse import Namespace
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import json

import pytest

from omni_story.library import hot_batch as batch
from omni_story.library.hot_pipeline import main
from omni_story.library.state import LibraryStopped
from omni_story.library.media import sha256_file


def arguments(tmp_path, **changes):
    values = dict(home=tmp_path, output=tmp_path/'batch', library=tmp_path,
        visual_library=tmp_path, parent_task=None, audio_music_bed=None,
        asr_model_dir=tmp_path, max_candidates=10, freshness_hours=72,
        count=3, concurrency=2, min_free_gb=2, worker_disk_mb=512,
        worker_memory_mb=1024, render_workers=1, max_video_mb=256, dry_run=False,
        capture_source='wellbyte',public_cache_root=None,prior_batch=None,prior_batch_sha256=None)
    return Namespace(**(values | changes))


def test_low_disk_does_not_launch_or_capture(tmp_path):
    args = arguments(tmp_path)
    with patch.object(batch.shutil, 'disk_usage', return_value=SimpleNamespace(free=2*batch.GIB)), \
         patch.object(batch, 'memory_available_bytes', return_value=16*batch.GIB), \
         patch.object(batch.server_jobs, 'start') as start:
        with pytest.raises(batch.ResourceLimit, match='no_capacity_no_requests'):
            batch.start_batch(args, {'project_root': str(tmp_path)})
        start.assert_not_called()


def test_capture_source_survives_supervisor_and_worker_commands(tmp_path):
    args = arguments(tmp_path, capture_source='public_browser',public_cache_root=tmp_path/'source')
    for action in ['_batch', '_run']:
        command = batch._command(args, action, args.output)
        assert command[command.index('--capture-source')+1] == 'public_browser'
        if action=='_batch':assert command[command.index('--public-cache-root')+1]==str(tmp_path/'source')


def test_single_job_start_keeps_explicit_public_source(tmp_path):
    with patch('omni_story.library.hot_pipeline._inputs', return_value={'project_root':str(tmp_path)}), \
         patch.object(batch.server_jobs, 'start', return_value={}) as launch:
        main(['--home', str(tmp_path), 'start', '--library', str(tmp_path),
              '--visual-library', str(tmp_path), '--output', str(tmp_path/'out'),
              '--capture-source', 'public_browser'])
    command=launch.call_args.args[0]
    assert command[command.index('--capture-source')+1]=='public_browser'


def test_capture_failure_cli_reports_failure_and_keeps_batch_receipt(tmp_path):
    args = arguments(tmp_path, count=5, concurrency=5)
    with patch('omni_story.library.hot_pipeline._inputs', return_value={'project_root':str(tmp_path)}), \
         patch('omni_story.discovery.hot_capture.capture_reference',
               side_effect=LibraryStopped('capture_non_json_response:http=502:no_replay')), \
         patch.object(batch, 'install_ffmpeg_wrapper', return_value=tmp_path), \
         patch.object(batch, 'memory_available_bytes', return_value=16*batch.GIB), \
         patch.object(batch.shutil, 'disk_usage', return_value=SimpleNamespace(free=32*batch.GIB)), \
         patch.object(batch.server_jobs, 'status', return_value={'state':'running'}), \
         patch.object(batch.server_jobs, 'start') as launch, \
         patch.object(batch.time, 'sleep'):
        code = main(['--home', str(tmp_path), '_batch', '--library', str(tmp_path),
                     '--visual-library', str(tmp_path), '--output', str(args.output),
                     '--asr-model-dir', str(tmp_path), '--count', '5', '--concurrency', '5'])
    assert code != 0
    launch.assert_not_called()
    result = json.loads((args.output/'batch_result.json').read_text())
    assert result['status'] == 'stopped'
    assert result['requested'] == 5 and result['launched'] == 0
    assert result['stop_reason'] == 'LibraryStopped:capture_non_json_response:http=502:no_replay'


def settled_prior(tmp_path):
    prior=tmp_path/'shared/jobs/prior';line=prior/'lines/001';line.mkdir(parents=True)
    reference=line/'reference.mp4';reference.write_bytes(b'actual received reference')
    final=line/'final.mp4';final.write_bytes(b'actual finished candidate')
    chain=line/'chain_result.json';chain.write_text('{}')
    result=dict(status='rough_to_skill_completed',content_delivery_ready=True,joint_quality_gate=True,delivery_kind='fine_candidate',
        final_video=str(final),final_sha256=sha256_file(final),chain_result_path=str(chain),
        chain_result_sha256=sha256_file(chain),capture=dict(path=str(reference),sha256=sha256_file(reference),
        evidence=dict(trend=dict(sentence_id='1'))))
    (line/'hot_result.json').write_text(json.dumps(result))
    row=dict(job_id='existing1',output=str(line),state='succeeded',trend_id='1',reference_sha256=sha256_file(reference))
    (prior/'batch_result.json').write_text(json.dumps(dict(status='stopped',requested=3,launched=1,jobs=[row],
        counts=dict(content_ready=1,failed_or_unresolved=0),stop_reason='LibraryStopped:public_hot_representative_missing')))
    args=arguments(tmp_path,output=tmp_path/'shared/jobs/remaining',prior_batch=prior,
        prior_batch_sha256=sha256_file(prior/'batch_result.json'))
    return args,row,result


@pytest.mark.parametrize('damage',['none','active','uncertain','final_changed','batch_changed','gate_false'])
def test_prior_batch_requires_settled_bound_ready_jobs(tmp_path,damage):
    args,row,result=settled_prior(tmp_path)
    if damage=='uncertain':
        (Path(row['output'])/'library_state.json').write_text(json.dumps(dict(calls=[dict(status='uncertain')])))
    if damage=='final_changed':Path(result['final_video']).write_bytes(b'changed')
    if damage=='batch_changed':(args.prior_batch/'batch_result.json').write_text('{}')
    if damage=='gate_false':
        result['joint_quality_gate']=False
        (Path(row['output'])/'hot_result.json').write_text(json.dumps(result))
    def status(path):
        return dict(state='running' if damage=='active' else 'failed' if Path(path)==args.prior_batch else 'succeeded',
                    supervisor_alive=damage=='active',job_id='existing1')
    with patch.object(batch.server_jobs,'status',side_effect=status):
        if damage=='none':assert batch._inherited_jobs(args)==[row]
        else:
            with pytest.raises(batch.ResourceLimit):batch._inherited_jobs(args)


def test_continuation_keeps_total_and_launches_only_remaining_distinct_jobs(tmp_path):
    args,prior_row,_=settled_prior(tmp_path)
    exclusions=[];launched=[]
    def capture(home,output,**options):
        number=str(len(exclusions)+2);exclusions.append(set(options['exclude_trend_ids']))
        assert prior_row['reference_sha256'] in options['exclude_reference_sha256s']
        output.mkdir(parents=True)
        value=dict(sha256=number,evidence=dict(trend=dict(sentence_id=number)))
        (output/'capture_receipt.json').write_text(json.dumps(value));return value
    def launch(command,output,cwd):
        output.mkdir(parents=True);launched.append(str(output))
        (output/'hot_result.json').write_text(json.dumps(dict(status='rough_to_skill_completed',
            delivery_kind='fine_candidate',content_delivery_ready=True,joint_quality_gate=True,final_video='result.mp4')))
        return dict(job_id='new'+str(len(launched)),state='starting')
    def status(path):
        if Path(path)==args.prior_batch:return dict(state='failed',supervisor_alive=False)
        if Path(path)==Path(prior_row['output']):return dict(state='succeeded',supervisor_alive=False,job_id='existing1')
        return dict(state='running' if Path(path)==args.output else 'succeeded',stop_requested=False)
    with patch('omni_story.library.hot_pipeline._inputs',return_value=dict(project_root=str(tmp_path))), \
         patch('omni_story.discovery.hot_capture.capture_reference',side_effect=capture), \
         patch.object(batch,'install_ffmpeg_wrapper',return_value=tmp_path), \
         patch.object(batch,'memory_available_bytes',return_value=16*batch.GIB), \
         patch.object(batch.shutil,'disk_usage',return_value=SimpleNamespace(free=32*batch.GIB)), \
         patch.object(batch.server_jobs,'status',side_effect=status), \
         patch.object(batch.server_jobs,'start',side_effect=launch),patch.object(batch.time,'sleep'):
        result=batch.run_batch(args)
    assert result['requested']==result['launched']==result['counts']['content_ready']==3
    assert result['jobs'][0]['output']==prior_row['output'] and len(launched)==2
    assert exclusions==[{'1'},{'1','2'}]
    assert [Path(p).name for p in launched]==['002','003']
    assert json.loads((args.output/'inherited_batch.json').read_text())['remaining_new_jobs']==2


@pytest.mark.parametrize('unknown,failed,unready,joint_failed', [
    (False, False, False, False), (True, False, False, False),
    (False, True, False, False), (False, False, True, False), (False, False, False, True)])
def test_distinct_parallel_jobs_and_unknown_stops_new_work(tmp_path, unknown, failed, unready, joint_failed):
    args = arguments(tmp_path)
    jobs, seen_exclusions, peak = {}, [], []

    def capture(home, output, **options):
        seen_exclusions.append(set(options['exclude_trend_ids']))
        number = len(seen_exclusions)
        output.mkdir(parents=True)
        value = dict(path=str(tmp_path/'video.mp4'), sha256=str(number),
                     evidence=dict(trend=dict(sentence_id=str(number))))
        (output/'capture_receipt.json').write_text(json.dumps(value))
        return value

    def launch(command, output, cwd):
        output.mkdir(parents=True)
        number = len(jobs)+1
        jobs[str(output)] = dict(job_id=str(number), state='running', polls=0)
        peak.append(sum(j['state']=='running' for j in jobs.values()))
        return dict(jobs[str(output)])

    def status(output):
        if Path(output) == args.output:
            return dict(state='running', stop_requested=False)
        item = jobs[str(output)]
        item['polls'] += 1
        if item['polls'] >= 5:
            item['state'] = 'failed' if failed else 'succeeded'
            result = dict(delivery_kind='candidate_needs_revision' if unready else 'fine_candidate',
                status='needs_audio_revision' if unready else 'rough_to_skill_completed',
                final_video=str(Path(output)/'fine.mp4'), content_delivery_ready=not unready,
                joint_quality_gate=not joint_failed)
            if not failed:
                (Path(output)/'hot_result.json').write_text(json.dumps(result))
        return dict(item)

    with patch('omni_story.library.hot_pipeline._inputs', return_value={'project_root':str(tmp_path)}), \
         patch('omni_story.discovery.hot_capture.capture_reference', side_effect=capture), \
         patch.object(batch, 'install_ffmpeg_wrapper', return_value=tmp_path), \
         patch.object(batch, 'memory_available_bytes', return_value=16*batch.GIB), \
         patch.object(batch.shutil, 'disk_usage', return_value=SimpleNamespace(free=32*batch.GIB)), \
         patch.object(batch.server_jobs, 'start', side_effect=launch), \
         patch.object(batch.server_jobs, 'status', side_effect=status), \
         patch.object(batch, '_admission_lock', return_value=nullcontext()), \
         patch.object(batch, '_unknown', side_effect=lambda output, terminal:
             'original_unknown_ledger' if unknown and terminal else None), \
         patch.object(batch.time, 'sleep'):
        code = main(['--home', str(tmp_path), '_batch', '--library', str(tmp_path),
                     '--visual-library', str(tmp_path), '--output', str(args.output),
                     '--asr-model-dir', str(tmp_path), '--count', str(args.count),
                     '--concurrency', str(args.concurrency)])
    result = json.loads((args.output/'batch_result.json').read_text())
    assert code == (1 if unknown or failed or unready or joint_failed else 0)
    assert max(peak) == 2
    assert seen_exclusions[:2] == [set(), {'1'}]
    if unknown:
        assert len(jobs) == 2
        assert result['status'] == 'stopped'
        assert (tmp_path/'shared/hot_batch_unknown.json').is_file()
    else:
        assert len(jobs) == 3
        assert seen_exclusions[2] == {'1', '2'}
        assert result['status'] == ('completed_with_failures' if failed or unready or joint_failed else 'completed')
        assert result['counts'] == dict(succeeded=0 if failed or unready or joint_failed else 3,
            failed_or_unresolved=3 if failed or unready or joint_failed else 0,
            content_ready=0 if failed or unready or joint_failed else 3)
