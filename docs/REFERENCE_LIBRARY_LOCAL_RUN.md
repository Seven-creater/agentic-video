# 参考驱动电影素材库：本地实施与运行

日期：2026-10-04，Asia/Shanghai。本文描述当前实施，不替代
[任务约定](REFERENCE_LIBRARY_SPEC.md) 与[研究历史](REFERENCE_LIBRARY_RESEARCH_20261004.md)。

**最新执行：** 用户再次授权一次端到端测试，使用 `--continue-semantic` 在原目录运行／恢复。
整条参考由 GLM 自主判断重要信息与剪法，独立精切事实和输出矛盾审核接入本次流程。
原 80 次请求与 16 个唯一窗口上限不变，单独授权 render_3，渲染总上限为四次。
真实协议失败、一次语义重规划和后续结果见 [完整参考续跑记录](REFERENCE_LIBRARY_SEMANTIC_RUN_20261004.md)。

**前一版本状态快照：** 首轮两版成片之后，用户明确授权在同目录追加一次渲染；当时 GLM 已完成并选择
34 秒新版 `render_2/final.mp4`，结果在 `result_revision_2.json`。累计 58/80 次请求、
16/16 个唯一窗口、3/3 次授权渲染。原 `result.json` 及全部历史证据保留。
官方 MCP 已停止，缓存恢复不增加请求。模型评分不能替代质量验收：画面描述存在矛盾，
当时新版 22–34 秒静音，节奏未核验。此前根据未核验 ASR 断言参考含解说已撤回；用户明确参考只有 BGM。
详见 [新版运行报告](REFERENCE_LIBRARY_REVISION_20261004.md)。
下文“在途／尚无成片”和两次渲染上限为当时快照；后续授权以本页最新执行及原任务的授权记录为准。

## 当前任务和状态

输入一条固定异源参考，从大电影库按需查找、精看、组织和剪辑真实片段。
参考决定主旨和剪法目标；素材证据允许重构段落，但不能擅自换参考或弱化主旨。
GLM 自主选择角色、窗口、slot 与实际源入出点，程序校验和执行。
旧 MiniMax 生成线、已冻结的抖音发现线及其运行记录不属于本次执行。

本次固定输入已完成真实库存核验：

| 输入 | 仓库内位置 | 实测时长 | 选定音轨 |
| --- | --- | ---: | --- |
| 固定参考 | `data/ref/video.mp4` | 21.933333 秒 | 全局 stream index 0 |
| 《功夫熊猫》 | `data/videos/` 下 2008 年 MKV | 5,529.536 秒 | 全局 stream index 2，国语 |
| 《功夫熊猫2》 | `data/videos/` 下 2011 年 MKV | 5,423.968 秒 | 全局 stream index 2，国语 |
| 《功夫熊猫3》 | `data/videos/` 下 2016 年 MKV | 5,694.720 秒 | 全局 stream index 2，国语 |

电影总量 16,648.224 秒，即 277.4704 分钟。文件全路径、SHA、流参数与时间基在
`runs/library_reference_20261004/catalog/inventory.json`；参考对应 `reference_catalog/inventory.json`。
这三部片是用户提供的当前真实库；早期“一两部国产动画”的设想仅为研究历史。

当前实际记录：真实参考分析完成；首张电影粗看原请求输出达到 8,192 上限且无可用 content，
`glm_003` 的一次协议修复随后成功。`glm_004` 约 306 秒后 `fetch failed`，无捕获回复，
其 `uncertain` 原记录与请求预算占用永久保留，不重放。
005、006 全局概览已经收到，007 仍在途；**实际素材库成片、盲读和剪法迁移验收尚未完成。**
历史错误与每次修正单独留档，较早的进度快照不被追溯写成成功。

## 执行分工与账户边界

```text
当前 Codex 会话
  → 官方 @z_ai/mcp-server / glm-5.3-flash
  ↔ 本地 MCP 文件队列和已落盘的请求、响应、用量
独立 Python omni-library
  → 固定参考理解 → 稀疏粗索引 → 模型查询精看 → 可修改 slots / EDL
  → FFmpeg 按原时间裁切 → 实际成片盲读与对照审阅
本地 faster-whisper small / CPU int8
  → 参考与精看窗口的带时间戳语言证据
```

当前运行无需租服务器和 GPU。GLM 推理由官方平台提供，CPU ASR、索引与 FFmpeg 在本机执行。
ASR 首次使用需要本地 Whisper small 权重，当前加载目录为 `runs/library_models/`。

**`omni-library` 不是独立 Coding Plan 模型 API 服务。** 官方 MCP 当前由 Codex 支持的会话连接，
Python 仅将已有提示与媒体提交至本地作业队列；`mcp_launch` 启动该连接的官方 MCP 子进程。
只有 Python 命令或一个 `mcp_ready.json` 文件，不代表有效 MCP 会话正在运行。
不会默改成标准 API、伪造客户端或把这套队列当成已获授权的独立服务器部署。
套餐适用范围见[官方订阅协议](https://docs.bigmodel.cn/cn/terms/subscription-agreement)，
视频输入边界见[官方视觉 MCP 文档](https://docs.bigmodel.cn/cn/coding-plan/mcp/vision-mcp-server)。

## 独立环境与连接

需要 Windows 本地 Python 3.13、Node.js/npm、PATH 中的 `ffmpeg` 和 `ffprobe`。
在仓库根目录建立独立环境，不改变冻结发现线的 `.venv`：

```powershell
Set-Location "C:\Users\29785\Desktop\omni-autonomous-screenplay"
py -3.13 -m venv .venv-library
.\.venv-library\Scripts\python.exe -m pip install -e ".[library,test]"
.\.venv-library\Scripts\omni-library.exe --help
```

`library` 可选依赖是 Pillow、faster-whisper 和 av；基础生成入口仍使用自己的依赖。
本轮官方 MCP 包为 `@z_ai/mcp-server@0.1.5`，本地 HTTP dispatcher 另依赖 `undici@7.16.0`。
包与 SDK 位于项目外，不把密钥或 npm 安装目录入库。
本机已有的包根目录为 `%LOCALAPPDATA%/OmniStory/glm-probe-20261004`；
`--package-root` 必须指向含 `node_modules/@z_ai/mcp-server/` 的目录，而非包内 `build/`。

如果需要准备这个外部包目录，同时安装官方 MCP 与 dispatcher 依赖：

```powershell
$libraryMcpRoot = Join-Path $env:LOCALAPPDATA "OmniStory\glm-probe-20261004"
npm.cmd install --prefix "$libraryMcpRoot" --ignore-scripts --no-audit --no-fund @z_ai/mcp-server@0.1.5 undici@7.16.0
```

官方 MCP 通过配置环境使用 16,384 输出 token 和 600 秒 timeout；本地 dispatcher 的
headers/body timeout 同为 600 秒。Guard 不改官方模型请求 body，仍阻断内部自动重试。
004 的约五分钟失败可能与此前 Node header timeout 有关；原 cause 未捕获，**只是原因候选**，
不是已确诊根因，调大 timeout 也不允许重放该未知 POST。

以下由当前 Codex 会话负责运行。先在保持活动的 MCP 进程中建立连接：

```powershell
$libraryMcpRoot = Join-Path $env:LOCALAPPDATA "OmniStory\glm-probe-20261004"
.\.venv-library\Scripts\python.exe -m omni_story.library.mcp_launch --output "runs\library_reference_20261004" --package-root "$libraryMcpRoot"
```

如果进程未提供 `Z_AI_API_KEY`，启动器显示 `GLM key (hidden):`，使用隐藏输入。
密钥只进入子进程环境，不以命令参数传递；不要写入 `.env`、源码、文档或请求记录。
本机 MSIX 环境可能将 `%LOCALAPPDATA%` 虚拟化；实际存在的路径以当前连接与包文件为准。

随后由同一 Codex 会话启动 Python 管线，两个进程在本次 run 的队列中协作：

```powershell
.\.venv-library\Scripts\omni-library.exe --reference "data\ref\video.mp4" --library "data\videos" --output "runs\library_reference_20261004"
```

`--no-asr` 只用于有意关闭语言证据的新配置；本次已锁定 `asr=true`，不能在续跑时更改。
不要为绕过已提交请求而改变参考、目录、提示或开新 run。

## 当前观察策略及未变的硬预算

| 参数 | 当前配置 |
| --- | --- |
| 模型请求总数 | 最多 80 次，协议修复计入 |
| 全局导航 | 三部电影各 18 张带原时间标签的稀疏概览帧 |
| 查询驱动区域粗看 | 全库由 GLM 最多选择 4 个区域，每个不超过 600 秒 |
| 精看窗口 | 每轮最多 8 个、不超过 90 秒的连续窗口；总硬上限仍为 16 个 |
| 计划／渲染轮次 | 最多 2 轮 |
| 音频证据 | CPU Whisper small/int8；仅转写语言 |
| 渲染 | 模型选择；支持原速及 0.5–2 倍速、fit/crop、none/grayscale、四种音频模式 |

初始方案为每 600 秒取 18 帧并遍历 30 页。实际只留下已完成和失败的原页记录，
没有完成全库 30 页观察。当前 `strategy_transition` 单独记录 `adaptive_coarse_v2`：
先全局导航，再由模型按参考需要选区域展开，随后连续精看。
原输入锁、80 次请求计数、16 个精看窗口和 2 个成片硬上限没有重置。

联系图是稀疏抽帧观察，不能宣称完整看过 277 分钟电影。帧间发生的事件仍是未观察；
GLM 的粗索引可指导查询，具体片段必须在真实连续窗口中精看。
本轮未加载 FlashVID 插件，没有执行 0.10 视觉 token 压缩；不要将抽帧称为 FlashVID。

精看代理保留原区间映射；EDL 的 `source_in_s/source_out_s` 是原片秒，
必须在对应已完成观察的 `window_id` 区间内。渲染采用有限预 seek、解码后 trim，
再转换输出帧率，不以 24 fps 帧编号直接解释原片时间基。

source/ref/mix/silent 音频策略和 gain 由模型决定。国语按全局 stream index 2 选择，
参考音轨 index 0；混合复用的是选定参考原音轨，未做音乐／人声自动分离。
目前未实现 J/L cut、字幕、旁白或新的素材生成。GLM 当前视觉 MCP 未验证能听实际音轨，
ASR 也不等同于节拍、音效或声画同步审阅，相关结论必须保留限制。

## 留档、恢复与结果判定

固定输出：`runs/library_reference_20261004/`。恢复时使用相同入口、输入与输出目录。
`runs/.library_runs.json` 注册相同任务，防止换目录重置预算。

| 产物 | 作用 |
| --- | --- |
| `library_state.json` | 输入／政策／预算锁、逐次提交状态与累计用量 |
| `current_status.json` | 当前阶段；不是完整成片成功证明 |
| `catalog/`、`reference_catalog/` | 实际原文件、SHA、时长、流和时间基 |
| `calls/*/request.json`、`response.json` | 逐调用原始记录；已知回复不会重新提交 |
| `mcp_http.jsonl` | 脱敏后的官方 MCP HTTP 请求／回复证据与用量 |
| `mcp_queue/` | request、started、response 作业记录；缺回复不能自动重放 |
| `artifacts/`、`failure.json` | 原失败、追加核对证据与实施修正 |
| `reference_reading.json`、`reference_asr.json` | 固定参考的模型理解与未核验 ASR 证据 |
| `coarse_index.json`、`watched_windows.json` | 已观察的粗看页和精看窗口；不存在的内容不能假称已完成 |
| `coarse_failures.json`、`coarse_zoom_search.json` | 未观察／协议失败区域及模型选择的进一步展开范围 |
| `plan_<轮次>.json` | 模型选择的 slots 与实际 EDL |
| `render_<轮次>/render_result.json` | 原片→输出时间线 provenance、输入冻结、实际媒体 SHA |
| `blind_reading_<轮次>.json`、`review_<轮次>.json` | 实际成片盲读与目标对照审阅 |
| `result.json` | 最终选定版本、`final_video`、状态、用量和保留限制 |

基础 state 未启用续跑政策时，已提交但结果不明的请求会停止继续提交。
本次用户的规则禁止重放未知请求，并不禁止所有其他新工作，因此单独追加
`continuation_policy = independent_media_no_unknown_replay_v1`：预算内允许新独立输入，
不清除原 `uncertain`，不改旧政策或预算。`submitted` 请求仍须先等待，不并行绕过它。

续跑校验同时禁止未知原请求摘要、相同媒体 SHA 和相同 observation lineage：
`kind + source_sha256 + source_start_s + source_end_s`。
仅换提示、文件名、编码或输出目录不能使未知观察获得重放资格；未知输入不能走格式修复。
004 已无捕获回复，原 `uncertain` 记录永久保留，未知 token 消耗／账单不得填写为零。

若日志中已经保存确定的 HTTP 200 回复，可追加核对证据恢复其明确结果；
保留原错误与恢复解释，不把无 content 当作有效 JSON 或成功粗索引。
已知格式失败最多一次协议修复，修复是新的计费请求并占用原预算。

`model_checked_library_candidate` 表示实际成片通过该轮模型的主旨、剪法和人物连续性检查；
`library_candidate_with_limitations` 表示已有实际成片候选但检查仍有限制。
两者均不是陌生观众真值或完整声画节奏验收。没有 `result.json` 与对应有效媒体时，不能宣布成片完成。

## 当前已完成的验证

渲染模块有 9 项真实 FFmpeg 合成测试，通过原时间切片、帧率转换、全局音轨、混合／循环、变速、
画幅、源／输出 SHA 变化、越界和冻结缓存复用。测试使用合成影片，不代表真实三部电影选片质量。

```powershell
.\.venv-library\Scripts\python.exe -m pytest tests/test_library_render.py -q
```

首轮真实运行还在继续。最终结果由实际记录追加，不把当前代码实现或 HTTP 成功当作端到端成片成功。

## 2026-10-04 追加：ASR 对齐故障与参考分析副本

真实首个精看调用 `glm_014` 已收到。处理第二个原片 1,260–1,350 秒窗口时，
Whisper 返回两处零时长词：局部时间 `0→0` 与 `89.97→89.97`。
首次严格的词级时间校验将整个窗口阻断，这是 ASR 对齐处理问题，不能据此丢弃有效段落或编造词时间。

新 `asr-time-validation-v2` 在解释任何时间之前，先保存 `raw_asr_NNN.json` 与 SHA。
零时长词继续保留原 text、原始时间和 `alignment_issue`，标为 `timing_usable=false`，
不提供可用于原片定位的 source 时间；有效 segment 与有效词仍保留。
严重越界、非有限时间等结果不作为可用证据，不通过人工扩大窗口或填造时间“修好”。
已有参考和首个精看 ASR 缓存的字节未改；新规则和新推理记录单独追加。
该实际本地窗口复验取得 46 个有效 segment。

管线将 ASR 的纯对齐失败作为可选语言证据失败留档，明确无可用语音证据后继续视觉观察；
源 SHA 改变、缓存完整性失败仍属于 fatal 技术问题，不能以“ASR 可选”绕过。
这项恢复不是重放已提交的未知 GLM 请求，也没有修改模型选片答案。

参考原件达到／超过官方 MCP 的 8 MB 输入边界时，入口可以生成目标 7.5 MB 的全时长分析副本。
保留完整 MP4 原件、原件 reference SHA 和代理 lineage；模型读取代理，创作目标仍绑定原参考。
分析副本不替代原文件，不能把降低采样精度描述成已经完成精确节奏审阅。

ASR 修复后，相关媒体和 execute 检查共 38 项通过；新环境全量回归 227 项通过、2 项跳过，
跳过原因是新 `.venv-library` 缺少冻结发现线的 `browser_use/requests`。
在原 `.venv` 中对两组 discovery 测试额外运行 28 项通过，覆盖这部分依赖范围；
这些数量不是相加后的不重复测试总数，也不证明成片质量。

写此次追加时，实际 4 个精看窗口已完成，第 5 个仍在途。**尚无素材库成片，不能宣称任务完成。**
# 参考知识与自主复看补充（2026-10-04）

当前最新阶段为参考剪法知识验证，见 [实施与实际结果](REFERENCE_CRAFT_IMPLEMENTATION_20261004.md)。
用原目录增加 `--reference-craft`，GLM 自主决定复看区间与知识卡；两阶段各最多一次修复，
不增加电影精看窗口或渲染。实际新增3次请求，累计79/80；停止连接后的缓存恢复不新增请求。
本次尚未证明比赛段压缩和速度技巧的迁移质量。旧成片、旧结果与旧76次用量记录保留。

# 跨窗口人物身份校验补充（2026-10-04）

**当前首轮已完成。** 同目录 43 次请求、16 个连续窗口、两版实际渲染，最终选择约 77 秒的 round 0；最终补审为主旨 partial、剪法 partial、人物连续性 pass，状态 `library_candidate_with_limitations`。详情与媒体见 [首轮运行报告](REFERENCE_LIBRARY_FIRST_RUN_20261004.md)。下文的在途描述仅保留实施历史。

官方连接已停止。已收结果可以无新增请求地复用；需要新模型工作时，显式重新运行 `mcp_launch` 会清除 stop 标记并建立连接，原始提交标记、状态、未知请求和预算保留。

新增剪法能力和独立的前向协议见 [剪法实施记录](EDITING_IMPLEMENTATION_20261004.md)。
支持 GLM 选定的真实尾帧定格和静态中文文字；历史段落中的“不支持字幕”描述对应当时版本。
当前固定任务已用完 2 次渲染，启用新协议不会增加预算，也不能重新解释已有付费计划。
可用 `--audit-editing` 只测量现有参考／成片的切镜候选与 EDL 接缝，不连接模型、不重剪。

用户随后明确授权在原目录追加一次渲染，总有效上限为 3；原输入锁中的 2 次历史预算不改。
新的 hash-bound `editing_revision_authorization` 保存授权、原请求和历史文件 SHA；80 次请求和
16 个唯一窗口仍是上限。经授权后使用 `--revise-editing`，只生成或恢复固定 `render_2`。
新观察、计划和审核放在 `artifacts/editing_revision_v1/`；比较选择在 `selection_revision_2.json`，
修订结果在 `result_revision_2.json`，原 `result.json` 及两版成片保留。这个入口不能反复增加额度。

```powershell
.\.venv-library\Scripts\omni-library.exe --reference "data\ref\video.mp4" --library "data\videos" --output "runs\library_reference_20261004" --revise-editing
```

真实首轮精看中发现角色编号只在各窗口内部稳定：相同字母可能指向不同角色，同一角色也可能得到不同编号。旧观察与原始回答保留不变。新计划要求 GLM 自行给出 `focus_role_bindings`，把稳定焦点身份对应到每个 `(window_id, role_id)`，并说明视觉身份依据。校验器要求绑定来自已确认角色，所选子区间有相交事件证据；它不能替代语义身份判断，实际成片仍需盲读和连续性审核。这个追加策略保存在同一运行的 `role_identity_policy` 记录中，不重置输入或预算。

首个实际成片的盲读获得“努力克服否定并获得认可”的含义，但对照审核把参考人物的具体身份作为硬要求，给出主旨 `fail`。依据用户既定的异源主旨迁移任务，后续提示明确区分观众理解与参考传记事实：允许人物、属性、形式和具体情节变化，仍需用实际画面表达同一意义。追加 `same_meaning_different_story_facts_v2`，旧参考解读、首次审核及失败评价不修改，也不强制后续审核判通过。首次渲染、盲读与首次审核保存在 `render_0/`、`blind_reading_0.json` 和 `review_0.json`。

## 主动精剪与 CPU 提案补充（2026-10-04）

新增前向模式 `--active-finecut`，包含 semantic-audit/editing-v2：草案后 GLM 自主精剪，
最终短片段重新独立观察，实际成片再做静音盲读、独立冗余审核和目标核对。
策略与手册快照持续生效，恢复无需再次提供开关。它不能激活已有付费计划或授予额外渲染。
不要给固定真实任务使用这个开关，也不要为了应用它复制任务到新目录。

当前已有任务只运行 CPU 提案：

```powershell
.\.venv-library\Scripts\python.exe -m omni_story.library --reference data/ref/video.mp4 --library data/videos --output runs/library_reference_20261004 --prepare-active-finecut
```

该命令不需要 MCP 连接，验证固定参考、素材库和旧历史后追加提案；重复执行相同提案不重复写记录。
它不调用模型、不产生新成片，也不改变原输入锁、请求上限或旧结果。
当前为 79/80 次请求、4 次渲染。下一轮提案预留最多 44 次新请求、8 个最终精切和一次新渲染，
尚未获预算授权；若批准，需另行接入同目录增量执行，不直接更改原 base max_requests=80。
没有新的电影唯一精看窗口，最终短区间仍需独立观察。细节与测试证据见
[主动精剪记录](ACTIVE_FINE_CUT_IMPLEMENTATION_20261004.md)。

后续用户明确取消程序请求总上限，44次提案仅保留历史。新增同目录 `--continue-finecut`
执行器不设数字调用cap，按阶段有序前进、一次格式修复、超时／不明结果停止，保留全部旧账本。
尚无真实增量执行授权；缺少授权记录会在模型调用和渲染前拒绝。用户当前先调研Max额度与无MCP路线，
没有激活新模型工作。官方新积分规则与旧版权益、Agent套餐接入与Python标准API的差异见
[额度与原生视频](GLM_MAX_QUOTA_AND_NATIVE_VIDEO_20261004.md)。
