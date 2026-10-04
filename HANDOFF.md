# 新窗口交接：Omni 自主参考视频 → 剧本 → 资产 → 素材 → 剪辑

**最新授权执行：** 先读 [完整参考与精切事实续跑](docs/REFERENCE_LIBRARY_SEMANTIC_RUN_20261004.md)。
用户要求自主分析全片，不给 GLM 旧解释或指定 5–17 秒。`--continue-semantic` 在原目录运行／恢复
这一次追加授权，render_3 是唯一新增版本，80 次总预算和 16 个唯一窗口不变。
059/060 参考协议失败以有局限的原始模型解释恢复导航；061/062 计划证据失败未改写，
一次新语义重规划由 GLM 自主重选，持久化最多五条精切和后续完整审核预算。
不得用旧 result_revision_2 冒充这次结果，也不得重放不明的 004。
本轮实际已生成 34 秒 render_3，结果在 result_semantic_revision_3.json；76/80 次请求，
review=None、review_status=incomplete_protocol_failure、semantic_gate_passed=false。
盲读文字依赖 essential，源观察与部分计划/盲读冲突，不能伪造终审通过或把所有模型反证当真值。
官方 MCP 已停止，同目录恢复不增加请求；58 个原调用、305 个历史文件和全部调用文件保持不变。
本段以下调用数和渲染数保留各历史版本的快照；实际最新状态以原目录 current_status 和新报告为准。

**2026-10-04 当前任务补充：** 先读 [电影素材库任务约定](docs/REFERENCE_LIBRARY_SPEC.md) 和
[端到端调研](docs/REFERENCE_LIBRARY_RESEARCH_20261004.md)。用户最新确认的是参考先确定目标，
再检索大素材库；旧 MiniMax 生成线与抖音发现线保持冻结。用户随后已授权实施及真实运行，
新入口、当前固定输入和恢复规则见下方追加记录及 [本地运行指南](docs/REFERENCE_LIBRARY_LOCAL_RUN.md)。
下文是 2026-10-02 的历史交接，不代表当前素材库路线、Git remote 或运行状态。

**同日最新工程补充：** [剪法实施记录](docs/EDITING_IMPLEMENTATION_20261004.md) 说明新增逐项剪法绑定、
候选的可见剪辑条件、真实切镜候选、定格与中文字幕、实际输出核验，以及 `--editing-v2`／`--audit-editing`。
首轮 43 次调用、两版成片和 partial 评价保留。用户随后明确同意在原任务追加一次渲染；
当前累计 58/80 次请求、16/16 个唯一窗口、3/3 次授权渲染。GLM 自主选中新版 34 秒的
`render_2/final.mp4`，修订结果为 `result_revision_2.json`；不要把旧 `result.json` 当作新版选片。
模型给出三项 pass，但其画面描述存在前后矛盾，22–34 秒实测静音；
没有完成可靠的剪法质量和声画节奏验收。完整记录见 [修订运行报告](docs/REFERENCE_LIBRARY_REVISION_20261004.md)。
官方 MCP 已正常停止，缓存恢复验证无新增请求；旧 43 次调用和 216 个受保护文件 SHA 均未变。
不可新增第四个渲染、重放未知 004、换目录或删状态重置预算。375 项测试通过、2 个依赖模块跳过；
工程测试不证明实际剪辑质量。

**同日后续用户澄清与审计：** 用户确认参考只有 BGM，核心是静音观察能理解准确内容。
此前依据未核验 ASR 推断参考有解说的事实表述已撤回，原记录保留。新内容标准已记入任务约定，
尚未接入运行提示。实际端到端审计判定画面叙事与剪法迁移部分成立，主要断点为
宽事件概括与精切错位、遗漏行动结果、计划影响审核、盲读矛盾未处理、最终选择继承错误。
详见 [端到端审计](docs/REFERENCE_LIBRARY_E2E_AUDIT_20261004.md)。本轮仅审计，仍为 58 次调用与三版视频。

## 2026-10-04 追加：本地素材库路线已开始实施

**后续实施补充：** [时间压缩与事实审核](docs/REFERENCE_TIME_COMPRESSION_20261004.md) 记录
参考比赛段的实际跳切、关键踢击强调、倒地与微笑，以及多精切 slot 的研究设计。
新增 `--semantic-audit` 包含 editing-v2：计划前启用，独立看每个精切，再核对计划要求，
成片无声盲读分动作／结果／文字／推断，未解决矛盾和局部不支持的事实阻止通过。
每段两次独立调用与协议修复预留计入原预算，搜索／计划预算持久化，恢复不改付费请求。
这是新协议工程与合成媒体测试；没有新 GLM 创作质量评测，当前真实 run 仍为 58/80、三次渲染。
不要把旧任务复制到新目录应用该模式以绕过预算；旧计划和评分不追溯改写。

本段优先于下方旧交接中的“仅整理、无新运行授权”等历史说明；用户本轮已授权新路线实施和实际测试。
该授权不启动旧生成／发现线，不改旧任务的付费提交身份。

- 真实参考固定在 `C:/Users/29785/Desktop/omni-autonomous-screenplay/data/ref/video.mp4`：
  SHA256 `2f95e24edd2cf4b79cc1f40f7e202174c53a6abcf084e728bb49e3ca92938a17`，21.933333 秒，720×1280。
- 真实电影库在 `data/videos/`，三部《功夫熊猫》共 16,648.224 秒，即 277.4704 分钟。
  各原件、SHA、实测时长、原时间基与音轨存于当前 run 的 `catalog/inventory.json`。
  三部片均记录国语全局音轨 index 2；参考音轨全局 index 0，不按默认英语轨剪辑。
- 当前模型为 `glm-5.3-flash` 官方视觉 MCP，连接在 Codex 会话内；本地 Python 向 MCP 队列提交工作，
  不直接构造 Coding Plan 模型 HTTP API。CPU Whisper small/int8 提供语言证据，FFmpeg 执行实际剪辑。
  没有租服务器、加载服务器模型或重新生成 MiniMax 素材。本轮粗看是抽帧联系图，未运行 FlashVID。
- 可选依赖安装在独立 `.venv-library`；新入口为 `omni-library`，无需变更旧 `.venv`。
  `mcp_launch` 读取 `Z_AI_API_KEY` 或隐藏输入，不把密钥放进仓库、文档和命令参数。
- 固定 run：`runs/library_reference_20261004/`。全部恢复均沿用它及 `runs/.library_runs.json` 注册记录，
  不新开目录、不删 `library_state.json`、不重置 80 次总请求预算。协议修复也计入。
- 已完成的真实阶段：原件库存与 SHA 核验、参考 ASR 缓存、GLM 实际参考分析。
  首张电影粗看已返回 HTTP 200，输出上限 8,192 token 耗尽且无可用 content。
  原始 MCP 错误、已捕获的 HTTP 回复、用量与恢复解释均保存；没有重放原 POST。
  同一预算内的一次协议修复仍在进行。电影检索、成片和成片审核尚未完成。
- 渲染模块的 9 项真实 FFmpeg 合成测试通过；这验证时间、音轨、混合、缓存等执行行为，
  不证明三部真实电影的片段选择、故事表达与剪法迁移已经验收。

接手先读 `current_status.json`、`library_state.json`、`failure.json` 及对应 `calls/`，
再核对 `mcp_http.jsonl`／队列响应；历史 failure 文件的存在不能覆盖后来追加的明确 HTTP 结果。
同样，没有 `result.json` 及其实际媒体时不能宣布成片成功。

### 同日追加：修复结果、未知请求与 adaptive_v2

上方“一次修复进行中”是实施初期快照；`glm_003_coarse_978d5360_00_repair` 已实际成功，
其原错误、HTTP 200 回复与修复用量全部保留。后续 `glm_004_coarse_978d5360_01`
约 306 秒后发生 `fetch failed`，没有捕获服务回复，继续永久保留 `uncertain` 原记录；
该请求仍占原 80 次预算中的一次，消耗 token／实际账单未知，不能填零或猜作退款。

规则是禁止未知请求重放，不是禁止所有新工作。当前 run 追加
`continuation_policy = independent_media_no_unknown_replay_v1`，允许其他新独立输入；
相同请求摘要、相同媒体 SHA 或同 `kind + sourceSHA + 原区间` 的 lineage 都不能再次提交，
改编码、文件名、提示或输出目录也不能绕过。尚在 `submitted` 的请求仍须先等结果；
未启用这一追加政策的基础 state 保留原保守阻断，不删除任何历史调用。

`strategy_transition` 记录从 `fixed_30_page_grid` 到 `adaptive_coarse_v2`：
每部电影 18 帧全局导航，由 GLM 在全库选择最多 4 个、不超过 600 秒的区域展开；
随后每轮最多 8 个、不超过 90 秒的连续精看窗口。原 16 个窗口、2 个成片、80 次请求硬上限不变。
005、006 全局概览已有明确回复，007 在途；尚无实际素材库成片。

外部官方 MCP 包根目录新增 `undici@7.16.0`，本地 dispatcher 的 headers/body timeout 为 600 秒，
官方 MCP 配置输出上限 16,384／timeout 600 秒；内部重试仍由 guard 阻断，官方模型请求 body 未改写。
约五分钟的 Node header timeout 是 004 的原因候选，原 cause 未捕获，不能当作确定根因。

更新日期：2026-10-02，Asia/Shanghai。

本文由当前源码、真实运行产物和本地保存的服务器执行记录核对而来。
本次只写交接文档，没有 SSH 连接、模型加载、API 调用或新增付费素材。
**服务器信息是历史留档，不代表此刻的 GPU、进程、文件或服务已经复查。**

## 1. 新窗口首先确认的事实

- 当前唯一主工作仓库：`C:/Users/29785/Desktop/omni-autonomous-screenplay`。
- 分支：`main`。写本文之前的 HEAD：`03fd06688db1d285044fa6d06477f3d46d37db78`。
- 当前仓库没有配置 Git remote。不要把旧仓库的 GitHub 同步状态当成这个仓库的状态。
- 用户最近明确要求：以整理代码为主，不需要多生成；随后要求复测，再要求本文。
  这不是新的付费运行或服务器启动授权。新窗口先阅读，不要自动启动模型或后台定时任务。
- 此仓库是独立 Python 包，不运行时导入旧 `douyin`／`douyin-final-pipeline` 的代码。
- 当前默认后端：阿里云 Qwen3.8 Omni + 阿里云生图／改图 + MiniMax H3 API。
  **服务器 Qwen3-Omni／本地 H3 尚未接入这个独立仓库的主 CLI。**
- 本地复测：69 passed，19.39 秒；12 个运行模块导入及 Python 3.10 语法检查通过。
  实际测试 Python 为 3.13.12，不把语法检查冒充 Python 3.10 上的完整运行。
- 离线 wheel 构建、临时目录安装、脱离源码目录的模块／console 入口及错误参数检查通过。
  这些验证不证明真实模型生成质量已经全部合格。
- 旧请求、raw response、失败、任务 ID、资产、素材和成片全部保留。

本文新增后，`git status` 可能显示本文未提交；不要因此清理或回滚文件。

## 2. 用户目标和不可丢失的要求

用户的唯一创作输入是参考视频。由 Omni 自主完成内容与剪辑理解、主题、故事、逐段剧本、
人物／道具／场景描述、资产审阅与选择、真实素材观察、切片及成片审阅。
Codex 负责通用程序、工具执行和证据核对，不在中途手写剧情、指定题材、选图或选片。

尤其不要恢复已经否定的旧要求：

1. 身体差异允许，但不是必选，也不是拒绝理由。
2. 新故事保留相似的主旨和表达机制；段落结构尽量相似，不锁死原片秒点、镜头数或段数。
3. 剧情要便于画面理解和后续生成，资产只要求粗身份、主要服装、场景和重要道具稳定。
4. **故事时间、素材生成时长、最终成片时长是三个概念。**
   先写完整事件，生成完整动作素材，Omni 看过实际素材以后才决定哪些瞬间剪入成片。
5. 内容审核不理想时，在有限预算内修订或由 Omni 选现有最佳候选继续，并保留缺点。
   不把拒绝记录改成通过，不因微小动作细节无限修稿。
6. 缺失文件、无效媒体区间、认证／余额失败和提交状态不明是执行故障，不能伪造结果。
7. 每条视频素材只提交一次；已有任务只查／下载。不能删 `submission.json` 或开新目录重置预算。
8. BGM 可复用、保持原速；故事不必复用原配音。当前实现是纯画面创作，没有新增字幕或配音模块。
9. 剪辑必须基于真实素材和贯穿下游的同一份参考记录，不是把所有视频按生成顺序串起来。

先读 [AGENTS.md](AGENTS.md)，其中的执行边界继续有效。

## 3. 按这个顺序阅读代码

下表路径均相对于 `C:/Users/29785/Desktop/omni-autonomous-screenplay`。
建议按顺序读，不必先翻旧工程里的大量历史 attempt。

| 顺序 | 文件 | 重点看什么 |
| --- | --- | --- |
| 1 | [README.md](README.md) | 当前流程、默认后端、真实限制，不把“已有代码”理解为所有实际运行均成功 |
| 2 | [docs/RUNNING.md](docs/RUNNING.md) | CLI、凭据、预算、付费边界、续跑与重复扣费保护 |
| 3 | [docs/CODE_MAP.md](docs/CODE_MAP.md) | 实际函数调用图、决策归属、各产物怎样传给下一层 |
| 4 | [omni_story/__main__.py](omni_story/__main__.py) | `main`；`all / screenplay / reedit` 分支；默认是付费全链 |
| 5 | [omni_story/pipeline.py](omni_story/pipeline.py) | `execute`、`analyze_reference`、`Loop.call`、`Loop.stage`、`blind_view`；整体规划后逐段写作、前文状态和有限反馈 |
| 6 | [omni_story/prompts.py](omni_story/prompts.py) | REFERENCE、REFERENCE_LOCAL、ROUTES、OUTLINE、SEGMENT、REVIEW、REVISION、BLIND、ALIGNMENT、SELECT_AVAILABLE |
| 7 | [omni_story/donor_prompts.py](omni_story/donor_prompts.py) | 从旧工程复制并标注来源的基础协议；本地文件，不是运行时导入旧工程 |
| 8 | [omni_story/contract.py](omni_story/contract.py) | `validate_outline`、`validate_segment`、固定资产引用、状态及字段检查；结构校验不等于语义真值 |
| 9 | [omni_story/reference.py](omni_story/reference.py) | `build_transfer`、`validate_editing`、`validate_coverage`、`validate_mapping`、`validate_style_review`；内容与剪辑共同来源 |
| 10 | [omni_story/production.py](omni_story/production.py) | 先读 `full_run`，再读 `execute_production`、`Calls.call`、`edit_sources`；最后读续跑、续剪和音乐分支 |
| 11 | [omni_story/production_prompts.py](omni_story/production_prompts.py) | PLAN、IMAGE_REVIEW、WATCH、EDIT、FINAL_REVIEW、SELECT_IMAGE、SELECT_RENDER、MUSIC_REPAIR；不能提前杜撰实际入出点 |
| 12 | [omni_story/media_backends.py](omni_story/media_backends.py) | `claim`、`AliyunImages`、`MiniMaxH3`；真实接口、原始任务及一次提交保护。`ImageHelper` 不是当前默认路由 |
| 13 | [omni_story/api.py](omni_story/api.py) | `QwenAPI`、`curl_json`；媒体／JSON 传输、凭据脱敏、配置与采样声明 |
| 14 | [omni_story/editing.py](omni_story/editing.py) | `compile_plan`、`edit_metrics`、`render`、`mux_music`、`review_copy`、`audio_measurements`；实测素材区间与 FFmpeg 执行 |
| 15 | [tests/test_pipeline.py](tests/test_pipeline.py)、[tests/test_reference_chain.py](tests/test_reference_chain.py) | 无人工答案、有限修订、共同参考传递、局部复看、预算及历史不覆盖 |
| 16 | [tests/test_production.py](tests/test_production.py)、[tests/test_aliyun_backend.py](tests/test_aliyun_backend.py)、[tests/test_api.py](tests/test_api.py) | 模拟后端＋真实 FFmpeg、图审、不可重发、缓存、接口及密钥保护 |
| 17 | [REAL_RUN_STATUS.md](REAL_RUN_STATUS.md)、[REFERENCE_EDITING_CHAIN.md](REFERENCE_EDITING_CHAIN.md)、[SOURCE_PROVENANCE.json](SOURCE_PROVENANCE.json) | 从历史状态看到最新追加段落；保留失败和效果不足，不只看通过记录 |

主调用链：

```text
__main__.main
  → production.full_run
    → pipeline.execute
      → analyze_reference：原音画字联合理解＋至多两次局部复看
      → reference.build_transfer：同一份内容／剪辑信息与来源
      → ROUTES：三主题，Omni 自主选择
      → Loop.stage(OUTLINE)：完整框架、固定人物／道具／场景
      → 各 unit 的 Loop.stage(SEGMENT)：逐段、前文状态、独立审核
      → BLIND / ALIGNMENT → screenplay.json
    → execute_production
      → PLAN → 固定资产主图 → 素材起始图 → 图审／有限改图／候选选择
      → H3 完整动作素材（最多三路并发）
      → edit_sources
        → WATCH 真实素材 → EDIT 实际入出点及顺序
        → compile_plan / render → FINAL_REVIEW 看实际成片
        → 最多一次重剪 → 现有成片候选选择
    → continue_music_coverage：必要时只调整音乐，画面锁定
```

普通文本预算 32 次、生产侧 36 次、显式续剪 12 次；格式修复也计入。
默认图片上限 12，明确使用 `--unlimited-image-jobs` 才移除该数量上限；
全生产阶段改图上限仍为 1，视频素材上限仍为 6。不是无限抽卡。

## 4. 先读哪些真实产物，当前到底完成到哪里

### 4.1 原女生跆拳道参考片

输入：`C:/Users/29785/Desktop/douyin/data/videos/7682719919410072847/video.mp4`。
SHA256：`2f95e24edd2cf4b79cc1f40f7e202174c53a6abcf084e728bb49e3ca92938a17`。
实测约 21.933333 秒。

运行目录：[runs/reference_7682719919410072847_001](runs/reference_7682719919410072847_001)。

按顺序看：

1. `input_lineage.json`、`reference_reading.json`、`reference_transfer.json`：读懂了什么、根据什么。
2. `routes.json`、`outline.json`、`screenplay.json`、`asset_bible.json`：自主选择的故事。
3. `blind_reading.json`、`alignment_review.json`、`result.json`：15 次文本请求，文本候选通过。
4. `production/production_plan.json`、`asset_inventory.json`、`start_frame_inventory.json`：素材及资产。
5. `production/calls/`、`production/video_jobs/*/submission.json`、`production/run_failure.json`：真实执行结果。

这次自主选中 **The Blind Chef's Precision（盲人厨师）**，四段剧本。
没有偷读旧单臂攀岩剧本或手工指定厨师。完成 17 张图片：10 主图、6 起始画面、1 改图。
生产侧 20 次 Omni 请求。部分图片仍有批评，由 Omni 选现有候选，不假称全部图审通过。

H3 六条素材各尝试一次，只有 M02 成功，实测 6.583333 秒。
M01／M03／M04 得到任务 ID 后 `failed / 1008 insufficient balance`；
M05／M06 创建返回 `402 insufficient_balance_error`，没有任务 ID。
因此该 run **尚无整片，也未做整批素材观看、剪辑及成片审核**。
不要把账户充值理解为失败任务可自动重发，不要继续这个 run 的付费任务而不先核对状态及授权。

### 4.2 第二条参考片

输入：`C:/Users/29785/Desktop/douyin/data/videos/7682750673951198510/video.mp4`。
SHA256：`706acfe6705b0afc487672c93f7ed71dc367ab9ce71f0ffab1351d1b35403bb4`。
约 37.533333 秒。

运行目录：[runs/reference_7682750673951198510_001](runs/reference_7682750673951198510_001)。
Omni 自主写《夜光园丁的黄昏觉醒》，16 次文本请求、五段剧本，已有五段视频素材。

- 旧成片：`production/render_music_1/final.mp4`，45.666667 秒，有音乐。
  SHA256：`9c38470da2e6103abf6d4aecc01306444905dfd98a4518cfba9b0c23a30fe36c`。
  最后只调整混音，没有再次模型看片，仍是有限制候选。
- 新版续剪：`editing_continuations/joint_reference_v2/production/render_0/final.mp4`，
  44.875 秒、1024×576、24 fps，无音轨。
  SHA256：`675f4b352ac18b2f55871d4c42fae1b0a9af124748780799c44571ad8ecfad56`。
  新增 12 次 Omni 请求，没有再次生成图片／视频。
- 模型看了实际成片并接受主线，但风格迁移 `partial_or_unknown`；
  12 行编辑表实际是 6 段连续素材、5 个连接，不是 12 个真正切镜。
  仍未显著压缩，且模型错误关闭了可独立复用的参考音乐。程序没有人工覆盖这个决定。

查看最终 `production/result.json` 与续剪自己的 `production/result.json`。
**旧 `run_failure.json` 可能仍存在，不能仅因该文件存在就断言后续没有成功；也不能仅看 accept 忽略限制。**

## 5. 当前云端代码怎么用

这里只给命令，不在交接时执行。模型 ID／地址来自当前代码，不是新查询得到的服务可用性承诺。

| 工作 | 当前模型 | 地址／接口 |
| --- | --- | --- |
| 理解、创作、审阅、剪辑决定 | `qwen3.8-omni-flash` | `https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions` |
| 人物／道具主图 | `qwen-image-3.0-pro` | 阿里云 `api/v1/services/aigc/multimodal-generation/generation` |
| 场景主图／4–9 图融合 | `wan2.7-image-pro` | 阿里云 `api/v1/services/aigc/image-generation/generation` |
| 1–3 图编辑 | `qwen-image-edit-plus-2025-12-15` | 阿里云 multimodal-generation 接口 |
| 动作视频 | `MiniMax-H3` | `https://api.minimaxi.com/v2/video_generation` 及 V2 查询接口 |

凭据环境变量：`DASHSCOPE_API_KEY`、`MINIMAX_API_KEY`。
可配置地址：`DASHSCOPE_BASE_URL`、`MINIMAX_BASE_URL`。不在文档或仓库中存密钥；代码不自动读取 `.env`。

先做免费本地检查：

```powershell
Set-Location "C:\Users\29785\Desktop\omni-autonomous-screenplay"
git status --short
python -m omni_story --help
python -m pytest tests -q
```

当用户另行明确要求付费运行时，全链入口示例：

```powershell
python -m omni_story --video "C:\path\to\reference.mp4" --output "runs\new_explicit_run" --unlimited-image-jobs
```

输入需小于 10 MB。上述命令是实际付费运行，**不是 dry-run**。
`new_explicit_run` 仅表示用户批准的新任务，不是绕过既有失败任务的办法。
续跑同一任务必须保留原输出目录和所有提交身份。

`--stage screenplay` 只到剧本；`--stage reedit` 是已有完整素材的有界续剪，仍有 Omni 费用。
文本阶段未形成完整 `result.json` 时没有通用逐调用续跑能力。
当前代码没有 `--backend server` 或 `--model-path` 的全链选项。

## 6. 服务器相关模型库存：仅列本链相关的历史记录

服务器：`10.1.4.86`，历史账号／路径为 `wangqihao`。用户确认过直接 `ssh 10.1.4.86` 可连接。
本次没有重新连接；下面路径、环境和 GPU 配方必须在真正启动之前再次只读核对。

| 模型 | 用途 | 已留档路径／ID | 环境及资源 |
| --- | --- | --- | --- |
| Qwen3-Omni-30B-A3B-Instruct | 原生音视频／图像理解、文本创作，thinker-only 推理 | `/data02/pretrained_model/cvr_learn/cvr_model/03_audio_vlm2vec_backbone/qwen3-omni-30b-a3b-instruct` | `/data02/usr/wangqihao/miniconda3/envs/omni_src/bin/python`；历史用 GPU0/1、BF16、device_map=auto |
| Qwen/Qwen-Image | 文生图 | `/home/wangqihao/.cache/huggingface/hub/models--Qwen--Qwen-Image/snapshots/75e0b4be04f60ec59a75f475837eced720f823b6` | `h3` 环境；QwenImagePipeline；历史 GPU4、CPU offload |
| Qwen/Qwen-Image-Edit-2511 | 保身份改图 | `/home/wangqihao/.cache/huggingface/hub/models--Qwen--Qwen-Image-Edit-2511/snapshots/6f3ccc0b56e431dc6a0c2b2039706d7d26f22cb9` | `h3` 环境；QwenImageEditPlusPipeline；历史 GPU5、CPU offload |
| MiniMax-H3 diffusers 权重 | 文字／首帧／参考条件视频生成 | `/data02/usr/wangqihao/Demo/checkpoints/MiniMax-H3-diffusers` | `/data02/usr/wangqihao/miniconda3/envs/h3/bin/python`；历史双卡 int8＋CPU offload，5–15 秒、24 fps、768 短边 |

图像缓存路径取自旧脚本常量，未实时确认 snapshot 仍存在；不存在时先定位本机缓存，不要自动下载新权重。
历史 h3 环境记录为 diffusers 0.40、torch 2.9 cu128；目前版本未复查。
本地 Qwen-Image/Edit 与当前云端 Qwen3.0／万相／edit-plus 不是同一权重。
服务器有其他 Qwen 文本／理解模型的历史记录，但不属于这条链需要启动的后端；本文不是实时全盘模型清单。

### 6.1 三个旧目录各自是什么

- `/data02/usr/wangqihao/Demo/research`：原研究工程；图像 worker 等旧模块位于这里。
- `/data02/usr/wangqihao/Demo/douyin_final_pipeline_20261001`：旧成功路线提炼仓库的完整服务器 checkout。
- `/data02/usr/wangqihao/Demo/final_result_route_20261001`：旧 transport 部署目录，含 Omni worker、perception 依赖与 H3 service；不是当前独立仓库。

本地对应历史文件：

- `C:/Users/29785/Desktop/douyin-final-pipeline/final_route/omni_worker.py`
- `C:/Users/29785/Desktop/douyin-final-pipeline/final_route/backends.py`
- `C:/Users/29785/Desktop/douyin-final-pipeline/src/generation/minimax_h3_ref2va_serve.py`
- `C:/Users/29785/Desktop/douyin/src/agentic_video/asset_studio/gen_worker.py`
- `C:/Users/29785/Desktop/douyin/src/agentic_video/asset_studio/backends.py`

不要把 `C:/Users/29785/Desktop/douyin-final-pipeline` 中的旧故事框架、攀岩资产或用户选择自动作为新输入。
不要恢复已停掉的指挥家／雕刻师 run、旧 heartbeat 或 `run_server_omni_local_pipeline.py` 简化路线。
当前独立仓库未确认有服务器部署，不能声称它已同步 GitHub／服务器。

## 7. 真正启动服务器模型之前：只读预检

下面示例在用户明确允许服务器工作后使用。SSH 进入后按 Linux Bash 执行，不是 PowerShell。

```powershell
ssh 10.1.4.86
```

```bash
nvidia-smi
nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv
ps -eo pid,args | rg 'omni_worker|minimax_h3_ref2va_serve|asset_studio.gen_worker'
test -x /data02/usr/wangqihao/miniconda3/envs/omni_src/bin/python
test -x /data02/usr/wangqihao/miniconda3/envs/h3/bin/python
test -d /data02/pretrained_model/cvr_learn/cvr_model/03_audio_vlm2vec_backbone/qwen3-omni-30b-a3b-instruct
test -d /data02/usr/wangqihao/Demo/checkpoints/MiniMax-H3-diffusers
```

同时核对两个图像 snapshot 和要用的 worker 源文件是否存在；`test` 退出码非零即先停下查原因。
GPU编号只是历史配方，不代表空闲。不要杀其他用户进程或盲目再次启动已有服务。
若本地还要使用 Qwen 图像 GPU4／5，H3 不得同时占 GPU4／5；可在确认空闲后用 GPU2／3 或6／7。
当前 API 路线不用 GPU，也不需要启动任何这些本地服务。

## 8. 服务器 Omni：启动方式与协议

旧部署的实际命令形式：

```bash
CUDA_VISIBLE_DEVICES=0,1 HF_HUB_OFFLINE=1 \
/data02/usr/wangqihao/miniconda3/envs/omni_src/bin/python -u \
  /data02/usr/wangqihao/Demo/final_result_route_20261001/final_route/omni_worker.py \
  --model /data02/pretrained_model/cvr_learn/cvr_model/03_audio_vlm2vec_backbone/qwen3-omni-30b-a3b-instruct \
  --cache /data02/usr/wangqihao/Demo/final_result_route_20261001/media
```

这是 **stdin/stdout JSON-lines worker，不是 HTTP OpenAI-compatible 服务**，没有 base URL 或端口。
旧 `RemoteOmniRunner` 用 SSH 保持管道、上传媒体、传请求、读取响应。
worker 收到首个非 `ping` 请求后才加载模型；只启动进程或 ping 返回 ready 不代表权重已加载／推理已通过。

协议入口：`ping`、`ask`、`watch`、`chat`。`watch` 的媒体路径须是服务器已存在的文件。
worker 输出 Answer 的 text／token／采样记录，不能直接冒充当前 `QwenAPI.request` 的 HTTP 响应。
`chat` 历史实现只处理一个 user 消息及 text／inline image，不能把 video_url 直接塞给它当视频已支持。

不要用 `nohup ... < /dev/null` 启动该 worker：stdin EOF 会让它退出。
应由保持 stdin/stdout 的受控客户端启动，并记录 stderr 日志和精确 PID；不要手工塞故事答案。

## 9. 服务器 H3：启动方式与接口

实际入口：`src.generation.minimax_h3_ref2va_serve`，旧服务使用 diffusers，
不是聊天记录里早期因 CUDA／驱动问题未采用的 SGLang 四卡启动方案。

源码默认端口 30011，但旧 transport 配置使用 **30111**；必须与客户端的 endpoint 一致。
下面示例使用30111、GPU2／3、loopback。**只在这两张卡空闲且获得启动授权后执行。**

原服务 `REPO` 与 `OUTDIR` 是模块常量，CLI 仅有 host／port，没有 `--output`。
不能虚构输出参数；下面用运行时设置 `OUTDIR` 的包装方式隔离新日志／媒体，不编辑旧源码：

```bash
cd /data02/usr/wangqihao/Demo/final_result_route_20261001
H3_JOB_ROOT="/data02/usr/wangqihao/Demo/h3_service_runs/$(date +%Y%m%d_%H%M%S)"
mkdir -p "$H3_JOB_ROOT"
CUDA_VISIBLE_DEVICES=2,3 HF_HUB_OFFLINE=1 \
/data02/usr/wangqihao/miniconda3/envs/h3/bin/python -u -c \
'import sys; from src.generation import minimax_h3_ref2va_serve as s; s.OUTDIR=sys.argv[1]; s.uvicorn.run(s.app,host="127.0.0.1",port=int(sys.argv[2]),log_level="warning")' \
  "$H3_JOB_ROOT/media" 30111
```

这是由当前源码导出的启动示例，本次未在服务器执行。默认前台运行，另开 SSH 会话只读检查：

```bash
curl -sS http://127.0.0.1:30111/health
curl -sS http://127.0.0.1:30111/v1/models
```

`health.status` 应从 loading 变为 ready；failed 是加载失败。
CPU offload 下 GPU 闲时显存低，不用显存占用单独判断服务就绪。

接口：

- `POST /v1/videos`：请求含 `model=MiniMaxAI/MiniMax-H3`、prompt、seconds、task、conditions、target，返回 id。
- `GET /v1/videos/<id>`：查询 queued／in_progress／completed／failed。
- `GET /v1/videos/<id>/content`：下载完成的真实视频。
- 首帧条件：`role=keyframe`、`frame_index=0`，选择 fl2va；普通参考图为 reference，选择 ref2va。
- 服务内部 `cuda:0`／`cuda:1` 对应 `CUDA_VISIBLE_DEVICES` 中的两张物理卡，不是固定GPU0／1。
- 一个服务实例队列串行；多服务并发要先确认独立卡对、端口和输出目录，不盲目启动三份。

注意：旧服务的 JOBS 索引在内存里；重启后不能假定原 task id 仍可查询。
不能因404或断线重发生成，应先找原进程、日志和已落盘媒体。

云端 `MiniMaxH3` 走 V2，服务器走 V1，两种协议不能只换 `MINIMAX_BASE_URL` 就认为接通。
旧 `final_route.backends.LocalH3Bridge` 做了协议／首帧转换、任务落档、查询和下载；它目前不在独立仓库内。
该旧桥还有针对旧竖屏编辑器的格式转换，不能未经核对直接套到当前支持横／竖画布的新主线。

## 10. 服务器 Qwen 生图／改图：启动方式

这两个 worker 属于原研究工程，不在旧 transport 部署目录或当前独立包中。
只有要改为本地图像后端时才需要启动；当前用户恢复并使用的是阿里云 API。

在确认路径及 GPU4／5 空闲后，分别在保持 stdin 的终端／受控客户端中启动：

```bash
cd /data02/usr/wangqihao/Demo/research
HF_HUB_OFFLINE=1 /data02/usr/wangqihao/miniconda3/envs/h3/bin/python -u \
  -m src.agentic_video.asset_studio.gen_worker t2i 4 \
  /home/wangqihao/.cache/huggingface/hub/models--Qwen--Qwen-Image/snapshots/75e0b4be04f60ec59a75f475837eced720f823b6
```

```bash
cd /data02/usr/wangqihao/Demo/research
HF_HUB_OFFLINE=1 /data02/usr/wangqihao/miniconda3/envs/h3/bin/python -u \
  -m src.agentic_video.asset_studio.gen_worker edit 5 \
  /home/wangqihao/.cache/huggingface/hub/models--Qwen--Qwen-Image-Edit-2511/snapshots/6f3ccc0b56e431dc6a0c2b2039706d7d26f22cb9
```

参数形式是 `<role> <gpu> [model_path]`，worker 自行设置 CUDA_VISIBLE_DEVICES。
输出 loading／ready 事件；stdin 每行 JSON 任务，stdout 每行 JSON 结果。
通用任务字段：id、prompt、width、height、seed、out_path；改图额外 `reference_path`。
关闭协议：`{"cmd":"shutdown"}`。不能关闭其他任务所属 worker。

代码实际调用：QwenImagePipeline／QwenImageEditPlusPipeline，BF16、enable_model_cpu_offload，
默认文生图50步、改图40步、true_cfg_scale=4.0；参考图经 PIL 加载后传 `image=[PIL.Image]`。
旧 worker **只接一张 reference_path**；当前云端主线最多多张参考图，不能声称旧worker已经等价支持多图。
旧 `asset_studio.backends` 提供 `_GenWorkerClient`、AssetImageT2IBackend、AssetImageEditBackend，
管理加载超时、请求超时及 stderr。不要直接nohup丢弃stdin，也不要手工编资产prompt作为自动链的运行输入。

## 11. 如果新窗口要将当前主线换成服务器模型

这项工作未完成，本次也未实施。先向用户确认要继续 API 路线还是切换本地；
仅有本交接不代表获得了模型运行或后端重构授权。

切换时先读上述旧 transport／worker／H3 bridge，但**不要运行旧整体创作流程**。
当前公开 CLI 没有本地后端开关，`QwenAPI`、`AliyunImages`、`MiniMaxH3` 仍是默认对象。
已有依赖注入接口可测试适配，但不能把它说成已有可执行的服务器全链。

必须保留当前创作／审核提示词、reference_transfer、逐段状态、真实素材观察、Omni剪辑决定，
仅在工具层对齐返回格式、任务身份、实际媒体和后端配置 SHA。
若真的改变后端，另记明确的来源变更，不能把新模型输出嫁接到旧缓存或重置失败视频预算。
服务器没有实时验证、GPU未知、原女生run素材未齐这三件事都要如实说明。

## 12. 给新窗口的首条消息（可以原样复制）

```text
请先完整阅读 C:\Users\29785\Desktop\omni-autonomous-screenplay\HANDOFF.md，
再按第3节顺序阅读当前代码和真实产物。当前主线是这个独立仓库，不是旧douyin研究workflow。
我的最终目标是只输入参考视频，由Omni自主完成故事、资产、素材和剪辑，不要中途人工写故事或选片。
最近只要求整理代码、离线测试及交接；请先确认现状，不自动付费生成、不启动服务器模型或定时任务。
服务器权重和历史启动协议在交接第6–10节；当前独立CLI还没接本地模型，不能只换baseurl冒充接入完成。
先告诉我你核对到的主线状态和下一步，等待我明确选择运行范围。
```

## 13. 交接完成核对

- 本文的源码和本地引用文件已核对；服务器绝对路径保留为历史证据，未声明实时存在。
- 运行代码基线为03fd066；本文只新增文档，不改Python、prompt、旧run或现有模型结果。
- 本次没有读取／写入API密钥，没有SSH、GPU占用、模型加载、生成或任务重发。
- 没有新建goal、自动化或后台监控；新窗口不应从聊天历史自动恢复这些任务。

## 2026-10-04 再追加：实际精看中的 ASR 工程修复

本节追加当前素材库实施事实，不修改上方旧交接、模型原回复或旧缓存。
首个实际 fine 调用 `glm_014` 已收到；第二个原片 1,260–1,350 秒窗口的 Whisper
包含两个零时长词（局部 `0→0`、`89.97→89.97`），首次过严 word 校验导致阻断。
新的 `asr-time-validation-v2` 先保存每次原始推理 `raw_asr_NNN.json` 与 SHA，
零时长词保留 text/raw/alignment_issue，但不给可用 source 时间；有效 segment 仍保留。
严重越界、非有限时间等不作可用证据；已有参考、首 fine 的 ASR 缓存字节未变。
该真实本地窗口复验有 46 个有效段。

可选 ASR 的纯对齐失败单独记录为无可用语音证据，随后继续视觉精看；
源文件／缓存完整性失败仍 fatal，不能自动伪造时间或绕过 SHA 检查。
入口另支持参考达到／超过 8 MB 时生成目标 7.5 MB 的全时长分析 proxy，
完整参考 MP4 和原 reference SHA 均保留，代理另存 lineage，不静默改换创作输入。

修复后媒体与 execute 相关检查 38 项通过；新环境全量 227 通过、2 跳过，
因为 `.venv-library` 未安装冻结发现线的 browser_use/requests；原 `.venv` 中两组
discovery 另有 28 项通过，补齐该依赖范围。不要把重叠测试数量相加为独立总数。
当前实际 4 个 fine 窗口已完成、第 5 个在途，仍没有素材库成片或最终审核结果。

## 2026-10-04 追加：首轮素材库闭环完成

上面的在途描述是实施历史。当前同一 `runs/library_reference_20261004/` 已完成 16 个连续窗口、两版渲染与模型选择。
选择 round 0，`render_0/final.mp4` 约 77 秒，SHA `e6d10d908d8ae508a3df9a36f8630e0b5161e838e8302414d4019bd9255b4111`。
首次评分保留不变；选中版本按最终明确的主旨/剪法规则另审，`selected_review_v2_0.json` 为 partial/partial/pass。
`result.json` 状态是 `library_candidate_with_limitations`。旧主体身份混淆的 fail 与选片理由不是最终质量通过，不能直接比较不同协议评分。

43/80 请求（42 received、004 uncertain），没有重放不明输入或重置预算。第二轮补看没有进入最终 EDL，两版都用了首轮相同的三个窗口，不能声称补看带来提升。
MCP 桥已在所有在途调用结束后关闭；停止连接后的实际缓存重跑没有新增请求，最终 SHA 未变。新未缓存工作在 stop 标记存在时会直接阻断；显式重连只清 stop 标记，不清记录。
本轮有工程和提示协议修复，自动创作由 GLM 完成，但不是冻结线零人工干预验收；未调用 Omni/Qwen、MiniMax、生图或 FlashVID。
最新全量 241 passed、2 skipped，停止连接保护另有 2 项定向通过；原环境发现相关 28 项此前通过，不相加夸大测试总量。
完整事实、费用边界、媒体与限制见 [首轮运行报告](docs/REFERENCE_LIBRARY_FIRST_RUN_20261004.md)。下一轮不得删除这次记录或换目录重置预算。
