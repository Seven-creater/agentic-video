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
        from .extension_budget import AUTHORIZATION, GOAL_AUTHORIZATION, get_authorization, stage_state
        if recorded.get('artifacts', {}).get('visual_story_skill_trial_v1'):
            from .visual_story_trial_state import VisualStoryState, get_auth
            state = VisualStoryState(args.output)
            authorization = get_auth(args.output)
            limit = None
            extension_env = {'OMNI_LIBRARY_VISUAL_STORY_AUTH_FILE': authorization['authorization_path'],
                             'OMNI_LIBRARY_VISUAL_STORY_AUTH_SHA256': authorization['authorization_sha256'],
                             'OMNI_LIBRARY_SLOT_FINECUT_MAX_CONCURRENCY': '1'}
        elif recorded.get('artifacts', {}).get('microclip_slot_finecut_v2'):
            from .microclip_v2_state import SlotMicroclipState, get_auth
            state = SlotMicroclipState(args.output)
            authorization = get_auth(args.output)
            limit = None
            extension_env = {'OMNI_LIBRARY_MICROCLIP_V2_AUTH_FILE': authorization['authorization_path'],
                             'OMNI_LIBRARY_MICROCLIP_V2_AUTH_SHA256': authorization['authorization_sha256'],
                             'OMNI_LIBRARY_SLOT_FINECUT_MAX_CONCURRENCY': '1'}
        elif recorded.get('artifacts', {}).get('microclip_finecut_v1'):
            from .microclip_state import MicroclipState, get_auth
            state = MicroclipState(args.output)
            authorization = get_auth(args.output)
            limit = None
            extension_env = {'OMNI_LIBRARY_MICROCLIP_AUTH_FILE': authorization['authorization_path'],
                             'OMNI_LIBRARY_MICROCLIP_AUTH_SHA256': authorization['authorization_sha256'],
                             'OMNI_LIBRARY_SLOT_FINECUT_MAX_CONCURRENCY': '1'}
        elif recorded.get('artifacts', {}).get('sf_parent_timeline_finecut_v1'):
            from .parent_cut_state import ParentCutState, get_auth
            state = ParentCutState(args.output)
            authorization = get_auth(args.output)
            limit = None
            extension_env = {'OMNI_LIBRARY_PARENT_CUT_AUTH_FILE': authorization['authorization_path'],
                             'OMNI_LIBRARY_PARENT_CUT_AUTH_SHA256': authorization['authorization_sha256'],
                             'OMNI_LIBRARY_SLOT_FINECUT_MAX_CONCURRENCY': '2'}
        elif recorded.get('artifacts', {}).get('sf_fact_grounded_reconstruction_v1'):
            from .forward_slot_budget import ForwardSlotState, get_auth
            state = ForwardSlotState(args.output)
            authorization = get_auth(args.output)
            limit = None
            extension_env = {'OMNI_LIBRARY_FORWARD_SLOT_AUTH_FILE': authorization['authorization_path'],
                             'OMNI_LIBRARY_FORWARD_SLOT_AUTH_SHA256': authorization['authorization_sha256'],
                             'OMNI_LIBRARY_SLOT_FINECUT_MAX_CONCURRENCY': '2'}
        elif recorded.get('artifacts', {}).get('slot_finecut_comparison_v1'):
            from .slot_finecut_budget import SlotFinecutState, load_authorization
            state = SlotFinecutState(args.output)
            authorization = load_authorization(args.output)
            limit = None
            extension_env = {'OMNI_LIBRARY_SLOT_FINECUT_AUTH_FILE': authorization['authorization_path'],
                             'OMNI_LIBRARY_SLOT_FINECUT_AUTH_SHA256': authorization['authorization_sha256']}
            if recorded.get('artifacts', {}).get('sf_parallel_execution_v1'):
                # SlotFinecutState validates this appended permission on load.
                extension_env['OMNI_LIBRARY_SLOT_FINECUT_MAX_CONCURRENCY'] = '2'
            if recorded.get('artifacts', {}).get('sf_independent_slot_recovery_v1'):
                from .independent_slot_recovery import read_validate
                from .media import sha256_file
                read_validate(args.output, state.data, authorization)
                recovery_path = state.data['artifacts']['sf_independent_slot_recovery_v1'][0]['path']
                extension_env.update(OMNI_LIBRARY_SLOT_FINECUT_RECOVERY_FILE=recovery_path,
                    OMNI_LIBRARY_SLOT_FINECUT_RECOVERY_SHA256=sha256_file(recovery_path))
        elif recorded.get('artifacts', {}).get(GOAL_AUTHORIZATION):
            from .goal_budget import get_authorization as get_goal_authorization, stage_state as goal_stage_state
            state = goal_stage_state(args.output)
            authorization = get_goal_authorization(state)
            from .media import sha256_file
            artifact_path = state.data['artifacts'][GOAL_AUTHORIZATION][0]['path']
            limit = None
            extension_env = {'OMNI_LIBRARY_EXTENSION_AUTH_FILE': artifact_path,
                             'OMNI_LIBRARY_EXTENSION_AUTH_SHA256': sha256_file(artifact_path),
                             'OMNI_LIBRARY_REQUEST_LIMIT_POLICY': authorization['request_limit_policy']}
            from .independent_source_resume import ARTIFACT, load
            if state.data['artifacts'].get(ARTIFACT):
                load(state, 11)
                independent_path = state.data['artifacts'][ARTIFACT][0]['path']
                extension_env.update(OMNI_LIBRARY_INDEPENDENT_SOURCE_AUTH_FILE=independent_path,
                    OMNI_LIBRARY_INDEPENDENT_SOURCE_AUTH_SHA256=sha256_file(independent_path))
        elif recorded.get('artifacts', {}).get(AUTHORIZATION):
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
                'OMNI_LIBRARY_VISUAL_STORY_AUTH_FILE', 'OMNI_LIBRARY_VISUAL_STORY_AUTH_SHA256',
                'OMNI_LIBRARY_MICROCLIP_V2_AUTH_FILE', 'OMNI_LIBRARY_MICROCLIP_V2_AUTH_SHA256',
                'OMNI_LIBRARY_MICROCLIP_AUTH_FILE', 'OMNI_LIBRARY_MICROCLIP_AUTH_SHA256',
                'OMNI_LIBRARY_PARENT_CUT_AUTH_FILE', 'OMNI_LIBRARY_PARENT_CUT_AUTH_SHA256',
                'OMNI_LIBRARY_FORWARD_SLOT_AUTH_FILE', 'OMNI_LIBRARY_FORWARD_SLOT_AUTH_SHA256',
                'OMNI_LIBRARY_MAX_REQUESTS', 'OMNI_LIBRARY_REQUEST_LIMIT_POLICY',
                'OMNI_LIBRARY_INDEPENDENT_SOURCE_AUTH_FILE', 'OMNI_LIBRARY_INDEPENDENT_SOURCE_AUTH_SHA256',
                'OMNI_LIBRARY_SLOT_FINECUT_RECOVERY_FILE', 'OMNI_LIBRARY_SLOT_FINECUT_RECOVERY_SHA256',
                'OMNI_LIBRARY_SLOT_FINECUT_MAX_CONCURRENCY',
                'OMNI_LIBRARY_SLOT_FINECUT_AUTH_FILE', 'OMNI_LIBRARY_SLOT_FINECUT_AUTH_SHA256'):
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
