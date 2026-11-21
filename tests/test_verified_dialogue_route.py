from pathlib import Path
import json
import hashlib
import pytest

from omni_story.library.approved_dialogue_preview import reference_path
from omni_story.library.reviewed_sequence_edit import heard_on_screen, selection_inputs, verified_turns


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def test_only_the_locked_cast_and_visible_faces_enter_the_edit(tmp_path):
    rows = [{"scene_id": scene, "role_bindings": {"speaker": {"role_id": "a", "appearance": "white ears"}},
             "coverage_shots": [dict(window_id=scene, source_sha256="f", source_start_s=1,
                                     source_end_s=2, visible=["a", "extra"])]} for scene in ("left", "right")]
    save(tmp_path / "material_lock.json", {"scenes": rows})
    plan, evidence, path = selection_inputs(tmp_path, allow_locked=True)
    assert path is None and plan["cast_hints"] == {"speaker": "a"}
    assert list(evidence["clips"]) == ["S1", "S2"]
    assert [p["actor"] for sc in evidence["scenes"] for p in sc["people"]] == ["a", "a"]
    rows[1]["role_bindings"]["speaker"]["role_id"] = "b"
    save(tmp_path / "material_lock.json", {"scenes": rows})
    with pytest.raises(ValueError, match="changes actor"):
        selection_inputs(tmp_path, allow_locked=True)


def test_an_unreviewed_sequence_cannot_be_replaced_by_an_older_lock(tmp_path):
    save(tmp_path / "autonomous_material/browser/scene_pick.json",
         {"plan": {"scene_sequence": [{"reference": "s"}]}})
    with pytest.raises(ValueError, match="visual review"):
        selection_inputs(tmp_path, allow_locked=True)


def test_find_reference_from_nested_job_and_verify_bytes(tmp_path):
    folder = tmp_path / "creative/evaluations/retry"
    file = tmp_path / "movie.mp4"
    file.write_bytes(b"reference")
    digest = hashlib.sha256(file.read_bytes()).hexdigest()
    save(tmp_path / "plan.json", {"reference": str(file), "reference_sha256": digest})
    save(folder / "original_reference_contract.json", {"reference_sha256": digest})
    assert reference_path(folder) == file
    file.write_bytes(b"changed")
    with pytest.raises(ValueError, match="reference changed"):
        reference_path(folder)


def test_a_line_is_off_screen_only_when_every_adapted_reference_turn_was(tmp_path):
    turns = tmp_path / "turns.json"
    save(turns, {"lines": [{"line_id": 1, "on_screen": False}, {"line_id": 2, "on_screen": True}]})
    save(tmp_path / "library_state.json", {"artifacts": {"reference_turns_verified": [{"path": str(turns)}]}})
    timed = verified_turns(tmp_path)
    assert not heard_on_screen({"reference_line_ids": [1]}, timed)
    assert heard_on_screen({"reference_line_ids": [1, 2]}, timed)
