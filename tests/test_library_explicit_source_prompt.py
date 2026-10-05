"""Forward fact formatting stays separate from historical prompts and judgments."""
from copy import deepcopy
import hashlib
import json

import pytest

from omni_story.library import goal_budget, semantic_audit as audit, semantic_pipeline, semantic_prompts
from omni_story.library.media import sha256_file
from omni_story.library.state import LibraryStopped, json_sha, write_json
from test_library_semantic_audit import slice_data, comparison, SOURCE_SHA


def template(prompt):
    return json.loads(prompt.split("metadata：", 1)[1])


def source_for(segment):
    return {"source_id": segment["source_id"], "sha256": SOURCE_SHA}


def test_full_template_binds_actual_source_and_has_all_typed_fields_without_creative_inputs(slice_data):
    plan, segment, proxy, _ = slice_data
    segment["slot_id"] = "HIDDEN_SLOT_ANSWER"
    segment["visual_claims"][0]["description"] = "HIDDEN_DESIRED_ACTION"
    before = deepcopy(slice_data)
    prompt = semantic_prompts.explicit_slice_observation_prompt(segment, source_for(segment), proxy)
    value = template(prompt)
    assert set(value) == {"protocol", "segment_id", "source_id", "source_sha256", "source_in_s",
        "source_out_s", "proxy_sha256", "observed_duration_s", "characters", "evidence", "uncertainties"}
    assert value["source_in_s"] == segment["source_in_s"]
    assert value["source_out_s"] == segment["source_out_s"]
    assert value["source_sha256"] == SOURCE_SHA and value["proxy_sha256"] == proxy["sha256"]
    assert set(value["characters"][0]) == {"character_id", "appearance"}
    assert set(value["evidence"][0]) == {"evidence_id", "kind", "local_start_s", "local_end_s",
        "description", "character_ids", "basis_evidence_ids"}
    assert value["uncertainties"] == []
    assert "uncertainties必须显式为字符串数组list[str]" in prompt
    assert "禁止省略uncertainties" in prompt and '对象数组' in prompt
    assert "0 <= local_start_s < local_end_s <= observed_duration_s" in prompt
    assert "仅引用本evidence表内其它直接事实的ID" in prompt
    assert "HIDDEN_SLOT_ANSWER" not in prompt and "HIDDEN_DESIRED_ACTION" not in prompt
    assert audit.validate_segment_observation(value, segment, SOURCE_SHA, proxy) is value
    assert slice_data == before


@pytest.mark.parametrize("duration", [.05, .25, 1, 4])
def test_template_interval_is_positive_and_local_even_for_a_subsecond_proxy(slice_data, duration):
    _, segment, proxy, _ = slice_data
    segment["source_out_s"] = segment["source_in_s"] + duration
    proxy.update(duration_s=duration, source_end_s=segment["source_out_s"])
    value = template(semantic_prompts.explicit_slice_observation_prompt(segment, source_for(segment), proxy))
    assert 0 <= value["evidence"][0]["local_start_s"] < value["evidence"][0]["local_end_s"] <= duration
    audit.validate_segment_observation(value, segment, SOURCE_SHA, proxy)


def test_legacy_source_prompt_exact_bytes_are_unchanged(slice_data):
    _, segment, proxy, _ = slice_data
    legacy = semantic_prompts.slice_observation_prompt(segment, source_for(segment), proxy)
    assert hashlib.sha256(legacy.encode("utf-8")).hexdigest() == "d5442a19d81202d834c07a0f898ac04332d698025e4db47e37cba6fa702a6ad9"
    assert legacy != semantic_prompts.explicit_slice_observation_prompt(segment, source_for(segment), proxy)


@pytest.mark.parametrize("forward", [False, True])
def test_source_prompt_hook_executes_existing_fact_and_claim_validators_without_normalization(
        tmp_path, slice_data, monkeypatch, forward):
    plan, segment, proxy, observation = slice_data
    proxy.update(path=str(tmp_path / "fixture.mp4"), kind="continuous_window")
    monkeypatch.setattr(semantic_pipeline, "prepare_window", lambda *args, **kwargs: deepcopy(proxy))
    calls = []
    _, check = comparison(plan, segment, observation)

    class FakeGLM:
        def call(self, name, prompt, media, validator, **kwargs):
            calls.append((name, prompt))
            value = deepcopy(observation if name.startswith("semantic_slice_") else check)
            assert validator(value) is value
            return value

    source = source_for(segment)
    kwargs = {"observation_prompt": semantic_prompts.explicit_slice_observation_prompt} if forward else {}
    manifest = semantic_pipeline.observe_selected_slices(FakeGLM(), plan, {segment["source_id"]: source},
        [{"window_id": segment["window_id"], "observation": {"roles": []}}], tmp_path / "cache", tmp_path / "out", 9,
        **kwargs)
    expected = semantic_prompts.explicit_slice_observation_prompt if forward else semantic_prompts.slice_observation_prompt
    assert calls[0][1] == expected(segment, source, proxy)
    assert calls[1][0].startswith("semantic_claims_9_")
    assert manifest["observations"] == [observation]
    assert manifest["segment_checks"] == [check]


def test_forward_prompt_does_not_turn_wrong_uncertainty_type_into_valid_fact(tmp_path, slice_data, monkeypatch):
    plan, segment, proxy, observation = slice_data
    proxy.update(path=str(tmp_path / "fixture.mp4"), kind="continuous_window")
    observation["uncertainties"] = {"description": "Wrong root shape."}
    before = deepcopy(observation)
    monkeypatch.setattr(semantic_pipeline, "prepare_window", lambda *args, **kwargs: deepcopy(proxy))

    class FakeGLM:
        def call(self, name, prompt, media, validator, **kwargs):
            validator(observation)
            pytest.fail("Wrong root uncertainty shape must remain invalid.")

    with pytest.raises(ValueError, match="uncertainties:list_required"):
        semantic_pipeline.observe_selected_slices(FakeGLM(), plan, {segment["source_id"]: source_for(segment)},
            [{"window_id": segment["window_id"], "observation": {"roles": []}}], tmp_path / "cache", tmp_path / "out", 9,
            observation_prompt=semantic_prompts.explicit_slice_observation_prompt)
    assert observation == before
    assert not (tmp_path / "out" / "semantic_audit").exists()


def cached_fact(tmp_path, slice_data, monkeypatch, target_round, forward_request):
    plan, segment, proxy, observation = deepcopy(slice_data)
    source = source_for(segment)
    media = tmp_path / "media" / "video.mp4"
    media.parent.mkdir()
    media.write_bytes(b"synthetic fixture bytes, no real media")
    proxy.update(path=str(media), sha256=sha256_file(media), kind="continuous_window")
    proxy["spec"] = {k: proxy[k] for k in ("kind", "source_sha256", "source_start_s", "source_end_s")}
    proxy["spec"]["fps"] = 30
    observation["proxy_sha256"] = proxy["sha256"]
    write_json(media.parent / "lineage.json", proxy)
    write_json(tmp_path / "catalog" / "inventory.json", {"sources": [source]})
    write_json(tmp_path / "watched_windows.json", [{"window_id": segment["window_id"], "observation": {"roles": []}}])
    write_json(tmp_path / "artifacts" / f"goal_feedback_round_{target_round}" / "plan.json", plan)
    key = json_sha({"segment": segment["segment_id"], "sha": source["sha256"],
                   "in": segment["source_in_s"], "out": segment["source_out_s"]})[:16]
    data = {"input_lock": {"library_sources": [source]}, "calls": [], "artifacts": {}}

    def received(ident, name, request, value):
        folder = tmp_path / "calls" / ident
        response = {"result": {"content": [{"type": "text", "text": json.dumps(value)}]}}
        for filename, content in (("request.json", request), ("response.json", response), ("parsed.json", value)):
            write_json(folder / filename, content)
        call = {"id": ident, "name": name, "status": "received", "request_sha256": json_sha(request),
                "response_sha256": json_sha(response)}
        data["calls"].append(call)
        return call

    received("known_finecut", f"active_{target_round}_finecut", {}, {"plan": plan})
    selected = semantic_prompts.explicit_slice_observation_prompt if forward_request else semantic_prompts.slice_observation_prompt
    call = received("known_fact", f"semantic_slice_{target_round}_{key}", {"tool": "analyze_video",
        "media_sha256": proxy["sha256"], "arguments": {"video_source": str(media),
            "prompt": selected(segment, source, proxy)}}, observation)
    proof = {"target_stage": f"semantic_slice_{target_round}_{key}", "call_id": call["id"],
        "segment": {k: segment[k] for k in ("segment_id", "source_id", "source_in_s", "source_out_s")},
        "source": source, "proxy": proxy, "value": observation, "plan_sha256": json_sha(plan)}
    monkeypatch.setattr(goal_budget, "verify_source", lambda source: None)
    from omni_story.library import research_resume
    monkeypatch.setattr(research_resume, "load_preplanning", lambda state, r: {} if r >= 9 else None)
    return data, (f"goal_cached_{target_round}_slice_{key}", proof)


@pytest.mark.parametrize("round_no,forward,valid", [(8, False, True), (9, True, True), (10, True, True),
                                                    (8, True, False), (9, False, False)])
def test_goal_cache_requires_correct_historical_or_forward_source_prompt(
        tmp_path, slice_data, monkeypatch, round_no, forward, valid):
    data, proposed = cached_fact(tmp_path, slice_data, monkeypatch, round_no, forward)
    if valid:
        assert goal_budget._cache_proofs(tmp_path, data, proposed=proposed)[proposed[1]["target_stage"]] == proposed[1]
    else:
        with pytest.raises(LibraryStopped, match="cached_independent_prompt_changed"):
            goal_budget._cache_proofs(tmp_path, data, proposed=proposed)
