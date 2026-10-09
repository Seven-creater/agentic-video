from pathlib import Path
import json

import pytest

pytest.importorskip('av')
pytest.importorskip('PIL')

from omni_story.library import server_chain_e2e as chain
from omni_story.library.state import LibraryStopped


@pytest.fixture
def setup_chain(tmp_path, monkeypatch):
    output, home = tmp_path / 'run', tmp_path / 'home'
    output.mkdir(); home.mkdir()
    base = output / 'artifacts/one_chain_e2e_v1'
    proof = dict(execution_directory=str(base), rough_directory=str(base / 'rough'),
        input_lock={}, baseline_request_count=35, catalog={'sources': [{'source_id': 'movie'}]},
        rough_plan={'authored': 'original_glm_147'}, reference={'sha256': 'reference', 'duration_s': 21.93})
    seed = dict(reference_sha256='reference', full_response={'reference': {'reference_sha256': 'reference'},
        'editing_reference': {}}, evidence_limit='historical partial coverage', source_call_id='glm_060')
    monkeypatch.setattr(chain.authorization, 'load', lambda output: proof)
    monkeypatch.setattr(chain, 'settings', lambda home: {})
    monkeypatch.setattr(chain, 'load_history', lambda cfg: {'reference_seed': seed, 'unknown_inputs': []})
    class State:
        def __init__(self, output, *args, **kwargs):
            self.output = output; self.data = {'request_count': 35}
        def _reload(self): pass
        def set_artifact(self, *args): pass
    monkeypatch.setattr(chain, 'LibraryState', State)
    events = []
    actual = tmp_path / 'rough.mp4'; actual.write_bytes(b'rough')
    def render(sources, plan, directory, **kwargs):
        events.append(('rough_render', plan))
        return dict(rendered_path=str(actual), sha256=chain.sha256_file(actual), duration_s=147)
    monkeypatch.setattr(chain, 'render_library_video', render)
    parent = dict(path=str(actual), sha256=chain.sha256_file(actual), duration_s=147)
    monkeypatch.setattr(chain, 'inventory_sources', lambda *args: {'sources': [parent]})
    monkeypatch.setattr(chain.evidence, 'layout', lambda source: {'crop': [0,0,10,10], 'picture_crop':[0,1,10,8]})
    monkeypatch.setattr(chain.evidence, 'proxy', lambda *args, **kwargs: {'path': str(actual)})
    fine = tmp_path / 'fine.mp4'; fine.write_bytes(b'fine')
    def refine(state, mcp, reference_context, parent_source, reference_source, destination):
        events.append(('finecut_actual', parent_source))
        assert parent_source['path'] == str(actual)
        assert reference_source['duration_s'] == 21.93
        return dict(final_video=str(fine), final_sha256=chain.sha256_file(fine), joint_quality_gate=True)
    monkeypatch.setattr(chain, 'execute_finecut', refine)
    class MCP:
        blind_status = 'pass'
        ready = True
        def call(self, name, prompt, media, validator, **kwargs):
            events.append(('call', name))
            value = dict(status=self.blind_status, apparent_story='actual visible order',
                         facts=[{'start_s':0,'end_s':2,'description':'observed action'}], problems=[], limitations=[])
            if name.endswith('review'): value['ready_for_finecut'] = self.ready
            validator(value)
            return value
    return output, home, base, events, MCP()


def test_actual_rough_precedes_observation_and_skill_finecut(setup_chain):
    output, home, base, events, mcp = setup_chain
    result = chain.execute_chain(output, home=home, model_factory=lambda state: mcp)
    assert [item[0] for item in events] == ['rough_render', 'call', 'call', 'finecut_actual']
    assert events[0][1] == {'authored':'original_glm_147'}
    assert result['rough_duration_s'] == 147 and result['joint_quality_gate'] is True
    assert (base / 'result.json').exists()
    assert (output / 'mcp_stop').read_text() == 'one_chain_e2e_settled'


def test_blind_failure_cannot_be_erased_by_target_review(setup_chain):
    output, home, base, events, mcp = setup_chain
    mcp.blind_status = 'fail'
    result = chain.execute_chain(output, home=home, model_factory=lambda state: mcp)
    assert result['status'] == 'rough_content_gate_not_established'
    assert not result['joint_quality_gate']
    assert not any(e[0] == 'finecut_actual' for e in events)


def test_partial_rough_is_delivered_without_joint_pass(setup_chain):
    output, home, base, events, mcp = setup_chain
    mcp.blind_status = 'partial'
    result = chain.execute_chain(output, home=home, model_factory=lambda state: mcp)
    assert result['status'] == 'rough_to_fine_completed'
    assert result['joint_quality_gate'] is False


def test_recorded_result_is_cache_only(setup_chain):
    output, home, base, events, mcp = setup_chain
    initial = chain.execute_chain(output, home=home, model_factory=lambda state: mcp)
    events.clear()
    assert chain.execute_chain(output, home=home, model_factory=lambda state: mcp) == initial
    assert events == []


def test_protocol_failure_stops_without_repeated_round(setup_chain):
    output, home, base, events, mcp = setup_chain
    def failing(*args, **kwargs):
        events.append(('failure', args[0]))
        raise LibraryStopped('model_protocol_repair_exhausted')
    mcp.call = failing
    with pytest.raises(LibraryStopped, match='repair_exhausted'):
        chain.execute_chain(output, home=home, model_factory=lambda state: mcp)
    report = json.loads((output / chain.authorization.FAILURE_REPORT).read_text())
    assert report['automatic_restart'] is False
    assert len([e for e in events if e[0] == 'failure']) == 1
    with pytest.raises(LibraryStopped, match='no_automatic_restart'):
        chain.execute_chain(output, home=home, model_factory=lambda state: mcp)


def test_cli_launches_fixed_worker_module_even_when_executed_as_main(setup_chain, monkeypatch):
    output, home, base, events, mcp = setup_chain
    original = output / 'original_authorization.json'; original.write_text('{}')
    (output / 'library_state.json').write_text(json.dumps({'artifacts': {
        'server_output_capacity_recovery': [{'path': str(original)}]}}))
    monkeypatch.setattr(chain, 'settings', lambda home: {'project_root': str(home)})
    monkeypatch.setattr(chain, 'credential', lambda home: 'synthetic-never-sent')
    registrations = []
    monkeypatch.setattr(chain.authorization, 'register', lambda *a, **kw: registrations.append((a, kw)))
    launched = []
    def start(command, *args, **kwargs):
        launched.append((command, kwargs)); return {'state': 'starting'}
    monkeypatch.setattr(chain.server_jobs, 'start_recovery', start)
    monkeypatch.setattr(chain, '__name__', '__main__')
    assert chain.main(['--home',str(home),'start','--output',str(output),
                       '--user-instruction','test the authorized chain']) == 0
    assert len(registrations) == len(launched) == 1
    assert launched[0][0][1:3] == ['-m','omni_story.library.server_chain_e2e']
    assert launched[0][1]['recovery_name'] == 'one_chain_e2e_v1'
