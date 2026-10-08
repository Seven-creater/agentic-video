"""Search guidance follows the allocated cap without removing evidence fields."""
import json

import pytest

from omni_story.library.contracts import validate_search
from omni_story.library.prompts import search_prompt


def _template_and_context(prompt):
    payload = prompt.split("输出：\n", 1)[1]
    template, offset = json.JSONDecoder().raw_decode(payload)
    return template, json.loads(payload[offset:])


@pytest.mark.parametrize("maximum", [1, 8, 12])
def test_search_window_limit_matches_allocated_context(maximum):
    context = {"max_windows_this_round": maximum, "remaining_window_budget": 16,
               "coarse_index": [{"source_id": "movie_1", "events": ["observed fact"]}]}
    prompt = search_prompt(context)
    assert f"一次请求不超过{maximum}个连续窗口" in prompt
    assert _template_and_context(prompt)[1] == context


def test_search_missing_allocation_preserves_twelve_window_default():
    prompt = search_prompt({"catalog": {"sources": []}})
    assert "一次请求不超过12个连续窗口" in prompt


def test_search_emits_windows_before_reason_and_keeps_all_required_fields():
    template, _ = _template_and_context(search_prompt({"max_windows_this_round": 8}))
    assert list(template) == ["windows", "reason"]
    assert set(template["windows"][0]) == {"source_id", "start_s", "end_s", "question", "role_ids"}
    assert template["reason"]
    template["windows"][0]["source_id"] = "movie_1"
    catalog = {"sources": [{"source_id": "movie_1", "sha256": "movie_hash", "duration_s": 100}]}
    assert validate_search(template, catalog, max_windows=8) is template


def test_search_allocation_eight_remains_enforced_by_contract():
    template, _ = _template_and_context(search_prompt({"max_windows_this_round": 8}))
    window = {**template["windows"][0], "source_id": "movie_1"}
    template["windows"] = [dict(window) for _ in range(9)]
    catalog = {"sources": [{"source_id": "movie_1", "sha256": "movie_hash", "duration_s": 100}]}
    with pytest.raises(ValueError, match="search:too_many_windows"):
        validate_search(template, catalog, max_windows=8)
