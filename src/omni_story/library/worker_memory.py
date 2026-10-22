"""Kernel-enforced memory isolation inside a delegated systemd service.

The coordinator lives in DelegateSubgroup=coordinator. Workers enter a sibling
cgroup before importing the media/model workflow; all descendants inherit it.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys
import uuid

LIMIT_BYTES = 1024 ** 3
CGROUP_ROOT = Path('/sys/fs/cgroup')


def current_group():
    line = next((line for line in Path('/proc/self/cgroup').read_text().splitlines()
                 if line.startswith('0::')), None)
    if line is None:
        raise RuntimeError('reliable_hard_memory_requires_cgroup_v2')
    return CGROUP_ROOT / line[3:].lstrip('/')


def verify_limits(group):
    expected = {'memory.max': str(LIMIT_BYTES), 'memory.swap.max': '0', 'memory.oom.group': '1'}
    if any((group / name).read_text().strip() != value for name, value in expected.items()):
        raise RuntimeError('reliable_hard_memory_limit_not_applied')


class WorkerGroups:
    def __init__(self):
        try:
            current = current_group()
            self.root = current.parent
            if (current.name != 'coordinator' or not self.root.name.endswith('.service') or
                    'memory' not in (self.root / 'cgroup.controllers').read_text().split()):
                raise RuntimeError('missing_delegation')
            if 'memory' not in (self.root / 'cgroup.subtree_control').read_text().split():
                (self.root / 'cgroup.subtree_control').write_text('+memory')
        except (OSError, RuntimeError) as error:
            raise RuntimeError('reliable_hard_memory_requires_delegated_systemd_service') from error

    def create(self, case_id, attempt):
        # Case IDs have already passed manifest validation. UUIDs prevent a
        # restarted coordinator from inheriting another attempt's OOM counters.
        group = self.root / ('worker_' + case_id + '_' + str(attempt) + '_' + uuid.uuid4().hex[:12])
        group.mkdir()
        for name, value in {'memory.max': str(LIMIT_BYTES), 'memory.swap.max': '0', 'memory.oom.group': '1'}.items():
            (group / name).write_text(value)
        verify_limits(group)
        return group

    def _bound(self, group):
        group = Path(group)
        if group.parent != self.root or not group.name.startswith('worker_'):
            raise RuntimeError('reliable_worker_cgroup_outside_service')
        return group

    def kill(self, group):
        group = self._bound(group)
        if group.exists():
            (group / 'cgroup.kill').write_text('1')

    def finish(self, group):
        group = self._bound(group)
        if not group.exists():
            return dict(available=False, reason='cgroup_lost_during_service_restart')
        if 'populated 1' in (group / 'cgroup.events').read_text():
            self.kill(group)
            return None  # Reap all descendants before freeing admission or resuming.
        events = dict((key, int(value)) for key, value in
                      (line.split() for line in (group / 'memory.events').read_text().splitlines()))
        return dict(available=True, cgroup=str(group), memory_limit_bytes=int((group / 'memory.max').read_text()),
            swap_limit_bytes=int((group / 'memory.swap.max').read_text()), oom_group=True, events=events,
            peak_memory_bytes=int((group / 'memory.peak').read_text()) if (group / 'memory.peak').exists() else None)

    def remove(self, group):
        group = self._bound(group)
        if group.exists():
            group.rmdir()


def enter_and_exec(group, command):
    group = Path(group).resolve(strict=True)
    if not group.is_relative_to(CGROUP_ROOT) or group == CGROUP_ROOT:
        raise RuntimeError('reliable_worker_cgroup_invalid')
    verify_limits(group)
    (group / 'cgroup.procs').write_text(str(os.getpid()))
    if current_group() != group:
        raise RuntimeError('reliable_worker_did_not_enter_cgroup')
    verify_limits(group)
    os.execvpe(command[0], command, os.environ)


if __name__ == '__main__':
    enter_and_exec(sys.argv[1], sys.argv[2:])
