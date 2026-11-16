# INC-20261116-09 找素材时一个候选窗口被接口拒绝（400:1210），整个任务停机

| 项 | 内容 |
|---|---|
| 发现日期 | 2026-11-16 |
| 影响范围 | scene_v1 素材浏览的看片阶段。任何一个候选窗口被接口拒绝，整个任务都会停；属于致命停机 |
| 状态 | 已修复（release scene_v1_schedule_80c0227b86bf），薛甄珠实验续跑中 |
| 相关提交 | 未提交 |
| 相关任务 | 薛甄珠带题粗看实验 fast_resume_20261116_103300 |

## 1. 问题长什么样
`provider_request_rejected:glm_040_material_scene_browser_blind_vl_619bcb9880042560:400:1210`。`agent/mcp_server.log` 原文：`HTTP 400: {"error":{"code":"1210","message":"Invalid API parameter, please check the documentation."}}`。随后 agent 自动重试，被 `mcp_guard.mjs:382` 按"同一请求只能发一次"拦下（`library_mcp_retry_or_budget_blocked`）。

## 2. 怎么查出来的
- 用 ffprobe 对比被拒视频和同一轮中被接受的两段视频：编码 h264 High、640×360、12fps、yuv420p，三者完全一致；时长 17 秒，大小 2.17MB（被接受的是 2.24MB 和 2.64MB）。提示词都是 1046 字。
- 以往问题记录里没有出现过 1210。
- 结论：被拒的原因无法从我方确定，可能和这段画面内容有关，也可能是接口偶发；但拒绝本身是明确的（`uncertain:false`，有 `provider_rejection.json`）。

## 3. 根因
素材浏览把"某一个候选窗口被接口明确拒绝"当成整个任务的致命错误。实际上这只意味着少了一个候选。

## 4. 解决方案
- `MaterialBrowser.watch()`：遇到 `provider_request_rejected:` 或 `provider_content_rejected:`，把这个窗口记入 `unwatchable`（写入 `unwatchable.json` 和 artifact `browser_window_provider_refused`），返回"已拒绝"标记，不再停机。其他失败（结果未知、本地错误）仍然照常停机。
- `prior_refusal()`：续跑时，如果任一祖先账本里这个窗口的 masks/blind/blindband 阶段已经是 `failed_known`，并且有 `provider_rejection.json`，就直接视为被拒，不再发送同样的请求。
- 连续性复核、待看列表和最终候选都排除这些窗口。GLM 的决策提示里会列出 `provider_refused_windows`，说明这些窗口不能看、也不能当候选。
- 适用于任何模板、任何素材窗口，没有针对具体窗口或模板的特殊处理。

## 5. 为什么不用其他方案
- 自动重试同一请求：违反"不重发同一请求"的守卫规则，而且 1210 是否稳定复现并不清楚。
- 对这段视频重新编码再试：没有证据表明是格式问题，三段视频的格式完全一样。
- 当作参考视频被拒、换参考（1301 规则）：被拒的只是候选素材中的一个窗口，不是参考视频。

## 6. 怎么验收
新增测试 `test_browser_provider_refusal.py`（3 项：被拒窗口跳过且不进入连续性复核；其他失败照常停机；续跑不重发已记录的拒绝）。服务器全量测试 203 项通过。实跑结果待补。

## 7. 量化数据
被拒 1 次，自动重试被守卫拦下 1 次。实验停机前已完成 40 次新调用。

## 8. 经验与后续
如果同一个窗口反复被拒，或者被拒的窗口越来越多，需要再查接口侧的原因（比如画面内容）。
