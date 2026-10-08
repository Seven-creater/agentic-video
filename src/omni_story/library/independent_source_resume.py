"""One independent library-evidence round; never recover or replay lost work."""
from pathlib import Path
from types import SimpleNamespace

from ..contract import require
from .media import prepare_window, sha256_file
from .pipeline import _read
from .state import file_lock, json_sha, scope_fingerprint, write_json

ARTIFACT = 'goal_research_independent_11'
POLICY = 'independent_library_evidence_after_unknown_v1'
ROUND = 11
DRAFT_CALL = 'glm_128_active_9_draft_repair'


def _scope(request):
    scope = request.get('observation_scope')
    if scope is None:
        media = request['arguments'].get('image_source') or request['arguments'].get('video_source')
        lineage = _read(Path(media).parent / 'lineage.json')
        scope = {k: lineage[k] for k in ('kind', 'source_sha256', 'source_start_s', 'source_end_s')}
    scope_fingerprint(scope)
    # New exclusion metadata only; the original requests stay byte-identical.
    return {k: int(v) if type(v) is float and v.is_integer() else v for k, v in scope.items()}


def _prefacts(draft, sources):
    result = []
    for original in draft['segments']:
        segment = {k: original[k] for k in ('segment_id', 'source_id', 'source_in_s', 'source_out_s')}
        sha = sources[segment['source_id']]['sha256']
        key = json_sha({'segment': segment['segment_id'], 'sha': sha,
                        'in': segment['source_in_s'], 'out': segment['source_out_s']})[:16]
        result.append({'stage': f'semantic_slice_{ROUND}_{key}', 'segment': segment, 'source_sha256': sha})
    return result


def enable(output, instruction):
    from .goal_budget import get_authorization, stage_state, _call_value
    state = stage_state(output)
    saved = load(state, ROUND)
    if saved:
        return saved
    require(isinstance(instruction, str) and instruction.strip(), 'independent:instruction_required')
    require(state.data['request_count'] == 131 and not any(c['status'] == 'submitted' for c in state.data['calls']),
            'independent:wrong_or_pending_baseline')
    require(not state.data['artifacts'].get(f'goal_round_{ROUND}'), 'independent:round_already_registered')
    lost = [c for c in state.data['calls'] if c['status'] == 'uncertain']
    require({c['id'] for c in lost} == {'glm_004_coarse_978d5360_01', 'glm_131_active_10_draft'},
            'independent:unexpected_unknown_inputs')
    require(state.data['artifacts'].get('goal_research_network_10'), 'independent:transport_stop_receipt_required')
    call = next(c for c in state.data['calls'] if c['id'] == DRAFT_CALL)
    _, draft = _call_value(state.output, call)
    sources = {s['source_id']: s for s in _read(state.output / 'catalog/inventory.json')['sources']}
    prefacts = _prefacts(draft, sources)
    require(len(prefacts) == 6 and len({p['stage'] for p in prefacts}) == 6, 'independent:expected_model_slices')
    exclusions = []
    for c in lost:
        request = _read(state.output / 'calls' / c['id'] / 'request.json')
        require(json_sha(request) == c['request_sha256'], 'independent:unknown_request_changed')
        exclusions.append({'call_id': c['id'], 'request_sha256': c['request_sha256'],
                           'media_sha256': request['media_sha256'], 'scope': _scope(request)})
    from .goal_feedback_continuation import _exhausted_slice_scopes
    exhausted = _exhausted_slice_scopes(state)
    for p in prefacts:
        scope = {'kind': 'continuous_window', 'source_sha256': p['source_sha256'],
                 'source_start_s': p['segment']['source_in_s'], 'source_end_s': p['segment']['source_out_s']}
        require(not any(scope_fingerprint(scope) == scope_fingerprint(e['scope']) for e in exclusions + exhausted),
                'independent:source_scope_excluded')
    folder = state.output / 'artifacts' / POLICY
    folder.mkdir(exist_ok=False)
    snapshot = folder / 'baseline_state.json'
    write_json(snapshot, state.data)
    protected = [{'path': str(p.resolve()), 'sha256': sha256_file(p)}
                 for p in sorted((state.output / 'calls').glob('*/*')) if p.is_file()]
    record = {'policy': POLICY, 'round': ROUND, 'task_id': state.data['task_id'],
        'user_instruction': instruction, 'input_lock_sha256': json_sha(state.data['input_lock']),
        'reference_sha256': state.data['input_lock']['reference_sha256'],
        'activation_baseline_requests': 131, 'baseline_request_count': 131,
        'baseline_state_path': str(snapshot), 'baseline_state_sha256': sha256_file(snapshot),
        'prefix_calls_sha256': json_sha(state.data['calls']), 'protected_files': protected,
        'admitted_unknown_call_ids': [c['id'] for c in lost], 'unknown_inputs': exclusions,
        'model_draft_call_id': call['id'], 'model_draft_request_sha256': call['request_sha256'],
        'model_draft_response_sha256': call['response_sha256'], 'model_draft_sha256': json_sha(draft),
        'prefacts': prefacts, 'planning_carrier_stage': prefacts[0]['stage'],
        'model_timeout_ms': 1200000, 'tool_timeout_ms': 1260000,
        'unknown_round_receipt': 'goal_research_network_10', 'no_numeric_request_ceiling': True,
        'reference_media_forbidden': True, 'old_calls_unchanged': True,
        'reference_is_known_cached_model_interpretation': True,
        'new_unique_windows': 0, 'new_renders': 1, 'repairs_per_stage': 1}
    state.set_artifact(ARTIFACT, record)
    return load(state, ROUND)


def load(state, round_no):
    entries = state.data['artifacts'].get(ARTIFACT, [])
    if not entries:
        return None
    require(len(entries) == 1, 'independent:one_strategy_required')
    record = _read(entries[0]['path'])
    require(json_sha(record) == entries[0]['sha256'] and record['policy'] == POLICY
            and record['round'] == ROUND and record['task_id'] == state.data['task_id']
            and record['input_lock_sha256'] == json_sha(state.data['input_lock'])
            and record['reference_sha256'] == state.data['input_lock']['reference_sha256'],
            'independent:strategy_changed')
    snapshot = Path(record['baseline_state_path']).resolve(strict=True)
    require(snapshot.is_relative_to(state.output) and sha256_file(snapshot) == record['baseline_state_sha256'],
            'independent:baseline_changed')
    old = _read(snapshot)
    count = record['baseline_request_count']
    require(count == record['activation_baseline_requests'] == 131
            and json_sha(old['calls']) == record['prefix_calls_sha256']
            and state.data['calls'][:count] == old['calls'], 'independent:old_calls_changed')
    for item in record['protected_files']:
        path = Path(item['path']).resolve(strict=True)
        require(path.is_relative_to(state.output) and sha256_file(path) == item['sha256'],
                'independent:protected_call_file_changed')
    for flag in ('reference_media_forbidden', 'old_calls_unchanged', 'reference_is_known_cached_model_interpretation',
                 'no_numeric_request_ceiling'):
        require(record.get(flag) is True, 'independent:scope_changed')
    require(record['new_unique_windows'] == 0 and record['new_renders'] == record['repairs_per_stage'] == 1
            and record['model_timeout_ms'] == 1200000 and record['tool_timeout_ms'] == 1260000,
            'independent:limits_changed')
    from .goal_budget import _call_value
    call = next(c for c in old['calls'] if c['id'] == record['model_draft_call_id'])
    _, draft = _call_value(state.output, call)
    sources = {s['source_id']: s for s in _read(state.output / 'catalog/inventory.json')['sources']}
    require(call['request_sha256'] == record['model_draft_request_sha256']
            and call['response_sha256'] == record['model_draft_response_sha256']
            and json_sha(draft) == record['model_draft_sha256']
            and _prefacts(draft, sources) == record['prefacts'], 'independent:model_ranges_changed')
    require(record['planning_carrier_stage'] == record['prefacts'][0]['stage'], 'independent:carrier_changed')
    unknowns = [c for c in old['calls'] if c['status'] == 'uncertain']
    require(record['admitted_unknown_call_ids'] == [c['id'] for c in unknowns]
            and record['unknown_inputs'] == [{'call_id': c['id'], 'request_sha256': c['request_sha256'],
                'media_sha256': (request := _read(state.output / 'calls' / c['id'] / 'request.json'))['media_sha256'],
                'scope': _scope(request)} for c in unknowns], 'independent:exclusions_changed')
    return record if round_no == ROUND else None


def permit_prefact(name, record):
    return name.removesuffix('_repair') in {p['stage'] for p in record['prefacts']}


def assert_no_unsettled(state, round_no):
    record = load(state, round_no)
    admitted = set(record['admitted_unknown_call_ids']) if record else set()
    require(not any(c['status'] == 'submitted' or c['status'] in {'uncertain', 'failed_known'}
                    and c['id'] not in admitted for c in state.data['calls']),
            'independent:new_or_pending_outcome_unknown')


def check_request(record, request):
    scope = request.get('observation_scope')
    fingerprint = scope_fingerprint(scope)
    require(scope['source_sha256'] != record['reference_sha256'], 'independent:reference_carrier_forbidden')
    require(not any(json_sha(request) == old['request_sha256'] or request['media_sha256'] == old['media_sha256']
                    or fingerprint == scope_fingerprint(old['scope']) for old in record['unknown_inputs']),
            'independent:unknown_input_replay_forbidden')


def planning_media(state):
    from .dense_source_frames import make_media
    record = load(state, ROUND)
    require(record is not None, 'independent:strategy_required')
    sources = {s['source_id']: s for s in _read(state.output / 'catalog/inventory.json')['sources']}
    p = record['prefacts'][0]
    segment = p['segment']
    source = sources[segment['source_id']]
    proxy = prepare_window(source, segment['source_in_s'], segment['source_out_s'], state.output / 'media_cache', fps=30)
    return make_media(segment, source, proxy)


def require_prefacts(state):
    """Never freeze or submit a creative context before all six facts exist."""
    from .goal_budget import _call_value
    record = load(state, ROUND)
    require(record is not None, 'independent:strategy_required')
    for item in record['prefacts']:
        candidates = [c for c in state.data['calls']
                      if c['name'] in {item['stage'], item['stage'] + '_repair'} and c['status'] == 'received'
                      and (state.output / 'calls' / c['id'] / 'parsed.json').is_file()]
        require(bool(candidates), 'independent:prefacts_required_before_planning')
        _, value = _call_value(state.output, candidates[-1])
        require(all(value[k] == item['segment'][k] for k in ('segment_id', 'source_id', 'source_in_s', 'source_out_s'))
                and value['source_sha256'] == item['source_sha256'], 'independent:prefact_binding_changed')


def execute(reference, library, output):
    from .goal_budget import stage_state, get_authorization
    from .pipeline import CodexMCP
    from .active_observation_compat import validate_observation
    from .dense_source_frames import make_media
    from .goal_feedback_continuation import _known_protocol_failure, _stop, execute_goal_continuation, _result
    state = stage_state(output)
    record = load(state, ROUND)
    require(record is not None, 'independent:explicit_strategy_required')
    result = _result(state, ROUND)
    if result is not None:
        return result
    with file_lock(state.output / '.goal_feedback_execution.lock'):
        assert_no_unsettled(state, ROUND)
        auth = get_authorization(state)
        if not state.data['artifacts'].get(f'goal_round_{ROUND}'):
            state.set_artifact(f'goal_round_{ROUND}', {'policy': 'goal_feedback_round_v1', 'round': ROUND,
                'task_id': state.data['task_id'], 'input_lock_sha256': record['input_lock_sha256'],
                'goal_guide_sha256': auth['goal_guide_sha256'], 'new_unique_windows': 0, 'max_renders': 1,
                'observation_compatibility': auth['observation_compatibility']})
        glm = CodexMCP(state, timeout_s=1320)
        sources = {s['source_id']: s for s in _read(state.output / 'catalog/inventory.json')['sources']}
        facts = []
        for p in record['prefacts']:
            segment = p['segment']
            source = sources[segment['source_id']]
            proxy = prepare_window(source, segment['source_in_s'], segment['source_out_s'], state.output / 'media_cache', fps=30)
            carrier = make_media(segment, source, proxy)
            try:
                value = glm.call(p['stage'], carrier['prompt'], carrier['path'],
                    lambda v, s=segment, src=source, pr=proxy: validate_observation(v, s, src['sha256'], pr, enabled=True),
                    image=True, scope={k: proxy[k] for k in ('kind', 'source_sha256', 'source_start_s', 'source_end_s')})
            except ValueError as error:
                if str(error) != 'model_protocol_repair_exhausted:' + p['stage']:
                    raise
                return _stop(state, ROUND, 'stopped_prefact_protocol_failure', _known_protocol_failure(state, p['stage'], error))
            facts.append({'stage': p['stage'], 'observation_sha256': json_sha(value),
                          'carrier_sha256': carrier['sha256'], 'normal_proxy_sha256': proxy['sha256']})
        state.set_artifact('goal_research_prefacts_11', {'policy': POLICY, 'round': ROUND, 'records': facts,
            'old_draft_not_new_obligations': True, 'model_facts_remain_fallible': True})
    return execute_goal_continuation(reference, library, output, round_no=ROUND)
