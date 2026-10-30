"""Real overnight artifacts plus explicitly controlled observations; no model calls."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from omni_story.library import contracts, pipeline, protocol_feedback, question_first as qfs

FIXTURES = Path(__file__).parent / 'fixtures/reference_value_20261030'


def received(name):
    return json.loads((FIXTURES / name).read_text())


@pytest.mark.parametrize('index', range(3))
def test_real_questions_move_literal_staging_out_of_hard_requirements(index):
    from test_question_first import film_cast_fixture
    data = received('bushaoxin_questions.json')
    data['casting'] = {'owner': [{'name': '平先生', 'aliases': [], 'films': [1]}]}
    fixed = [dict(id=b['id'], function=b['function'], reference_evidence={}) for b in data['beats']]
    result = qfs.validate_reply(data, dict(stage='questions', beats=fixed, film_cast=film_cast_fixture()))
    beat = result['beats'][index]
    assert set(beat['must_see']) <= {'who_relation', 'action_class', 'shot_need', 'mouth_open_needed'}
    assert any('单据' in value for value in beat['nice_to_have'])
    assert beat['normalised_must_see'] and beat['must_see']['mouth_open_needed'] is True
    assert '摆手' not in json.dumps(beat['must_see'], ensure_ascii=False)


@pytest.mark.parametrize('key,value', [('prop', '账本'), ('who_relation', '甲' * 49),
    ('action_class', '说' * 17), ('shot_need', '脸' * 25), ('mouth_open_needed', 'yes')])
def test_minimal_schema_repair_and_lossless_non_crashing_normalisation(key, value):
    from omni_story.library.reference_requirements import minimal_visual_contract, validate_must_see, normalise_must_see
    beat = dict(id='b', must_see={key: value}, nice_to_have=['existing'])
    with pytest.raises(ValueError) as caught:
        validate_must_see(beat['must_see'])
    assert caught.value.violation['repair_hint']
    assert caught.value.violation['expected'] == minimal_visual_contract()
    normalise_must_see(beat)
    validate_must_see(beat['must_see'])
    assert any(value in item for item in beat['nice_to_have'])
    assert beat['nice_to_have'][0] == 'existing'
    assert beat['normalised_must_see'][0]['value'] == value
    before = deepcopy(beat)
    normalise_must_see(beat)
    assert beat == before


def test_minimal_match_is_usable_without_nice_details_and_nice_only_ranks():
    from test_question_first import close_reply
    beat = dict(id='b1', roles=[], must_see=dict(who_relation='一人说另一人听', action_class='说话',
        shot_need='脸可见', mouth_open_needed=True), nice_to_have=['柜台', '单据', '叹服'], equivalents=[])
    reply = close_reply(satisfies='must_see', verdict='partial', nice_to_have_score=0)
    reply['minimal_checks'] = {key: True for key in beat['must_see']}
    result = qfs.validate_reply(reply, dict(stage='close', beat=beat, window=(100, 110)))
    assert result['verdict'] == 'usable'
    prompt = qfs.close_prompt(beat, dict(beats=[beat], casting={}), {}, (100, 110))
    assert 'nice_to_have只用于排序' in prompt
    assert '某一种 equivalents' not in prompt


def test_partial_evidence_stays_searchable_but_does_not_hide_material_gap():
    candidate = dict(style='3d', verdict='partial')
    report = dict(beats=[dict(id='b', must_see=dict(action_class='说话'), candidates=[candidate])])
    assert qfs.material_gaps(report)[0]['reason'] == 'no_minimal_visual_candidate'
    candidate['verdict'] = 'usable'
    assert qfs.material_gaps(report) == []


def test_av_prompt_distinguishes_real_coherence_from_reference_form(tmp_path, monkeypatch):
    from omni_story.library import visual_audio as av
    original = received('bushaoxin_av_review.json')
    assert any('双人对话' in item for item in original['missing_core_meaning'])
    assert any('割裂' in item for item in original['missing_core_meaning'])
    monkeypatch.setattr(av, 'probe_media', lambda p: dict(duration_s=10))
    media = tmp_path / 'actual.mp4'; media.write_bytes(b'controlled media')
    captured = []
    def reviewer(path, folder, *, prompt):
        captured.append(prompt)
        return dict(http_status=200, parsed=original)
    av.review_final_video(media, tmp_path / 'review', dict(reference=dict(theme='守住规矩',
        intended_takeaway='人心比利润重要', strategy=dict(speech_mode='dialogue',
        adaptation_mode='reinterpret', timing_driver='speech')), segments=[]), av_reviewer=reviewer)
    prompt = captured[0]
    assert 'meaning_transfer_v1' in prompt and 'picture_audio_coherence' in prompt
    assert '形式差异本身不是missing_core_meaning' in prompt
    assert '不能把第三人称讲故事当成角色对白形式匹配' not in prompt
    assert '若仅靠人物轮流说话替代了' not in prompt
    # No rewriting historical verdicts to conceal legitimate coherence failures.
    assert av.read(tmp_path / 'review/review_v0.json') == original


def test_finecut_target_prompt_uses_same_meaning_transfer_contract(tmp_path, monkeypatch):
    from test_review_verdict_consistency import reviewer, verdict
    from omni_story.library import story_finecut as flow
    value = reviewer(story_context=True)
    value.knowledge, value.reference, value.observed = '', {}, {'story': []}
    value.inspections, value.base = [], tmp_path
    value.auth = dict(parent=dict(duration_s=50), target_duration_s=50)
    monkeypatch.setattr(value, 'blind_proxy', lambda result: dict(path='blind.mp4'))
    monkeypatch.setattr(flow.trial, 'proxy', lambda *a, **kw: dict(path='actual.mp4'))
    prompts = []
    def call(name, prompt, media, scope, validator, **kwargs):
        prompts.append(prompt)
        result = (dict(apparent_story='说话', status='partial', limitations=[], problems=[], facts=[])
                  if name == flow.PREFIX + 'blind' else verdict('deliver', []))
        validator(result)
        return result
    monkeypatch.setattr(value, 'call', call)
    value.review({}, dict(sha256='a'*64, rendered_path='actual.mp4', duration_s=50), 0)
    assert 'meaning_transfer_v1' in prompts[-1] and 'picture_audio_coherence' in prompts[-1]
    assert '若轮流说话替代了核心视觉表达' not in prompts[-1]


def test_plan_tables_intersect_content_observation_and_roles_without_mutation():
    from test_plan_role_event_feedback import windows
    observed = windows()[-1]
    source = dict(source_id=observed['source_id'], sha256=observed['source_sha256'], duration_s=1800,
                  content_range=dict(content_start_s=1720, content_end_s=1729))
    before = deepcopy(observed)
    table = contracts.plan_field_contract(windows=[observed], catalog=dict(sources=[source]))
    ranges = table['selected_range']['windows'][0]['allowed_source_ranges']
    assert ranges and all(1720 <= row['source_in_s'] < row['source_out_s'] <= 1729 for row in ranges)
    assert all(row['identity_confirmed'] is True for row in table['cast']['bindable_triples'])
    assert table['segment_roles']['windows'][0]['role_event_ranges']['A_asker']
    assert observed == before


@pytest.mark.parametrize('call', ['glm_168_plan_0_repair', 'glm_176_plan_0_repair2',
    'glm_171_plan_0_repair2', 'glm_167_plan_0_repair'])
def test_real_local_seconds_rejection_lists_content_and_observed_choices(call):
    from test_plan_role_event_feedback import windows
    record = next(row for row in received('protocol_failures.json') if row['call'] == call)
    plan = record['reply_excerpt']; segment = deepcopy(plan['segments'][0])
    window = windows()[-1]
    # Missing request/observations in these truncated logs: explicit controlled
    # context only. The offending selection, IDs and error code remain received.
    window.update(window_id=segment['window_id'], source_id=segment['source_id'])
    window['observation'].update(window_id=segment['window_id'], source_id=segment['source_id'])
    segment.update(role_ids=['A_asker'])
    source = dict(source_id=window['source_id'], sha256=window['source_sha256'], duration_s=6000,
                  content_range=dict(content_start_s=30, content_end_s=5000))
    p = dict(reference_sha256=plan['reference_sha256'], segments=[segment], audio_mode='silent',
             slots=[dict(slot_id=segment['slot_id'], intended_takeaway='controlled', segment_ids=[segment['segment_id']])],
             width=720, height=1280, fps=30, limitations=[])
    with pytest.raises(ValueError) as caught:
        contracts.validate_plan(p, dict(sources=[source]), [window], p['reference_sha256'], allow_unfocused=True)
    detail = caught.value.violation
    assert detail['expected']['allowed_source_ranges']
    assert detail['expected']['content_range_s'] == [30, 5000]
    assert detail['repair_hint']


def test_real_fine_foreign_namespace_is_repairable_and_producer_filters_context():
    record = next(row for row in received('protocol_failures.json') if row['error'].startswith('fine:'))
    fine = record['reply_excerpt']
    digest = fine['roles'][1]['entity_id'].split(':')[0]
    window = dict(window_id=fine['window_id'], source_id=fine['source_id'], source_sha256=digest,
                  source_start_s=210, source_end_s=216, status='watched')
    with pytest.raises(ValueError, match='fine:entity_source_namespace_required') as caught:
        contracts.validate_fine(deepcopy(fine), window)
    assert caught.value.violation['expected']['prefix'] == digest + ':'
    context = pipeline.fine_identity_context(window, [dict(window, observation=fine),
        dict(window, source_sha256='foreign', observation=dict(roles=[dict(role_id='foreign', entity_id='foreign:actor')]))])
    assert all(r['source_sha256'] == digest for r in context['already_watched_roles'])
    assert all(r['entity_id'].startswith(digest + ':') for r in context['already_watched_roles'])
    assert context['entity_id_contract']['prefix'] == digest + ':'


def test_real_voice_failure_gets_concrete_allowed_voice_and_keeps_guard():
    record = next(row for row in received('protocol_failures.json') if row['error'].endswith('dialogue_speaker_voice_changed'))
    # The evidence cuts off audio_draft; use the real speaker references and
    # segments, with controlled voices, rather than invent missing dialogue.
    plan = record['reply_excerpt']; segment = plan['segments'][0]
    speaker = dict(window_id=segment['window_id'], role_id=segment['role_ids'][0])
    draft = [dict(id=f'line_{i}', mode='dialogue', text='controlled', voice=voice,
        speaker=speaker, segment_ids=[segment['segment_id']]) for i, voice in enumerate(['Ethan', 'Cherry'])]
    with pytest.raises(ValueError, match='plan:dialogue_speaker_voice_changed') as caught:
        contracts.validate_dialogue_speakers(draft, [segment])
    assert caught.value.violation['expected']['allowed_voices'] == ['Ethan']
    feedback = protocol_feedback.diagnose('plan_0', dict(segments=[segment], audio_draft=draft),
        dict(stage='plan', windows=[], require_audio_draft=True, validation_error=caught.value))
    assert any(row['expected'].get('allowed_voices') == ['Ethan'] for row in feedback if isinstance(row['expected'], dict))


@pytest.mark.parametrize('coherence', ['contradictory', 'unverifiable'])
def test_explicit_coherence_failure_cannot_deliver_even_when_theme_matches(coherence):
    from omni_story.library.visual_audio import content_ready
    from test_review_verdict_consistency import reviewer, verdict, target_validator
    review = dict(theme_alignment='matches', speech_clear=True, repair_at='none', missing_core_meaning=[],
                  picture_audio_coherence=coherence)
    context = dict(reference=dict(strategy=dict(speech_mode='narration', adaptation_mode='reinterpret', timing_driver='speech')))
    assert not content_ready(review, context)
    result = dict(verdict('deliver', []), picture_audio_coherence=coherence)
    with pytest.raises(ValueError, match='picture_audio_coherence_unresolved') as caught:
        target_validator(reviewer(story_context=True))(result)
    assert caught.value.violation['repair_hint']


@pytest.mark.parametrize('guard', ['mouth', 'identity', 'style'])
def test_minimal_positive_answer_cannot_override_quality_guards(guard):
    from test_question_first import close_reply
    beat = dict(id='b1', roles=['owner'], must_see=dict(action_class='说话', mouth_open_needed=True), equivalents=[])
    anchor = dict(name='平先生', aliases=[], clip='anchor.mp4')
    data = close_reply(minimal_checks=dict(action_class=True, mouth_open_needed=True), nice_to_have_score=100)
    if guard == 'mouth': data['mouth_moving'] = False
    if guard == 'identity': data['same_character_as_anchor'] = False
    if guard == 'style': data['style'] = 'mixed'
    result = qfs.validate_reply(data, dict(stage='close', beat=beat, anchors=dict(owner=anchor), window=(100, 110)))
    if guard == 'style':
        report = dict(beats=[dict(beat, candidates=[result])], target_style='3d')
        assert qfs.material_gaps(report)
    else:
        assert result['verdict'] == 'partial'


def test_real_coherence_issue_and_unsupported_cast_are_still_rejected():
    from omni_story.library.visual_audio import content_ready
    from test_e2e_plan_bindings import received as plan_reply, observations
    review = received('bushaoxin_av_review.json')
    context = dict(reference=dict(strategy=dict(speech_mode='narration', adaptation_mode='reinterpret', timing_driver='speech')))
    assert not content_ready(review, context)
    with pytest.raises(ValueError, match='cast_entity_not_supported_by_observation'):
        contracts.validate_task_cast(plan_reply(0)['cast'], observations())


@pytest.mark.parametrize('evidence', sorted({r['evidence'] for r in received('protocol_failures.json')}))
def test_five_exhaustions_offer_copyable_triples_ranges_and_role_events(evidence):
    records = [row for row in received('protocol_failures.json') if row['evidence'] == evidence]
    plan = next(row['reply_excerpt'] for row in records if row['call'].endswith('plan_0'))
    windows = []
    # These logs do not contain the complete watched_windows/request table.
    # Reuse real selection IDs; the controlled observations test the mechanical
    # prompt/validator agreement, not the absent pixels or historical acceptance.
    for window_id in dict.fromkeys(row['window_id'] for row in plan['segments']):
        segments = [row for row in plan['segments'] if row['window_id'] == window_id]
        source_id = segments[0]['source_id']
        sha = source_id.removeprefix('src_').ljust(64, '0')
        low, high = min(row['source_in_s'] for row in segments), max(row['source_out_s'] for row in segments)
        role_ids = list(dict.fromkeys(role for row in segments for role in row['role_ids']))
        fine = dict(window_id=window_id, source_id=source_id,
            roles=[dict(role_id=role, identity_confirmed=True, entity_id=sha + ':controlled_' + str(i),
                state='controlled context', identity_evidence='controlled context') for i, role in enumerate(role_ids)],
            events=[dict(local_start_s=0, local_end_s=high-low, role_ids=role_ids, observed_fact='controlled context')],
            usable_ranges=[dict(local_in_s=0, local_out_s=high-low, role_ids=role_ids, event_indices=[0],
                camera='controlled context', continuity_notes='controlled context')], uncertainties=[])
        windows.append(dict(window_id=window_id, source_id=source_id, source_sha256=sha,
            source_start_s=low, source_end_s=high, status='watched', observation=fine))
    before = deepcopy((plan, windows))
    table = contracts.plan_field_contract(windows=windows)
    assert table['cast']['bindable_triples']
    for index, row in enumerate(table['cast']['bindable_triples']):
        assert row['identity_confirmed'] is True
        cast = [dict(cast_id='copy_' + str(index), entity_id=row['entity_id'],
            bindings=[dict(window_id=row['window_id'], role_id=row['role_id'])])]
        contracts.validate_task_cast(cast, windows)
    for row in table['selected_range']['windows']:
        window = next(w for w in windows if w['window_id'] == row['window_id'])
        for choice in row['allowed_source_ranges']:
            assert contracts.matching_plan_source_ranges(choice, row)
            contracts.validate_plan_segment_roles(choice, window, '$.segments[0]')
        assert table['segment_roles']['windows'][windows.index(window)]['role_event_ranges']
    assert (plan, windows) == before


def test_optional_details_rank_without_becoming_eligibility_conditions(tmp_path):
    from test_question_first import source_fixture, close_reply
    source = source_fixture(tmp_path, 400)
    candidates = [dict(close_reply(), source_id=source['source_id'], film=1,
        film_in_s=start, film_out_s=start+4, clip=str(start)+'.mp4', nice_to_have_score=score)
        for start, score in [(100, 0), (200, 99)]]
    report = dict(beats=[dict(id='b', function='意义', roles=[], must_see=dict(action_class='说话'),
        dub_can_carry=['主旨'], candidates=candidates)], cast_anchors={}, target_style='3d')
    chosen = qfs.to_search_result(report, dict(sources=[source]), max_windows=1)
    assert chosen['windows'][0]['start_s'] == 198
    assert chosen['question_first_gaps'] == []
    report['beats'][0]['candidates'] = candidates[:1]
    assert qfs.to_search_result(report, dict(sources=[source]), max_windows=1)['windows'][0]['start_s'] == 98


def test_invalid_optional_rank_metadata_cannot_discard_minimal_visual_evidence():
    from test_question_first import close_reply
    beat = dict(id='b', roles=[], must_see=dict(action_class='说话', mouth_open_needed=False), equivalents=[])
    result = qfs.validate_reply(close_reply(nice_to_have_score='high'),
        dict(stage='close', beat=beat, window=(100, 110)))
    assert result['verdict'] == 'usable'
    assert result['nice_to_have_score'] == 0 and result['normalised_nice_to_have_score'] == 'high'


def test_legacy_optional_equivalent_id_cannot_veto_usable_shot():
    from test_question_first import close_reply
    beat = dict(id='b', roles=[], must_see=dict(action_class='说话', mouth_open_needed=False), equivalents=['一个可选实例'])
    result = qfs.validate_reply(close_reply(satisfies='equivalent', equivalent_id='E99'),
        dict(stage='close', beat=beat, window=(100, 110)))
    assert result['verdict'] == 'usable'
    assert result['normalised_equivalent_id'] == 'E99' and result['satisfies_unmatched']
