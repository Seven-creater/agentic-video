# 服务器真实剪辑测试：2026-10-09

用户在素材上传完成后授权服务器端真实测试，随后明确授权自动修复工程错误、同步 GitHub 与服务器，以及审核实际成片。本轮与旧本地 Goal 分开，MiniMax 与抖音路线仍冻结。

## 输入与执行边界

- 任务目录：`/home/ubuntu/apps/agentic-video/shared/runs/server_edit_test_20261009`。
- 初始 release：`5b5e22b6fc08902bc0fdfc56a7cc82912a56f229`。
- 后台 job：`c3ad5767191d4e2b8d3863d7fc8dccdc`，启动于北京时间 00:11:37；独立检查确认发起 SSH 退出后仍运行。
- 使用已 SHA 校验的固定参考和三部电影。当前参考复用原 received060 子对象，保留其覆盖报告与模型估计限制，不重发 unknown131 的完整参考。
- GLM 自主检索、观察、分段和选择入出点；启用已有 `active_fine_cut_v1`、semantic-audit 与 editing-v2。最多两轮候选，每阶段一次格式修复，没有数值视觉请求总上限。
- 原 274-call 迁移基线与 unknown004/131/166/265 保持不变；新视觉作业与 OpenCode 聊天用量另记。不删除历史、不重放未知请求、不自动新开一轮。

## 实际遇到的输出截断

`glm_009_search_0` 的唯一原始 POST 返回 HTTP200，耗时 436.253 秒，但 JSON 未闭合，因此 received 不等于 parsed。

原始平台数据：`finish_reason=length`、completion_tokens=16,384，其中 reasoning_tokens=16,124；最终 content 只有 347 字符。这证明输出上限耗尽并截断 JSON，不是连接异常或已证明的套餐额度问题。没有分段计时，不能确定耗时由哪一部分产生。

实际官方 `@z_ai/mcp-server@0.1.5` 在 `visionCompletions` 内硬编码 `thinking.type=enabled`，工具 schema 与配置均无原生关闭入口。保持官方包和请求正文不改，不能用臆造环境变量关闭思考。[官方配置文档](https://docs.z.ai/devpack/mcp/vision-mcp-server#environment-variable-configuration)

唯一修复 `glm_010_search_0_repair` 已 HTTP200、完成解析；任务随后进入自主选择的连续窗口精看。原始失败、token 用量与回复保留。本记录此处不声称已出片或质量通过。

## 前向工程修正

1. 搜索提示的窗口上限取实际 `max_windows_this_round`，修复文字 12 与当前校验 8 的矛盾；输出模板先列完整 windows，再列简短 reason。必要字段、证据和上下文保留。
2. 协议失败若实际回复标记 `length`，新诊断记录 completion/reasoning tokens 与 content 字符数；唯一修复提示要求减少重复论述、保留必需结构。原始异常和一次修复边界不变，历史请求及失败文件不覆写。

这些修改改善约束一致性和可诊断性，不证明已解决官方模型思考预算占比，也不改变正在运行的旧版本进程。代码部署时先准备新 release，待当前任务结束后验证安装并切换，避免混用版本。

本地相关回归：287 passed、1 个 Windows 不适用的 POSIX 检查 skipped；包括原失败／唯一修复缓存恢复、未知结果禁止重放、实际窗口上限、OpenCode guard、服务器进程管理与端到端合成媒体执行。合成测试不构成 GLM 剪辑质量证据。

后续真实成片、SHA、审核与部署结果在本文件追加。
