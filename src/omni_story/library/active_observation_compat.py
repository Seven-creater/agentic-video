"""Explicit continuation-only compatibility for description-shaped warnings.

Only the validation copy is normalized. Model replies, evidence, times, hashes
and saved parsed values remain exactly the original JSON objects.
"""
from copy import deepcopy
import json
from pathlib import Path

from ..contract import require
from . import semantic_audit as audit
from .state import json_sha

POLICY = 'active_finecut_description_uncertainties_v1'
ARTIFACT = 'active_finecut_observation_compatibility'


def _validation_copy(value, enabled):
    if not enabled:
        return value
    require(isinstance(value, dict), 'active_compat:object_required')
    result = deepcopy(value)
    warnings = result.get('uncertainties')
    if not isinstance(warnings, list):
        # Preserve the strict validator's original ordering and repair prompt.
        # Missing warnings are still rejected, never inferred as an empty list.
        return result
    normalized = []
    for row in warnings:
        if (isinstance(row, dict) and set(row) == {'description'} and
                isinstance(row['description'], str) and row['description'].strip()):
            normalized.append(row['description'])
        else:
            # Leave every other object untouched so the original validator
            # rejects it in its normal order, preserving paid repair digests.
            normalized.append(row)
    result['uncertainties'] = normalized
    return result


def validate_observation(value, segment, source_sha, proxy, *, enabled=False):
    audit.validate_segment_observation(_validation_copy(value, enabled), segment, source_sha, proxy)
    return value


def validate_claims(value, observation, claims, *, enabled=False):
    audit.validate_segment_claim_check(_validation_copy(value, enabled), observation, claims)
    return value


def _expected(state):
    return {'policy': POLICY, 'task_id': state.data['task_id'],
        'input_lock_sha256': json_sha(state.data['input_lock']),
        'scope': ['semantic_slice_4', 'semantic_claims_4'], 'opt_in': True,
        'allowed_shape': 'uncertainties item: string or object with only nonempty description string',
        'normalization_scope': 'Deep validation copy only; return and save the original complete model JSON.',
        'unchanged': ['source_and_proxy_sha', 'source_ranges', 'local_evidence_bounds', 'characters',
                      'evidence_types', 'inference_basis', 'claim_ids', 'observation_sha',
                      'old_route_defaults', 'raw_replies', 'prior_protocol_failures'],
        'no_paid_replay_or_third_format_repair': True}


def enable(state):
    from .extension_budget import get_authorization
    get_authorization(state)  # This opt-in cannot activate on a legacy route.
    state._reload()
    expected = _expected(state)
    records = state.data['artifacts'].get(ARTIFACT, [])
    if not records:
        state.set_artifact(ARTIFACT, expected)
    _verify(state)


def _verify(state):
    records = state.data['artifacts'].get(ARTIFACT, [])
    require(len(records) == 1, 'active_compat:explicit_single_opt_in_required')
    value = json.loads(Path(records[0]['path']).read_text(encoding='utf-8'))
    require(json_sha(value) == records[0]['sha256'] and value == _expected(state), 'active_compat:policy_changed')


def observation_validator(state):
    _verify(state)
    return lambda value, segment, source_sha, proxy: validate_observation(value, segment, source_sha, proxy, enabled=True)


def claim_validator(state):
    _verify(state)
    return lambda value, observation, claims: validate_claims(value, observation, claims, enabled=True)
