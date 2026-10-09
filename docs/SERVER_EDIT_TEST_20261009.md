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

## 原任务已知失败与容量恢复实现

原 job 于北京时间 01:42 停止，exit1。23 次视觉作业全部 received，无 pending/uncertain，无渲染。八个连续精看窗口已完成；其中窗口 2、4、8 的首答存在协议错误，各自唯一修复成功，原记录均保留。

`glm_022_plan_0` 与 `glm_023_plan_0_repair` 均实际 HTTP200、`finish_reason=length`、空 content、completion_tokens=16,384；reasoning_tokens 分别 16,365 与 16,363。这是可核验的生成容量失败，不是已证明的 GLM 剪辑能力失败或套餐额度问题。

用户已授权自动工程修复并继续。新实现只登记一次 `opencode_known_output_starvation_recovery_v1`：在原目录保存 state 基线、全部旧 call 文件 SHA、旧 HTTP journal 前缀 SHA 和原控制文件；复用八个窗口，用原 GLM 规划提示加通用容量说明，调用精确别名 `plan_0_capacity_v2`。原计划的两个失败不改为成功、不重发其原请求；新别名独立登记且最多一次格式修复。未知请求始终禁止重放。

输出容量提高到官方原生配置 32,768 tokens / 1,200 秒，仍保留原 input lock、16 个窗口和两轮候选。后台恢复有独立 token/run.lock，原 job、原 failure.json 与旧调用不可覆写；恢复失败另存文件。代码修复及合成测试不代表已生成成片，实际恢复结果另行追加。

本地与 CI 相同的离线检查组合：365 passed、3 个 Windows 不适用检查 skipped；新增容量恢复专项 65 项均通过。测试包括唯一修复／缓存／未知结果不重放、Python 与 JS 原始记录校验、原生输出容量、旧 HTTP 前缀追加保护、专用失败文件和后台恢复控制。另一只读协作检查未发现当前路径的发布阻断项。上述均未调用真实模型。

## 第一次恢复启动的 CPU 阻断

修复 release `0846a72` 已直连部署；Linux wheel 环境 383 项通过，排除仅适用 editable 的布局断言，并另通过正式 wheel 安装检查、四 CLI help、pip check 与 doctor。发起 SSH 的服务器观察源仍为国内 `222.247.37.43`。

恢复控制 `6d78d7a9c12d4dc3b048720d30d0c794` 在北京时间 08:56:43 启动后于 CPU 预检 exit1：`library_file_set_changed`。原因是初建库存只纳入媒体扩展名，而缓存恢复把 `.upload.lock` 旁文件也算入集合。三部电影的路径、大小、mtime 和缓存未改变；这次还没有进入模型或修改调用台账，仍 23 received、零渲染。原容量授权、第一次恢复控制/日志/run.lock 保留。

前向修复使缓存集合检查采用与初建库存相同的 `MEDIA_EXTENSIONS`。17 项针对旁文件、增删真实媒体、同名媒体 mtime 变化的回归通过。只有这次已知、零新调用的 CPU 阻断可登记一次 `opencode_capacity_catalog_preflight_fix_v1`，用独立 `output_capacity_v1_preflight_fix` 控制继续**同一未使用的容量授权**；不增加任何模型请求授权、别名、候选轮次或精看额度。新控制绑定第一次失败 job/log/run.lock，旧控制和原 23 次请求不重写。真实恢复结果仍待后续追加。

## 容量修正的实际结果与剩余初始候选

`8fb1390` 的正式 Linux wheel 检查 438 passed、1 个 editable 专属断言 deselected；另有 wheel 安装检查和 doctor。控制 `e3a3e623b0c64cfbbfba677ca5915bb0` 在北京时间 09:04:40 启动、09:15:47 exit1。国内 SSH 观察源仍为 `222.247.37.43`。

24 `plan_0_capacity_v2` 实际 HTTP200/stop，completion20,350、reasoning16,461，content9,638 字符；存在末尾 JSON 多余字符，原失败保留。唯一修复25也 HTTP200/stop，completion8,718、reasoning3,731，content12,480 字符；JSON 可解析，但 `plan:caption_event_outside_selected_range` 校验失败。容量修正已让正文完整返回，不能据此宣称素材选择正确。

具体冲突是模型 `seg_1` 自选原片832–839秒，窗口offset790，对应local42–49；其字幕引用的event3却在local35–38，两者完全不重叠。程序不会改成另一个事件编号、修改字幕或替换切点。旧25次均received，无pending/uncertain，仍八个完成窗口、零渲染。

原测试允许两候选，第一份方案现在明确记为 rejected/no render。`opencode_remaining_initial_candidate_feedback_v1` 只使用尚未执行的候选1，**没有第三候选、新别名或额外格式修复**。登记保存25-call原始state、已看窗口快照、原控制/失败/调用文件SHA，将GLM自己的失败方案及区间交集诊断送作反馈。跳过候选0，保留16窗口和两渲染上限；后续实际观察仍由GLM选择，真实局部精剪和实际成片审阅保持原契约。

固定控制名 `remaining_candidate_v1`、独立token/run.lock；新失败文件 `failure_remaining_candidate_v1.json`。所有旧失败和控制保留；原25-call前缀不可改，未知请求仍不可重放。此处仅描述实现和已知失败，尚无新成片或质量验收结论。

新恢复路径的本地 CI 离线组合497 passed、3个Windows不适用检查 skipped。端到端合成执行证明跳过失败的round0、复用八窗口、仅render_1、selected_round/review_1一致，失败另存文件。损坏窗口在写入登记前拒绝，旧调用/失败/控制保持。这些检查没有真实模型质量含义。只读提示与校验交叉检查确认字幕区间要求原先已明确；前向精剪提示另说明 retained 保留原源区间、改切点须用 replaced，不改旧方案、旧提示或任何validator。

`79e7e1d` 正式 Linux wheel：598 passed、1个editable专属断言deselected，pip check、13个打包资源、四CLI及doctor通过；wheel SHA `1e75d9f4c84be0b7495f27fc121cc23d51159decad1037178cdc1dcd7968a4c8`。登记前额外绑定原watched_windows bytes，防止时间offset等元数据变动后产生错误诊断；服务器原八窗口SHA实际一致。该项前向补测5 passed，精剪契约/执行80 passed。

current原子切换到79e7e1d；后台控制 `73d38b19d3e74d85ad7568ba3b1c736d` 于北京时间09:35:26启动remaining_candidate_v1，原25-call前缀复核一致。已登记26 search_1，使用原定候选1；此启动快照还不是实际POST回执、渲染或质量结果。新模型结果继续追加。

实际26在09:42:14返回HTTP200并解析，选择四个新的90秒窗口，优先补足可见对抗/动作结果和日常收束；这是GLM自己的缺项判断，不是Codex提供切点。真实请求max_tokens=32,768、thinking仍为官方enabled，只有一次POST；旧25-call前缀和input lock复核一致。后续进入27 fine观察；此处仍未产生渲染或质量结论。

GitHub [79e7e1d 的 Windows job](https://github.com/Seven-creater/agentic-video/actions/runs/37870325131/job/113626682144) 实际失败：`test_server_capacity_recovery.py` 的 Node subprocess 默认cp1252读取UTF-8中文JSON，reader thread UnicodeDecodeError令stdout为None。四个失败共享同一helper，Linux检查不受此问题影响。最小修复仅给测试helper指定UTF-8，并增加强制默认cp1252的真实Node回归；未改生产管线或运行中的release。完整CI清单强制cp1252：501 passed、3个平台skips。新GitHub结果须另验，不能用本地通过代替。
