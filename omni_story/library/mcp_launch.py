"""Connect an official vision MCP for the active Codex task, keeping credentials in memory."""
from __future__ import annotations

import argparse
import getpass
import os
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--package-root', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    secret = os.environ.get('Z_AI_API_KEY') or getpass.getpass('GLM key (hidden): ')
    # An explicit new connection resumes the bridge; it does not erase jobs,
    # original submission markers, records, or budgets.
    (args.output / 'mcp_stop').unlink(missing_ok=True)
    env = dict(os.environ, Z_AI_API_KEY=secret, OMNI_LIBRARY_MAX_REQUESTS='80')
    try:
        return subprocess.call(['node', str(Path(__file__).with_name('mcp_bridge.mjs')),
                                str(args.output.resolve()), str(args.package_root.resolve())], env=env)
    finally:
        env.pop('Z_AI_API_KEY', None)
        secret = None


if __name__ == '__main__':
    raise SystemExit(main())
