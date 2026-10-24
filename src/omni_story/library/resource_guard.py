"""Small process-safe limits for new batch workers; existing jobs opt out."""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import time


class ResourceLimit(RuntimeError):
    pass


def before_request(*, provider='list_price'):
    """Check opt-in batch limits before a request is recorded as submitted."""
    if os.environ.get('OMNI_WORKER_OUTPUT'):
        resource_check(required_bytes=16 * 1024 ** 2)
    if os.environ.get('OMNI_EVAL_BUDGET_FILE'):
        from ..evaluation.budget import reserve_request
        reserve_request(provider=provider)


def memory_available_bytes():
    try:
        for line in Path('/proc/meminfo').read_text().splitlines():
            if line.startswith('MemAvailable:'):
                return int(line.split()[1]) * 1024
        return os.sysconf('SC_AVPHYS_PAGES') * os.sysconf('SC_PAGE_SIZE')
    except (OSError, ValueError, AttributeError):
        raise ResourceLimit('resource_memory_available_unreadable') from None


def directory_size(path):
    """Count worker-owned bytes, without following links into shared media."""
    total = 0
    for root, dirs, files in os.walk(path, followlinks=False):
        dirs[:] = [name for name in dirs if not (Path(root) / name).is_symlink()]
        for name in files:
            file = Path(root) / name
            try:
                if not file.is_symlink():
                    total += file.stat().st_size
            except FileNotFoundError:
                pass
    return total


def _integer(name, default):
    try:
        value = int(os.environ.get(name, default))
    except (TypeError, ValueError):
        raise ResourceLimit('resource_invalid_setting:' + name) from None
    if value < 0:
        raise ResourceLimit('resource_invalid_setting:' + name)
    return value


def resource_check(output=None, *, min_free_bytes=None, max_worker_bytes=None,
                   required_bytes=0, minimum_memory_bytes=0):
    output = Path(output or os.environ.get('OMNI_WORKER_OUTPUT') or Path.cwd()).resolve()
    volume = output
    while not volume.exists():
        volume = volume.parent
    reserve = (_integer('OMNI_MIN_FREE_BYTES', 2 * 1024 ** 3)
               if min_free_bytes is None else min_free_bytes)
    maximum = (_integer('OMNI_WORKER_MAX_BYTES', 0)
               if max_worker_bytes is None else max_worker_bytes)
    if any(type(value) is not int or value < 0
           for value in (reserve, maximum, required_bytes, minimum_memory_bytes)):
        raise ResourceLimit('resource_invalid_limits')
    free = shutil.disk_usage(volume).free
    used = directory_size(output)
    remaining = maximum - used if maximum else None
    memory = memory_available_bytes()
    result = dict(free_bytes=free, reserve_bytes=reserve, worker_bytes=used,
                  remaining_worker_bytes=remaining, available_memory_bytes=memory)
    if free - reserve <= 0 or free - reserve < required_bytes:
        raise ResourceLimit(f'resource_disk_reserve:free={free}:reserve={reserve}')
    if remaining is not None and (remaining <= 0 or remaining < required_bytes):
        raise ResourceLimit(f'resource_worker_budget:used={used}:maximum={maximum}')
    if memory < minimum_memory_bytes:
        raise ResourceLimit(f'resource_memory_reserve:available={memory}:required={minimum_memory_bytes}')
    return result


@contextmanager
def ffmpeg_slot(resource_dir, slots=1):
    """The kernel releases a slot when its process exits, including crashes."""
    import fcntl
    if type(slots) is not int or not 1 <= slots <= 10:
        raise ResourceLimit('resource_invalid_ffmpeg_slots')
    root = Path(resource_dir)
    root.mkdir(parents=True, exist_ok=True)
    handle = None
    while handle is None:
        for index in range(slots):
            candidate = (root / f'ffmpeg_{index}.lock').open('a+b')
            try:
                fcntl.flock(candidate.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                candidate.close()
            else:
                handle = candidate
                break
        if handle is None:
            time.sleep(.2)
    try:
        yield handle.fileno()
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def install_ffmpeg_wrapper(resource_dir, *, real_ffmpeg=None, python=None):
    """Return a private PATH entry; arguments are forwarded as shell argv."""
    executable = Path(real_ffmpeg or shutil.which('ffmpeg') or '').resolve(strict=True)
    if not executable.is_file():
        raise ResourceLimit('resource_real_ffmpeg_missing')
    interpreter = Path(python or sys.executable).resolve(strict=True)
    directory = Path(resource_dir).resolve() / 'bin'
    directory.mkdir(parents=True, exist_ok=True)
    wrapper = directory / 'ffmpeg'
    content = ('#!/bin/sh\nexec ' + shlex.quote(str(interpreter)) +
               ' -m omni_story.library.resource_guard --real-ffmpeg ' +
               shlex.quote(str(executable)) + ' -- "$@"\n')
    if wrapper.exists() and wrapper.read_text(encoding='utf-8') != content:
        raise ResourceLimit('resource_wrapper_already_bound_to_different_runtime')
    wrapper.write_text(content, encoding='utf-8')
    wrapper.chmod(0o755)
    return directory


def _run_ffmpeg(executable, arguments):
    import resource
    root = os.environ.get('OMNI_RESOURCE_DIR')
    if not root or not os.environ.get('OMNI_WORKER_OUTPUT'):
        raise ResourceLimit('resource_batch_environment_missing')
    threads = _integer('OMNI_FFMPEG_THREADS', 2)
    if not 1 <= threads <= 16:
        raise ResourceLimit('resource_invalid_ffmpeg_threads')
    with ffmpeg_slot(root, _integer('OMNI_FFMPEG_SLOTS', 1)) as lock_fd:
        measured = resource_check()
        maximum = measured['free_bytes'] - measured['reserve_bytes']
        if measured['remaining_worker_bytes'] is not None:
            maximum = min(maximum, measured['remaining_worker_bytes'])
        # These pipeline commands have one output as the final argument. The
        # second threads option limits its encoder, not just the first decoder.
        args = list(arguments)
        if args and (not args[-1].startswith('-') or args[-1] == '-'):
            args[-1:-1] = ['-threads', str(threads)]
        command = [executable, '-threads', str(threads), '-filter_threads', str(threads),
                   '-filter_complex_threads', str(threads), *args]
        def limit_file():
            current = resource.getrlimit(resource.RLIMIT_FSIZE)
            limit = maximum if current[1] == resource.RLIM_INFINITY else min(maximum, current[1])
            resource.setrlimit(resource.RLIMIT_FSIZE, (limit, limit))
        child = subprocess.Popen(command, preexec_fn=limit_file, pass_fds=(lock_fd,))
        result = child.wait()
        # Recheck total worker growth too; file-size limits apply per file.
        resource_check()
        return result if result >= 0 else 128 - result


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < 3 or args[0] != '--real-ffmpeg' or args[2] != '--':
        print('resource_guard requires --real-ffmpeg ABSOLUTE_PATH -- ARGS', file=sys.stderr)
        return 2
    executable = Path(args[1])
    try:
        if not executable.is_absolute() or not executable.is_file():
            raise ResourceLimit('resource_real_ffmpeg_missing')
        return _run_ffmpeg(str(executable), args[3:])
    except (ResourceLimit, OSError) as error:
        print(str(error), file=sys.stderr)
        return 73


if __name__ == '__main__':
    raise SystemExit(main())
