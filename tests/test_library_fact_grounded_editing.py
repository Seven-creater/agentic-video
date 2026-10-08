"""Synthetic GLM replies and real FFmpeg; no real run or quality claim."""
from copy import deepcopy
import json
from pathlib import Path
from threading import Barrier, Lock
import shutil

import pytest

from test_library_slot_finecut_execute import prepared
from omni_story.library import fact_grounded_editing as fg, fact_grounded_edit_plan as planning
from omni_story.library import scoped_edit_evidence as scoped, semantic_audit as audit
from omni_story.library.media import prepare_window, probe_media, sha256_file
from omni_story.library.state import json_sha, write_json


pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
                               reason="Synthetic integration requires FFmpeg and FFprobe")


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def payload(prompt, marker):
    return json.JSONDecoder().raw_decode(prompt[prompt.index(marker):])[0]


def observed(segment, source, proxy):
    return {"protocol": audit.SEMANTIC_PROTOCOL, "segment_id": segment["segment_id"],
        "source_id": source["source_id"], "source_sha256": source["sha256"],
        "source_in_s": segment["source_in_s"], "source_out_s": segment["source_out_s"],
        "proxy_sha256": proxy["sha256"], "observed_duration_s": proxy["duration_s"],
        "characters": [{"character_id": "shape", "appearance": "Colored synthetic geometric figure."}],
        "evidence": [{"evidence_id": "visible", "kind": "visual_action", "local_start_s": 0,
            "local_end_s": proxy["duration_s"], "description": "The synthetic figure remains visible.",
            "character_ids": ["shape"], "basis_evidence_ids": []}], "uncertainties": []}


def append_observation(output, source, segment, *, parsed=True, status="received"):
    proxy = prepare_window(source, segment["source_in_s"], segment["source_out_s"], output/"media_cache", fps=30)
    observation = observed(segment, source, proxy)
    request = {"observation_scope": {k: proxy[k] for k in
        ("kind", "source_sha256", "source_start_s", "source_end_s")}}
    response = {"status": "complete", "result": {"content": [
        {"type": "text", "text": json.dumps(observation, ensure_ascii=False)}]}}
    state = read(output/"library_state.json")
    call_id = "historical_" + str(len(state["calls"]))
    folder = output/"calls"/call_id
    write_json(folder/"request.json", request)
    write_json(folder/"response.json", response)
    if parsed:
        write_json(folder/"parsed.json", observation)
    else:
        write_json(folder/"protocol_failure.json", {"error": "Known synthetic format failure."})
    state["calls"].append({"id": call_id, "name": "semantic_slice_" + call_id, "status": status,
        "request_sha256": json_sha(request), "response_sha256": json_sha(response)})
    write_json(output/"library_state.json", state)
    return folder, observation


def test_received_facts_reuses_only_bound_received_parsed_source_and_keeps_history(prepared):
    output, _, _, protected = prepared
    catalog = read(output/"catalog/inventory.json")
    source = catalog["sources"][0]
    segment = {"segment_id": "old_local_id", "source_in_s": .2, "source_out_s": 1.8}
    folder, observation = append_observation(output, source, segment)
    failed, _ = append_observation(output, source, {**segment, "segment_id": "failed"}, parsed=False)
    unknown, _ = append_observation(output, source, {**segment, "segment_id": "unknown"}, status="uncertain")
    before = {p: p.read_bytes() for directory in (folder, failed, unknown) for p in directory.glob("*.json")}
    cached = fg.received_facts(output, catalog)
    assert len(cached) == 1 and cached[0]["observation"] == observation
    assert cached[0]["origin_parsed_file_sha256"] == sha256_file(folder/"parsed.json")
    assert all(p.read_bytes() == content for p, content in before.items())
    assert all(p.read_bytes() == content for p, content in protected.items())
    # Request and response caches must stay tied to their original ledger.
    write_json(folder/"request.json", {"changed": True})
    with pytest.raises(ValueError, match="received_cache_changed"):
        fg.received_facts(output, catalog)


def test_received_parsed_fact_cannot_diverge_from_known_raw_response(prepared):
    output = prepared[0]
    catalog = read(output/"catalog/inventory.json")
    folder, observation = append_observation(output, catalog["sources"][0],
        {"segment_id": "historic", "source_in_s": .2, "source_out_s": 1.8})
    changed = deepcopy(observation)
    changed["evidence"][0]["description"] = "A different valid-looking synthetic observation."
    write_json(folder/"parsed.json", changed)
    with pytest.raises(ValueError, match="parsed_fact_not_original_model_body"):
        fg.received_facts(output, catalog)


@pytest.fixture
def task(prepared):
    output, preparation, methods, protected = prepared
    old, execution = output/"previous_finecuts", output/"forward_finecuts"
    for parent in preparation["parents"]:
        plan = parent["original_plan"]
        outline = {"slots": []}
        proposals, selections = [], []
        for segment, original in zip(plan["segments"], parent["provenance"], strict=True):
            slot = next(s for s in plan["slots"] if s["slot_id"] == segment["slot_id"])
            outline["slots"].append({**deepcopy(slot), "start_s": original["output_in_s"],
                                      "end_s": original["output_out_s"]})
            proposals.append({"slot_id": slot["slot_id"], "candidates": [{"candidate_id": "c1",
                "operations": [{"essential_intervals": [{"information": "Synthetic figure is visible.",
                    "min_readable_s": .1}]}]}]})
            selections.append({"slot_id": slot["slot_id"], "candidate_id": "c1"})
        folder = old/parent["baseline_id"]
        write_json(folder/"outline.json", outline)
        write_json(folder/"proposals.json", proposals)
        prior = {"plan": plan, "selections": selections}
        write_json(folder/"assembly.json", prior)
        write_json(folder/"assembly_before_source_feedback.json", prior)
    return {"output": output, "preparation": preparation, "methods": methods, "protected": protected,
        "old": old, "execution": execution}


def reconstruction(task, parent):
    original = parent["original_plan"]
    plan = deepcopy(original)
    plan["segments"], plan["slots"] = [], []
    for index, before in enumerate(original["segments"]):
        sid = f"forward_{index}"
        a, b = before["source_in_s"]+.2, before["source_out_s"]-.2
        speed, hold = (2, 0) if index == 0 else (.5, .1)
        plan["segments"].append({**before, "segment_id": sid, "source_in_s": a, "source_out_s": b,
            "speed": speed, "freeze_tail_s": hold,
            "source_claims": [{"claim_id": f"source_{index}", "kind": "visual_state",
                "description": "The synthetic figure is visible."}],
            "output_operation_claims": [{"claim_id": f"speed_{index}", "kind": "speed",
                "description": "The synthetic motion has the selected playback speed.", "expected_value": speed}]
                + ([{"claim_id": f"hold_{index}", "kind": "tail_hold",
                     "description": "The real last frame is held.", "expected_value": hold}] if hold else [])})
        plan["segments"][-1].pop("visual_claims", None)
        slot = next(s for s in original["slots"] if s["slot_id"] == before["slot_id"])
        plan["slots"].append({**slot, "segment_ids": [sid]})
    plan["editing_bindings"][0]["segment_ids"] = [s["segment_id"] for s in plan["segments"]]
    old = task["old"]/parent["baseline_id"]
    groups = planning.canonical_information_groups(read(old/"outline.json"), read(old/"proposals.json"),
                                                   prior_assembly=read(old/"assembly.json"))
    for group, segment in zip(groups, plan["segments"], strict=True):
        group["exposures"] = [{"segment_id": segment["segment_id"],
            "source_start_s": segment["source_in_s"], "source_end_s": segment["source_out_s"],
            "continues_in_tail_frame": False, "source_claim_ids": [segment["source_claims"][0]["claim_id"]]}]
    return {"status": "planned", "baseline_id": parent["baseline_id"], "parent_sha256": parent["sha256"],
        "plan": plan, "essential_groups": groups, "transition_checks": [{
            "from_segment_id": plan["segments"][0]["segment_id"], "to_segment_id": plan["segments"][1]["segment_id"],
            "relation": "Synthetic visible geometry continues.", "status": "planned"}],
        "limitations": ["Synthetic fixture is not a model quality experiment."], "reason": "Synthetic substantive retiming."}


class Backend:
    def __init__(self, task):
        self.task, self.lock = task, Lock()
        self.data = {"calls": []}
        self.renders, self.results, self.jobs = [], {}, []
        self.barrier = Barrier(2)
        self.mode = "success"


class FakeState:
    def __init__(self, backend):
        self.backend = backend
        self.output = backend.task["output"]
        self.data = backend.data
        self.authorization = {"preparation_path": "synthetic_preparation",
            "reference_methods_path": str(self.output/"editing_reference_v2.json"),
            "knowledge_path": str(self.output/"synthetic_handbook.txt"),
            "old_execution_directory": str(backend.task["old"]),
            "execution_directory": str(backend.task["execution"]),
            "exhausted_source_inputs": [], "unknown_inputs": []}
        Path(self.authorization["knowledge_path"]).write_text("Generic test knowledge.", encoding="utf-8")

    def _reload(self):
        pass

    def assert_protected(self):
        assert all(path.read_bytes() == content for path, content in self.backend.task["protected"].items())

    def assert_source_inputs(self, plan, catalog):
        assert catalog["sources"] and plan["segments"]
        self.assert_protected()

    def claim_render(self, round_no, plan, manifest):
        assert not manifest["blockers"]
        with self.backend.lock:
            assert round_no not in self.backend.renders
            self.backend.renders.append(round_no)
        return self.backend.task["execution"]/f"render_{round_no}"/"render"

    def finish(self, round_no, values):
        result = {**deepcopy(values), "baseline_id": f"render_{round_no}"}
        with self.backend.lock:
            if round_no in self.backend.results:
                assert self.backend.results[round_no] == result
            else:
                self.backend.results[round_no] = result
                write_json(self.backend.task["execution"]/f"render_{round_no}"/"result.json", result)
        return self.backend.task["execution"]/f"render_{round_no}"/"result.json"


class FakeGLM:
    def __init__(self, state):
        self.state, self.backend = state, state.backend

    def call(self, name, prompt, media, validator, **kwargs):
        assert Path(media).is_file()
        with self.backend.lock:
            self.backend.jobs.append(name)
        if name.endswith("_reconstruct"):
            # Both parents must enter the model planning stage before either
            # proceeds: serial execution would time out this synthetic barrier.
            self.backend.barrier.wait(timeout=20)
        attempts = 2 if self.backend.mode == "protocol_failure" and name.endswith("_reconstruct") else 1
        for attempt in range(attempts):
            value = self.answer(name, prompt, media)
            if attempts == 2:
                value.pop("plan")
            actual_name = name + ("_repair" if attempt else "")
            with self.backend.lock:
                call_id = "synthetic_" + str(len(self.state.data["calls"]))
                folder = self.state.output/"calls"/call_id
                request = {"arguments": {"prompt": prompt}, "synthetic_media": str(media), **kwargs}
                response = {"result": {"content": [{"type": "text", "text": json.dumps(value)}]}}
                write_json(folder/"request.json", request)
                write_json(folder/"response.json", response)
                self.state.data["calls"].append({"id": call_id, "name": actual_name, "status": "received",
                    "request_sha256": json_sha(request), "response_sha256": json_sha(response)})
            try:
                validator(value)
            except (ValueError, KeyError) as error:
                write_json(folder/"protocol_failure.json", {"error": str(error), "attempt": attempt})
                if attempt == attempts-1:
                    raise ValueError("model_protocol_repair_exhausted:" + name) from error
            else:
                write_json(folder/"parsed.json", value)
                return deepcopy(value)

    def answer(self, name, prompt, media):
        round_no = int(name.split("_")[1])
        parent = next(p for p in self.backend.task["preparation"]["parents"] if p["round"] == round_no)
        if name.endswith("_reconstruct"):
            return reconstruction(self.backend.task, parent)
        if "_slice_" in name:
            value = payload(prompt, '{"protocol":')
            value["characters"] = [{"character_id": "shape", "appearance": "Synthetic colored geometric figure."}]
            value["evidence"] = [{"evidence_id": "visible", "kind": "visual_action", "local_start_s": 0,
                "local_end_s": value["observed_duration_s"], "description": "Synthetic geometry remains visible.",
                "character_ids": ["shape"], "basis_evidence_ids": []}]
            return value
        if name.endswith("_compare"):
            context = payload(prompt, '{"immutable_observations":')
            records = []
            for envelope in context["immutable_observations"]:
                sid = envelope["segment_id"]
                checks = []
                for claim in context["contracts"][sid]["required_claims"]:
                    basis = {"explanation": "The independently recorded synthetic figure matches this state."}
                    if claim["kind"] == "role_presence":
                        relation = "appearance_matches_context"
                        basis.update(role_id=claim["role_id"], character_ids=["shape"])
                    else:
                        relation = "observed_state"
                    blocked = self.backend.mode == "source_blocked" and claim["kind"] == "visual_state"
                    checks.append({"claim_id": claim["claim_id"], "status": "partial" if blocked else "supported",
                        "evidence_ids": ["visible"], "semantic_relation": "insufficient_scope" if blocked else relation,
                        "basis": basis, "reason": "Synthetic source judgment.",
                        "limitations": ["Synthetic missing support."] if blocked else []})
                records.append({"segment_id": sid, "observation_sha256": envelope["observation_sha256"],
                    "claim_checks": checks, "uncertainties": []})
            return {"protocol": scoped.PROTOCOL, "plan_sha256": context["response_contract"]["plan_sha256"],
                    "segments": records, "limitations": []}
        if name.endswith("_blind"):
            assert "OLD_CREATIVE_ANSWER" not in prompt
            value = json.JSONDecoder().raw_decode(prompt.split("绑定：", 1)[1])[0]
            duration = probe_media(media)["duration_s"]
            return {**value, "observed_story": "Synthetic geometry is visible.", "apparent_theme": "Synthetic continuity.",
                "main_characters": ["Geometric figure"], "text_dependency": "none",
                "confusions": ["Synthetic unexplained transition."] if self.backend.mode == "blind_confusions" else [],
                "evidence": [{"evidence_id": "output_visible", "claim_id": "blind_fact", "kind": "visual_action",
                    "start_s": 0, "end_s": duration, "observed_fact": "The synthetic geometry remains visible.",
                    "basis_evidence_ids": []}]}
        if name.endswith("_economy"):
            assert "OLD_CREATIVE_ANSWER" not in prompt
            context = payload(prompt, '{"actual_output":')
            actual = context["actual_output"]
            return {"video_sha256": actual["sha256"], "economy_status": "pass", "narrative_readability": "pass",
                "segment_checks": [{"segment_id": segment["segment_id"], "status": "necessary",
                    "reason": "Synthetic output fixture.", "output_evidence": [{
                        "start_s": segment["output_in_s"], "end_s": segment["output_out_s"],
                        "observed_fact": "Synthetic geometry is visible."}]} for segment in actual["provenance"]],
                "limitations": ["Synthetic judgments are not real model quality evidence."]}
        if name.endswith("_review"):
            context = payload(prompt, '{"reference":')
            value = deepcopy(context["response_contract"])
            value.update(theme_status="pass", editing_status="pass", continuity_status="pass", contradictions=[],
                claim_checks=[{"claim_id": claim["claim_id"], "status": "supported",
                    "evidence_ids": ["output_visible"], "reason": "Synthetic actual-output fixture.", "limitations": [],
                    **({"executed_value": claim["expected_value"]}
                       if claim["kind"] in {"output_speed", "output_tail_hold"} else {})}
                    for claim in context["required_claims"]],
                limitations=["Synthetic replies are not a GLM quality pass."])
            return value
        raise AssertionError("Unexpected stage " + name)


def execute_synthetic(monkeypatch, task, mode):
    from omni_story.library import forward_slot_budget
    backend = Backend(task)
    backend.mode = mode
    monkeypatch.setattr(forward_slot_budget, "ForwardSlotState", lambda output: FakeState(backend))
    monkeypatch.setattr(fg, "load_preparation", lambda path: deepcopy(task["preparation"]))
    monkeypatch.setattr(fg, "CodexMCP", FakeGLM)
    return backend, fg.execute(task["output"])


def test_two_routes_parallel_real_ffmpeg_and_zero_call_render_resume(task, monkeypatch):
    backend, result = execute_synthetic(monkeypatch, task, "success")
    assert backend.renders == [0, 3] or backend.renders == [3, 0]
    assert len(result["results"]) == 2
    for candidate in result["results"]:
        assert Path(candidate["final_video"]).is_file()
        assert sha256_file(candidate["final_video"]) == candidate["final_sha256"]
        assert candidate["status"] == "model_checked_candidate"
        assert candidate["model_quality_gate_passed"] is True
    calls = len(backend.data["calls"])
    monkeypatch.setattr(fg, "CodexMCP", lambda state: pytest.fail("Completed routes must resume without model calls"))
    resumed = fg.execute(task["output"])
    assert resumed == result and len(backend.data["calls"]) == calls


def test_two_routes_source_blocked_stop_before_render_without_automatic_replan(task, monkeypatch):
    backend, result = execute_synthetic(monkeypatch, task, "source_blocked")
    assert not backend.renders
    assert all(candidate["status"] == "stopped_source_evidence" for candidate in result["results"])
    assert all(not candidate["model_quality_gate_passed"] for candidate in result["results"])
    assert all(not name.endswith(("_blind", "_economy", "_review")) for name in backend.jobs)
    assert len([name for name in backend.jobs if name.endswith("_reconstruct")]) == 2


def test_known_original_and_sole_repair_failure_stop_without_render_or_loop(task, monkeypatch):
    backend, result = execute_synthetic(monkeypatch, task, "protocol_failure")
    assert len(backend.data["calls"]) == 4 and not backend.renders
    assert all(candidate["status"] == "stopped_protocol_failure" for candidate in result["results"])
    assert all((task["output"]/"calls"/c["id"]/"protocol_failure.json").is_file() for c in backend.data["calls"])
    assert not any((task["output"]/"calls"/c["id"]/"parsed.json").exists() for c in backend.data["calls"])


def test_old_source_body_reused_for_new_target_id_only_new_geometry_calls_model(task):
    parent = task["preparation"]["parents"][0]
    catalog = read(task["output"]/"catalog/inventory.json")
    plan = reconstruction(task, parent)["plan"]
    first = plan["segments"][0]
    folder, original_observation = append_observation(task["output"], catalog["sources"][0],
        {**first, "segment_id": "historical_local_id"})
    cached = fg.received_facts(task["output"], catalog)
    old_bytes = {path: path.read_bytes() for path in folder.glob("*.json")}
    backend = Backend(task)
    state, glm = FakeState(backend), None
    glm = FakeGLM(state)
    facts, bindings, envelopes = fg.observe_sources(glm, state, parent, plan, catalog, cached,
                                                   task["execution"]/"cache_binding_test")
    assert facts[0] == original_observation and facts[0]["segment_id"] == "historical_local_id"
    assert bindings[0]["segment_id"] == first["segment_id"]
    assert bindings[0]["observation_sha256"] == json_sha(original_observation)
    assert envelopes[0]["segment_id"] == first["segment_id"]
    assert envelopes[0]["observation"] == original_observation
    assert len(backend.jobs) == 1 and "_slice_" in backend.jobs[0]
    assert facts[1]["segment_id"] == plan["segments"][1]["segment_id"]
    assert all(path.read_bytes() == content for path, content in old_bytes.items())


def test_actual_blind_confusions_prevent_joint_quality_pass_despite_target_pass(task, monkeypatch):
    backend, result = execute_synthetic(monkeypatch, task, "blind_confusions")
    assert len(backend.renders) == 2
    assert all(candidate["status"] == "candidate_with_limitations" for candidate in result["results"])
    assert all(not candidate["model_quality_gate_passed"] for candidate in result["results"])


def exposure_case(*, minimum=6, fact_end=4, evidence_kind="visual_action"):
    plan = {"fps": 30, "segments": [{"segment_id": "s", "source_in_s": 0, "source_out_s": 4,
                                    "speed": 1, "freeze_tail_s": 2}]}
    exposure = {"segment_id": "s", "source_start_s": 0, "source_end_s": 4,
                "continues_in_tail_frame": True, "source_claim_ids": ["core"]}
    reconstruction = {"plan": plan, "essential_groups": [{"group_id": "g", "min_readable_s": minimum,
        "exposures": [deepcopy(exposure), deepcopy(exposure)]}]}
    observations = [{"segment_id": "s", "observation": {"source_in_s": 0, "source_out_s": 4,
        "evidence": [{"evidence_id": "direct", "kind": evidence_kind,
                      "local_start_s": 0, "local_end_s": fact_end}]}}]
    comparison = {"segments": [{"segment_id": "s", "claim_checks": [{"claim_id": "core",
        "status": "supported", "evidence_ids": ["direct"], "reason": "Synthetic check."}]}]}
    return reconstruction, observations, comparison


def test_exposure_union_does_not_double_count_same_information_or_overlapping_facts():
    case = exposure_case(minimum=7)
    blockers = fg.evidence_blockers(*case)
    assert len(blockers) == 1 and blockers[0]["recorded_exposure_s"] == 6
    assert not fg.evidence_blockers(*exposure_case(minimum=6))


def test_tail_hold_only_extends_information_supported_through_the_real_last_frame():
    blockers = fg.evidence_blockers(*exposure_case(minimum=4, fact_end=3))
    assert len(blockers) == 1 and blockers[0]["recorded_exposure_s"] == 3


@pytest.mark.parametrize("kind", ["visible_text", "inference"])
def test_text_and_inference_do_not_gain_visual_information_exposure(kind):
    blockers = fg.evidence_blockers(*exposure_case(minimum=1, evidence_kind=kind))
    assert blockers[0]["recorded_exposure_s"] == 0


def test_every_claim_needs_support_even_when_other_visual_facts_cover_full_exposure():
    case = exposure_case()
    case[-1]["segments"][0]["claim_checks"].append({"claim_id": "unresolved",
        "status": "unverifiable", "evidence_ids": [], "reason": "Scope is unknown."})
    blockers = fg.evidence_blockers(*case)
    assert blockers[0]["kind"] == "missing_source_support"
    assert blockers[0]["claim_id"] == "unresolved"


def test_one_information_group_cannot_borrow_unmapped_other_claim_exposure():
    case = exposure_case(minimum=3, fact_end=1)
    case[1][0]["observation"]["evidence"].append({"evidence_id": "other_visible",
        "kind": "visual_action", "local_start_s": 0, "local_end_s": 4})
    case[-1]["segments"][0]["claim_checks"].append({"claim_id": "other_claim",
        "status": "supported", "evidence_ids": ["other_visible"], "reason": "Different information is visible."})
    blockers = fg.evidence_blockers(*case)
    assert blockers[0]["recorded_exposure_s"] == 1


@pytest.fixture
def review_case():
    plan = {"fps": 30, "slots": [{"slot_id": "original_slot", "intended_takeaway": "An observable state is established."}],
        "segments": [{"segment_id": "final", "speed": .5, "freeze_tail_s": 1,
            "output_operation_claims": [
                {"claim_id": "speed_claim", "kind": "speed", "description": "Playback is slowed.", "expected_value": .5},
                {"claim_id": "hold_claim", "kind": "tail_hold", "description": "The last real frame is held.", "expected_value": 1}]}]}
    reconstruction = {"plan": plan, "essential_groups": [{"group_id": "essential",
        "slot_id": "original_slot", "information": "Specific original essential information.",
        "min_readable_s": 1}], "limitations": []}
    rendered = {"sha256": "c"*64, "provenance": [{"output_in_s": 0, "output_out_s": 2}]}
    blind = {"protocol": audit.SEMANTIC_PROTOCOL, "video_sha256": rendered["sha256"],
        "observed_story": "Synthetic geometry is visible.", "apparent_theme": "Synthetic continuity.",
        "main_characters": ["Geometric figure"], "text_dependency": "none", "confusions": [],
        "evidence": [{"evidence_id": "output_visible", "claim_id": "blind_fact", "kind": "visual_action",
            "start_s": 0, "end_s": 2, "observed_fact": "Synthetic geometry remains visible.",
            "basis_evidence_ids": []}]}
    claims = fg.required_output_claims(reconstruction)
    value = {"protocol": "scoped_output_review_v1", "video_sha256": rendered["sha256"],
        "theme_status": "pass", "editing_status": "pass", "continuity_status": "pass",
        "claim_checks": [{"claim_id": claim["claim_id"], "status": "supported",
            "reason": "Synthetic actual evidence.", "evidence_ids": ["output_visible"], "limitations": [],
            **({"executed_value": claim["expected_value"]}
               if claim["kind"] in {"output_speed", "output_tail_hold"} else {})} for claim in claims],
        "contradictions": [], "limitations": []}
    return reconstruction, blind, rendered, value


def test_actual_review_requires_original_slot_and_each_original_essential_information(review_case):
    reconstruction, blind, rendered, value = review_case
    assert fg.validate_output_review(value, reconstruction, blind, rendered) is value
    claims = fg.required_output_claims(reconstruction)
    assert any(c["kind"] == "slot_takeaway" and c["description"] == reconstruction["plan"]["slots"][0]["intended_takeaway"]
               for c in claims)
    info = next(c for c in claims if c["kind"] == "information_takeaway")
    assert info["description"] == reconstruction["essential_groups"][0]["information"]
    value["claim_checks"] = [c for c in value["claim_checks"] if c["claim_id"] != info["claim_id"]]
    with pytest.raises(ValueError, match="checked_exactly_once"):
        fg.validate_output_review(value, reconstruction, blind, rendered)


@pytest.mark.parametrize("mutation,match", [
    (lambda r,b,v,x: x.update(contradictions=["The blind reading contradicts the target explanation."]), "unresolved_contradiction"),
    (lambda r,b,v,x: b.update(text_dependency="essential"), "unresolved_text_dependency"),
    (lambda r,b,v,x: b.update(text_dependency="unverifiable"), "unresolved_text_dependency"),
    (lambda r,b,v,x: b["evidence"][0].update(kind="visible_text"), "cannot_prove_visible_action"),
    (lambda r,b,v,x: b["evidence"][0].update(kind="inference"), "cannot_prove_visible_action"),
    (lambda r,b,v,x: next(c for c in x["claim_checks"] if c["claim_id"] == "speed_claim").update(executed_value=1),
     "execution_value_mismatch"),
    (lambda r,b,v,x: b["evidence"][0].update(end_s=.9), "own_actual_output_evidence"),
])
def test_target_pass_cannot_hide_actual_output_contradiction_text_dependency_or_unexecuted_hold(review_case, mutation, match):
    reconstruction, blind, rendered, value = review_case
    mutation(reconstruction, blind, rendered, value)
    with pytest.raises(ValueError, match=match):
        fg.validate_output_review(value, reconstruction, blind, rendered)
