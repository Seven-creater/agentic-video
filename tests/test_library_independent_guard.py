"""Narrow round-11 ordering and binding checks, using temporary metadata only."""
from copy import deepcopy
import pytest

from omni_story.library import goal_budget as goal, dense_source_frames
from omni_story.library.state import LibraryStopped, write_json
from test_library_goal_budget import setup, register


def strategy():
    return {'admitted_unknown_call_ids': ['glm_131_active_10_draft'],
            'prefacts': [{'stage': f'semantic_slice_11_{i:016x}', 'source_sha256': 'e' * 64,
                         'segment': {'segment_id': f'seg_{i}', 'source_id': 'movie',
                                     'source_in_s': 1300 + i, 'source_out_s': 1301 + i}}
                        for i in range(6)]}


def previous_unknown():
    return {'active_10_draft': {'id': 'glm_131_active_10_draft', 'name': 'active_10_draft',
                               'status': 'uncertain'}}


def fixture(tmp_path, monkeypatch):
    state = setup(tmp_path)
    register(state, 11)
    record = strategy()
    monkeypatch.setattr(goal, '_independent', lambda output, data, round_no=11: record if round_no == 11 else None)
    return state, record


def test_only_listed_prefacts_can_follow_preserved_unknown(tmp_path, monkeypatch):
    state, record = fixture(tmp_path, monkeypatch)
    names = previous_unknown()
    goal._progress(state.output, state.data, record['prefacts'][0]['stage'], names, {})
    with pytest.raises(LibraryStopped, match='predecessor_unsettled'):
        goal._progress(state.output, state.data, 'semantic_slice_11_' + 'f' * 16, names, {})
    names['active_10_draft']['id'] = 'different_unknown'
    with pytest.raises(LibraryStopped, match='previous_round_not_finished'):
        goal._progress(state.output, state.data, record['prefacts'][0]['stage'], names, {})


def test_all_six_received_parsed_facts_are_required_before_draft(tmp_path, monkeypatch):
    state, record = fixture(tmp_path, monkeypatch)
    names = previous_unknown()
    for i, row in enumerate(record['prefacts']):
        call = {'id': f'fixture_{i}', 'name': row['stage'], 'status': 'received'}
        names[row['stage']] = call
        if i < 5:
            write_json(state.output / 'calls' / call['id'] / 'parsed.json', {'synthetic': True})
    with pytest.raises(LibraryStopped, match='predecessor_unsettled'):
        goal._progress(state.output, state.data, 'active_11_draft', names, {})
    write_json(state.output / 'calls' / 'fixture_5' / 'parsed.json', {'synthetic': True})
    goal._progress(state.output, state.data, 'active_11_draft', names, {})
    names['active_11_draft'] = {'id': 'draft', 'name': 'active_11_draft', 'status': 'received'}
    with pytest.raises(LibraryStopped, match='stage_progress_regressed'):
        goal._progress(state.output, state.data, record['prefacts'][0]['stage'], names, {})
    with pytest.raises(LibraryStopped, match='predecessor_unsettled'):
        goal._progress(state.output, state.data, 'active_11_review', names, {})


def test_prefact_repairs_remain_one_known_immediate_parent(tmp_path, monkeypatch):
    state, record = fixture(tmp_path, monkeypatch)
    row = record['prefacts'][0]
    names = previous_unknown()
    original = {'id': 'original', 'name': row['stage'], 'status': 'received'}
    names[row['stage']] = original
    goal._progress(state.output, state.data, row['stage'] + '_repair', names, {}, 'original')
    original['status'] = 'uncertain'
    with pytest.raises(LibraryStopped, match='invalid_repair_parent'):
        goal._progress(state.output, state.data, row['stage'] + '_repair', names, {}, 'original')


def test_prefact_request_cannot_forge_scope_or_carrier(tmp_path, monkeypatch):
    record = strategy()
    row = record['prefacts'][0]
    scope = {'kind': 'continuous_window', 'source_sha256': row['source_sha256'],
             'source_start_s': row['segment']['source_in_s'], 'source_end_s': row['segment']['source_out_s']}
    image = tmp_path / 'sheet.jpg'
    image.write_bytes(b'synthetic carrier')
    write_json(tmp_path / 'manifest.json', {'normal_proxy': scope})
    request = {'tool': 'analyze_image', 'arguments': {'image_source': str(image)},
               'media_sha256': 'c' * 64, 'observation_scope': scope}
    monkeypatch.setattr(dense_source_frames, 'saved_media', lambda proxy: {'path': str(image), 'sha256': 'c' * 64})
    goal._check_prefact_request(record, row['stage'], request)
    for field, value in [('source_sha256', 'a' * 64), ('source_start_s', 1299),
                         ('source_end_s', 1302), ('kind', 'sparse_contact_sheet')]:
        wrong = deepcopy(request)
        wrong['observation_scope'][field] = value
        with pytest.raises(LibraryStopped, match='prefact_source_scope_changed'):
            goal._check_prefact_request(record, row['stage'], wrong)
    with pytest.raises(LibraryStopped, match='prefact_carrier_changed'):
        goal._check_prefact_request(record, row['stage'], {**request, 'media_sha256': 'd' * 64})
