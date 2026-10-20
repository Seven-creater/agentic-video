"""Frozen reference-driven video experiments; check/collect never invoke models."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .manifest import load_manifest
from .report import summarize, write_reports


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home', type=Path)
    commands = parser.add_subparsers(dest='command', required=True)
    check = commands.add_parser('check')
    check.add_argument('--manifest', type=Path, required=True)
    check.add_argument('--preparation', action='store_true')
    for name in ('start', 'continue-pending', '_batch', '_continue', 'collect', 'judge'):
        command = commands.add_parser(name)
        command.add_argument('--manifest', type=Path, required=True)
        command.add_argument('--output', type=Path, required=True)
        if name in ('start', 'continue-pending', '_batch', '_continue'):
            command.add_argument('--concurrency', type=int, default=5)
        if name == 'continue-pending':
            command.add_argument('--reason', required=True)
        if name == '_continue':
            command.add_argument('--checkpoint', type=Path, required=True)
        if name == 'judge':
            command.add_argument('--run-id', required=True)
    args = parser.parse_args(argv)
    if args.command == 'check':
        manifest = load_manifest(args.manifest, require_complete=not args.preparation)
        result = dict(experiment_id=manifest['experiment_id'], cases=len(manifest['cases']),
            planned_runs=len(manifest['planned_runs']), evaluation_ready=manifest['evaluation_ready'],
            model_requests_sent=0, paid_budget_cny=manifest['budget']['max_cost_cny'])
    elif args.command in ('start', 'continue-pending', '_batch', '_continue'):
        if args.home is None:
            parser.error('--home is required for server execution')
        from .runner import start, continue_pending, run_batch
        result = (start(args) if args.command == 'start' else continue_pending(args)
                  if args.command == 'continue-pending' else run_batch(args))
    else:
        manifest = load_manifest(args.manifest, require_complete=False)
        if args.command == 'judge':
            from .judge import evaluate
            result = evaluate(manifest, args.output, args.run_id, home=args.home)
        else:
            from .collect import collect, execution_interval
            rows = collect(manifest, args.output)
            result = write_reports(summarize(manifest, rows, execution=execution_interval(args.output, rows)), args.output/'reports')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
