"""Small real local-page smoke test; synthetic media never becomes a reference."""
import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading

from browser_use import ActionResult, Agent, BrowserSession

from ..pipeline import write
from .browser import BrowseOnlyTools, new_browser, start_browser
from .llm import make_llm
from .prompts import FIXTURE_TASK
from .state import DiscoveryStopped

HTML = """<!doctype html><html lang="zh"><meta charset="utf-8"><title>Local browser test</title>
<style>body{font:24px sans-serif;margin:40px;background:#fff;color:#222}button,input{font-size:24px;padding:14px}
.shapes{display:flex;gap:120px;margin:35px}.shapes i{display:block;width:110px;height:110px}</style>
<h1>本地浏览测试</h1><div class="shapes"><i style="background:blue;border-radius:50%"></i><i style="background:orange"></i></div>
<input id="query" type="search" placeholder="输入测试搜索词"><button id="search" onclick="window.searched=!!document.getElementById('query').value;document.getElementById('results').hidden=false">搜索</button>
<div id="results" hidden><button onclick="window.opened=true;document.getElementById('player').hidden=false">打开测试视频</button></div>
<div id="player" hidden>合成测试视频页面已打开</div><div style="height:1500px"></div>
<p id="bottom">页面底部：完成后调用 finish_fixture</p></html>"""


async def smoke(state):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    browser = new_browser(fixture=True)
    tools = BrowseOnlyTools(fixture=True)
    completed = {}
    try:
        await start_browser(browser)

        @tools.action("Finish local smoke test after search, video opening and scrolling. Report the observed left/right colors and shapes.")
        async def finish_fixture(left_color: str, left_shape: str, right_color: str, right_shape: str,
                                 browser_session: BrowserSession):
            page = await browser_session.must_get_current_page()
            actual = json.loads(await page.evaluate("() => ({searched:!!window.searched,opened:!!window.opened,bottom:scrollY+innerHeight>=document.documentElement.scrollHeight-80})"))
            actual["visual_reading"] = {"left_color": left_color, "left_shape": left_shape,
                                        "right_color": right_color, "right_shape": right_shape}
            valid = (left_color.lower() in {"blue", "蓝色", "蓝"} and left_shape.lower() in {"circle", "圆形", "圆"}
                     and right_color.lower() in {"orange", "橙色", "橙"} and right_shape.lower() in {"square", "正方形", "方形"})
            if not all(actual[k] for k in ("searched", "opened", "bottom")) or not valid:
                return ActionResult(error="Local checks did not pass. Complete the missing page actions and observe shapes again.")
            completed.update(actual)
            write(state.output / "fixture_checks.json", actual)
            return ActionResult(is_done=True, success=True, extracted_content="Local browser smoke passed.")

        async def before(agent):
            state.step()
        agent = Agent(task=FIXTURE_TASK, llm=make_llm(state), browser=browser, tools=tools,
                      initial_actions=[{"navigate": {"url": f"http://127.0.0.1:{server.server_port}/"}}],
                      use_vision=True, use_thinking=False, use_judge=False, max_actions_per_step=1,
                      max_failures=1, final_response_after_failure=False, enable_planning=False,
                      message_compaction=False, enable_signal_handler=False, llm_timeout=150,
                      file_system_path=str(state.output / "browser_files"))
        await agent.run(max_steps=12, on_step_start=before)
        agent.save_history(state.output / "browser_history.json")
        if not completed:
            raise DiscoveryStopped("local_browser_smoke_not_completed")
        return state.result(status="browser_smoke_passed", checks=completed)
    finally:
        await browser.kill()
        server.shutdown()
        server.server_close()
