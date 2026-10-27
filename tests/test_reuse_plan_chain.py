"""Chain-traced --reuse-plan seeding across settled tasks; no network or real keys."""
import json

import pytest

from omni_story.library import server_cli as cli
from omni_story.library.media import sha256_file
from omni_story.library.state import LibraryStopped, write_json


@pytest.fixture(autouse=True)
def settled_worker_groups(monkeypatch):
    monkeypatch.setattr(cli.server_jobs, '_group_running', lambda pid: False)


@pytest.fixture
def home(tmp_path):
    return tmp_path


def settled(task, name):
    task.mkdir()
    write_json(task / 'watched_windows.json', [])
    write_json(task / '.omni-server/job.json', {'job_id': name, 'state': 'failed'})
    return task.resolve()


def plan_ancestor(home, name):
    task = settled(home / name, name)
    call = dict(id=f'glm_{name}_plan_0', name='plan_0', status='received')
    write_json(task / 'library_state.json', dict(task_id=name, request_count=1, calls=[call],
        input_lock=dict(configuration=dict(prior_requests=0))))
    return task


def reuse_child(home, name, seed):
    task = settled(home / name, name)
    call = dict(id=f'glm_{name}_render_0', name='render_0', status='received')
    write_json(task / 'library_state.json', dict(task_id=name, request_count=1, calls=[call],
        input_lock=dict(configuration=dict(prior_requests=4,
            received_plan_seed=seed['received_plan_seed'],
            received_visual_seed=seed['received_visual_seed']))))
    return task


def pointing_seed(target, call_id):
    binding = {'path': str(target / 'library_state.json'),
               'sha256': sha256_file(target / 'library_state.json')}
    return {'received_plan_seed': {'ledger': binding, 'call_id': call_id},
            'received_visual_seed': {'ledger': binding,
                'windows': {'path': str(target / 'watched_windows.json'),
                            'sha256': sha256_file(target / 'watched_windows.json')}}}


def state_of(task):
    return json.loads((task / 'library_state.json').read_text(encoding='utf-8'))


def test_direct_parent_with_own_plan_keeps_the_original_binding(home):
    parent = plan_ancestor(home, 'parent')
    binding = {'path': str(parent / 'library_state.json'),
               'sha256': sha256_file(parent / 'library_state.json')}
    assert cli.received_plan_config(parent) == {
        'received_plan_seed': {'ledger': binding, 'call_id': 'glm_parent_plan_0'},
        'received_visual_seed': {'ledger': binding,
            'windows': {'path': str(parent / 'watched_windows.json'),
                        'sha256': sha256_file(parent / 'watched_windows.json')}}}


def test_parent_reusing_an_ancestor_returns_the_original_seed_without_rewriting(home):
    ancestor = plan_ancestor(home, 'ancestor')
    original = cli.received_plan_config(ancestor)
    parent = reuse_child(home, 'parent', original)
    before = sorted(path.name for path in parent.iterdir())
    ledger_before = (parent / 'library_state.json').read_bytes()
    result = cli.received_plan_config(parent)
    assert result == original
    assert result['received_plan_seed']['ledger']['path'] == str(ancestor / 'library_state.json')
    assert sorted(path.name for path in parent.iterdir()) == before
    assert (parent / 'library_state.json').read_bytes() == ledger_before


def test_two_level_chain_flattens_to_the_earliest_plan_ancestor(home):
    ancestor = plan_ancestor(home, 'ancestor')
    original = cli.received_plan_config(ancestor)
    middle = reuse_child(home, 'middle', original)
    child = reuse_child(home, 'child', cli.received_plan_config(middle))
    assert cli.received_plan_config(middle) == original
    assert cli.received_plan_config(child) == original


def test_unflattened_seed_chain_walks_every_link_to_the_plan_ancestor(home):
    ancestor = plan_ancestor(home, 'ancestor')
    original = cli.received_plan_config(ancestor)
    middle = reuse_child(home, 'middle', original)
    parent = reuse_child(home, 'parent', pointing_seed(middle, 'glm_ancestor_plan_0'))
    assert cli.received_plan_config(parent) == original


def test_modified_middle_windows_in_unflattened_chain_rejects_the_reuse(home):
    ancestor = plan_ancestor(home, 'ancestor')
    middle = reuse_child(home, 'middle', cli.received_plan_config(ancestor))
    parent = reuse_child(home, 'parent', pointing_seed(middle, 'glm_ancestor_plan_0'))
    write_json(middle / 'watched_windows.json', [dict(window_id='changed')])
    with pytest.raises(LibraryStopped, match='reuse_plan_seed_changed'):
        cli.received_plan_config(parent)


def test_modified_ancestor_ledger_or_windows_rejects_the_reuse(home):
    ancestor = plan_ancestor(home, 'ancestor')
    parent = reuse_child(home, 'parent', cli.received_plan_config(ancestor))
    saved = state_of(ancestor)
    write_json(ancestor / 'library_state.json', {**saved, 'task_id': 'edited'})
    with pytest.raises(LibraryStopped, match='reuse_plan_seed_changed'):
        cli.received_plan_config(parent)
    write_json(ancestor / 'library_state.json', saved)
    write_json(ancestor / 'watched_windows.json', [dict(window_id='w0')])
    with pytest.raises(LibraryStopped, match='reuse_plan_seed_changed'):
        cli.received_plan_config(parent)


def test_unsettled_or_unknown_parent_rejects_before_any_chain_walk(home):
    ancestor = plan_ancestor(home, 'ancestor')
    parent = reuse_child(home, 'parent', cli.received_plan_config(ancestor))
    write_json(parent / '.omni-server/job.json', {'job_id': 'parent', 'state': 'running'})
    with pytest.raises(LibraryStopped, match='reuse_plan_parent_not_settled'):
        cli.received_plan_config(parent)
    write_json(parent / '.omni-server/job.json', {'job_id': 'parent', 'state': 'failed'})
    state = state_of(parent)
    write_json(parent / 'library_state.json', {**state, 'request_count': 2, 'calls': state['calls'] + [
        dict(id='glm_parent_fine_0', name='fine_0', status='uncertain')]})
    with pytest.raises(LibraryStopped, match='reuse_plan_parent_results_not_known'):
        cli.received_plan_config(parent)


def test_parent_without_plan_or_seed_still_requires_a_received_plan(home):
    task = settled(home / 'plain', 'plain')
    write_json(task / 'library_state.json', dict(task_id='plain', request_count=1,
        calls=[dict(id='glm_plain_render_0', name='render_0', status='received')],
        input_lock=dict(configuration=dict(prior_requests=1))))
    with pytest.raises(LibraryStopped, match='reuse_plan_received_plan_required'):
        cli.received_plan_config(task)


def test_self_referential_seed_is_rejected_as_a_cycle(home):
    parent = settled(home / 'parent', 'parent')
    forged = {'path': str(parent / 'library_state.json'), 'sha256': '0' * 64}
    write_json(parent / 'library_state.json', dict(task_id='parent', request_count=1,
        calls=[dict(id='glm_parent_render_0', name='render_0', status='received')],
        input_lock=dict(configuration=dict(prior_requests=4,
            received_plan_seed={'ledger': forged, 'call_id': 'glm_parent_plan_0'},
            received_visual_seed={'ledger': forged,
                'windows': {'path': str(parent / 'watched_windows.json'), 'sha256': '0' * 64}}))))
    with pytest.raises(LibraryStopped, match='reuse_plan_cycle_detected'):
        cli.received_plan_config(parent)


def test_mutual_seed_cycle_is_rejected(home):
    parent, other = settled(home / 'parent', 'parent'), settled(home / 'other', 'other')
    forged = {'path': str(parent / 'library_state.json'), 'sha256': '0' * 64}
    write_json(other / 'library_state.json', dict(task_id='other', request_count=1,
        calls=[dict(id='glm_other_render_0', name='render_0', status='received')],
        input_lock=dict(configuration=dict(prior_requests=4,
            received_plan_seed={'ledger': forged, 'call_id': 'glm_other_plan_0'},
            received_visual_seed={'ledger': forged,
                'windows': {'path': str(other / 'watched_windows.json'), 'sha256': '0' * 64}}))))
    write_json(parent / 'library_state.json', dict(task_id='parent', request_count=1,
        calls=[dict(id='glm_parent_render_0', name='render_0', status='received')],
        input_lock=dict(configuration=dict(prior_requests=4,
            **pointing_seed(other, 'glm_other_plan_0')))))
    with pytest.raises(LibraryStopped, match='reuse_plan_cycle_detected'):
        cli.received_plan_config(parent)


def test_longer_seed_chains_than_ten_links_are_rejected(home):
    ancestor = plan_ancestor(home, 'ancestor')
    original = cli.received_plan_config(ancestor)
    links = [reuse_child(home, 'link_0', original)]
    for index in range(1, 11):
        links.append(reuse_child(home, f'link_{index}', pointing_seed(links[-1], 'glm_ancestor_plan_0')))
    assert cli.received_plan_config(links[9]) == original
    with pytest.raises(LibraryStopped, match='reuse_plan_chain_too_long'):
        cli.received_plan_config(links[10])
