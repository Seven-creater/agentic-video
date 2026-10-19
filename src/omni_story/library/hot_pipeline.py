"""One server job: capture a current same-style trend, then edit and voice it."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sys
import uuid

from . import server_cli, server_jobs
from .media import sha256_file
from .state import LibraryStopped, write_json


def _now():
    return datetime.now(timezone.utc).isoformat()


def _start_defaults(args, parser):
    path = args.home / 'hot-video.json'
    saved = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    args.capture_source = args.capture_source or saved.get('capture_source', 'wellbyte')
    if args.capture_source not in {'wellbyte', 'public_browser'}:
        parser.error('invalid capture_source in hot-video.json')
    for name in ('library', 'visual_library', 'parent_task', 'audio_music_bed'):
        if getattr(args, name) is None and saved.get(name):
            value = Path(saved[name])
            setattr(args, name, value if value.is_absolute() else args.home / value)
    missing = [name for name in ('library', 'visual_library') if getattr(args, name) is None]
    if args.output is None:
        label = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '_' + uuid.uuid4().hex[:8]
        base = (args.parent_task / 'evaluations' if args.parent_task else
                Path(saved.get('output_root', args.home / 'shared/runs')))
        args.output = base / ('hot_' + label)
    if missing:
        parser.error('missing arguments: ' + ', '.join(missing) + '; provide CLI options or home/hot-video.json')


def _asr_model_dir(args, config):
    selected = args.asr_model_dir or config.get('asr_model_dir')
    if selected:
        selected = Path(selected)
        if not selected.is_absolute():
            selected = args.home / selected
        candidates = [selected.resolve()]
    else:
        candidates = [path.parent.resolve() for path in
            (args.home / 'shared/library_models').rglob('config.json')
            if (path.parent / 'model.bin').is_file()]
    if not candidates:
        raise ValueError('offline ASR model missing; use --asr-model-dir with config.json and model.bin')
    if len(candidates) != 1:
        raise ValueError('offline ASR model ambiguous; select one with --asr-model-dir')
    model = candidates[0]
    if not all((model / name).is_file() for name in ('config.json', 'model.bin')):
        raise ValueError(f'offline ASR model missing config.json or model.bin: {model}')
    return model


def _inputs(args):
    if getattr(args, 'public_cache_root', None) is not None and args.capture_source != 'public_browser':
        raise ValueError('public-cache-root requires public_browser capture')
    library = args.library.resolve(strict=True)
    visual_library = args.visual_library.resolve(strict=True)
    if not library.is_dir() or not any(library.iterdir()) or not visual_library.is_dir():
        raise ValueError('library must be nonempty and visual-library must be a directory')
    if args.max_candidates < 1 or args.freshness_hours <= 0:
        raise ValueError('max-candidates and freshness-hours must be positive')
    output = args.output.resolve()
    if args.parent_task:
        server_cli.parent_baseline(args.parent_task, output / 'edit')
    config = server_cli.settings(args.home)
    args.asr_model_dir = _asr_model_dir(args, config)
    server_cli.load_history(config)
    server_cli.credential(args.home)
    return config


def _editing_arguments(args, reference):
    command = ['--home', str(args.home.resolve()), '_run', '--reference', str(reference),
               '--library', str(args.library.resolve(strict=True)),
               '--visual-library', str(args.visual_library.resolve(strict=True)),
               '--output', str(args.output.resolve() / 'edit'), '--creative-audio',
               '--reference-context', str((args.output / 'capture/capture_receipt.json').resolve(strict=True))]
    for name in ('parent_task', 'audio_music_bed', 'asr_model_dir'):
        value = getattr(args, name)
        if value is not None:
            command += ['--' + name.replace('_', '-'), str(value.resolve(strict=True))]
    return command


def _run(args):
    _inputs(args)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    # A direct invocation also cannot replay capture or editing after interruption.
    with (output / 'hot_input.json').open('x', encoding='utf-8') as handle:
        json.dump(dict(created_at=_now(), library=str(args.library.resolve()),
            visual_library=str(args.visual_library.resolve()),
            parent_task=str(args.parent_task.resolve()) if args.parent_task else None,
            audio_music_bed=str(args.audio_music_bed.resolve()) if args.audio_music_bed else None,
            asr_model_dir=str(args.asr_model_dir),
            max_candidates=args.max_candidates, freshness_hours=args.freshness_hours,
            capture_source=args.capture_source),
            handle, ensure_ascii=False, indent=2)
    stage = 'capture'
    progress_path = output / 'hot_progress.json'
    write_json(progress_path, dict(stage=stage, updated_at=_now()))
    try:
        supplied = getattr(args, 'reference_receipt', None)
        if supplied:
            captured = json.loads(supplied.read_text(encoding='utf-8'))
            server_cli.reference_context_file(supplied, Path(captured['path']))
            (output / 'capture').mkdir()
            shutil.copyfile(supplied, output / 'capture/capture_receipt.json')
        else:
            from omni_story.discovery.hot_capture import capture_reference
            if args.capture_source == 'public_browser':
                from omni_story.discovery.public_capture import capture_reference
            captured = capture_reference(args.home, output / 'capture',
                max_candidates=args.max_candidates, freshness_hours=args.freshness_hours,
                visual_library=args.visual_library)
        reference = Path(captured['path']).resolve(strict=True)
        if not reference.is_file() or sha256_file(reference) != captured['sha256']:
            raise LibraryStopped('hot_capture_reference_binding_changed')
        stage = 'edit'
        write_json(progress_path, dict(stage=stage, updated_at=_now(), capture=captured,
            edit_output=str(output / 'edit')))
        # Use the established chain in this supervised process, not a second job.
        code = server_cli.main(_editing_arguments(args, reference))
        if code:
            raise LibraryStopped(f'hot_edit_exit_{code}')
        result_path = output / 'edit/chain_result.json'
        chain = json.loads(result_path.read_text(encoding='utf-8'))
        final = chain.get('final_video') or chain.get('candidate_video')
        final_sha = chain.get('final_sha256') or chain.get('candidate_sha256')
        if not final or not final_sha or sha256_file(final) != final_sha:
            raise LibraryStopped('hot_edit_final_binding_changed')
        fine = chain.get('finecut', {}) or {}
        audio = fine.get('creative_audio', {}) or {}
        result = dict(status=chain['status'], capture=captured,
            chain_result_path=str(result_path), chain_result_sha256=sha256_file(result_path),
            final_video=final, final_sha256=final_sha,
            delivery_kind=('fine_candidate' if chain['status'] == 'rough_to_skill_completed'
                           else 'candidate_needs_revision'),
            joint_quality_gate=chain.get('joint_quality_gate', False),
            content_delivery_ready=chain.get('content_delivery_ready',
                audio.get('content_delivery_ready', False)),
            quality_limitations=dict(rough_review=chain.get('rough_review'),
                finecut_limits=fine.get('limitations', []),
                finecut_model_limits=fine.get('model_limitations', {}),
                audio_limitations=audio.get('limitations', []),
                missing_core_meaning=chain.get('missing_core_meaning', [])),
            usage=chain.get('usage'), completed_at=_now())
        write_json(output / 'hot_result.json', result)
        write_json(progress_path, dict(stage='settled', updated_at=_now(),
            status=result['status'], result_path=str(output / 'hot_result.json')))
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    except Exception as error:
        failure = dict(stage=stage, error=str(error), type=type(error).__name__,
                       automatic_restart=False, failed_at=_now())
        write_json(output / 'hot_failure.json', failure)
        write_json(progress_path, dict(stage='stopped', failure=failure, updated_at=_now()))
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home', type=Path, default=server_cli._home())
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('start', '_run', 'batch', '_batch'):
        p = commands.add_parser(name, help={'start': 'Capture and create one video.',
            'batch': 'Queue distinct references with resource-bounded parallel editing.'}.get(name, argparse.SUPPRESS))
        p.add_argument('--library', type=Path, required=name in {'_run', '_batch'})
        p.add_argument('--visual-library', type=Path, required=name in {'_run', '_batch'})
        p.add_argument('--output', type=Path, required=name in {'_run', '_batch'})
        p.add_argument('--parent-task', type=Path)
        p.add_argument('--audio-music-bed', type=Path)
        p.add_argument('--asr-model-dir', type=Path,
                       help='Offline Whisper model; otherwise use server.json or the unique shared/library_models directory.')
        p.add_argument('--max-candidates', type=int, default=10)
        p.add_argument('--freshness-hours', type=float, default=72)
        p.add_argument('--capture-source', choices=['wellbyte', 'public_browser'])
        if name == '_run':
            p.add_argument('--reference-receipt', type=Path, help=argparse.SUPPRESS)
        if name in {'batch', '_batch'}:
            p.add_argument('--public-cache-root', type=Path, help=argparse.SUPPRESS)
            p.add_argument('--prior-batch', type=Path, help=argparse.SUPPRESS)
            p.add_argument('--prior-batch-sha256', help=argparse.SUPPRESS)
            p.add_argument('--count', type=int, default=10)
            p.add_argument('--concurrency', type=int, default=2,
                           help='Editing processes requested, 1..32; actual admission also checks memory and disk.')
            p.add_argument('--min-free-gb', type=float, default=2)
            p.add_argument('--worker-disk-mb', type=int, default=512)
            p.add_argument('--worker-memory-mb', type=int, default=1024)
            p.add_argument('--render-workers', type=int, default=1)
            p.add_argument('--max-video-mb', type=int, default=256)
            p.add_argument('--dry-run', action='store_true')
    for name in ('status', 'logs', 'stop'):
        p = commands.add_parser(name)
        p.add_argument('--output', type=Path, required=True)
        if name == 'logs':
            p.add_argument('--lines', type=int, default=80)
    args = parser.parse_args(argv)
    if args.command in {'_run', '_batch'}:
        args.capture_source = args.capture_source or 'wellbyte'
    if args.command in {'batch', '_batch'}:
        from .hot_batch import run_batch, start_batch
        if args.command == 'batch':
            _start_defaults(args, parser)
            config = _inputs(args)
            start_batch(args, config)
        else:
            result = run_batch(args)
            return 0 if result['status'] == 'completed' else 1
    elif args.command == 'start':
        _start_defaults(args, parser)
        config = _inputs(args)
        command = [sys.executable, '-m', 'omni_story.library.hot_pipeline',
                   '--home', str(args.home.resolve()), '_run',
                   '--library', str(args.library.resolve()),
                   '--visual-library', str(args.visual_library.resolve()),
                   '--output', str(args.output.resolve()),
                   '--max-candidates', str(args.max_candidates),
                   '--freshness-hours', str(args.freshness_hours),
                   '--capture-source', args.capture_source]
        for name in ('parent_task', 'audio_music_bed', 'asr_model_dir'):
            value = getattr(args, name)
            if value is not None:
                command += ['--' + name.replace('_', '-'), str(value.resolve(strict=True))]
        print(json.dumps(server_jobs.start(command, args.output, Path(config['project_root'])),
                         ensure_ascii=False, indent=2))
    elif args.command == '_run':
        _run(args)
    elif args.command == 'logs':
        print(server_jobs.logs(args.output, args.lines), end='')
    else:
        if args.command == 'stop' and (args.output / 'batch_progress.json').is_file():
            from .hot_batch import stop_batch
            status = stop_batch(args.output)
        else:
            status = getattr(server_jobs, args.command)(args.output)
        for name in ('hot_progress', 'hot_result', 'hot_failure', 'batch_progress', 'batch_result'):
            path = args.output / f'{name}.json'
            if path.is_file():
                status[name] = json.loads(path.read_text(encoding='utf-8'))
        print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
