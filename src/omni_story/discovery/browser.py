"""Local Chrome tools. Media URLs must match the current work and its real player."""
import asyncio
import json
import os
from pathlib import Path
import re
import subprocess
import time
from urllib.parse import parse_qs, quote, urlparse

os.environ.setdefault("ANONYMIZED_TELEMETRY", "false")
os.environ.setdefault("BROWSER_USE_SETUP_LOGGING", "false")

from browser_use import ActionResult, Agent, Browser, BrowserSession, Tools
import requests

from ..api import QwenAPI
from ..pipeline import sha, write
from .download.direct_downloader import DirectCDNDownloader
from .download.manifest import Manifest
from .download.models import DownloadItem
from .llm import make_llm
from .media import prepare, restore_downloads, verify_cached
from . import prompts
from .acquisition import parse_observed_detail
from .review import audition, handoff, select
from .state import DiscoveryStopped, LIMITS


def profile_path():
    # MSIX-hosted terminals can virtualize LOCALAPPDATA differently between launches.
    # Use the user's stable physical Local folder on Windows, with an explicit override.
    local = Path.home() / "AppData" / "Local" if os.name == "nt" else Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local"))
    return Path(os.environ.get("QWEN_BROWSER_PROFILE_DIR") or
                str(local / "OmniStory" / "DouyinProfile")).absolute()


def chrome_path():
    configured = os.environ.get("QWEN_BROWSER_EXECUTABLE")
    candidates = [configured] if configured else [
        str(Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Google/Chrome/Application/chrome.exe"),
        str(Path(os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)")) / "Microsoft/Edge/Application/msedge.exe")]
    for value in candidates:
        if value and Path(value).is_file():
            return str(Path(value).resolve())
    raise ValueError("local_Chrome_or_Edge_missing_set_QWEN_BROWSER_EXECUTABLE")


def direct_connection():
    return os.environ.get("QWEN_BROWSER_DIRECT", "1").strip().lower() not in {"0", "false", "no"}


def download_session():
    session = requests.Session()
    # Windows registry proxies are also picked up by requests when trust_env is enabled.
    session.trust_env = not direct_connection()
    return session


def new_browser(*, fixture=False):
    path = profile_path()
    if fixture:
        path = path.parent / "FixtureProfile"
    path.mkdir(parents=True, exist_ok=True)
    browser = Browser(executable_path=chrome_path(), user_data_dir=None, headless=False,
                   use_cloud=False, keep_alive=True, enable_default_extensions=False,
                   args=["--no-proxy-server"] if fixture or direct_connection() else [],
                   allowed_domains=["127.0.0.1", "localhost"] if fixture else ["douyin.com", "*.douyin.com"],
                   accept_downloads=False, auto_download_pdfs=False)
    # 0.13.10 clones supplied Chrome profiles in model_post_init. Ours is a dedicated
    # profile, so assign its path after construction to preserve the actual login.
    browser.browser_profile.user_data_dir = path.resolve()
    return browser


async def start_browser(browser):
    expected = Path(browser.browser_profile.user_data_dir).resolve()
    await browser.start()
    if Path(browser.browser_profile.user_data_dir).resolve() != expected:
        raise DiscoveryStopped("dedicated_profile_unavailable_no_temporary_login_fallback")


GATE_JS = """() => {
 const visible = e => {const r=e.getBoundingClientRect();return r.width>0&&r.height>0&&getComputedStyle(e).visibility!=='hidden'};
 const nodes=[...document.querySelectorAll('[role="dialog"], [class*="captcha"], [id*="captcha"], [class*="verify"]')];
 return {gated:nodes.filter(visible).some(e=>/验证码|安全验证|拖动滑块|点击验证|扫码登录|手机号登录|登录后继续/.test(e.innerText||''))};
}"""


async def gated(browser):
    page = await browser.must_get_current_page()
    return json.loads(await page.evaluate(GATE_JS))["gated"] is True


async def authenticated(browser):
    session = await browser.get_or_create_cdp_session()
    result = await session.cdp_client.send.Network.getCookies(
        {"urls": ["https://www.douyin.com/"]}, session_id=session.session_id)
    # Only return a boolean. Account cookies are never sent to a model or saved in run logs.
    return any(c["name"] in {"sessionid", "sessionid_ss"} and c.get("value") for c in result["cookies"])


async def wait_login(browser, state=None, *, require_auth=False):
    def pause():
        from contextlib import nullcontext
        return state.human_pause("douyin_login_or_verification") if state else nullcontext()
    with pause():
        print("请在专用 Chrome 窗口扫码登录或处理验证码；会话将自动检测并继续，Ctrl+C 可保留记录退出。", flush=True)
        while True:
            if (not require_auth or await authenticated(browser)) and not await gated(browser):
                return
            await asyncio.sleep(3)


class BrowseOnlyTools(Tools):
    def __init__(self, *, fixture=False):
        super().__init__(exclude_actions=["search", "extract", "upload_file", "write_file", "replace_file",
                                        "read_file", "evaluate", "save_as_pdf", "dropdown_options", "select_dropdown"])
        self.fixture = fixture

    async def act(self, action, browser_session, **kwargs):
        payload = action.model_dump(exclude_none=True)
        if "capture_candidate" in payload:
            # Capture includes an admitted paid AV request; the generic 180s tool timeout is too short.
            kwargs["action_timeout"] = 2400
        for name in ("click", "input"):
            if name not in payload or self.fixture:
                continue
            params = payload[name]
            if params.get("index") is None:
                return ActionResult(error="Use DOM index for browsing; unrestricted coordinate clicks are disabled.")
            node = await browser_session.get_dom_element_by_index(params["index"])
            if node is None:
                return ActionResult(error="Element missing; observe the page again.")
            text = node.get_all_children_text(max_depth=2) + " " + " ".join(node.attributes.values())
            if re.search(r"点赞|关注|评论|收藏|私信|发送|发布|购买|充值|订阅|like|follow|comment|message|purchase", text, re.I):
                return ActionResult(error="This control changes the account or communicates; browsing only.")
            if name == "input" and not ("搜索" in text or "search" in text.lower()):
                return ActionResult(error="Text entry is limited to search boxes; login is handled by the user.")
        if "send_keys" in payload and not self.fixture:
            if payload["send_keys"]["keys"] not in {"Space", "ArrowDown", "ArrowUp", "PageDown", "PageUp", "Escape"}:
                return ActionResult(error="Only playback/scroll keys are allowed; use search_douyin for searching.")
        return await super().act(action, browser_session, **kwargs)


PLAYER_JS = r"""() => {
 const candidates=[...document.querySelectorAll('video')].map(v=>{
  const r=v.getBoundingClientRect();return {v,area:Math.max(0,Math.min(r.right,innerWidth)-Math.max(r.left,0))*Math.max(0,Math.min(r.bottom,innerHeight)-Math.max(r.top,0))};
 }).filter(x=>x.area>10000&&getComputedStyle(x.v).visibility!=='hidden').sort((a,b)=>b.area-a.area);
 if(!candidates.length)return {error:'visible_player_missing'};
 if(candidates[1]&&candidates[1].area>candidates[0].area*.8)return {error:'ambiguous_players'};
 const v=candidates[0].v;
 const aid=(location.pathname.match(/^\/video\/(\d+)/)||[])[1];
 const bound={urls:[],video_ids:[]};
 const roots=[window._ROUTER_DATA,window.__INITIAL_STATE__];
 for(const id of ['RENDER_DATA','__NEXT_DATA__','__UNIVERSAL_DATA_FOR_REHYDRATION__']){
  const s=document.getElementById(id);if(s){try{roots.push(JSON.parse(s.textContent))}catch(e){try{roots.push(JSON.parse(decodeURIComponent(s.textContent)))}catch(e){}}}
 }
 const visited=new WeakSet();let count=0;
 function walk(o){
  if(!o||typeof o!=='object'||visited.has(o)||++count>20000)return;visited.add(o);
  if(String(o.aweme_id||o.awemeId||'')===aid){
   const vid=o.video||o.videoInfo||{};
   for(const a of [vid.play_addr,vid.playAddr,vid.download_addr,vid.downloadAddr]){
    if(a){for(const u of (a.url_list||a.urlList||[])){if(typeof u==='string')bound.urls.push(u)}
     if(typeof a.uri==='string')bound.video_ids.push(a.uri)}
   }
   for(const id of [vid.vid,vid.video_id])if(typeof id==='string')bound.video_ids.push(id);
  }
  for(const child of Object.values(o))if(child&&typeof child==='object')walk(child);
 }
 for(const root of roots)walk(root);
 return {page_url:location.href,src:v.currentSrc||v.src,duration_s:v.duration,
         width:v.videoWidth,height:v.videoHeight,user_agent:navigator.userAgent,
         bound_media:{aweme_id:aid,urls:[...new Set(bound.urls)],video_ids:[...new Set(bound.video_ids)]},
         network_urls:performance.getEntriesByType('resource').map(e=>e.name)};
}"""


def identify_player(value):
    parsed = urlparse(value.get("page_url", ""))
    match = re.fullmatch(r"/video/(\d+)/?", parsed.path)
    if parsed.hostname not in {"douyin.com", "www.douyin.com"} or not match:
        raise ValueError("open_the_work_standalone_video_page_first")
    if value.get("error"):
        raise ValueError(value["error"])
    media = urlparse(value.get("src", ""))
    bound = value.get("bound_media") or {}
    direct = media.scheme == "https" and media.hostname and not media.username and not media.password
    if not direct and not (bound.get("aweme_id") == match[1] and (bound.get("urls") or bound.get("video_ids"))):
        raise ValueError("player_has_no_downloadable_full_https_url")
    if not isinstance(value.get("duration_s"), (int, float)) or not 0 < value["duration_s"] < 600:
        raise ValueError("player_duration_invalid_or_over_ten_minutes")
    return match[1]


def standalone_work_url(url):
    """Resolve the work already opened by Qwen; never infer an ID from truncated UI."""
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in {"douyin.com", "www.douyin.com"} or parsed.username or parsed.password:
        raise ValueError("current_page_is_not_douyin")
    match = re.fullmatch(r"/video/(\d+)/?", parsed.path)
    ids = [match[1]] if match else parse_qs(parsed.query).get("modal_id", [])
    if len(ids) != 1 or not ids[0].isascii() or not ids[0].isdecimal():
        raise ValueError("open_one_work_modal_before_resolving_its_page")
    return "https://www.douyin.com/video/" + ids[0]


def editing_searches(state):
    return list(dict.fromkeys(q for q in state.data["searches"] if "剪辑" in q))


class NetworkEvidence:
    def __init__(self, browser):
        self.browser, self.requests, self.enabled = browser, {}, set()
        self.completed = set()
        browser.cdp_client.register.Network.requestWillBeSent(self.request)
        browser.cdp_client.register.Network.responseReceived(self.response)
        browser.cdp_client.register.Network.loadingFinished(self.finished)

    def finished(self, event, session_id):
        self.completed.add((session_id, event.get("requestId")))

    def request(self, event, session_id):
        req = event.get("request", {})
        url = req.get("url", "")
        self.requests[(session_id, url)] = {"url": url, "type": event.get("type"), "observed_at": time.time(),
                                          "request_id": event.get("requestId")}
        redirected = event.get("redirectResponse")
        if redirected:
            old = redirected["url"]
            self.requests[(session_id, old)] = {"url": old, "redirect_url": url, "observed_at": time.time()}

    def response(self, event, session_id):
        resp = event.get("response", {})
        url = resp.get("url", "")
        row = self.requests.setdefault((session_id, url), {"url": url})
        row.update(status=resp.get("status"), mime_type=resp.get("mimeType"), request_id=event.get("requestId"))

    async def enable(self):
        session = await self.browser.get_or_create_cdp_session()
        if session.session_id not in self.enabled:
            await session.cdp_client.send.Network.enable(session_id=session.session_id)
            self.enabled.add(session.session_id)
        return session

    async def bind_observed_detail(self, value):
        """For a blob player, read only the actual same-work public detail response."""
        src = value.get("src", "")
        if src.startswith("https://"):
            return value
        try:
            aid = standalone_work_url(value.get("page_url", "")).rsplit("/", 1)[1]
        except ValueError:
            return value
        if (value.get("bound_media") or {}).get("urls") or (value.get("bound_media") or {}).get("video_ids"):
            return value
        session = await self.enable()
        for (sid, url), row in reversed(list(self.requests.items())):
            parsed = urlparse(url)
            if sid != session.session_id or parsed.path.rstrip("/") not in {"/aweme/v1/web/aweme/detail", "/aweme/v1/aweme/detail"} or parse_qs(parsed.query).get("aweme_id") != [aid]:
                continue
            request_id = row.get("request_id")
            if row.get("status") != 200 or (sid, request_id) not in self.completed:
                continue
            try:
                body = await session.cdp_client.send.Network.getResponseBody({"requestId": request_id}, session_id=sid)
                raw = body["body"]
                if body.get("base64Encoded"):
                    import base64
                    raw = base64.b64decode(raw).decode("utf-8")
                bound = parse_observed_detail(url, raw, aid)
            except (ValueError, KeyError, UnicodeError, RuntimeError):
                continue
            if bound:
                value["bound_media"] = bound
                value["bound_media_source"] = "observed_same_work_detail_response"
                break
        return value

    async def for_player(self, value):
        session = await self.enable()
        url = value["src"]
        observed = self.requests.get((session.session_id, url))
        if observed and url.startswith("https://"):
            return {"source": "cdp_network", **observed, "download_url": url}
        if url.startswith("https://") and url in value.get("network_urls", []):
            return {"source": "player_and_resource_timing", "url": url, "download_url": url}
        bound = value.get("bound_media") or {}
        aid = identify_player(value)
        if bound.get("aweme_id") != aid:
            raise ValueError("hydrated_media_work_id_mismatch")
        for candidate in bound.get("urls", []):
            parsed = urlparse(candidate)
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
                continue
            proof = self.requests.get((session.session_id, candidate))
            if proof or candidate in value.get("network_urls", []):
                return {"source": "work_bound_page_metadata_and_network", "aweme_id": aid,
                        "download_url": candidate, "network": proof or {"url": candidate, "source": "resource_timing"}}
        vids = set(bound.get("video_ids", []))
        for (sid, observed_url), proof in self.requests.items():
            parsed = urlparse(observed_url)
            if sid == session.session_id and parsed.scheme == "https" and not parsed.username and not parsed.password:
                if vids.intersection(parse_qs(parsed.query).get("video_id", [])):
                    return {"source": "work_bound_video_id_and_cdp_network", "aweme_id": aid,
                            "download_url": observed_url, "network": proof}
        raise ValueError("player_url_not_observed_in_actual_network")


async def run_browser(state, *, stage="screenplay"):
    # Completed results are local artifacts; do not reopen Douyin or request its credentials.
    if state.data.get("selection"):
        return await asyncio.to_thread(handoff, state, state.data["selection"], stage=stage)
    policy = getattr(prompts, "SEARCH_POLICY", "creative_utility_v1")
    if not state.data.get("policy_changes") or state.data["policy_changes"][-1]["policy"] != policy:
        state.data.setdefault("policy_changes", []).append({
            "policy": policy, "recorded_at": time.time(),
            "reason": "User requested queries with 剪辑 and filmable meaningful stories with editing beats",
            "at_step": state.data["steps"], "at_qwen_call": state.data["qwen_calls"]})
    state.data["status"] = "running"
    state.save()
    await asyncio.to_thread(restore_downloads, state)
    omni = QwenAPI()
    for row in state.data["candidates"].values():
        if row.get("media"):
            verify_cached(row["media"])
            await asyncio.to_thread(audition, state, row, omni)
    if len(state.reviewed) >= LIMITS["reviews"]:
        selection = await asyncio.to_thread(select, state, omni)
        return await asyncio.to_thread(handoff, state, selection, stage=stage)
    browser = new_browser()
    llm = make_llm(state)
    first_call = len(state.data["calls"])
    manifest = Manifest(state.output / "videos" / "manifest.json", state.output / "videos")
    try:
        await start_browser(browser)
        network = NetworkEvidence(browser)
        await network.enable()
        page = await browser.must_get_current_page()
        resume_page = state.data.get("resume_page")
        await page.navigate(standalone_work_url(resume_page) if resume_page else "https://www.douyin.com/")
        if not await authenticated(browser) or await gated(browser):
            await wait_login(browser, state, require_auth=True)
        tools = BrowseOnlyTools()

        @tools.action("Search Douyin using a model-chosen query. No popularity ranking.")
        async def search_douyin(query: str, browser_session: BrowserSession):
            if not query.strip() or len(query) > 100:
                return ActionResult(error="Choose a nonempty search query under 100 characters.")
            if "剪辑" not in query:
                return ActionResult(error="Include 剪辑 in the model-chosen query. Seek easy-to-film meaningful stories and learnable editing beats.")
            page = await browser_session.must_get_current_page()
            await page.navigate("https://www.douyin.com/search/" + quote(query.strip(), safe="") + "?type=video")
            if query.strip() not in state.data["searches"]:
                state.data["searches"].append(query.strip())
                state.save()
            return ActionResult(extracted_content="Search opened. Observe and compare actual videos.")

        @tools.action("Open the exact standalone page of the video Qwen already opened in a search modal. Do not type a truncated ID.")
        async def open_current_video_page(browser_session: BrowserSession):
            try:
                url = standalone_work_url(await browser_session.get_current_page_url())
            except ValueError as exc:
                return ActionResult(error=str(exc))
            page = await browser_session.must_get_current_page()
            await network.enable()
            await page.navigate(url)
            return ActionResult(extracted_content="Current model-chosen work opened: " + url + ". Observe playback, then capture_candidate if useful.")

        @tools.action("Pause for human Douyin login or verification, without model requests.")
        async def wait_for_login(browser_session: BrowserSession):
            state.data["pause_requested"] = True
            state.save()
            return ActionResult(extracted_content="Human pause scheduled before the next model step; no captcha-solving actions.")

        @tools.action("Capture current standalone video, download its real player URL, and request a full Omni audition.")
        async def capture_candidate(preview_reason: str, browser_session: BrowserSession):
            state.check_time()
            if len(state.reviewed) >= LIMITS["reviews"]:
                return ActionResult(extracted_content="Full-video audition budget reached. Finish browsing; Omni will select.")
            page = await browser_session.must_get_current_page()
            value = {}
            for attempt in range(3):
                value = await network.bind_observed_detail(json.loads(await page.evaluate(PLAYER_JS)))
                if not value.get("error") and isinstance(value.get("duration_s"), (int, float)) and value["duration_s"] > 0:
                    break
                await asyncio.sleep(2)
            try:
                aid = identify_player(value)
            except ValueError as exc:
                # Even missing media is a real observed candidate when its work ID is known.
                observed_page = value.get("page_url") or await browser_session.get_current_page_url()
                match = re.search(r"/video/(\d+)", observed_page)
                if match:
                    state.candidate({"aweme_id": match[1], "page_url": observed_page,
                                     "status": "skipped", "failure": str(exc), "preview_reason": preview_reason})
                return ActionResult(extracted_content="Acquisition unavailable: " + str(exc) + ". Continue finding another video.")
            candidate = {"aweme_id": aid, "page_url": "https://www.douyin.com/video/" + aid,
                         "duration_s": value["duration_s"], "preview_reason": preview_reason,
                         "status": "observed", "searches": list(state.data["searches"])}
            if not state.candidate(candidate):
                return ActionResult(extracted_content="This work is already recorded. Find a different candidate.")
            try:
                candidate["network_evidence"] = await network.for_player(value)
                url = candidate["network_evidence"]["download_url"]
                candidate["download_url"] = url
                state.save()
                with download_session() as session:
                    downloader = DirectCDNDownloader(state.output / "videos", user_agent=value["user_agent"],
                                                     referer=candidate["page_url"], timeout=60, session=session)
                    result = await asyncio.to_thread(downloader.download_video,
                        DownloadItem(aweme_id=aid, url=url, media_type=4))
                if not result.success:
                    raise ValueError(result.error)
                media = await asyncio.to_thread(prepare, result.video_path, expected_duration=value["duration_s"])
                candidate.update(media=media, status="verified")
                manifest.record_success(aid, url=url, title=None, video_path=Path(media["original_path"]),
                                        file_size=Path(media["original_path"]).stat().st_size)
                manifest.save()
                state.save()
            except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
                candidate.update(status="skipped", failure=str(exc)[:500])
                manifest.record_failure(aid, url=value.get("src", ""), title=None, stage="acquisition", error=str(exc))
                manifest.save()
                state.save()
                return ActionResult(extracted_content="Download/verification failed; recorded. Find another candidate.")
            await asyncio.to_thread(audition, state, candidate, omni)
            return ActionResult(extracted_content=f"Verified full video {aid} and completed Omni audition. "
                                f"Full auditions: {len(state.reviewed)}/{LIMITS['reviews']}. "
                                "Compare other candidates; Omni will make final selection.")

        async def before_step(agent):
            if state.remaining_seconds() <= 120 or state.data["qwen_calls"] >= LIMITS["qwen_calls"]:
                state.data["browsing_stop_reason"] = "budget_reserved_for_comparison"
                state.save()
                agent.state.stopped = True
                return
            await network.enable()
            if state.data.pop("pause_requested", False) or not await authenticated(browser) or await gated(browser):
                await wait_login(browser, state, require_auth=True)
            try:
                state.data["resume_page"] = standalone_work_url(await browser.get_current_page_url())
                state.data["resume_page_source"] = "Exact model-opened current work URL at browser-step checkpoint"
            except ValueError:
                state.data.pop("resume_page", None)
                state.data.pop("resume_page_source", None)
            state.step()

        async def should_stop():
            if state.remaining_seconds() <= 120 or state.data["qwen_calls"] >= LIMITS["qwen_calls"]:
                return True
            if len(state.reviewed) >= LIMITS["reviews"] and len(editing_searches(state)) >= 2:
                return True
            return len(state.data["candidates"]) >= LIMITS["candidates"]

        agent = Agent(task=prompts.TASK + "\n已记录状态（不要重复）：" + json.dumps({
            "candidate_ids": list(state.data["candidates"]), "searches": state.data["searches"],
            "current_policy_searches": editing_searches(state),
            "resumed_current_work": resume_page,
            "remaining_steps": max(0, LIMITS["steps"] - state.data["steps"]),
            "remaining_full_auditions": LIMITS["reviews"] - len(state.reviewed)}, ensure_ascii=False),
            llm=llm, browser=browser, tools=tools, use_vision=True, use_thinking=False,
            use_judge=False, max_actions_per_step=1, max_failures=1, final_response_after_failure=False,
            enable_planning=False, message_compaction=False, enable_signal_handler=False,
            file_system_path=str(state.output / "browser_files"), register_should_stop_callback=should_stop,
            llm_timeout=150, step_timeout=2700)
        history = await agent.run(max_steps=max(1, LIMITS["steps"] - state.data["steps"]), on_step_start=before_step)
        history_path = state.output / f"browser_history_{state.data['steps']:03d}.json"
        agent.save_history(history_path)
        state.checkpoint()
        if any(c["status"] in {"submitted", "rejected"} for c in state.data["calls"][first_call:]):
            raise DiscoveryStopped("model_request_failed_or_outcome_unknown_check_calls")
        if len(editing_searches(state)) < 2:
            raise DiscoveryStopped("fewer_than_two_model_chosen_search_directions")
        selection = await asyncio.to_thread(select, state, omni)
        return await asyncio.to_thread(handoff, state, selection, stage=stage)
    finally:
        # Close only this dedicated browser; the profile preserves login cookies.
        await browser.kill()


async def login():
    browser = new_browser()
    try:
        print("专用抖音 Chrome：" + ("直连（不修改系统代理）" if direct_connection() else "使用系统代理"), flush=True)
        await start_browser(browser)
        page = await browser.must_get_current_page()
        await page.navigate("https://www.douyin.com/")
        if not await authenticated(browser) or await gated(browser):
            await wait_login(browser, require_auth=True)
        print("抖音登录态已保存在本地专用配置。", flush=True)
    finally:
        await browser.kill()
