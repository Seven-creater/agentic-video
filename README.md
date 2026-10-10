# Agentic Video

**给定参考视频，从更大的电影素材库中寻找画面，剪出表达相同主旨的新视频。**

Agentic Video 是一个参考驱动的自动剪辑研究原型。参考视频提供表达目标和剪辑意图；模型从已有电影素材中检索、观察和组合片段，形成新故事，再通过精剪和实际成片审阅改进表达。

我们希望观众从画面中看懂人物的处境、行动和结果，同时学习参考的关键瞬间选择、时间压缩、信息揭示和节奏安排。当前重点是电影素材库路线，使用 GLM 理解与决策，由本地程序校验并执行剪辑。

## 核心流程

```mermaid
flowchart LR
    R["参考视频：主旨与剪辑意图"] --> Q["表达需求"]
    L["电影素材库：轻量导航"] --> S["检索与按需精看"]
    Q --> S
    S --> P["组织故事段落与粗剪编辑表"]
    P --> C["渲染可播放粗剪"]
    C --> A["实际粗剪内容与前后呼应审核"]
    A -->|"内容可用"| F["完整粗剪观察、局部补看与技能精剪"]
    A -->|"内容缺项"| S
    F --> E["精剪渲染"]
    E --> V["实际成片盲读与对照审核"]
    V -->|"有新证据和真实修改"| F
    V -->|"可交付或无进一步进展"| D["交付视频与局限记录"]
```

这张图描述任务闭环；具体运行按已登记的阶段和停止条件执行，不无限重剪。

**当前默认服务器入口为 `omni-server start`：实际粗剪 → 审核 → 老师通用 skill 精剪。**
它直接衔接已求证的历史粗剪方法与`visual-story-finecut`，使用实际粗剪视频作为精剪输入。
时长随参考变化，不固定22秒；新任务取消固定总时长、段数、请求数、观察数和轮次数上限，
以新证据、真实剪辑操作及实际审核进展决定继续或停止。
入口、原聊天证明及双参考测试见[干净主流程](docs/HISTORICAL_CLEAN_CHAIN.md)。
本轮实际运行、失败原因和修复验证见[双参考服务器试验记录](docs/CLEAN_CHAIN_SERVER_TRIAL_20261010.md)；
代码检查通过与实际视频质量分别记录。

新增完整功能测试可显式添加`--functional-test`：即使粗剪质量审核失败，也继续精剪和最终审核，负面结果与质量门槛照常记录。本模式只验证整条线路能执行，不代表内容质量通过。2026-10-10最新完整测试中，短参考在粗剪交接时因审核字段类型错误停止，长参考已进入skill精剪；通用字段反馈修复正在验证，整体验收尚未通过。下面的已完成结果属于此前粗剪续跑试验。

**2026-10-10服务器并行实测已结束：两份新的实际精剪均已生成，联合质量仍未通过。**

| 参考 | 实际粗剪→技能精剪 | 执行与质量 |
| --- | --- | --- |
| 21.93秒参考 | 108秒→20.47秒；11段，4处慢放和0.5秒冻结 | 渲染、逐帧/PTS及音轨来源核验通过；人物误认、关键结果与结尾可读性仍有问题 |
| 198.46秒参考 | 250.7秒→197.8秒；14段原速，两处尾帧停留 | 核验通过；“诚信与食品质量”被替换为“自我接纳”，主旨迁移失败，部分补段冗余 |

两路由GLM自主观察和剪辑，没有输入教师影片切点或人工补写剧情；最后分别为`pass/partial`和`partial/partial`的盲读/目标审核，`joint_quality_gate=false`。最终音轨均来自原参考，长参考含对白，因此长版还有动画与参考对白内容不对应的限制。模型调用已经停止，不为强行通过循环重剪。

粗剪与精剪是同一条流水线的连续阶段；新实验恢复方法顺序，不保证模型选择重复旧77秒切点。历史本地77→21.9及2026-10-09服务器178→21.8分别保留，见[历史服务器输出](docs/SERVER_RESTORED_FINECUT_OUTCOME_20261009.md)和[流程回顾](docs/WORKFLOW_RECAP_20261009.md)。

本轮下载到原工作区的交付副本路径如下，与服务器最终视频字节一致，媒体不进Git：

```text
C:\Users\29785\Desktop\omni-autonomous-screenplay\runs\history_clean_chain_20261010\delivery\reference1_glm_finecut.mp4
C:\Users\29785\Desktop\omni-autonomous-screenplay\runs\history_clean_chain_20261010\delivery\reference2_glm_finecut.mp4
```

当前主线由四个文件负责：`server_cli.py`启动、`clean_chain.py`衔接、`pipeline.py`粗剪、`story_finecut.py`精剪。
旧恢复工具只用于历史复查，其入口和结果见[原方法恢复记录](docs/RESTORED_ORIGINAL_METHOD_20261009.md)；新任务使用下面的`omni-server`命令。

- **参考先确定目标。** 素材库提供实现目标的真实画面；不根据素材库擅自替换参考或弱化主旨。
- **段落可以重构。** 模型依据可用素材划分故事段落（slots），允许改变段落数量、顺序和具体情节，同时保持人物身份与事件联系。
- **观看围绕需求展开。** 稀疏画面用于导航，再精看有用区间和上下文，避免先把几小时素材全部精细描述一遍。
- **粗剪建立内容，精剪分配注意力。** 粗剪检查故事前后能否呼应；精剪保留关键变化、删掉冗余过程，为身份和结果留出看清的时间。目标时长接近参考，不一味追求最短。
- **检查真正输出的视频。** 先隐藏预设故事答案，静音描述实际可见内容，再对照目标；将字幕提供的信息与画面证据分开判断。音乐与声画节奏另行评价。

任务约定见 [参考驱动素材库规范](docs/REFERENCE_LIBRARY_SPEC.md)。

## 当前实现

| 部分 | 当前方式 |
| --- | --- |
| 视频理解与剪辑决策 | GLM-5.3-Flash，经 Codex 或独立 OpenCode 执行端连接官方视觉 MCP |
| 调度 | Python 管线、文件作业队列；Linux 可启动脱离终端的后台任务 |
| 素材观察 | 全局稀疏联系表、模型选择的连续片段、真实时间戳帧与局部密帧 |
| 剪辑执行 | FFmpeg 裁切、拼接、局部变速、字幕和真实尾帧停留；各阶段支持范围见实现文档 |
| 可选语言证据 | CPU `faster-whisper` small/int8，辅助理解对白 |
| 验证与恢复 | 文件 SHA、原片时间映射、观察范围校验、实际输出核查、缓存复用与失败留档 |

模型负责参考解释、检索需求、角色与段落选择、入出点和编辑表（EDL）；程序负责证据约束与执行。Codex 另有一次明确授权的教师示范，记录与自主 GLM 试验分开保存。

本地旧入口仍需要有效的 Codex 官方 MCP 连接。新增 `omni-server` 使用服务器上的 OpenCode 和官方视觉 MCP，可以在关闭 Codex、断开 SSH 后继续执行，不需要 GPU。两种连接方式均不把 Coding Plan 当成普通模型 HTTP 服务。

当前实测使用联系表和普通视频代理，**没有使用 FlashVID**；FlashVID 保留为后续粗看研究方案。代理的本地帧率不代表云端模型逐帧观看，云端内部采样策略尚未确认。ASR 也不能证明可见动作、人物身份或音乐节拍。

## 历史本地试验

截至 **2026-10-08**，电影库检索、粗剪和已有粗剪的精剪均已有真实运行记录。该次通用技能试验由 GLM 自主处理完整粗剪，完成一版候选及一次局部修订；后续服务器双参考结果以[当前试验记录](docs/CLEAN_CHAIN_SERVER_TRIAL_20261010.md)为准。

| 项目 | 实际结果 |
| --- | --- |
| 固定参考 | 21.93 秒 |
| 素材库 | 三部《功夫熊猫》，合计约 277.47 分钟 |
| 本次精剪输入 | 77.37 秒粗剪 |
| GLM 精剪交付 | 21.9 秒，1280×720、30 fps |
| 本次执行操作 | 9 段不连续素材，均为原速；结尾停留 0.4 秒 |
| 技术核验 | 交付画面与选中渲染逐帧一致；参考音轨按原速封装 |
| 内容与剪法评价 | 基本事件顺序可读；联合质量通过尚未建立 |

GLM 在这次试验中实现了关键片段选择和时间压缩，没有选择慢放或提速。虽然目标审核给出 `pass`，无字盲读仍是 `partial`；独立审阅发现学习过程、对手结果和部分画面因果缺口，文字仍承担部分含义。音乐卡点、声画艺术及陌生素材泛化也尚未通过验证。

因此，当前成果是**有真实视频与审阅证据的研究原型**，后续需要进一步验证叙事清晰度、关键边界和技巧迁移。

- [历史 GLM 技能试验：实际剪法与审阅局限](docs/GLM_VISUAL_STORY_SKILL_TRIAL_20261008.md)
- [教师示范：完整思考、操作与复盘](docs/CODEX_TEACHER_FINECUT_20261008.md)
- [教师与 GLM：输入隔离及实际编辑表对照](docs/GLM_TEACHER_INPUT_COMPARISON_20261008.md)

2026-10-09恢复试验的历史交付路径如下；素材与运行视频不随代码上传。

```text
C:\Users\29785\Desktop\omni-autonomous-screenplay\runs\server_edit_test_20261009\delivery\server_glm_finecut_20261009.mp4
```

## 剪辑技能与知识

仓库包含两套通用技能，用于把剪辑判断拆成可观察、可执行和可复查的步骤。

| 技能 | 用途 |
| --- | --- |
| [visual-story-finecut](skills/visual-story-finecut/SKILL.md) | 完整粗剪的精炼：判断信息贡献、保留关键变化、按缺项补看、分配识别时间、审阅实际成片 |
| [glm-microclip-finecut](skills/glm-microclip-finecut/SKILL.md) | 小范围动作定位：真实时间戳帧、局部迭代观察、边界确认和实际短片核查 |
| [操作决策卡](skills/visual-story-finecut/references/decision-cards.md) | 判断何时使用省略、慢放、提速或停留，以及每种操作需要什么证据 |

技能提供通用方法。教师示范的电影剧情和具体切点不作为自主 GLM 试验的答案输入；接入技能本身也不代表模型已经掌握所有剪辑技巧。

## 本地准备

当前验证环境为 Windows、Python 3.13、Node.js/npm，`ffmpeg` 和 `ffprobe` 需在 PATH 中。Python 源码位于 `src/omni_story/`；开发时从仓库根目录以 editable 模式安装，CLI 和模块导入使用这份源码。运行所需知识卡与技能文本也随 wheel 打包；根目录 `skills/` 与 `craft_knowledge/` 保留供开发和技能维护使用。

```powershell
git clone https://github.com/Seven-creater/agentic-video.git
cd agentic-video
py -3.13 -m venv .venv-library
.\.venv-library\Scripts\python.exe -m pip install -e ".[library,test]"
.\.venv-library\Scripts\omni-library.exe --help
```

开发安装、三个 CLI 的本地检查、测试和 wheel 构建见 [开发说明](docs/DEVELOPMENT.md)；源码入口见 [代码地图](docs/CODE_MAP.md)。

输入通常放在：

```text
data/ref/video.mp4     # 固定参考视频
data/videos/          # 电影素材库
runs/                 # 本地请求、观察、编辑表、渲染与审阅记录
```

接通官方 MCP 和具体运行步骤见 [本地运行说明](docs/REFERENCE_LIBRARY_LOCAL_RUN.md)。该文包含早期运行快照，当前主线与状态请以[干净主流程](docs/HISTORICAL_CLEAN_CHAIN.md)、[双参考服务器记录](docs/CLEAN_CHAIN_SERVER_TRIAL_20261010.md)和原任务授权为准。

恢复既有任务时复用已完成的结果，保留历史失败与来源记录；结果不明的模型请求不自动重发，不通过删除状态或更换目录重置任务。密钥通过环境或隐藏输入提供，不写入代码与命令行参数。

## 独立服务器运行

服务器路线使用 OpenCode 的国内 Coding Plan 端点，OpenCode 调用绑定到当前任务的官方视觉 MCP。Python 接收原始视觉回复，执行证据校验和 FFmpeg 剪辑；不使用 OpenCode 的总结代替视频观察。

部署环境需要 Python 3.13、Node 22、FFmpeg/FFprobe、OpenCode、`@z_ai/mcp-server@0.1.5` 与 `undici@7.16.0`。完整配置和停止规则见 [服务器运行指南](docs/SERVER_RUN.md)。密钥只通过环境或服务器终端隐藏输入保存，不进入仓库。

已配置的服务器可使用：

```bash
source /home/ubuntu/apps/agentic-video/env.sh
omni-server doctor
omni-server start \
  --reference /home/ubuntu/apps/agentic-video/shared/data/ref/video.mp4 \
  --library /home/ubuntu/apps/agentic-video/shared/data/videos \
  --output /home/ubuntu/apps/agentic-video/shared/runs/my_edit
omni-server status --output /home/ubuntu/apps/agentic-video/shared/runs/my_edit
omni-server logs --output /home/ubuntu/apps/agentic-video/shared/runs/my_edit
omni-server stop --output /home/ubuntu/apps/agentic-video/shared/runs/my_edit
```

`start` 接受新的任务目录，以实际观察、编辑和审核进展决定继续或停止，不设两轮候选或固定总请求数。每个格式错误最多一次协议修复；未知请求、已知传输失败或修复失败时停止，不自动重放或清零用量。视觉调用与 OpenCode agent 用量分别记录。素材上传后校验 SHA，存在 `.part` 文件时拒绝开工。

已结束任务若已有核验过的实际粗剪，可以显式传入`--rough-task <父任务> --parent-task <同一父任务>`，在该父任务的`evaluations`下继续相同后半链。它保留原粗剪、审核和历史用量；执行规则见[实际粗剪续接](docs/HISTORICAL_CLEAN_CHAIN.md)。

历史 Windows 运行保持原件。迁移清单保留 274 次视觉调用基线和所有未知观察的排除范围；同一参考复用已接收的 GLM 导航子对象，保留其协议局限。新服务器请求单独追加计数，不通过迁移清零、重发旧请求或修改旧视频。本轮两份真实电影剪辑已完成渲染和审阅，质量结论以各自原始记录及独立审计为准。

## 项目结构与路线

| 路径 | 内容 |
| --- | --- |
| `src/omni_story/` | Python 包源码；CLI 模块入口与共享媒体、后端逻辑 |
| `src/omni_story/library/` | 当前电影素材库管线、观察、精剪、渲染及恢复逻辑 |
| `skills/`、`craft_knowledge/` | 通用剪辑技能与决策知识 |
| `docs/` | 任务规范、文献调研、实现与实际实验记录 |
| `tests/` | 管线、证据校验、媒体执行与恢复测试 |
| `scripts/`、`.github/workflows/` | 无模型安装检查与 Windows / Linux 持续集成 |
| `src/omni_story/discovery/` | 已冻结的 Qwen 抖音参考发现路线 |
| `src/omni_story/` 其他模块 | 已冻结的 Omni / MiniMax 生成素材路线 |
| `data/`、`runs/` | 本地媒体及运行产物，Git 忽略 |

当前开发围绕参考驱动的素材库剪辑。抖音发现与 MiniMax 生成路线保留代码和历史，不作为当前默认流程启动。

继续开发前可阅读 [HANDOFF.md](HANDOFF.md) 与 [AGENTS.md](AGENTS.md)。研究依据见 [端到端研究](docs/REFERENCE_LIBRARY_RESEARCH_20261004.md)、[剪辑技巧研究](docs/EDITING_TECHNIQUE_RESEARCH_20261004.md) 和 [参考时间压缩研究](docs/REFERENCE_TIME_COMPRESSION_20261004.md)。

此前 README 中的详细进度、失败和旧路线命令已保存在 [历史 README 快照](docs/README_HISTORY_20261008.md)，供追溯。
