"""Structural rejection tests; no model requests or real-movie renders."""
from copy import deepcopy
import json

import pytest

from omni_story.library.slot_finecut_contracts import (
    neutral_facts_prompt, proposal_prompt, slots_prompt,
    validate_facts, validate_parent_navigation, validate_proposal, validate_slots,
)


@pytest.fixture
def inputs():
    parent = {"baseline_id": "synthetic_parent", "sha256": "a" * 64, "duration_s": 10,
              "provenance": [{"source_id": "film", "source_sha256": "b" * 64,
                              "window_id": "w1", "source_in_s": 102, "source_out_s": 107,
                              "output_in_s": 0, "output_out_s": 5, "role_ids": ["A"]},
                             {"source_id": "film", "source_sha256": "b" * 64,
                              "window_id": "w1", "source_in_s": 107, "source_out_s": 112,
                              "output_in_s": 5, "output_out_s": 10, "role_ids": ["A"]}]}
    slots = {"baseline_id": parent["baseline_id"], "parent_sha256": parent["sha256"],
             "slots": [{"slot_id": "s1", "start_s": 0, "end_s": 5,
                        "intended_takeaway": "The character changes state.",
                        "entry_state": "Initial state", "exit_state": "Changed state",
                        "link_to_previous": "Opening", "link_to_next": "Changed state leads on"},
                       {"slot_id": "s2", "start_s": 5, "end_s": 10,
                        "intended_takeaway": "The change has a visible result.",
                        "entry_state": "Changed state", "exit_state": "Visible result",
                        "link_to_previous": "Shows the previous change", "link_to_next": "Ending"}],
             "limitations": [], "uncertainties": []}
    facts = {"baseline_id": parent["baseline_id"], "parent_sha256": parent["sha256"],
             "slot_id": "s1", "slot_start_s": 0, "slot_end_s": 5, "time_domain": "slot_local_output",
             "evidence": [{"evidence_id": "e1", "start_s": 1, "end_s": 3,
                           "observed_fact": "A figure raises an arm.", "kind": "action", "basis": "visual"}],
             "limitations": [], "uncertainties": []}
    windows = [{"window_id": "w1", "source_id": "film", "source_sha256": "b" * 64,
                "source_start_s": 100, "source_end_s": 120,
                "observation": {"usable_ranges": [{"local_in_s": 0, "local_out_s": 10, "role_ids": ["A"]},
                                                   {"local_in_s": 10, "local_out_s": 20, "role_ids": ["B"]}]}}]
    proposal = {"baseline_id": parent["baseline_id"], "parent_sha256": parent["sha256"], "slot_id": "s1",
                "candidates": [{"candidate_id": "c1", "meaning_status": "preserved",
                                "rationale": "Keep the visible state change.", "limitations": [],
                                "operations": [{"parent_segment_index": 0, "source_in_s": 102, "source_out_s": 106,
                                                "speed": 1, "freeze_tail_s": 0, "evidence_ids": ["e1"],
                                                "reason": "Retain the observable arm change.",
                                                "essential_intervals": [{"source_start_s": 102, "source_end_s": 106,
                                                                         "min_readable_s": 2, "information": "Arm position",
                                                                         "evidence_ids": ["e1"],
                                                                         "continues_in_tail_frame": False}]}]}]}
    return parent, slots, facts, windows, proposal


def test_valid_contracts_are_pure_and_do_not_mark_quality_pass(inputs):
    parent, slots, facts, windows, proposal = inputs
    original = deepcopy(inputs)
    assert validate_slots(slots, parent) is slots
    assert validate_facts(facts, parent, slots["slots"][0]) is facts
    assert validate_proposal(proposal, parent, slots["slots"][0], facts, windows) is proposal
    assert inputs == original
    assert "quality_status" not in proposal


@pytest.mark.parametrize("change", ["gap", "overlap", "uncovered_tail", "duplicate_id", "zero_duration", "sha"])
def test_slot_coverage_and_binding_rejections(inputs, change):
    parent, slots, *_ = inputs
    if change in {"gap", "overlap"}:
        slots["slots"][1]["start_s"] += .1 if change == "gap" else -.1
    elif change == "uncovered_tail":
        slots["slots"][1]["end_s"] -= .1
    elif change == "duplicate_id":
        slots["slots"][1]["slot_id"] = "s1"
    elif change == "zero_duration":
        slots["slots"][0]["end_s"] = 0
    else:
        slots["parent_sha256"] = "c" * 64
    with pytest.raises(ValueError):
        validate_slots(slots, parent)


@pytest.mark.parametrize("change", ["source_time", "outside", "global_output", "basis", "plot"])
def test_neutral_facts_do_not_mix_clocks_or_story_answers(inputs, change):
    parent, slots, facts, *_ = inputs
    if change == "source_time":
        facts["evidence"][0]["source_in_s"] = 103
    elif change == "outside":
        facts["evidence"][0]["end_s"] = 5.1
    elif change == "global_output":
        facts["time_domain"] = "parent_output"
    elif change == "basis":
        del facts["evidence"][0]["basis"]
    else:
        facts["intended_takeaway"] = "Goal answer"
    with pytest.raises(ValueError):
        validate_facts(facts, parent, slots["slots"][0])


def test_slot_refinement_can_extend_source_and_output_duration(inputs):
    parent, slots, facts, windows, proposal = inputs
    operation = proposal["candidates"][0]["operations"][0]
    operation.update(source_in_s=100, source_out_s=109, speed=.5)
    assert (109 - 100) / .5 > slots["slots"][0]["end_s"]
    assert validate_proposal(proposal, parent, slots["slots"][0], facts, windows) is proposal


@pytest.mark.parametrize("change", ["cross_usable", "role_pool", "missing_roles", "wrong_sha", "other_slot", "four_candidates", "unknown_evidence", "speed", "hold"])
def test_proposals_reject_incompatible_sources_and_transforms(inputs, change):
    parent, slots, facts, windows, proposal = inputs
    operation = proposal["candidates"][0]["operations"][0]
    if change == "cross_usable":
        operation.update(source_in_s=109, source_out_s=111)
    elif change == "role_pool":
        operation.update(source_in_s=112, source_out_s=115)
    elif change == "missing_roles":
        del parent["provenance"][0]["role_ids"]
    elif change == "wrong_sha":
        windows[0]["source_sha256"] = "c" * 64
    elif change == "other_slot":
        operation["parent_segment_index"] = 1
    elif change == "four_candidates":
        proposal["candidates"] = [deepcopy(proposal["candidates"][0]) for _ in range(4)]
        for i, candidate in enumerate(proposal["candidates"]):
            candidate["candidate_id"] = "c" + str(i)
    elif change == "unknown_evidence":
        operation["evidence_ids"] = ["missing"]
    elif change == "speed":
        operation["speed"] = 3
    else:
        operation["freeze_tail_s"] = 11
    with pytest.raises(ValueError):
        validate_proposal(proposal, parent, slots["slots"][0], facts, windows)


def test_each_essential_interval_needs_its_own_exposure(inputs):
    parent, slots, facts, windows, proposal = inputs
    essential = proposal["candidates"][0]["operations"][0]["essential_intervals"][0]
    essential.update(source_start_s=102, source_end_s=103, min_readable_s=2)
    with pytest.raises(ValueError, match="declared_readability_shortfall"):
        validate_proposal(proposal, parent, slots["slots"][0], facts, windows)
    # A separately bound proposal may remain a fallible navigation proposal;
    # the returned model estimate and status are never rewritten into a pass.
    raw = deepcopy(proposal)
    validate_proposal(proposal, parent, slots['slots'][0], facts, windows, enforce_readability=False)
    assert proposal == raw
    # A real unresolved proposal is retained as unresolved rather than a pass.
    proposal["candidates"][0].update(meaning_status="unresolved", limitations=["Exposure is insufficient."])
    validate_proposal(proposal, parent, slots["slots"][0], facts, windows)


def test_hold_only_exposes_information_continuing_to_tail(inputs):
    parent, slots, facts, windows, proposal = inputs
    operation = proposal["candidates"][0]["operations"][0]
    operation["freeze_tail_s"] = 2
    essential = operation["essential_intervals"][0]
    essential.update(source_start_s=102, source_end_s=103, min_readable_s=2, continues_in_tail_frame=True)
    with pytest.raises(ValueError, match="hold_cannot_expose_earlier_information"):
        validate_proposal(proposal, parent, slots["slots"][0], facts, windows)
    essential.update(source_start_s=105, source_end_s=106)
    validate_proposal(proposal, parent, slots["slots"][0], facts, windows)


def test_prompts_hide_intended_story_from_neutral_reader(inputs):
    parent, slots, facts, windows, _ = inputs
    slot = slots["slots"][0]
    prompt = neutral_facts_prompt(parent, slot)
    assert slot["intended_takeaway"] not in prompt
    assert "source_in_s" not in prompt
    payload = json.loads(prompt.rsplit("\n", 1)[1])
    assert payload["response_contract"]["time_domain"] == "slot_local_output"
    assert "previously_recorded_reference" in slots_prompt(parent, {"unknowns": ["speed"]})
    creative = proposal_prompt(parent, slot, facts, windows)
    assert slot["intended_takeaway"] in creative
    assert "0.5" in creative and "continues_in_tail_frame" in creative


def test_forward_prompts_distinguish_input_wrapper_from_result(inputs):
    parent, slots, facts, windows, _ = inputs
    for prompt in (slots_prompt(parent, {}), neutral_facts_prompt(parent, slots['slots'][0]),
                   proposal_prompt(parent, slots['slots'][0], facts, windows)):
        assert '最终JSON的最外层必须直接是response_contract列出的字段' in prompt
        assert '不得复制整个输入JSON' in prompt
    assert '两者人物和事件不同是预期' in slots_prompt(parent, {})


def test_inferred_kind_remains_nonvisual_evidence(inputs):
    parent, slots, facts, _, _ = inputs
    facts['evidence'][0].update(kind='inference', basis='visible_text')
    validate_facts(facts, parent, slots['slots'][0])
    facts['evidence'][0]['basis'] = 'inference'
    validate_facts(facts, parent, slots['slots'][0])
    facts['evidence'][0]['basis'] = 'visual'
    with pytest.raises(ValueError, match='inference_kind_cannot_be_visual_fact'):
        validate_facts(facts, parent, slots['slots'][0])


def _point_navigation(parent, slot, report):
    report['evidence'][0]['end_s'] = report['evidence'][0]['start_s']
    return {'schema_version': 'sf_parent_point_navigation_v1',
            'request_binding': {'baseline_id': parent['baseline_id'], 'parent_sha256': parent['sha256'],
                'slot_id': slot['slot_id'], 'parent_slot_start_s': slot['start_s'],
                'parent_slot_end_s': slot['end_s'], 'local_start_s': 0,
                'local_end_s': slot['end_s'] - slot['start_s'], 'time_domain': 'slot_local_output'},
            'model_report': report,
            'temporal_support': [{'evidence_id': 'e1', 'start_s': 1, 'end_s': 1,
                'temporal_kind': 'point', 'instant_only': True, 'duration_unknown': True,
                'exposure_proof': False, 'cannot_prove_completed_action': True}]}


def test_parent_point_navigation_does_not_become_an_interval_fact(inputs):
    parent, slots, facts, windows, proposal = inputs
    slot = slots['slots'][0]
    navigation = _point_navigation(parent, slot, facts)
    before = deepcopy(navigation)
    with pytest.raises(ValueError, match='outside_bound_interval'):
        validate_facts(facts, parent, slot)
    assert validate_parent_navigation(navigation, parent, slot) is navigation
    # A proposal may use a point as a search cue. It still contains independent
    # source intervals; this does not set any source or output quality pass.
    assert validate_proposal(proposal, parent, slot, facts, windows, navigation=navigation) is proposal
    assert navigation == before
    assert 'quality_status' not in navigation


@pytest.mark.parametrize('change', ['invent_duration', 'exposure', 'completed_action', 'parent_bounds', 'clock'])
def test_point_navigation_rejects_duration_creation_and_binding_changes(inputs, change):
    parent, slots, facts, windows, proposal = inputs
    slot = slots['slots'][0]
    navigation = _point_navigation(parent, slot, facts)
    if change == 'invent_duration':
        navigation['temporal_support'][0]['end_s'] = 2
    elif change == 'exposure':
        navigation['temporal_support'][0]['exposure_proof'] = True
    elif change == 'completed_action':
        navigation['temporal_support'][0]['cannot_prove_completed_action'] = False
    elif change == 'parent_bounds':
        navigation['request_binding']['parent_slot_end_s'] += 1
    else:
        navigation['request_binding']['time_domain'] = 'source_global'
    with pytest.raises(ValueError):
        validate_proposal(proposal, parent, slot, facts, windows, navigation=navigation)
