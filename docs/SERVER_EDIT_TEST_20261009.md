# 服务器真实剪辑测试：2026-10-09

## 最新实际结果：147秒粗剪已渲染，36在视觉POST前停止

`one_chain_e2e_v1`实际job在2026-10-09 12:16（北京时间）exit1停止。
已完成原GLM147秒草稿的可播放渲染；SHA
`d9503944ccb1f95c87f2a42f20c23e56b9a673b6e677329aa56794736245c68a`，
文件47342786字节，已通过国内直连SSH下载并验证同一SHA。

独立本地CPU审核已完整解码4410帧，视频PTS从0至146.966667秒严格递增；
音频可解码至147.008秒。这里只验证媒体执行与信号存在，不证明内容连贯或音乐节拍。
记录：`runs/server_edit_test_20261009/audit/one_chain_rough_cpu_20261009.json`。

36号`chain_e2e_v1_rough_blind`失败于视觉POST之前。官方MCP原始错误为
`server_chain_authorization_old_file_changed`，只有旧根目录`mcp_server.log`变化：
原2726312字节前缀SHA仍为
`c819bb106a0747b5eaf0b2a8e5c943710119dac7e8afdb10a930aebae18ac2fe`，
启动时追加6782字节。这是运行日志与不可变历史保护冲突，不能解释成GLM不会审看/剪辑。
当前36条＝35received＋1failed_known，36号HTTP记录为空；OpenCode执行端聊天用量另行保留。

前向修复将官方MCP stderr改到`calls/<job>/agent/mcp_server.log`，保留原共享日志；
同时保留官方嵌套错误而非仅记录`opencode_no_original_vision_reply`。对应34项测试通过。
没有重发36、修改原失败状态、恢复Goal或启动额外轮次，尚无精剪或联合质量通过。

实际粗剪本地完整路径：
`C:\Users\29785\Desktop\omni-autonomous-screenplay\runs\server_edit_test_20261009\delivery\server_rough_147s.mp4`

服务器完整路径：
`/home/ubuntu/apps/agentic-video/shared/runs/server_edit_test_20261009/artifacts/one_chain_e2e_v1/rough/final.mp4`

## 最新授权：同一任务接通实际粗剪 → 技能精剪

用户在历史回顾后明确要求“直接到服务器上端到端测试一遍，看哪里错了，就修改”，
并继续。追加 `one_chain_e2e_v1`：保留已完成检索、12个观察窗口和35条已知回复，
使用GLM32原始147秒草稿作为粗剪编辑表，先渲染实际视频、静音盲读、审核内容和人物连续性。
内容可用时，自动送入通用skill整体观察与模型选择的局部连续片段/真实PTS精看，
精剪时长绑定参考21.933333秒，而非旧模型自定的150秒。

新授权只允许1次粗剪、最多2次精剪渲染和1次实际证据修订，格式错误各最多修复一次。
旧152秒方案、34/35失败及所有原控制/HTTP记录保留；旧wrapper补丁不登记或部署。
无新候选搜索、未知重放或Goal恢复。此节是实施范围，实际启动和输出另行追加。

服务器首次验证release `ef55be0`：正常wheel安装、pip check成功，394项通过，
1项旧visual_story_trial回归失败。原因是局部网格标签写死
`C:/Windows/Fonts/arial.ttf`，Ubuntu不存在该路径。改用Pillow内置跨平台字体，
标签仍只表示真实源时间；原有PNG缓存不重写。本地对应新旧精剪46项通过。
这次失败在部署检查阶段，未切换current或调用模型；服务器仍35条已知回复、零渲染。

实际修正版本`4ff6385`在Ubuntu完成395项检查、正常wheel安装核验和doctor，均成功。
2026-10-09 12:12（北京时间）启动同一任务的`one_chain_e2e_v1`，
job `76f08daefdb1489bbdd1e91cf80c2e31`；后台supervisor `991599`运行中，
先进入`rendering_actual_rough`。授权SHA（JSON）
`3bd62bc9724912ab3ced542b3c67cf1e600b49d8bb8435f284aec9516eb94c9c`，
文件SHA`5dbe4b2e255196fe358d66e96d64562bdd61c5ed2e14f8f499f9e13a84db5a5a`。
本次SSH服务端记录国内来源`222.247.225.66`，旧35条仍全部received；
启动快照尚无新模型请求或新完成视频。GitHub后续`2782eee`仅补可选媒体依赖缺失时
跳过相关测试，生产Python/JS与已验证部署版本相同。真实结果仍需后续日志与视频验证。

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

## 剩余候选的真实规划与精剪进度

`59a978d` 的真实 [GitHub Actions](https://github.com/Seven-creater/agentic-video/actions/runs/37871342321) Windows、Ubuntu 均成功，包括测试与 wheel 构建、安装检查。该 release 已在服务器安装并通过针对性82项检查、1项editable专属断言deselected；生产代码与已完整验证的79e7e1d相同。运行中的后台任务仍绑定79e7e1d，不切换其环境或重启任务。

26选择的四个90秒窗口依次由27–30观察完成，每次首答均解析通过；已完成窗口共12个，仍在原16个上限内。31 `plan_1` 实际HTTP200并返回JSON，但所选末段跨越两个不同人物支持范围，`plan:range_not_supported_by_fine_observation` 拒绝该方案。模型自选源区间4978–4990、窗口offset4920，对应local58–70；其请求人物在local31–60支持范围内可用，而local60–70只支持村民。程序没有合并证据范围、改人物ID或人工裁点。

32是该阶段唯一格式/契约修复，实际HTTP200、parsed且完整计划校验通过。GLM自主将末段拆分，最后一段4980–4990只引用村民。已接受的是17段、5个slot、147秒的**中间故事草稿**，audio_mode=source；不是最终精剪或交付成片。`draft_plan_1.json` SHA为 `e01dfbdc14fef71260f46c692fde7cd0638e2d24fcd135bef380d43c048c2ceb`。31原始回复和失败保留，32不将31追改为成功。

北京时间10:15:27登记33 `finecut_1`，10:15:39.950实际POST；截至10:20:59仍submitted，32 received、1 submitted、无uncertain。接下来依原协议精炼草稿，独立观察最终选中源片段，再渲染并审看实际成片。这份进度记录没有新渲染或质量结论。只读复核确认原25-call前缀及登记保护文件全部保持一致；不额外开启候选、请求修复或自动循环。

33于北京时间10:25:08.995实际HTTP200，首答解析和现有协议校验通过；`finecut_1.json` SHA `71e37576ca3249394010891f2607100ab0aa1b125a307d3780215baddc462ab8`。但模型将duration.target_s自行设为150，total_s=152，远超参考21.933333秒，也比147秒草稿更长。17段中16段源区间保持不变，另一段扩大5秒；草稿已有的0.5倍慢放保持，未选择加速或尾帧停留。这是**时长目标与精炼未达到要求的模型方案**，不能因parsed、采用慢放或程序未报错就称精剪成功。

现有前向协议只限制总时长180秒、允许模型自定target和超目标理由；实际目标约束与用户要求脱节。将该工程缺口与素材选择/实际成片语义问题分开保留。当前唯一候选仍按原版本进入最终源片段独立核验：北京时间10:25:24登记34 `semantic_slice_1_dac50d617957d391`，10:25:37.465实际POST；此时无渲染。未人工改33的方案、加入额外重规划、改变当前任务的规则或重新提交旧请求。

新 `plan_evidence_diagnostics_v1` 仅向前增加确定性反馈：保留原错误字符串和严格谓词，给出失败segment/window、原source/local区间、所需人物、已观察usable区间及字幕引用的原事件区间；唯一修复据此由模型自己调整。旧failure和已保存repair请求字节不覆写，最多两attempt不变。新增22项及合同/队列/provider/remaining相关回归共222项通过，另有只读交叉审查。这些合成验证没有模型质量含义，也不改变当前79e7e1d任务。

## 35-call停止与不确定性容器错误

控制73d38b19d3e74d85ad7568ba3b1c736d于北京时间10:28:58.305895 exit1。34原观察与35唯一修复均实际HTTP200，35回执时间10:28:45.956，失败同为 `semantic/uncertainties:text_required`。35次全部received，无pending/uncertain、零渲染；supervisor已退出。累计 captured usage为prompt1,218,109、completion347,354，旧274迁移基线单独保留，不能把视觉作业数等同于OpenCode聊天总请求数。

旧默认slice提示没有列出uncertainties类型。34返回 `[{"description":原文字}]`，唯一修复35改为 `[{"text":同一原文字}]`，严格协议要求string[]，所以仍失败。两回复的metadata、人物、五条typed证据与两条根级inference完全相同；不是网络丢失，也不是已确认的视觉动作错误。35 response bytes SHA `cb2445c9a29c4c0baf1a0cc40c91c25650c65764ffcfb1aac5088a4beb2ea7a0`，protocol failure SHA `c1d25ef8e0be56ef8e10f6c790e4fbdeceb10af83c14e17d78162a2789111d85`。

只读复现证明仅解开35的text容器、逐字保留不确定性文字，现有严格观察validator全部通过；normalized json_sha为 `bdf42fc4ac4ce6a39fc578720100c5d0ecd6f974dc1a44d32ec0973d7177ea91`。该本地复现使用原请求metadata，实际服务器media/ledger仍需登记时核验。两条根级inference不属于typed evidence，不能转入证据或用于验证主张。原始回复、失败和未创建parsed的状态保持。

正在实现唯一的 `opencode_known_slice_uncertainty_wrapper_reconciliation_v1`：登记35-call原始prefix、控制/失败/方案/窗口和source/proxy的SHA；仅对这对已知回复做确定性容器解包。保留原副本，消费者只取严格验证的canonical证据字段，未验证root扩展在独立记录中保留并向下游传递generic限制。不会第三次请求该片段、不新增候选/渲染授权、不改GLM切点、总时长或旧协议结果。新控制固定 `slice_wrapper_v1`，仍只完成原剩余候选；后续未提交source请求使用已经存在的explicit完整schema提示。此处仅记录实施边界，尚未声称已登记、启动或出片。

新证据诊断加入CI清单后的本地完整组合523 passed、3个平台skips。后续wrapper/提示修复的独立测试与实际Linux发布另行追加，不与这次检查混为一谈。
