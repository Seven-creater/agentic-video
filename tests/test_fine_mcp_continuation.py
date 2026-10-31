"""Known rejected fine observations must not poison independent creative work."""
from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from omni_story.library import opencode_provider, pipeline
from omni_story.library.state import LibraryStopped, write_json
from test_creative_server_retry import fixture, journal, run_guard
from test_e2e_fine_failure import fine_run
from test_qfs_mcp_continuation import BLOCKED, REPAIR, submit


STAGE = 'fine_' + 'a' * 16


def failed_observation(tmp_path, *, suffix='_repair', outcome=429):
    state, package, _ = fixture(tmp_path)
    state.complete_call(state.data['calls'][-1], dict(status='complete'))
    call, body = submit(state, STAGE, 'Independent continuous-window observation')
    for repair in ['_repair', '_repair2'][:{'': 0, '_repair': 1, '_repair2': 2}[suffix]]:
        assert run_guard(state, package, body, [200])['results'] == [200]
        state.complete_call(call, dict(status='complete', result=dict(content=[])))
        call, body = submit(state, STAGE + repair,
                            body['messages'][-1]['content'][-1]['text'] + REPAIR, repair_of=call)
    if outcome is not None:
        assert run_guard(state, package, body, [outcome])['sends'] == 1
    state.fail_call(call, 'known local transport rejection', uncertain=False)
    return state, package, call


@pytest.mark.parametrize('suffix,outcome', [('', None), ('_repair', None), ('', 400), ('_repair', 429),
    ('_repair2', 503), ('_repair', 500)])
def test_known_rejection_allows_new_fine_and_plan_without_resending(tmp_path, suffix, outcome):
    state, package, failed = failed_observation(tmp_path, suffix=suffix, outcome=outcome)
    original = deepcopy(state.data['calls'])
    failure_path = state.output / 'calls' / failed['id'] / 'failure.json'
    failure_bytes = failure_path.read_bytes()
    prior_sends = journal(state) if (state.output / 'mcp_http.jsonl').exists() else []
    call, body = submit(state, 'fine_' + 'b' * 16, 'Next independent fine observation')
    assert run_guard(state, package, body, [200, 200]) == dict(sends=1,
        results=[200, 'library_mcp_retry_or_budget_blocked'], delays=[], timeline=['send'])
    state.complete_call(call, dict(status='complete'))
    plan, body = submit(state, 'plan_0', 'Plan using only received watched windows')
    assert run_guard(state, package, body, [200])['results'] == [200]
    state.complete_call(plan, dict(status='complete'))
    assert state.data['calls'][:len(original)] == original
    assert failure_path.read_bytes() == failure_bytes
    events = journal(state)
    assert events[:len(prior_sends)] == prior_sends
    assert [e['job_id'] for e in events[len(prior_sends):] if e['type'] == 'request'] == [call['id'], plan['id']]
    assert not any('retry_permit' in e['type'] for e in events)
    request = json.loads((failure_path.parent / 'request.json').read_text())
    with pytest.raises(LibraryStopped, match='recorded_request_not_received_no_replay:'):
        opencode_provider.OpenCodeMCP._submit(
            SimpleNamespace(state=state, output=state.output), failed['name'], request)


@pytest.mark.parametrize('change', ['200', '204', '200_then_429', 'unknown', 'unanswered',
    'uncertain_failure', 'missing_uncertain', 'missing_failure', 'uncertain_row', 'submitted_row',
    'plan_0', 'review_0', 'select_render', 'chain_e2e_v1_fine_observe', 'fine_bad_repair3',
    'visual_library_v1', 'visual_audio_v1', 'legacy'])
def test_only_proven_known_fine_rejections_are_non_poisoning(tmp_path, change):
    state, package, failed = failed_observation(tmp_path)
    _, body = submit(state, 'fine_' + 'b' * 16, 'Next independent fine observation')
    failure = state.output / 'calls' / failed['id'] / 'failure.json'
    events = journal(state)
    if change in {'200', '204'}:
        events[-1]['status'] = int(change)
    elif change == '200_then_429':
        events[-1]['status'] = 200
        events += [dict(type='request', seq=3, job_id=failed['id']),
                   dict(type='response', seq=3, job_id=failed['id'], status=429)]
    elif change == 'unknown':
        events.append(dict(type='unknown_result', seq=3, job_id=failed['id']))
    elif change == 'unanswered':
        events.append(dict(type='request', seq=3, job_id=failed['id']))
    elif change in {'uncertain_failure', 'missing_uncertain'}:
        write_json(failure, dict(error='known rejection', **({'uncertain': True} if change == 'uncertain_failure' else {})))
    elif change == 'missing_failure':
        failure.unlink()
    elif change in {'uncertain_row', 'submitted_row'}:
        state.data['calls'][-2]['status'] = change.removesuffix('_row')
    elif change in {'visual_library_v1', 'visual_audio_v1', 'legacy'}:
        state.data['input_lock']['configuration']['workflow'] = change
    else:
        state.data['calls'][-2]['name'] = change
    write_json(state.path, state.data)
    (state.output / 'mcp_http.jsonl').write_text(''.join(json.dumps(e) + '\n' for e in events))
    assert run_guard(state, package, body, [200]) == dict(sends=0, results=[BLOCKED], delays=[], timeline=[])


def test_429_still_has_no_automatic_retry(tmp_path):
    state, package, _ = fixture(tmp_path)
    state.complete_call(state.data['calls'][-1], dict(status='complete'))
    call, body = submit(state, STAGE, 'Independent fine observation rejected by rate limit')
    assert run_guard(state, package, body, [429, 200]) == dict(sends=1,
        results=[429, 'library_mcp_retry_or_budget_blocked'], delays=[], timeline=['send'])
    assert [e['type'] for e in journal(state)] == ['request', 'response']
    assert journal(state)[-1]['job_id'] == call['id']


def test_pipeline_excludes_failed_window_from_plan_and_question_first_validation(fine_run, monkeypatch):
    case = fine_run
    validate = Mock(wraps=pipeline._validate_question_first_windows)
    monkeypatch.setattr(pipeline, '_validate_question_first_windows', validate)
    with pytest.raises(case.ReachedPlan):
        case.run()
    assert case.fine_count == 2 and case.seen[-1] == 'plan_0'
    windows = json.loads((case.output / 'watched_windows.json').read_text())
    failure = case.plan_context['reference']['fine_observation_failures'][0]
    assert len(windows) == len(case.plan_context['watched_windows']) == 1
    assert [w['window_id'] for w in case.plan_context['watched_windows']] == [windows[0]['window_id']]
    assert failure['window_id'] != windows[0]['window_id']
    assert windows[0]['status'] == 'watched'
    # Reuse checks must accept the surviving window with its original subtitle masks.
    source_map = validate.call_args.args[1]
    validate(windows, source_map, case.output)
    assert windows[0]['question_first_subtitle_bar'] == source_map[windows[0]['source_id']]['subtitle_bar']
    opencode_provider.subprocess.Popen.assert_not_called()
