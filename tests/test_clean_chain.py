"""The chain's sequencing and settlement, without model or media services."""
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from omni_story.library import clean_chain as chain


@pytest.fixture
def harness(tmp_path, monkeypatch):
    cases = {}

    class State:
        def __init__(self, output):
            self.output = output
            self.data = {'request_count': 5, 'calls': []}
            self.artifacts = []
        def _reload(self):
            pass
        def usage(self):
            return {'requests': self.data['request_count'], 'max_requests': None}
        def set_artifact(self, name, value):
            self.artifacts.append((name, value))

    class MCP:
        def __init__(self, state):
            self.state = state
        def call(self, name, prompt, media, validator):
            case = cases[self.state.output]
            case['events'].append('selected_review')
            case['review_prompt'] = prompt
            assert Path(media).read_bytes() == b'actual whole rough proxy'
            assert chain.sha256_file(case['rough']['path']) == case['rough']['sha256']
            if case.get('raise_at') == 'review':
                raise ValueError('model_protocol_repair_exhausted:selected_review')
            validator(case['review'])
            self.state.data['request_count'] += 1
            self.state.data['calls'].append(dict(id='new_received_review', name=name, status='received'))
            return dict(case['review'])

    def make(name='a', selected=0):
        base = tmp_path / name
        base.mkdir()
        reference = base / 'reference.mp4'
        reference.write_bytes(name.encode() + b' fixed reference')
        library = base / 'movies'
        library.mkdir()
        output = base / 'run'
        ref = dict(path=str(reference), sha256=chain.sha256_file(reference), duration_s=22)
        reading = dict(reference_sha256=ref['sha256'], theme='Observed reference meaning')
        seed = dict(source_call_id='received_case_' + name, full_response={'reference': reading},
                    evidence_limit='Received interpretation has native sampling limits.')
        case = dict(events=[], selected=selected, reference=ref, seed=seed,
            review=dict(reference_sha256=ref['sha256'], theme_status='partial', editing_status='partial',
                        continuity_status='pass', evidence=['Actual output 1–2s action.'],
                        limitations=['Audio craft unverified.'], revision_requests=[]))
        cases[output] = case
        def factory(state):
            assert state.output == output
            case['state'] = state
            case['mcp'] = MCP(state)
            return case['mcp']
        case['arguments'] = dict(reference=reference, library=library, output=output,
            model_factory=factory, reference_seed=seed, registry_path=tmp_path / 'registry.json', asr=False)
        return case

    def rough_pipeline(reference, library, output, **options):
        case = cases[output]
        case['options'] = options
        if case.get('raise_at') == 'rough':
            raise ValueError('model_protocol_repair_exhausted:plan_0')
        state = State(output)
        options['model_factory'](state)
        movie = output / 'actual_rough.mp4'
        movie.write_bytes(str(output).encode() + b' actual model rough')
        case['events'].append('actual_render')
        source = dict(path=str(movie), sha256=chain.sha256_file(movie), duration_s=77)
        case['rough'] = source
        selected = case['selected']
        original_review = dict(case['review'], theme_status='fail')
        result = dict(final_video=str(movie), final_sha256=source['sha256'], selected_round=selected,
            selected_review_path=str(output / f'review_{selected}.json'), review=original_review,
            evidence_limit='Model observation limits')
        chain.write_json(output / f'render_{selected}/render_result.json',
            dict(sha256=source['sha256'], measured_duration_s=77, provenance=[{'segment_id': 'model_owned'}]))
        chain.write_json(output / f'plan_{selected}.json', {'segments': [{'segment_id': 'model_owned'}]})
        chain.write_json(output / f'blind_reading_{selected}.json', {'observed_story': 'Actual rough actions.'})
        chain.write_json(output / 'reference_catalog/inventory.json', {'sources': [case['reference']]})
        chain.write_json(output / 'reference_reading.json', case['seed']['full_response']['reference'])
        case['events'].extend(('rough_review', 'rough_selection'))
        chain.write_json(output / 'result.json', result)
        case['original_result_bytes'] = (output / 'result.json').read_bytes()
        return result

    def inventory(path, output):
        case = next(case for case in cases.values() if case.get('rough', {}).get('path') == str(path))
        return {'sources': [case['rough']]}

    def prepare(source, start, end, cache, *, label):
        assert start == 0 and end == source['duration_s'] and label == 'clean_selected_rough'
        assert chain.sha256_file(source['path']) == source['sha256']
        path = cache / 'actual_proxy.mp4'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'actual whole rough proxy')
        return {'path': str(path)}

    def finecut(state, mcp, context, parent, ref, output):
        case = cases[state.output]
        case['events'].append('skill')
        assert state is case['state'] and mcp is case['mcp']
        assert parent is case['rough'] and ref == case['reference']
        assert state.data['request_count'] == 6
        handoff = json.loads((state.output / 'rough_handoff.json').read_text(encoding='utf-8'))
        assert handoff['rough_video_path'] == parent['path']
        assert handoff['rough_source_sha256'] == parent['sha256']
        assert handoff['rough_duration_s'] == 77
        assert handoff['rough_review_call_id'] == 'new_received_review'
        assert context['actual_rough_handoff'] == handoff
        assert context['prior_rough_review'] == case['review']
        expected_limit = case['seed']['evidence_limit'] if case['arguments']['reference_seed'] else 'Model observation limits'
        assert context['evidence_limit'] == expected_limit
        assert output == state.output / 'skill_finecut'
        if case.get('barrier'):
            case['barrier'].wait(timeout=10)
        if case.get('raise_at') == 'skill':
            raise ValueError('model_protocol_repair_exhausted:skill')
        output.mkdir()
        movie = output / 'fine.mp4'
        movie.write_bytes(str(state.output).encode() + b' actual fine')
        state.data['request_count'] += 7
        return dict(final_video=str(movie), final_sha256=chain.sha256_file(movie), joint_quality_gate=True)

    monkeypatch.setattr(chain.pipeline, 'execute', rough_pipeline)
    monkeypatch.setattr(chain, 'inventory_sources', inventory)
    monkeypatch.setattr(chain, 'proxy', prepare)
    monkeypatch.setattr(chain, 'execute_finecut', finecut)
    return make


@pytest.mark.parametrize('selected', [0, 1, 4])
def test_actual_render_and_selected_review_precede_skill_without_replacing_rough(harness, selected):
    case = harness(selected=selected)
    result = chain.execute(**case['arguments'])
    assert case['events'] == ['actual_render', 'rough_review', 'rough_selection', 'selected_review', 'skill']
    output = case['arguments']['output']
    assert (output / 'result.json').read_bytes() == case['original_result_bytes']
    assert result['rough']['review']['theme_status'] == 'fail'
    assert result['rough_review'] == case['review']
    assert result['joint_quality_gate'] is False
    assert result['usage']['requests'] == 13
    assert (output / 'mcp_stop').read_text() == 'reference_rough_skill_settled'
    prefix = chain.selected_review.review_prompt({})[:-2]
    assert case['review_prompt'].startswith(prefix)
    review_context = json.loads(case['review_prompt'][len(prefix):])
    assert review_context['actual_render_sha256'] == case['rough']['sha256']
    assert review_context['blind_reading']['observed_story'] == 'Actual rough actions.'
    assert review_context['review_output_contract'] == chain.contracts.review_output_contract()
    options = case['options']
    assert all(options[field] is False for field in ('active_finecut', 'semantic_audit', 'editing_v2', 'asr'))
    assert options['max_requests'] is None and options['max_fine'] is None
    assert options['prompt_module'] is chain.ROUGH_PROMPTS
    assert options['render_fn'] is chain.historical.render_library_video
    assert options['provider_config']['workflow'] == chain.POLICY
    assert not {'max_rounds', 'max_renders', 'max_fine'} & options['provider_config'].keys()
    assert options['reference_seed']['source_call_id'] == 'received_case_a'


@pytest.mark.parametrize('status', ['pass', 'partial', 'unverifiable', 'fail'])
def test_quality_tracks_actual_rough_review_and_fine_gate(harness, status):
    case = harness()
    case['review'].update({field: status for field in chain.REVIEW_FIELDS})
    result = chain.execute(**case['arguments'])
    assert result['joint_quality_gate'] is (status == 'pass')
    assert ('skill' in case['events']) is (status != 'fail')
    if status == 'fail':
        assert result['status'] == 'rough_review_failed_candidate'
        assert result['final_video'] == case['rough']['path']


@pytest.mark.parametrize('failed_field', chain.REVIEW_FIELDS)
def test_functional_test_continues_failed_actual_rough_review_and_keeps_false_quality_gate(harness, failed_field):
    case = harness()
    case['review'].update({field: 'pass' for field in chain.REVIEW_FIELDS})
    case['review'][failed_field] = 'fail'
    case['arguments']['provider_config'] = {'quality_policy': 'functional_test_keep_negative_reviews'}

    result = chain.execute(**case['arguments'])

    assert case['events'] == ['actual_render', 'rough_review', 'rough_selection', 'selected_review', 'skill']
    assert result['status'] == 'rough_to_skill_completed'
    assert result['rough_review'] == case['review']
    assert result['rough_review'][failed_field] == 'fail'
    assert result['joint_quality_gate'] is False
    assert result['final_video'] == result['finecut']['final_video']
    assert result['final_sha256'] == chain.sha256_file(result['final_video'])
    assert (case['arguments']['output'] / 'result.json').read_bytes() == case['original_result_bytes']
    assert result['usage']['requests'] == 13


def test_settled_evaluation_cannot_change_quality_policy_to_start_finecut(harness):
    case = harness()
    case['review']['theme_status'] = 'fail'
    result = chain.execute(**case['arguments'])
    assert result['status'] == 'rough_review_failed_candidate'
    prior = list(case['events'])
    case['arguments']['provider_config'] = {'quality_policy': 'functional_test_keep_negative_reviews'}

    with pytest.raises(chain.LibraryStopped, match='cached_inputs_changed'):
        chain.execute(**case['arguments'])

    assert case['events'] == prior and 'skill' not in case['events']


@pytest.mark.parametrize('stage', ['rough', 'review', 'skill'])
def test_terminal_failure_is_recorded_and_blocks_automatic_restart(harness, stage):
    case = harness()
    case['raise_at'] = stage
    with pytest.raises(ValueError, match='repair_exhausted'):
        chain.execute(**case['arguments'])
    output = case['arguments']['output']
    failure = json.loads((output / 'chain_failure.json').read_text(encoding='utf-8'))
    assert failure['automatic_restart'] is False
    prior = list(case['events'])
    with pytest.raises(chain.LibraryStopped, match='terminal_failure_no_automatic_restart'):
        chain.execute(**case['arguments'])
    assert case['events'] == prior and not (output / 'chain_result.json').exists()


def test_settled_cache_only_reuses_hash_verified_artifacts(harness):
    case = harness()
    original = chain.execute(**case['arguments'])
    prior = list(case['events'])
    assert chain.execute(**case['arguments']) == original
    assert case['events'] == prior
    Path(original['final_video']).write_bytes(b'changed')
    with pytest.raises(chain.LibraryStopped, match='cached_artifact_changed'):
        chain.execute(**case['arguments'])
    assert case['events'] == prior


def test_cache_rejects_changed_received_reference_context_without_calls(harness):
    case = harness()
    chain.execute(**case['arguments'])
    prior = list(case['events'])
    case['seed']['evidence_limit'] = 'changed evidence'
    with pytest.raises(chain.LibraryStopped, match='cached_inputs_changed'):
        chain.execute(**case['arguments'])
    assert case['events'] == prior


def test_interrupted_chain_is_not_automatically_resumed(harness):
    case = harness()
    output = case['arguments']['output']
    output.mkdir()
    (output / 'chain_started').write_text(chain.POLICY)
    with pytest.raises(chain.LibraryStopped, match='interrupted_no_automatic_restart'):
        chain.execute(**case['arguments'])
    assert case['events'] == []


def test_two_parallel_tasks_keep_their_injected_state_media_and_result_separate(harness):
    first, second = harness('first'), harness('second', selected=1)
    first['barrier'] = second['barrier'] = Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        outputs = list(pool.map(lambda case: chain.execute(**case['arguments']), (first, second)))
    assert first['state'] is not second['state'] and first['mcp'] is not second['mcp']
    assert outputs[0]['final_video'] != outputs[1]['final_video']
    assert outputs[0]['rough_sha256'] != outputs[1]['rough_sha256']
    assert all(result['usage']['requests'] == 13 for result in outputs)


def test_asr_model_directory_is_forwarded_without_changing_chain_method(harness, tmp_path):
    case = harness()
    model = tmp_path / 'offline_small'
    model.mkdir()
    case['arguments'].update(asr=True, asr_model_dir=model)
    chain.execute(**case['arguments'])
    assert case['options']['asr'] is True and case['options']['asr_model_dir'] == model.resolve()


def test_fresh_reference_observation_context_does_not_require_historical_call_id(harness):
    case = harness()
    case['arguments']['reference_seed'] = None
    result = chain.execute(**case['arguments'])
    assert result['status'] == 'rough_to_skill_completed'
    assert case['options']['reference_seed'] is None
    prefix = chain.selected_review.review_prompt({})[:-2]
    context = json.loads(case['review_prompt'][len(prefix):])
    assert context['reference'] == case['seed']['full_response']['reference']
    assert context['reference_protocol_limit'] == 'Model observation limits'


@pytest.mark.parametrize('round_no', [0, 1, 7])
def test_progress_prompts_remove_total_plan_caps_and_keep_historical_phase(round_no):
    context = {'reference_duration_s': 480, 'render_capabilities':
               {'max_duration_s': 180, 'max_segments': 32, 'speed': [0.5, 2]}}
    prompt = chain.ROUGH_PROMPTS.for_round(round_no).plan_prompt(context)
    assert '不得超过180秒' not in prompt and '最多32个segments' not in prompt
    assert '"max_duration_s"' not in prompt and '"max_segments"' not in prompt
    assert '总成片时长和segments数量不设固定上限' in prompt
    marker = '本任务是异源素材的主旨迁移'
    assert (marker in prompt) is (round_no > 0)
    assert context['render_capabilities']['max_duration_s'] == 180
    # These integrity checks still verify unchanged archived bytes.
    assert chain.historical.provenance()['templates_sha256']


@pytest.mark.parametrize('round_no', [0, 1, 7])
def test_review_type_context_preserves_phase_and_supplied_evidence(round_no):
    context = {'reference': {'sha256': 'unchanged-reference'},
               'blind_reading': {'evidence': [{'observed_fact': 'Original uncertain fact.'}]}}
    original = json.loads(json.dumps(context))
    phase = chain.historical.for_round(min(round_no, 1))
    prefix = phase.review_prompt({})[:-2]
    prompt = chain.ROUGH_PROMPTS.for_round(round_no).review_prompt(context)
    assert prompt.startswith(prefix)
    received_context = json.loads(prompt[len(prefix):])
    contract = received_context.pop('review_output_contract')
    assert received_context == original and context == original
    assert contract == chain.contracts.review_output_contract()
    assert chain.selected_review.provenance()['template_sha256']


def test_search_keeps_only_tool_batching_and_selection_accepts_many_candidates():
    context = {'remaining_window_budget': 16, 'max_windows_this_round': 8,
               'instruction': '最多选择8个精看窗口；第二轮只补具体缺项；不要换参考。'}
    prompt = chain.ROUGH_PROMPTS.search_prompt(context)
    assert '观察总数不设固定上限' in prompt and '每批不超过8个' in prompt
    assert 'remaining_window_budget' not in prompt and '第二轮' not in prompt
    assert context['remaining_window_budget'] == 16
    selected = chain.ROUGH_PROMPTS.select_prompt([{'round': index} for index in range(5)])
    assert '比较两个实际成片' not in selected and '比较这些实际成片' in selected


def test_stale_provider_total_caps_do_not_restrict_new_random_reference(harness):
    case = harness(selected=9)
    case['arguments']['provider_config'] = {'max_rounds': 2, 'max_renders': 2, 'max_fine': 16}
    result = chain.execute(**case['arguments'])
    assert not {'max_rounds', 'max_renders', 'max_fine'} & result['input']['configuration'].keys()
    assert result['rough']['selected_round'] == 9


def test_uncapped_search_has_no_none_count_and_can_report_no_new_evidence():
    prompt = chain.ROUGH_PROMPTS.search_prompt({'max_windows_this_round': None})
    assert 'None个' not in prompt
    assert 'windows可为[]' in prompt
