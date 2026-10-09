"""Once-only historical043 review completion after a settled restoration job.

This module cannot generate another rough candidate or resume failed work. Its
proof binds the original controller, full ledger prefix and actual handoff; an
unused finecut is available only after the corrected actual-video review gate.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
import os
from pathlib import Path

from . import contracts, restoration_policy as policy, server_jobs
from .media import prepare_window, sha256_file
from .resources import historical_selected_review_v1 as historical
from .resources import original_rough_v1 as rough_templates
from .server_restored import RestorationMCP, control_output, usable_rough
from .state import LibraryState, LibraryStopped, json_sha
from .story_finecut import PREFIX, execute_finecut
from .visual_story_trial import write_once

POLICY = 'restored_selected_historical043_completion_v1'
PROOF = 'restoration_selected_completion.json'
RESULT = 'restoration_selected_result.json'
FAILURE = 'restoration_selected_failure.json'
STARTED = 'restoration_selected_execution.json'
SUPPLEMENT = 'rough_handoff_selected_completion.json'
ARTIFACT = 'restored_rough_selected_completion'


def _read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _require(value, reason):
    if not value:
        raise LibraryStopped('restored_selected_completion:' + reason)


def _exclusive(path, value):
    # A never-removed marker prevents a second execution even after a crash.
    with Path(path).open('x', encoding='utf-8') as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.flush()
        os.fsync(handle.fileno())


def _bound(path):
    path = Path(path).resolve(strict=True)
    _require(path.is_file(), 'bound_file_required')
    return {'path': str(path), 'sha256': sha256_file(path)}


def _controller(lane):
    directory = control_output(lane) / '.omni-server'
    job = _read(directory / 'job.json')
    command = job.get('command', [])
    _require(job.get('state') == 'succeeded' and job.get('exit_code') == 0 and
             job.get('finished_at') and job.get('job_id') and
             not server_jobs._identity(job.get('supervisor_pid', 0)) and
             not server_jobs._group_running(job.get('child_pid', 0)), 'original_controller_not_settled')
    _require('omni_story.library.server_restored' in command and '_run' in command and
             '--output' in command and
             Path(command[command.index('--output') + 1]).resolve() == lane and
             (directory / 'run.lock').read_text(encoding='utf-8').strip() == job['job_id'],
             'original_controller_binding_changed')
    return directory, job


def register(lane):
    """Bind the proven omission; this does not launch or dispatch model work."""
    lane = Path(lane).resolve(strict=True)
    _require(not any((lane / name).exists() for name in (PROOF, RESULT, FAILURE, STARTED)),
             'completion_already_registered')
    _require((lane / 'restoration_result.json').is_file() and
             not (lane / 'restoration_failure.json').exists(), 'successful_original_result_required')
    authorization = policy.load(lane)
    _require(authorization is not None, 'original_authorization_required')
    state = _read(lane / 'library_state.json')
    _require(state['calls'] and all(row['status'] == 'received' for row in state['calls']),
             'unsettled_original_no_carryover')
    controller, job = _controller(lane)
    original = _read(lane / 'restoration_result.json')
    _require(original['policy'] == policy.POLICY and original['status'] in
             {'rough_content_not_established', 'restored_rough_to_fine_completed'}, 'original_result_scope_changed')
    rough = original['rough']
    _require(_read(lane / 'result.json') == rough, 'original_rough_result_changed')
    selected = rough['selected_round']
    _require(type(selected) is int and selected in (0, 1), 'selected_round_required')
    stage = f'selected_review_v2_{selected}'
    _require(not any(row['name'].startswith('selected_review_v2_') for row in state['calls']) and
             not state['artifacts'].get('editing_review_policy') and
             not state['artifacts'].get('selected_audit_policy') and
             Path(rough['selected_review_path']).resolve() == lane / f'review_{selected}.json' and
             not (lane / (stage + '.json')).exists(), 'historical043_omission_not_proven')
    policy._handoff(lane, state)
    handoff = _read(lane / 'rough_handoff.json')
    source = _read(lane / 'selected_rough_catalog/inventory.json')['sources'][0]
    reference = _read(lane / 'reference_catalog/inventory.json')['sources'][0]
    render = _read(lane / f'render_{selected}/render_result.json')
    compiled = _read(lane / f'render_{selected}/render_input.json')['compiled']
    plan = _read(lane / f'plan_{selected}.json')
    blind = _read(lane / f'blind_reading_{selected}.json')
    review = _read(lane / f'review_{selected}.json')
    _require(handoff['selected_round'] == selected and handoff['rough_review'] == rough['review'] == review and
             source['path'] == handoff['rough_video_path'] == rough['final_video'] == original['rough_video'] ==
             render['rendered_path'] and
             source['sha256'] == handoff['rough_source_sha256'] == rough['final_sha256'] ==
             original['rough_sha256'] == render['sha256'] and
             source['duration_s'] == handoff['rough_duration_s'] == original['rough_duration_s'] ==
             render['measured_duration_s'] and source['duration_s'] > 0 and
             Path(source['path']).resolve(strict=True).is_relative_to(lane) and
             compiled['plan'] == plan and compiled['segments'] == render['provenance'] and
             reference['sha256'] == authorization['reference_seed']['reference_sha256'], 'selected_actual_binding_changed')
    original_call = next(row for row in state['calls'] if row['id'] == handoff['rough_review_call_id'])
    request = _read(lane / 'calls' / original_call['id'] / 'request.json')
    _require(original_call['name'].removesuffix('_repair') == f'review_{selected}' and
             request['arguments']['prompt'].startswith(rough_templates.review_prompt({})[:-2]) and
             _read(lane / 'calls' / original_call['id'] / 'parsed.json') == review,
             'original027_review_required')
    fine_calls = [row for row in state['calls'] if row['name'].startswith(PREFIX)]
    unused_fine = original['status'] == 'rough_content_not_established' and not fine_calls
    fine_result = lane / 'skill_finecut/result.json'
    if unused_fine:
        _require(not (lane / 'skill_finecut').exists(), 'unused_fine_scope_not_clean')
    else:
        _require(original['status'] == 'restored_rough_to_fine_completed' and fine_calls and fine_result.is_file() and
                 _read(fine_result) == original['finecut'], 'partial_fine_no_restart')
    context = dict(reference=authorization['reference_seed']['full_response']['reference'],
        actual_render_sha256=source['sha256'], blind_reading=blind, plan=plan, provenance=render['provenance'],
        audio_review_limit='GLM vision MCP has not heard actual output audio; preserve limitation.',
        reference_protocol_limit=authorization['reference_seed']['evidence_limit'])
    files = [lane / 'authorization.json', lane / 'restoration_result.json', lane / 'result.json',
             lane / 'rough_handoff.json', lane / 'selected_rough_catalog/inventory.json',
             lane / 'reference_catalog/inventory.json', lane / f'plan_{selected}.json',
             lane / f'blind_reading_{selected}.json', lane / f'review_{selected}.json',
             lane / f'render_{selected}/render_result.json', lane / f'render_{selected}/render_input.json',
             controller / 'job.json', controller / 'run.lock', Path(source['path']), Path(original['final_video'])]
    if (lane / 'mcp_stop').exists():
        files.append(lane / 'mcp_stop')
    if fine_result.exists():
        files.append(fine_result)
    for call in state['calls']:
        folder = lane / 'calls' / call['id']
        _require(json_sha(_read(folder / 'response.json')) == call['response_sha256'], 'old_response_changed')
        files.extend(path for name in ('request.json', 'response.json', 'parsed.json', 'protocol_failure.json')
                     if (path := folder / name).is_file())
    for entries in state['artifacts'].values():
        files.extend(Path(item['path']) for item in entries)
    proof = dict(policy=POLICY, lane=str(lane), original_authorization_sha256=sha256_file(lane / 'authorization.json'),
        original_controller_job_id=job['job_id'], original_controller=str(controller),
        selected_round=selected, selected_stage=stage, historical043_provenance=historical.provenance(),
        original_state=deepcopy(state), original_result=original, original_handoff=handoff,
        parent_source=source, reference_source=reference, review_context=context,
        unused_original_finecut=unused_fine, added_rough_renders=0, added_fine_renders=0,
        automatic_restart=False, protected_files=[_bound(path) for path in dict.fromkeys(files)])
    _exclusive(lane / PROOF, proof)
    return lane / PROOF


def load(lane, correction_sha256):
    """Verify the frozen prefix and only the unused selected-review/fine stages."""
    lane = Path(lane).resolve(strict=True)
    _require(sha256_file(lane / PROOF) == correction_sha256, 'completion_proof_changed')
    proof = _read(lane / PROOF)
    _require(proof['policy'] == POLICY and proof['lane'] == str(lane) and
             proof['historical043_provenance'] == historical.provenance() and
             proof['added_rough_renders'] == proof['added_fine_renders'] == 0 and
             proof['automatic_restart'] is False, 'completion_scope_changed')
    _require(not (lane / 'restoration_failure.json').exists(), 'original_terminal_failure')
    for item in proof['protected_files']:
        _require(Path(item['path']).is_absolute() and sha256_file(item['path']) == item['sha256'],
                 'protected_original_changed')
    policy.load(lane)
    controller, job = _controller(lane)
    _require(str(controller) == proof['original_controller'] and job['job_id'] == proof['original_controller_job_id'],
             'original_controller_changed')
    state = _read(lane / 'library_state.json')
    old = proof['original_state']
    count = old['request_count']
    _require(state['calls'][:count] == old['calls'] and
             all(state[key] == old[key] for key in ('task_id', 'input_lock', 'max_requests', 'policy_version')),
             'original_ledger_prefix_changed')
    for name, entries in old['artifacts'].items():
        _require(state['artifacts'].get(name, [])[:len(entries)] == entries, 'original_artifact_prefix_changed')
    _require(state['artifacts'].get('restored_rough_handoff') == old['artifacts']['restored_rough_handoff'],
             'original_handoff_must_remain_single')
    for call in state['calls'][count:]:
        stem = call['name'].removesuffix('_repair')
        _require(stem == proof['selected_stage'] or
                 proof['unused_original_finecut'] and stem.startswith(PREFIX), 'new_rough_or_extra_stage_forbidden')
        if stem.startswith(PREFIX):
            _supplement(lane, state, proof)
    return proof


def _supplement(lane, state, proof):
    entries = state['artifacts'].get(ARTIFACT, [])
    _require(len(entries) == 1, 'corrected_handoff_required')
    binding = _read(entries[0]['path'])
    _require(json_sha(binding) == entries[0]['sha256'] and binding['path'] == str(lane / SUPPLEMENT),
             'corrected_handoff_changed')
    supplement = _read(lane / SUPPLEMENT)
    _require(json_sha(supplement) == binding['sha256'] and
             supplement['original_handoff'] == proof['original_handoff'] and
             supplement['original_handoff_sha256'] == sha256_file(lane / 'rough_handoff.json') and
             supplement['rough_source_sha256'] == proof['parent_source']['sha256'] and
             supplement['selected_round'] == proof['selected_round'], 'corrected_handoff_changed')
    review = _read(supplement['corrected_review_path'])
    call = next((row for row in state['calls'] if row['id'] == supplement['corrected_review_call_id']), None)
    _require(call is not None and call['status'] == 'received' and
             call['name'].removesuffix('_repair') == proof['selected_stage'] and
             _read(lane / 'calls' / call['id'] / 'parsed.json') == review and
             json_sha(review) == supplement['corrected_review_sha256'] and usable_rough({'review': review}),
             'corrected_gate_not_established')
    contracts.validate_review(review, proof['reference_source']['sha256'])
    return supplement


class CompletionMCP(RestorationMCP):
    def __init__(self, state, *, correction_sha256, **kwargs):
        self.correction_sha256 = correction_sha256
        super().__init__(state, **kwargs)

    def _submit(self, name, request, *, repair_of=None):
        proof = load(self.output, self.correction_sha256)
        _require((self.output / STARTED).is_file() and not (self.output / FAILURE).exists() and
                 not (self.output / RESULT).exists(), 'execution_not_active')
        stem = name.removesuffix('_repair')
        _require(stem == proof['selected_stage'] or
                 proof['unused_original_finecut'] and stem.startswith(PREFIX), 'stage_not_authorized')
        if stem.startswith(PREFIX):
            self.state._reload()
            _supplement(self.output, self.state.data, proof)
        else:
            scope = request.get('observation_scope')
            source = proof['parent_source']
            _require(scope == dict(kind='continuous_window', source_sha256=source['sha256'],
                     source_start_s=0, source_end_s=source['duration_s']) and
                     request['tool'] == 'analyze_video' and request['arguments']['prompt'].startswith(
                         historical.review_prompt(proof['review_context'])), 'selected_actual_input_changed')
        return super()._submit(name, request, repair_of=repair_of)


def execute(state, mcp, *, correction_sha256):
    """Complete exactly once, preserving the main result and original stop file."""
    lane = Path(state.output).resolve(strict=True)
    proof = load(lane, correction_sha256)
    if (lane / RESULT).exists():
        result = _read(lane / RESULT)
        _require(result['correction_sha256'] == correction_sha256 and
                 sha256_file(result['final_video']) == result['final_sha256'], 'final_cache_changed')
        return result
    _require(not (lane / FAILURE).exists(), 'terminal_no_restart')
    _require(not (lane / STARTED).exists(), 'execution_already_started_no_restart')
    state._reload()
    _require(all(row['status'] == 'received' for row in state.data['calls']) and
             state.data['request_count'] == proof['original_state']['request_count'], 'unsettled_or_started_no_carryover')
    _exclusive(lane / STARTED, dict(policy=POLICY, correction_sha256=correction_sha256))
    try:
        source = proof['parent_source']
        proxy = prepare_window(source, 0, source['duration_s'], lane / 'cache', fps=12)
        review = mcp.call(proof['selected_stage'], historical.review_prompt(proof['review_context']), proxy['path'],
            lambda value: contracts.validate_review(value, proof['reference_source']['sha256']),
            scope=dict(kind='continuous_window', source_sha256=source['sha256'],
                       source_start_s=0, source_end_s=source['duration_s']))
        review_path = lane / (proof['selected_stage'] + '.json')
        write_once(review_path, review)
        state._reload()
        call = next(row for row in reversed(state.data['calls']) if
                    row['name'].removesuffix('_repair') == proof['selected_stage'])
        result = dict(policy=POLICY, correction_sha256=correction_sha256,
            selected_round=proof['selected_round'], corrected_review=review,
            corrected_review_call_id=call['id'], original_result_path=str(lane / 'restoration_result.json'),
            original_handoff_path=str(lane / 'rough_handoff.json'), final_video=source['path'],
            final_sha256=source['sha256'], joint_quality_gate=False, added_render_grant=0)
        if not proof['unused_original_finecut']:
            fine = proof['original_result']['finecut']
            _require(sha256_file(fine['final_video']) == fine['final_sha256'], 'existing_fine_changed')
            result.update(status='selected_review_completed_existing_fine_reused', finecut=fine,
                final_video=fine['final_video'], final_sha256=fine['final_sha256'],
                joint_quality_gate=(review['theme_status'] == 'pass' and fine.get('joint_quality_gate') is True))
        elif usable_rough({'review': review}):
            supplement = dict(policy=POLICY, original_handoff=proof['original_handoff'],
                original_handoff_sha256=sha256_file(lane / 'rough_handoff.json'),
                rough_source_sha256=source['sha256'], selected_round=proof['selected_round'],
                corrected_review_path=str(review_path), corrected_review_sha256=json_sha(review),
                corrected_review_call_id=call['id'], original_unused_finecut=True, added_render_grant=0)
            write_once(lane / SUPPLEMENT, supplement)
            state.set_artifact(ARTIFACT, {'path': str(lane / SUPPLEMENT), 'sha256': json_sha(supplement)})
            authorization = policy.load(lane)
            seed = authorization['reference_seed']
            context = dict(reference=seed['full_response']['reference'], source_call_id=seed['source_call_id'],
                evidence_limit=seed['evidence_limit'], prior_rough_review=review,
                prior_rough_evidence=proof['review_context'], actual_rough_handoff=supplement)
            fine = execute_finecut(state, mcp, context, source, proof['reference_source'], lane / 'skill_finecut')
            result.update(status='selected_review_completed_original_finecut_completed', finecut=fine,
                final_video=fine['final_video'], final_sha256=fine['final_sha256'],
                joint_quality_gate=(review['theme_status'] == 'pass' and fine.get('joint_quality_gate') is True))
        else:
            result.update(status='selected_review_content_not_established_no_finecut')
        state._reload()
        _require(all(row['status'] == 'received' for row in state.data['calls']), 'completion_has_unsettled_calls')
        load(lane, correction_sha256)
        result['usage'] = policy.aggregate_usage(policy.load(lane), state.data['request_count'])
        write_once(lane / RESULT, result)
        return result
    except Exception as error:
        _failure(lane, correction_sha256, error)
        raise


def _failure(lane, correction_sha256, error):
    write_once(lane / FAILURE, dict(policy=POLICY, correction_sha256=correction_sha256,
        error=str(error), type=type(error).__name__,
        request_count=_read(lane / 'library_state.json')['request_count'], automatic_restart=False))


def main(argv=None):
    from .server_cli import _home, credential, settings
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home', type=Path, default=_home())
    subs = parser.add_subparsers(dest='command', required=True)
    for name in ('register', '_run'):
        command = subs.add_parser(name)
        command.add_argument('--output', type=Path, required=True)
        if name == '_run':
            command.add_argument('--correction-sha256', required=True)
    args = parser.parse_args(argv)
    if args.command == 'register':
        path = register(args.output)
        result = {'path': str(path), 'sha256': sha256_file(path)}
    else:
        proof = load(args.output, args.correction_sha256)
        # Cached/terminal work never starts the native model client.
        if (args.output / RESULT).exists() or (args.output / FAILURE).exists() or (args.output / STARTED).exists():
            state = type('ReadOnlyState', (), {'output': args.output})()
            result = execute(state, None, correction_sha256=args.correction_sha256)
        else:
            try:
                config = settings(args.home)
                state = LibraryState(args.output, proof['original_state']['input_lock'], max_requests=None,
                                     registry_path=args.home / 'shared/server_library_runs.json')
                os.environ['Z_AI_API_KEY'] = credential(args.home)
                mcp = CompletionMCP(state, correction_sha256=args.correction_sha256,
                    package_root=config['mcp_package_root'], executable=config['opencode_executable'],
                    exclusions=policy.load(args.output)['unknown_inputs'])
                result = execute(state, mcp, correction_sha256=args.correction_sha256)
            except Exception as error:
                if not (args.output / FAILURE).exists():
                    _failure(args.output, args.correction_sha256, error)
                raise
            finally:
                os.environ.pop('Z_AI_API_KEY', None)
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
