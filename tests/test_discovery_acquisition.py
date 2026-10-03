import copy
import json

import pytest

from omni_story.discovery.acquisition import parse_observed_detail


DETAIL_URL = "https://www.douyin.com/aweme/v1/web/aweme/detail/?aweme_id=123&msToken=private-token"


def body():
    return {"status_code": 0, "aweme_detail": {"aweme_id": "123", "author": {"secret": "private-account-data"},
            "video": {"play_addr": {"url_list": ["https://v3-web.douyinvod.com/media.mp4?signature=valid"],
                                    "uri": "v0300_example"},
                      "download_addr": {"url_list": ["https://v3-web.douyinvod.com/download.mp4"]},
                      "vid": "v0300_example"}}}


@pytest.mark.parametrize("representation", [lambda b: b, json.dumps, lambda b: json.dumps(b).encode()])
def test_observed_detail_returns_only_same_work_media_identifiers(representation):
    result = parse_observed_detail(DETAIL_URL, representation(body()), "123")
    assert result == {"aweme_id": "123", "urls": ["https://v3-web.douyinvod.com/media.mp4?signature=valid",
                       "https://v3-web.douyinvod.com/download.mp4"], "video_ids": ["v0300_example"]}
    assert "private" not in json.dumps(result)


@pytest.mark.parametrize("request_url", [
    "https://www.douyin.com/aweme/v1/web/aweme/detail/?aweme_id=999",
    "https://www.douyin.com/aweme/v1/web/aweme/detail/?aweme_id=123&aweme_id=999",
    "https://www.douyin.com/aweme/v1/web/aweme/detail/?aweme_id=123&aweme_id=123",
    "https://www.douyin.com/aweme/v1/web/aweme/detail/",
    "https://www.douyin.com/aweme/v1/web/aweme/feed/?aweme_id=123",
    "https://evil.example/aweme/v1/web/aweme/detail/?aweme_id=123",
    "http://www.douyin.com/aweme/v1/web/aweme/detail/?aweme_id=123",
    "https://name:password@www.douyin.com/aweme/v1/web/aweme/detail/?aweme_id=123",
])
def test_detail_request_must_be_official_and_identify_exactly_the_target(request_url):
    assert parse_observed_detail(request_url, body(), "123") is None


@pytest.mark.parametrize("aweme_id", ["999", "", "１２３", "123.0", True, 123.0])
def test_detail_body_cannot_prove_another_or_invalid_work(aweme_id):
    value = body()
    value["aweme_detail"]["aweme_id"] = aweme_id
    assert parse_observed_detail(DETAIL_URL, value, "123") is None


@pytest.mark.parametrize("response", ["not json", "[]", b"\xff", {}, {"status_code": 1, "aweme_detail": {}},
                                      {"aweme_detail": {"aweme_id": "123", "video": []}}])
def test_invalid_response_is_not_media_evidence(response):
    assert parse_observed_detail(DETAIL_URL, response, "123") is None


def test_invalid_addresses_are_removed_and_identifiers_are_deduplicated():
    value = body()
    good = "https://v3-web.douyinvod.com/media.mp4"
    value["aweme_detail"]["video"] = {"play_addr": {"url_list": [good, good,
        "http://v3-web.douyinvod.com/media.mp4", "javascript:alert(1)", "https://127.0.0.1/media.mp4",
        "https://localhost/media.mp4", "https://10.0.0.1/media.mp4", "https://user:secret@cdn.example/media.mp4",
        "https://cdn.example/media.mp4#fragment", "https://cdn.example:8443/media.mp4", "https://cdn.\nexample/media.mp4",
        "https://[invalid/media.mp4", None], "uri": "v0300_example"}, "vid": "v0300_example"}
    original = copy.deepcopy(value)
    assert parse_observed_detail(DETAIL_URL, value, "123") == {
        "aweme_id": "123", "urls": [good], "video_ids": ["v0300_example"]}
    assert value == original


def test_observed_equivalent_detail_endpoint_and_integer_response_id():
    value = body()
    value["aweme_detail"]["aweme_id"] = 123
    assert parse_observed_detail("https://www.iesdouyin.com/aweme/v1/aweme/detail?aweme_id=123", value, "123")


def test_no_usable_media_or_oversized_body_is_rejected():
    value = body()
    value["aweme_detail"]["video"] = {"play_addr": {"url_list": "https://cdn.example/video.mp4", "uri": "https://private"}}
    assert parse_observed_detail(DETAIL_URL, value, "123") is None
    assert parse_observed_detail(DETAIL_URL, " " * (4 * 1024 * 1024 + 1), "123") is None
