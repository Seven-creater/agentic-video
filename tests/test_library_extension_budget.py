"""Synthetic ledger tests; no real authorization, MCP, movie or rendering."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from omni_story.library import extension_budget as extension
from omni_story.library.state import LibraryState, LibraryStopped, json_sha, write_json


def request(number, *, scope=1):
    return {"prompt": str(number), "media_sha256": f"{number:064x}",
            "observation_scope": {"kind": "continuous_window", "source_sha256": "e" * 64,
                                  "source_start_s": scope, "source_end_s": scope + 1}}


def base(tmp_path, count=1, *, uncertain=False):
    state = LibraryState(tmp_path / "run", {"reference_sha256": "a" * 64,
        "library_sources": [{"source_id": "fixture", "sha256": "b" * 64}]})
    for number in range(1, count + 1):
        call, _ = state.begin_call("old_" + str(number), request(number, scope=number))
        if uncertain and number == count:
            state.fail_call(call, "synthetic lost reply")
            state.enable_independent_continuation()
        else:
            state.complete_call(call, {"reply": number}, usage={"prompt_tokens": 1, "completion_tokens": 2})
    (state.output / "render_0").mkdir()
    (state.output / "render_0" / "final.mp4").write_bytes(b"synthetic historical media")
    write_json(state.output / "result.json", {"historical": True})
    write_json(state.output / "watched_windows.json", {"windows": []})
    state.set_artifact("active_finecut_preflight", {"synthetic": True, "new_requests": 0})
    return state


def authorize(state):
    return extension.authorize(state.output, "继续下一步；重新剪辑视频。")


def new_call(state, name, number, *, repair_of=None):
    call, folder = state.begin_call(name, request(number, scope=number), repair_of=repair_of)
    assert json.loads((folder / "request.json").read_text(encoding="utf-8")) == request(number, scope=number)
    assert json.loads(state.path.read_text(encoding="utf-8"))["calls"][-1]["status"] == "submitted"
    state.complete_call(call, {"reply": number}, usage={"prompt_tokens": 3, "completion_tokens": 4})
    return call


def source_prefix(state):
    new_call(state, "active_4_draft", 250)
    new_call(state, "active_4_finecut", 251)


def test_missing_authorization_fails_before_state_or_file_mutation(tmp_path):
    state = base(tmp_path)
    before = state.path.read_bytes()
    files = set(state.output.rglob("*"))
    with pytest.raises(LibraryStopped, match="authorization_required"):
        extension.stage_state(state.output)
    assert state.path.read_bytes() == before
    assert set(state.output.rglob("*")) == files


def test_authorization_preserves_original_base_and_records_task_intent(tmp_path):
    state = base(tmp_path, count=79)
    original = deepcopy(state.data)
    old_video = (state.output / "render_0" / "final.mp4").read_bytes()
    policy = authorize(state)
    saved = json.loads(state.path.read_text(encoding="utf-8"))
    assert saved["max_requests"] == 80
    assert saved["request_count"] == 79
    assert saved["calls"] == original["calls"]
    assert saved["input_lock"] == original["input_lock"]
    assert policy["baseline_request_count"] == 79
    assert policy["effective_request_limit"] is None
    assert policy["additional_requests"] is None
    assert policy["request_limit_policy"] == extension.REQUEST_LIMIT_POLICY
    assert policy["render_index"] == 4 and policy["max_segments"] == 32
    assert policy["new_unique_windows"] == 0
    assert policy["user_authorization"] == "继续下一步；重新剪辑视频。"
    assert "user_removed_program_request_cap" in policy["budget_source"]
    assert (state.output / "render_0" / "final.mp4").read_bytes() == old_video
    assert Path(policy["handbook_path"]).read_bytes() == (extension.PACKAGE / "ACTIVE_FINE_CUT.md").read_bytes()
    assert extension.authorize(state.output, "later text must not rewrite old authorization") == policy


@pytest.mark.parametrize("additional", [0, 43, 44, 45, 80, True, 44.0])
def test_numeric_quota_cannot_be_reintroduced(tmp_path, additional):
    state = base(tmp_path)
    with pytest.raises(LibraryStopped, match="numeric_request_allowance_not_authorized"):
        extension.authorize(state.output, "continue", additional)
    assert extension.AUTHORIZATION not in json.loads(state.path.read_text(encoding="utf-8"))["artifacts"]


@pytest.mark.parametrize("text", ["", "  ", None, 1])
def test_trusted_task_text_required(tmp_path, text):
    state = base(tmp_path)
    with pytest.raises(LibraryStopped, match="authorization_text_missing"):
        extension.authorize(state.output, text)


def test_round_count_starts_zero_and_base_80_remains_frozen(tmp_path):
    original = base(tmp_path, count=79)
    authorize(original)
    state = extension.stage_state(original.output)
    assert state.max_requests == float("inf")
    assert state.usage()["extension_requests"] == 0
    new_call(state, "active_4_draft", 100)
    new_call(state, "active_4_finecut", 101)
    usage = state.usage()
    assert usage["requests"] == 81 and usage["extension_requests"] == 2
    assert usage["base_max_requests"] == 80 and usage["extension_remaining_requests"] is None
    assert usage["max_requests"] is usage["effective_request_limit"] is None
    json.dumps(usage, allow_nan=False)
    assert usage["prompt_tokens"] == 79 + 6
    assert json.loads(state.path.read_text(encoding="utf-8"))["max_requests"] == 80
    assert extension.stage_state(original.output).usage() == usage
    frozen = extension.historical_state(state)
    assert frozen.usage()["requests"] == 79 and frozen.usage()["max_requests"] == 80
    assert frozen.usage()["prompt_tokens"] == 79


@pytest.mark.parametrize("name", ["plan_4", "active_4_selection", "active_5_draft", "active_4_search",
    "semantic_slice_4_abc", "semantic_claims_batch_4", "active_4_draft_repair_repair"])
def test_stage_whitelist_blocks_before_budget_consumption(tmp_path, name):
    original = base(tmp_path)
    authorize(original)
    state = extension.stage_state(original.output)
    before = state.path.read_bytes()
    with pytest.raises(LibraryStopped, match="stage_not_authorized"):
        state.begin_call(name, request(100))
    assert state.path.read_bytes() == before


def test_one_original_one_repair_per_stage(tmp_path):
    original = base(tmp_path)
    authorize(original)
    state = extension.stage_state(original.output)
    call = new_call(state, "active_4_draft", 100)
    new_call(state, "active_4_draft_repair", 101, repair_of=call)
    with pytest.raises(LibraryStopped, match="duplicate_paid_stage"):
        state.begin_call("active_4_draft", request(102))
    with pytest.raises(LibraryStopped, match="duplicate_paid_stage"):
        state.begin_call("active_4_draft_repair", request(103), repair_of=call)


def test_repair_cannot_use_historical_or_different_stage_parent(tmp_path):
    original = base(tmp_path)
    authorize(original)
    state = extension.stage_state(original.output)
    with pytest.raises(LibraryStopped, match="invalid_repair_parent"):
        state.begin_call("active_4_draft_repair", request(100), repair_of=original.data["calls"][0])
    call = new_call(state, "active_4_draft", 101)
    with pytest.raises(LibraryStopped, match="invalid_repair_parent"):
        state.begin_call("active_4_finecut_repair", request(102), repair_of=call)


def test_maximum_32_structural_source_slices_and_same_key_claim_pair(tmp_path):
    original = base(tmp_path)
    authorize(original)
    state = extension.stage_state(original.output)
    source_prefix(state)
    for n in range(32):
        key = f"{n:016x}"
        new_call(state, "semantic_slice_4_" + key, 100 + n * 2)
        new_call(state, "semantic_claims_4_" + key, 101 + n * 2)
    with pytest.raises(LibraryStopped, match="slice_limit_exceeded"):
        state.begin_call("semantic_slice_4_" + "f" * 16, request(200))
    assert state.usage()["extension_requests"] == 66


def test_claim_checks_need_their_source_observation(tmp_path):
    original = base(tmp_path)
    authorize(original)
    state = extension.stage_state(original.output)
    source_prefix(state)
    with pytest.raises(LibraryStopped, match="claims_before_observation"):
        state.begin_call("semantic_claims_4_" + "f" * 16, request(100))


def test_write_before_send_pending_blocks_all_new_calls(tmp_path):
    original = base(tmp_path)
    authorize(original)
    state = extension.stage_state(original.output)
    call, folder = state.begin_call("active_4_draft", request(100))
    assert (folder / "request.json").is_file()
    assert state.usage()["extension_requests"] == 1
    with pytest.raises(LibraryStopped, match="request_outcome_unknown_no_replay"):
        state.begin_call("active_4_finecut", request(101))
    state.complete_call(call, {"synthetic": True})
    new_call(state, "active_4_finecut", 102)


def test_new_uncertain_call_cannot_continue_or_refund(tmp_path):
    original = base(tmp_path)
    authorize(original)
    state = extension.stage_state(original.output)
    call, _ = state.begin_call("active_4_draft", request(100))
    state.fail_call(call, "unknown extension reply")
    resumed = extension.stage_state(original.output)
    assert resumed.usage()["extension_requests"] == 1
    with pytest.raises(LibraryStopped, match="new_request_outcome_unknown_no_replay"):
        resumed.begin_call("active_4_finecut", request(101))


def test_original_unknown_media_scope_and_digest_not_replayed(tmp_path):
    original = base(tmp_path, uncertain=True)
    authorize(original)
    state = extension.stage_state(original.output)
    for value in (request(1, scope=2), request(2, scope=1), request(1, scope=1)):
        with pytest.raises(LibraryStopped, match="unknown_media_must_not_be_resubmitted"):
            state.begin_call("active_4_draft", value)
    new_call(state, "active_4_draft", 100)
    assert state.usage()["uncertain_calls"] == [original.data["calls"][0]["id"]]


def test_authorization_with_pending_original_rejected(tmp_path):
    original = base(tmp_path)
    original.begin_call("unfinished_old", request(10))
    with pytest.raises(LibraryStopped, match="pending_original_request"):
        authorize(original)


@pytest.mark.parametrize("path", ["render_4", "render_catalog_4", "render_5"])
def test_no_preexisting_unapproved_render_can_be_authorized(tmp_path, path):
    original = base(tmp_path)
    (original.output / path).mkdir()
    with pytest.raises(LibraryStopped, match="without_authorization|unapproved_original_render"):
        authorize(original)


@pytest.mark.parametrize("relative", ["result.json", "watched_windows.json", "render_0/final.mp4"])
def test_original_results_media_and_window_inventory_are_protected(tmp_path, relative):
    original = base(tmp_path)
    authorize(original)
    (original.output / relative).write_bytes(b"modified")
    with pytest.raises(LibraryStopped, match="historical_file_changed"):
        extension.stage_state(original.output)


def test_handbook_and_frozen_baseline_are_hash_bound(tmp_path):
    original = base(tmp_path)
    policy = authorize(original)
    Path(policy["handbook_path"]).write_text("changed", encoding="utf-8")
    with pytest.raises(LibraryStopped, match="historical_file_changed"):
        extension.get_authorization(original)


@pytest.mark.parametrize("field,value", [("max_requests", 123), ("request_count", 0), ("continuation_policy", "changed")])
def test_original_state_headers_and_ledger_cannot_change(tmp_path, field, value):
    original = base(tmp_path)
    authorize(original)
    data = json.loads(original.path.read_text(encoding="utf-8"))
    data[field] = value
    write_json(original.path, data)
    with pytest.raises(LibraryStopped):
        extension.stage_state(original.output)


def test_old_call_status_cannot_be_reconciled_through_extension(tmp_path):
    original = base(tmp_path, uncertain=True)
    authorize(original)
    state = extension.stage_state(original.output)
    with pytest.raises(LibraryStopped, match="historical_call_is_read_only"):
        state.reconcile_received(original.data["calls"][0], {}, evidence={})


def test_old_artifact_key_is_immutable_but_new_results_append(tmp_path):
    original = base(tmp_path)
    authorize(original)
    state = extension.stage_state(original.output)
    with pytest.raises(LibraryStopped, match="historical_artifact_is_read_only"):
        state.set_artifact("active_finecut_preflight", {"modified": True})
    state.set_artifact("active_4_result", {"synthetic": True})
    assert extension.get_authorization(state)["baseline_request_count"] == 1


def test_historical_view_allows_only_bound_render_globs_and_forbids_writes(tmp_path):
    original = base(tmp_path)
    authorize(original)
    state = extension.stage_state(original.output)
    new_call(state, "active_4_draft", 100)
    (state.output / "render_4").mkdir()
    write_json(state.output / "render_4" / "render_result.json", {"synthetic": True})
    (state.output / "render_catalog_4").mkdir()
    frozen = extension.historical_state(state)
    assert [p.name for p in frozen.output.glob("render_*")] == ["render_0"]
    assert list(frozen.output.glob("render_*/render_result.json")) == []
    assert frozen.data["request_count"] == 1
    assert Path(frozen.output) == state.output
    assert extension.historical_state(frozen) is frozen
    for operation in (lambda: frozen.begin_call("old", {}), lambda: frozen.set_artifact("x", {}), frozen._save):
        with pytest.raises(LibraryStopped, match="historical_view_is_read_only"):
            operation()
    assert extension.verify_historical_allocation(state).data["request_count"] == 1


def test_historical_view_still_detects_new_unapproved_render(tmp_path):
    original = base(tmp_path)
    authorize(original)
    frozen = extension.historical_state(extension.stage_state(original.output))
    (original.output / "render_5").mkdir()
    with pytest.raises(LibraryStopped, match="unapproved_render_directory"):
        frozen._reload()


def test_ordered_progress_stops_new_source_work_after_output_review(tmp_path):
    original = base(tmp_path)
    authorize(original)
    state = extension.stage_state(original.output)
    source_prefix(state)
    key = "0" * 16
    new_call(state, "semantic_slice_4_" + key, 100)
    new_call(state, "semantic_claims_4_" + key, 101)
    for number, name in enumerate(("active_4_blind", "active_4_economy", "active_4_review"), 102):
        new_call(state, name, number)
    count = state.usage()["extension_requests"]
    with pytest.raises(LibraryStopped, match="stage_progress_regressed"):
        state.begin_call("semantic_slice_4_" + "1" * 16, request(200))
    assert state.usage()["extension_requests"] == count


def test_stage_cannot_skip_draft_or_requirements_before_blind(tmp_path):
    original = base(tmp_path)
    authorize(original)
    state = extension.stage_state(original.output)
    with pytest.raises(LibraryStopped, match="stage_predecessor_unsettled"):
        state.begin_call("active_4_finecut", request(100))
    source_prefix(state)
    with pytest.raises(LibraryStopped, match="blind_before_source_evidence"):
        state.begin_call("active_4_blind", request(101))
    new_call(state, "semantic_slice_4_" + "0" * 16, 102)
    with pytest.raises(LibraryStopped, match="stage_predecessor_unsettled"):
        state.begin_call("active_4_blind", request(103))


def test_received_known_stage_requires_cache_reuse_instead_of_new_post(tmp_path):
    original = base(tmp_path)
    authorize(original)
    state = extension.stage_state(original.output)
    call = new_call(state, "active_4_draft", 100)
    before = state.usage()["extension_requests"]
    resumed = extension.stage_state(original.output)
    assert resumed.data["calls"][-1]["id"] == call["id"]
    with pytest.raises(LibraryStopped, match="duplicate_paid_stage"):
        resumed.begin_call("active_4_draft", request(100))
    assert resumed.usage()["extension_requests"] == before


def test_live_old_route_calls_cannot_expand_new_stage_scope(tmp_path):
    original = base(tmp_path)
    authorize(original)
    # The legacy class keeps its own80 limit, but the extension must reject any
    # intervening foreign call rather than treating it as authorized new work.
    original.begin_call("foreign_old_stage", request(100))
    with pytest.raises(LibraryStopped, match="stage_not_authorized"):
        extension.stage_state(original.output)


def test_authorization_bytes_and_baseline_snapshot_tamper_rejected(tmp_path):
    original = base(tmp_path)
    policy = authorize(original)
    Path(policy["baseline_state_path"]).write_text("{}", encoding="utf-8")
    with pytest.raises(LibraryStopped, match="historical_file_changed"):
        extension.get_authorization(original)


def test_state_authorization_entry_cannot_silently_change_new_request_policy(tmp_path):
    original = base(tmp_path)
    authorize(original)
    data = json.loads(original.path.read_text(encoding="utf-8"))
    entry = data["artifacts"][extension.AUTHORIZATION][0]
    policy = json.loads(Path(entry["path"]).read_text(encoding="utf-8"))
    policy["effective_request_limit"] = 10000
    write_json(entry["path"], policy)
    entry["sha256"] = json_sha(policy)
    write_json(original.path, data)
    with pytest.raises(LibraryStopped, match="request_limits_changed"):
        extension.stage_state(original.output)


def test_runtime_cap_mutation_is_replaced_by_authorized_unbounded_policy(tmp_path):
    original = base(tmp_path, count=79)
    authorize(original)
    state = extension.stage_state(original.output)
    # The original saved ceiling is historical. The validated facade removes it
    # only at runtime, while stage uniqueness still prohibits empty work loops.
    state.max_requests = 1
    assert extension.get_authorization(state)["effective_request_limit"] is None
    assert state.data["max_requests"] == 80
    new_call(state, "active_4_draft", 100)
    assert state.max_requests == float("inf")
