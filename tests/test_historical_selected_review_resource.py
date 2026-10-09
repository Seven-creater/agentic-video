"""Historical selected-review restoration is generic and performs no dispatch."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from omni_story.library.resources import historical_selected_review_v1 as selected


PREFIX_SHA = "2acbf827ddfa126ca31def2fbcb59257bd149ab8f1f1fb2d2c446c3ba9ce4ac7"


def test_exact_historical_prefix_has_independent_provenance():
    proof = selected.provenance()
    prefix = selected.review_prompt({})[:-2]
    assert proof["policy"] == "historical_selected_review_v1"
    assert proof["origin"]["call_id"] == "glm_043_selected_review_v2_0"
    assert len(prefix) == proof["generic_prefix_characters"] == 1007
    assert hashlib.sha256(prefix.encode("utf-8")).hexdigest() == PREFIX_SHA
    assert proof["template_sha256"] == PREFIX_SHA
    assert proof["origin"]["request_file_sha256"] == (
        "e67b56db9c20b3c82a1a7ac46b02efbccbd6a3ede19ce13a3fb4b5153909b2c5")
    assert proof["origin"]["request_canonical_json_sha256"] == (
        "bb5b24c127cc8e32a24323476c9abbac996edebb1ef987b7fc8317d2c4917d8f")
    assert proof["historical_context_and_model_answers_included"] is False
    assert proof["selection_rationale_is_not_quality_verdict"] is True


def test_local_historical_request_matches_extracted_prefix_when_available():
    run = Path(__file__).resolve().parents[1] / "runs/library_reference_20261004"
    call = run / "calls/glm_043_selected_review_v2_0"
    request_path = call / "request.json"
    if not request_path.exists():
        pytest.skip("Private historical run is not packaged or required at runtime")
    raw = request_path.read_bytes()
    request = json.loads(raw)
    proof = selected.provenance()
    assert hashlib.sha256(raw).hexdigest() == proof["origin"]["request_file_sha256"]
    canonical = json.dumps(request, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"), allow_nan=False).encode("utf-8")
    assert hashlib.sha256(canonical).hexdigest() == proof["origin"]["request_canonical_json_sha256"]
    prompt = request["arguments"]["prompt"]
    assert hashlib.sha256(prompt.encode("utf-8")).hexdigest() == proof["origin"]["original_prompt_sha256"]
    assert selected.review_prompt({})[:-2] == prompt[:prompt.index('{"reference":')]
    answer = json.loads((call / "parsed.json").read_bytes())
    assert (answer["theme_status"], answer["editing_status"], answer["continuity_status"]) == (
        "partial", "partial", "pass")


def test_different_person_and_animation_are_permitted_without_forcing_pass():
    prefix = selected.review_prompt({})[:-2]
    assert "允许改变参考的具体人物、人物属性、纪实/动画形式和具体情节" in prefix
    assert "不能仅因换了人物或片种判定主旨失败" in prefix
    assert "如果差异导致实际含义改变，仍应如实partial/fail" in prefix
    assert "故事段落顺序相似或slot数量相似，不能证明" in prefix
    assert "每项status只能pass/partial/fail/unverifiable" in prefix
    assert "音频节奏未核验不能声称完整声画迁移pass" in prefix


def test_prompt_uses_only_current_caller_context_without_mutating_it():
    context = {
        "reference": {"reference_sha256": "a" * 64, "theme": "current observation"},
        "actual_render_sha256": "b" * 64,
        "blind_reading": {"observed_story": "current animated animal action"},
        "plan": {"segments": [{"source_in_s": 2.1, "source_out_s": 3.4}]},
        "provenance": [{"output_in_s": 0, "output_out_s": 1.3}],
        "audio_review_limit": "Actual-output audio has not been heard.",
    }
    original = deepcopy(context)
    prompt = selected.review_prompt(context)
    assert json.loads(prompt[1007:]) == context
    assert context == original
    prefix = prompt[:1007]
    for historical_answer in ("功夫熊猫", "无臂女性", "1273.85", "2219.50", "4770.30",
                              "77.366667", "21.933333", "e6d10d908d8ae508"):
        assert historical_answer not in prefix


@pytest.mark.parametrize("filename", ["manifest.json", "review_prefix.txt"])
def test_changed_resource_bytes_are_rejected(tmp_path, monkeypatch, filename):
    source = Path(selected.__file__).parent
    for name in ("manifest.json", "review_prefix.txt"):
        (tmp_path / name).write_bytes((source / name).read_bytes())
    with (tmp_path / filename).open("ab") as stream:
        stream.write(b" ")
    monkeypatch.setattr(selected, "_ROOT", tmp_path)
    expected = "manifest_changed" if filename == "manifest.json" else "template_changed"
    with pytest.raises(ValueError, match=expected):
        selected.review_prompt({})
