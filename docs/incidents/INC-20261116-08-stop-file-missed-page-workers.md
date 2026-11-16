# INC-20261116-08 停止标记管不到目录分页子任务，停掉任务后留下 2 个结果未知的请求

| 项 | 内容 |
|---|---|
| 发现日期 | 2026-11-16 |
| 影响范围 | scene_v1 素材浏览的目录分类：分页子任务各有独立账本。"安全停止"的流程会因此失效；属于运维阻塞，同时浪费了 GLM 调用次数 |
| 状态 | 已修复（release scene_v1_schedule_494a1498343f）；2 条未结记录原样保留，没有重发 |
| 相关提交 | 未提交 |
| 相关任务 | 薛甄珠带题粗看实验 fast_resume_20261116_073524（已停止） |

## 1. 问题长什么样
- 为了把实验换成新版本，先在任务目录放了 `mcp_stop`。主账本 0 次调用、`current_status.requests=0`，Claude 据此判断"没有在途请求"，执行了 `systemctl --user stop`。
- 之后启动器的安全检查拒绝在这个父任务上续跑（`launch_reviewed_template` 远程第 27 行）。原因是 `autonomous_material/browser/pages/page_019`、`page_020` 两个子账本里各有 1 条 `submitted`：`glm_001_material_scene_browser_scan_19`、`scan_20`。两条的 agent_events 都只有 `step_start`，没有回复，说明已经发给 GLM，但被停在生成过程中。

## 2. 怎么查出来的
- 用启动器同样的检查逐个扫描子账本，找到这 2 条；再读 agent_events 确认它们已开始生成、没有回复。
- 定位代码：`pipeline.CodexMCP._submit` / `_submit_text` 和 `opencode_provider.OpenCodeMCP._submit` 只检查 `self.output/'mcp_stop'`。分页子任务的 `output` 是 `pages/page_NNN`，看不到主任务的停止标记。
- 为什么会有新请求：INC-20261116-01 修复后，目录从 1389 个窗口变成 1384 个，最后两页的内容变了，原来的分类结果对不上，只能重新分类。

## 3. 根因
- 代码问题：停止机制没有覆盖隔离子账本。
- 操作问题：Claude 只检查了主账本就判断"没有在途请求"，漏看了子账本。

## 4. 解决方案
- `CodexMCP._stop_requested()`：先看本目录的 `mcp_stop`；如果子账本的 input_lock 写着 `configuration.browser_catalog_page.parent`，再看父任务目录的 `mcp_stop`。三处提交前的停止检查都改用它。
- 2 条未结记录不改、不重发。实验改为冷启动（不挂在这个父任务下），不会碰到这两条记录。
- 操作规则：以后停任务前，检查范围要和启动器一致，扫描任务目录下全部 `library_state.json`。

## 5. 为什么不用其他方案
- 手工把 2 条改成 failed_known：属于篡改账本，没有对账依据。
- 在原父任务上续跑：启动器检查会拦下，这道检查是正确的，不应绕过。
- 等 GLM 自然返回再停：进程已经停了，等不到回复。

## 6. 怎么验收
新增测试 `test_child_ledger_stop.py`（2 项）。服务器全量测试 197 项通过。

## 7. 量化数据
浪费 2 次 GLM 调用（按次计费，结果未知），Qwen 没有花费。实验改冷启动，额外增加约 1 小时（读参考和 20 页目录分类）。

## 8. 经验与后续
- 凡是有隔离子账本的并发阶段，停止和对账都要覆盖子账本。
- 遗留：2 条 `submitted` 记录留在 `fast_template_20261115_080047_21_薛甄珠吵架_01_f42891d19811/creative/evaluations/fast_resume_20261116_073524/` 下。这个父任务以后不能再作为续跑起点，除非用户同意某种对账方式。
