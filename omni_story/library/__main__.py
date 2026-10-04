"""Local movie-library execution with an active Codex official-MCP connection."""
from __future__ import annotations

import argparse
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference', type=Path, required=True)
    parser.add_argument('--library', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--no-asr', action='store_true')
    args = parser.parse_args(argv)
    from .pipeline import execute
    execute(args.reference.resolve(strict=True), args.library.resolve(strict=True), args.output,
            asr=not args.no_asr)
    return 0


if __name__ == '__main__':
    main()
