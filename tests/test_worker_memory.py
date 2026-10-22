"""One delegated, real kernel OOM test; no media services or paid calls."""
import json
import subprocess
import sys
import time

import pytest

from omni_story.library.worker_memory import WorkerGroups, LIMIT_BYTES, current_group


def test_kernel_limit_kills_worker_tree_and_preserves_sibling(tmp_path):
    if sys.platform != 'linux' or current_group().name != 'coordinator':
        pytest.skip('Run under systemd Delegate=memory DelegateSubgroup=coordinator')
    groups=WorkerGroups()
    hog=groups.create('memory_fault',1);healthy=groups.create('independent',1)
    good_ready=tmp_path/'healthy_ready';release=tmp_path/'release'
    child_pid=tmp_path/'hog_child'
    healthy_code=('from pathlib import Path;import time;'+
        'Path('+repr(str(good_ready))+').write_text("ready");'+
        '\nwhile not Path('+repr(str(release))+').exists():time.sleep(.05)')
    hog_code=('import subprocess,sys,time;from pathlib import Path;'+
        'child=subprocess.Popen([sys.executable,"-c","import time;time.sleep(90)"]);'+
        'Path('+repr(str(child_pid))+').write_text(str(child.pid));'+
        'chunks=[];'+
        '\nfor i in range(1280):chunks.append(bytearray(1024*1024));time.sleep(.001)')
    def launch(group,code):
        return subprocess.Popen([sys.executable,'-m','omni_story.library.worker_memory',str(group),
            sys.executable,'-c',code],start_new_session=True)
    sibling=launch(healthy,healthy_code);over=None
    try:
        deadline=time.monotonic()+10
        while not good_ready.exists() and time.monotonic()<deadline:time.sleep(.05)
        assert good_ready.exists()
        over=launch(hog,hog_code)
        over.wait(timeout=45)
        assert over.returncode==-9
        usage=None
        deadline=time.monotonic()+10
        while usage is None and time.monotonic()<deadline:
            usage=groups.finish(hog)
            if usage is None:time.sleep(.05)
        assert usage and usage['memory_limit_bytes']==LIMIT_BYTES and usage['swap_limit_bytes']==0
        assert usage['events']['oom_group_kill']>=1 and usage['events']['oom_kill']>=2
        assert sibling.poll() is None and child_pid.exists()
        release.write_text('finish');sibling.wait(timeout=10)
        assert sibling.returncode==0
        sibling_usage=groups.finish(healthy)
        assert sibling_usage and sibling_usage['events']['oom_kill']==0
        print('KERNEL_MEMORY_PROOF '+json.dumps(dict(hog=usage,sibling=sibling_usage,coordinator_survived=True)))
    finally:
        for process,group in ((over,hog),(sibling,healthy)):
            if process and process.poll() is None:groups.kill(group);process.wait(timeout=10)
            if groups.finish(group) is not None:groups.remove(group)
