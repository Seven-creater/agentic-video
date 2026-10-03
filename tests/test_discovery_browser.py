import asyncio
import base64
import json
from pathlib import Path

import pytest

pytest.importorskip("browser_use")

from browser_use.llm.messages import UserMessage
from pydantic import BaseModel, ConfigDict
from omni_story.discovery.browser import identify_player, BrowseOnlyTools, NetworkEvidence, new_browser, download_session, standalone_work_url, editing_searches
from omni_story.discovery.llm import make_llm
from omni_story.discovery.state import DiscoveryStopped, State, session_lock


class Navigate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str


class Action(BaseModel):
    model_config = ConfigDict(extra="forbid")
    navigate: Navigate


class Envelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: list[Action]


def response(value):
    body = {"id": "synthetic", "object": "chat.completion", "created": 0, "model": "synthetic",
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": json.dumps(value)}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8}}
    return {"http_status": 200, "body_text": json.dumps(body), "elapsed_s": 0.001}


def test_qwen_json_adapter_validates_repairs_once_and_keeps_credentials_out_of_logs(tmp_path, monkeypatch):
    monkeypatch.setenv("QWEN_BROWSER_API_KEY", "synthetic-secret-for-test")
    s = State(tmp_path / "session", {})
    answers = iter([response({"action": [{"navigate": "wrong nested type"}]}),
                    response({"action": [{"navigate": {"url": "https://www.douyin.com/"}}]})])
    sent = []
    def fake(curl, url, key, method, payload, **kwargs):
        assert key == "synthetic-secret-for-test" and method == "POST"
        assert payload["enable_thinking"] is False and payload["response_format"] == {"type": "json_object"}
        assert kwargs["noproxy"] == "*"
        sent.append(payload)
        return next(answers)
    monkeypatch.setattr("omni_story.discovery.llm.curl_json", fake)
    image = "data:image/png;base64," + base64.b64encode(b"synthetic screenshot bytes").decode()
    llm = make_llm(s)
    messages = [UserMessage(content=[{"type": "text", "text": "Observe this fixture."},
                                    {"type": "image_url", "image_url": {"url": image}}])]
    value = asyncio.run(llm.ainvoke(messages, output_format=Envelope))
    assert len(sent) == s.data["qwen_calls"] == 2
    assert value.completion.action[0].navigate.url == "https://www.douyin.com/"
    assert llm.max_retries == 0
    assert len(list((s.output / "screenshots").glob("*.png"))) == 2
    for path in s.output.rglob("*.json"):
        data = path.read_text(encoding="utf-8")
        assert "synthetic-secret-for-test" not in data
        assert "data:image/png;base64" not in data


def test_qwen_transport_failure_is_never_retried(tmp_path, monkeypatch):
    monkeypatch.setenv("QWEN_BROWSER_API_KEY", "synthetic-secret-for-test")
    s = State(tmp_path / "session", {})
    sent = []
    def fake(*args, **kwargs):
        sent.append(1)
        raise RuntimeError("API_transport_failed:35 synthetic-secret-for-test Bearer another-secret " + "x" * 2000)
    monkeypatch.setattr("omni_story.discovery.llm.curl_json", fake)
    with pytest.raises(DiscoveryStopped, match="unknown"):
        asyncio.run(make_llm(s).ainvoke([UserMessage(content="Return JSON")], output_format=Envelope))
    assert len(sent) == 1 and s.data["calls"][0]["status"] == "submitted"
    failure = json.loads((s.output / "calls/qwen_001_browser/transport_failure.json").read_text())
    assert failure["curl_exit_code"] == 35 and failure["error_type"] == "RuntimeError"
    assert len(failure["error_message"]) <= 1000
    assert "synthetic-secret-for-test" not in failure["error_message"]
    assert "another-secret" not in failure["error_message"]


def test_qwen_curl_direct_route_overrides_environment_proxies_without_changing_other_api_calls(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import os
    from omni_story.api import curl_json
    monkeypatch.setenv("QWEN_BROWSER_API_KEY", "synthetic-secret-for-test")
    proxy = "http://127.0.0.1:7897"
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        monkeypatch.setenv(name, proxy)
    commands = []
    expected = response({"action": [{"navigate": {"url": "https://www.douyin.com/"}}]})
    def fake_run(args, **kwargs):
        commands.append(args)
        assert "synthetic-secret-for-test" not in " ".join(args)
        return SimpleNamespace(returncode=0, stdout=(expected["body_text"] + "\n200").encode())
    monkeypatch.setattr("omni_story.api.subprocess.run", fake_run)
    s = State(tmp_path / "session", {})
    value = asyncio.run(make_llm(s).ainvoke([UserMessage(content="Return JSON")], output_format=Envelope))
    assert value.completion.action[0].navigate.url == "https://www.douyin.com/"
    assert len(commands) == 1 and s.data["qwen_calls"] == 1
    assert commands[0][commands[0].index("--noproxy") + 1] == "*"
    assert "--retry" not in commands[0]
    assert all(os.environ[name] == proxy for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"))
    curl_json("curl", "https://example.invalid", "synthetic", "POST", {})
    assert "--noproxy" not in commands[1]


def test_permissive_browser_schema_still_rejects_unknown_action_fields(tmp_path, monkeypatch):
    from pydantic import create_model
    monkeypatch.setenv("QWEN_BROWSER_API_KEY", "synthetic-secret-for-test")
    scroll = create_model("Scroll", down=(bool, True), pages=(float, 1.0))
    action = create_model("ScrollAction", scroll=(scroll, ...))
    envelope = create_model("ScrollEnvelope", action=(list[action], ...))
    s = State(tmp_path / "session", {})
    answers = iter([response({"action": [{"scroll": {"down": True, "num_pages": 3.0}}]}),
                    response({"action": [{"scroll": {"down": True, "pages": 3.0}}]})])
    monkeypatch.setattr("omni_story.discovery.llm.curl_json", lambda *a, **k: next(answers))
    result = asyncio.run(make_llm(s).ainvoke([UserMessage(content="Scroll three pages")], output_format=envelope))
    assert result.completion.action[0].scroll.pages == 3.0
    assert s.data["qwen_calls"] == 2
    validation = json.loads((s.output / "calls/qwen_001_browser/validation.json").read_text())
    assert "unknown_model_field:num_pages" in validation["error"]


def test_real_browser_use_root_action_schema_accepts_valid_and_rejects_unknown_fields():
    from omni_story.discovery.llm import reject_unknown_fields
    action_model = BrowseOnlyTools().registry.create_action_model()
    raw = {"scroll": {"down": True, "pages": 3.0}}
    parsed = action_model.model_validate(raw)
    reject_unknown_fields(raw, parsed)
    with pytest.raises(ValueError, match="unknown_model_field:num_pages"):
        invalid = {"scroll": {"down": True, "num_pages": 3.0}}
        reject_unknown_fields(invalid, action_model.model_validate(invalid))


def test_only_standalone_current_player_with_real_https_media_is_accepted():
    value = {"page_url": "https://www.douyin.com/video/123", "src": "https://cdn.example/video.mp4", "duration_s": 20}
    assert identify_player(value) == "123"
    for changes in ({"page_url": "https://www.douyin.com/search/foo"}, {"src": "blob:https://www.douyin.com/1"},
                    {"duration_s": None}, {"error": "ambiguous_players"}, {"page_url": "https://evil.test/video/123"}):
        with pytest.raises(ValueError):
            identify_player({**value, **changes})


def test_open_current_work_preserves_complete_modal_id_and_rejects_truncation():
    assert standalone_work_url("https://www.douyin.com/search/test?modal_id=7686517200710041234&foo=bar") == "https://www.douyin.com/video/7686517200710041234"
    assert standalone_work_url("https://www.douyin.com/video/123") == "https://www.douyin.com/video/123"
    for url in ("https://evil.test/?modal_id=123", "https://www.douyin.com/?modal_id=123...abc",
                "https://www.douyin.com/?modal_id=123&modal_id=456", "https://www.douyin.com/search/foo"):
        with pytest.raises(ValueError):
            standalone_work_url(url)


def test_new_editing_searches_do_not_count_historical_policy_queries(tmp_path):
    state = State(tmp_path, {})
    state.data["searches"] = ["短剧 人物关系 反转", "AI短片 叙事 场景转换", "剧情剪辑 低成本", "短剧剪辑 卡点", "剧情剪辑 低成本"]
    assert editing_searches(state) == ["剧情剪辑 低成本", "短剧剪辑 卡点"]


def test_browser_excludes_external_or_file_mutation_tools():
    names = set(BrowseOnlyTools().registry.registry.actions)
    assert {"navigate", "click", "input", "scroll", "screenshot"} <= names
    assert not names & {"search", "extract", "evaluate", "write_file", "upload_file", "read_file", "replace_file", "save_as_pdf"}


def test_douyin_direct_route_is_isolated_from_system_proxy(tmp_path, monkeypatch):
    monkeypatch.setenv("QWEN_BROWSER_PROFILE_DIR", str(tmp_path / "profile"))
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7897")
    monkeypatch.delenv("QWEN_BROWSER_DIRECT", raising=False)
    browser = new_browser()
    assert "--no-proxy-server" in browser.browser_profile.args
    assert Path(browser.browser_profile.user_data_dir) == (tmp_path / "profile").resolve()
    with download_session() as session:
        assert session.trust_env is False
    monkeypatch.setenv("QWEN_BROWSER_DIRECT", "0")
    assert "--no-proxy-server" not in new_browser().browser_profile.args
    assert "--no-proxy-server" in new_browser(fixture=True).browser_profile.args
    with download_session() as session:
        assert session.trust_env is True
    assert __import__("os").environ["HTTPS_PROXY"] == "http://127.0.0.1:7897"


def test_concurrent_session_cannot_double_send(tmp_path):
    with session_lock(tmp_path):
        with pytest.raises(DiscoveryStopped, match="already_running"):
            with session_lock(tmp_path):
                pytest.fail("concurrent writer entered")
    with session_lock(tmp_path):
        pass


def test_completed_reference_is_delivered_without_browser_network_or_model_credentials(tmp_path, monkeypatch):
    from omni_story.discovery import browser
    from omni_story.pipeline import sha
    media = tmp_path / "video.mp4"
    media.write_bytes(b"synthetic previously validated media")
    s = State(tmp_path / "session", {})
    s.candidate({"aweme_id": "123", "page_url": "https://www.douyin.com/video/123",
                 "media": {"original_path": str(media), "analysis_path": str(media),
                           "original_sha256": sha(media), "analysis_sha256": sha(media)},
                 "review": {"limitations": []}})
    s.data["selection"] = {"selected_aweme_id": "123", "reason": "previous Omni choice", "limitations": []}
    s.save()
    def unexpected(*a, **k):
        pytest.fail("completed reference must not reopen a browser or require a model")
    monkeypatch.setattr(browser, "new_browser", unexpected)
    monkeypatch.setattr(browser, "make_llm", unexpected)
    monkeypatch.setattr(browser, "QwenAPI", unexpected)
    result = asyncio.run(browser.run_browser(s, stage="reference"))
    assert result["status"] == "reference_selected" and result["usage"]["qwen"]["calls"] == 0


def test_blob_player_never_selects_unrelated_network_video():
    evidence = NetworkEvidence.__new__(NetworkEvidence)
    async def session():
        from types import SimpleNamespace
        return SimpleNamespace(session_id="current")
    evidence.enable = session
    evidence.requests = {("current", "https://cdn.test/unrelated.mp4"): {"mime_type": "video/mp4"},
                         ("current", "https://cdn.test/bound.mp4"): {"mime_type": "video/mp4"}}
    value = {"page_url": "https://www.douyin.com/video/123", "src": "blob:https://www.douyin.com/player",
             "duration_s": 10, "bound_media": {"aweme_id": "123", "urls": ["https://cdn.test/bound.mp4"], "video_ids": []}}
    assert asyncio.run(evidence.for_player(value))["download_url"] == "https://cdn.test/bound.mp4"
    value["bound_media"]["urls"] = ["https://cdn.test/not-observed.mp4"]
    with pytest.raises(ValueError, match="actual_network"):
        asyncio.run(evidence.for_player(value))
    value["bound_media"]["aweme_id"] = "999"
    with pytest.raises(ValueError):
        asyncio.run(evidence.for_player(value))


def test_real_local_browser_navigation_input_click_scroll_and_screenshot_without_paid_calls(tmp_path, monkeypatch):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading
    from omni_story.discovery.fixture import HTML
    from omni_story.discovery.browser import gated
    monkeypatch.setenv("QWEN_BROWSER_PROFILE_DIR", str(tmp_path / "browser-profile"))
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    async def exercise():
        browser = new_browser(fixture=True)
        browser.browser_profile.headless = True
        try:
            await browser.start()
            page = await browser.must_get_current_page()
            await page.navigate(f"http://127.0.0.1:{server.server_port}/")
            assert Path(browser.browser_profile.user_data_dir) == (tmp_path / "FixtureProfile").resolve()
            assert await gated(browser) is False
            field = (await page.get_elements_by_css_selector("#query"))[0]
            await field.fill("offline browser validation")
            await (await page.get_elements_by_css_selector("#search"))[0].click()
            await (await page.get_elements_by_css_selector("#results button"))[0].click()
            await page.evaluate("() => {window.scrollTo(0,document.documentElement.scrollHeight);return true}")
            actual = json.loads(await page.evaluate("() => ({searched:!!window.searched,opened:!!window.opened,scrolled:scrollY>500})"))
            assert all(actual.values())
            screenshot = base64.b64decode(await page.screenshot())
            assert screenshot.startswith(b"\x89PNG")
            await page.evaluate("() => {const d=document.createElement('div');d.setAttribute('role','dialog');d.textContent='安全验证：拖动滑块';document.body.append(d);return {ok:true}}")
            assert await gated(browser) is True
        finally:
            await browser.kill()
    try:
        asyncio.run(exercise())
    finally:
        server.shutdown()
        server.server_close()
