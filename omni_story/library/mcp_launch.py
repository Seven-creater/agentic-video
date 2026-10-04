"""Connect an official vision MCP for the active Codex task, keeping credentials in memory."""
from __future__ import annotations

import argparse
import getpass
import json
import os
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--package-root', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    extension_env = {}
    limit = 80
    state_path = args.output / 'library_state.json'
    if state_path.exists():
        recorded = json.loads(state_path.read_text(encoding='utf-8'))
        from .extension_budget import AUTHORIZATION, get_authorization, stage_state
        if recorded.get('artifacts', {}).get(AUTHORIZATION):
            state = stage_state(args.output)
            authorization = get_authorization(state)
            from .media import sha256_file
            artifact_path = state.data['artifacts'][AUTHORIZATION][0]['path']
            limit = None
            extension_env = {'OMNI_LIBRARY_EXTENSION_AUTH_FILE': artifact_path,
                             'OMNI_LIBRARY_EXTENSION_AUTH_SHA256': sha256_file(artifact_path),
                             'OMNI_LIBRARY_REQUEST_LIMIT_POLICY': authorization['request_limit_policy']}
    secret = os.environ.get('Z_AI_API_KEY') or getpass.getpass('GLM key (hidden): ')
    # An explicit new connection resumes the bridge; it does not erase jobs,
    # original submission markers, records, or budgets.
    (args.output / 'mcp_stop').unlink(missing_ok=True)
    env = dict(os.environ, Z_AI_API_KEY=secret)
    for key in ('OMNI_LIBRARY_EXTENSION_AUTH_FILE', 'OMNI_LIBRARY_EXTENSION_AUTH_SHA256',
                'OMNI_LIBRARY_MAX_REQUESTS', 'OMNI_LIBRARY_REQUEST_LIMIT_POLICY'):
        env.pop(key, None)
    if limit is not None:
        env['OMNI_LIBRARY_MAX_REQUESTS'] = str(limit)
    env.update(extension_env)
    try:
        return subprocess.call(['node', str(Path(__file__).with_name('mcp_bridge.mjs')),
                                str(args.output.resolve()), str(args.package_root.resolve())], env=env)
    finally:
        env.pop('Z_AI_API_KEY', None)
        secret = None


if __name__ == '__main__':
    raise SystemExit(main())
