"""Offline checks for the batch's costly boundaries, without model requests."""
from argparse import Namespace
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import json

import pytest

from omni_story.library import hot_batch as batch


def arguments(tmp_path, **changes):
    values = dict(home=tmp_path, output=tmp_path/'batch', library=tmp_path,
        visual_library=tmp_path, parent_task=None, audio_music_bed=None,
        asr_model_dir=tmp_path, max_candidates=10, freshness_hours=72,
        count=3, concurrency=2, min_free_gb=2, worker_disk_mb=512,
        worker_memory_mb=1024, render_workers=1, max_video_mb=256, dry_run=False)
    return Namespace(**(values | changes))


def test_low_disk_does_not_launch_or_capture(tmp_path):
    args = arguments(tmp_path)
    with patch.object(batch.shutil, 'disk_usage', return_value=SimpleNamespace(free=2*batch.GIB)), \
         patch.object(batch, 'memory_available_bytes', return_value=16*batch.GIB), \
         patch.object(batch.server_jobs, 'start') as start:
        with pytest.raises(batch.ResourceLimit, match='no_capacity_no_requests'):
            batch.start_batch(args, {'project_root': str(tmp_path)})
        start.assert_not_called()


@pytest.mark.parametrize('unknown,failed', [(False, False), (True, False), (False, True)])
def test_distinct_parallel_jobs_and_unknown_stops_new_work(tmp_path, unknown, failed):
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
            result = dict(delivery_kind='fine_candidate', status='rough_to_skill_completed',
                final_video=str(Path(output)/'fine.mp4'), content_delivery_ready=True)
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
        batch.run_batch(args)
    result = json.loads((args.output/'batch_result.json').read_text())
    assert max(peak) == 2
    assert seen_exclusions[:2] == [set(), {'1'}]
    if unknown:
        assert len(jobs) == 2
        assert result['status'] == 'stopped'
        assert (tmp_path/'shared/hot_batch_unknown.json').is_file()
    else:
        assert len(jobs) == 3
        assert seen_exclusions[2] == {'1', '2'}
        assert result['status'] == ('completed_with_failures' if failed else 'completed')
        assert result['counts'] == dict(succeeded=0 if failed else 3,
            failed_or_unresolved=3 if failed else 0, content_ready=0 if failed else 3)
