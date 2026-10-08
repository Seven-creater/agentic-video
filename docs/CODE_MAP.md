# 实际代码地图

## 源码布局与入口

Python 包使用 `src` 布局，从仓库根目录执行 `python -m pip install -e ".[library,test]"` 后开发。包名仍为 `omni_story`，模块命令仍为 `python -m omni_story.library`。目录移动不改变已有任务的授权、预算或运行记录；旧实验文档中的路径保留其历史语境。

```text
src/omni_story/       Python 包源码
  library/           当前参考驱动电影素材库路线
  discovery/         冻结的抖音参考发现路线
tests/               单元、协议与媒体执行测试
docs/                当前说明及历史实验记录
skills/              通用剪辑技能
craft_knowledge/     通用剪辑知识卡
data/、runs/         本地媒体与运行记录（Git 忽略）
```

| CLI | 源码入口 | 范围 |
| --- | --- | --- |
| `omni-library` | [`src/omni_story/library/__main__.py`](../src/omni_story/library/__main__.py) | 当前电影素材库路线 |
| `omni-story` | [`src/omni_story/__main__.py`](../src/omni_story/__main__.py) | 冻结的参考到生成素材路线 |
| `omni-discover` | [`src/omni_story/discovery/__main__.py`](../src/omni_story/discovery/__main__.py) | 冻结的抖音参考发现路线 |

安装、无模型本地检查、测试与构建命令见 [开发说明](DEVELOPMENT.md)。

## 当前电影素材库路线

| 模块 | 用途 |
| --- | --- |
| [`library/pipeline.py`](../src/omni_story/library/pipeline.py) | 基础检索、观察、计划、渲染与实际输出审阅 |
| [`library/state.py`](../src/omni_story/library/state.py) | 输入锁、请求账本、缓存和恢复约束 |
| [`library/media.py`](../src/omni_story/library/media.py)、[`library/render.py`](../src/omni_story/library/render.py) | 媒体取证、时间范围与 FFmpeg 执行 |
| [`library/mcp_launch.py`](../src/omni_story/library/mcp_launch.py)、[`library/mcp_bridge.mjs`](../src/omni_story/library/mcp_bridge.mjs) | 当前 Codex 官方视觉 MCP 会话与文件队列连接 |
| [`library/visual_story_trial.py`](../src/omni_story/library/visual_story_trial.py)、[`library/visual_story_trial_state.py`](../src/omni_story/library/visual_story_trial_state.py) | 独立授权的通用精剪技能试验及状态约束 |
| [`library/microclip_v2.py`](../src/omni_story/library/microclip_v2.py) | 独立授权的小范围动作精剪 v2 |

各续跑协议按对应实现文档和已登记授权执行。最新实际状态见 [GLM 通用技能试验记录](GLM_VISUAL_STORY_SKILL_TRIAL_20261008.md)；这些源码入口本身不授权新模型请求、重发未知请求或增加渲染。

以下链路说明保留生成素材路线的代码与数据关系，当前默认开发路线以电影素材库为主。

## 冻结生成路线的主入口与自动链

```text
__main__.main(--video, --output, --stage=all)
  → production.full_run
     ├─ 文本尚未完成：pipeline.execute
     │   → pipeline.analyze_reference
     │      → Loop.call + prompts.REFERENCE：原声原字内容／剪辑联合观察
     │      → 可选 REFERENCE_LOCAL：Omni 提出至多两个局部问题
     │      → reference.build_transfer：共同参考记录及父 SHA
     │   → ROUTES：三个主题及自主选题
     │   → Loop.stage(OUTLINE)：整体框架和固定资产
     │   → 逐 unit Loop.stage(SEGMENT)：前文状态、逐段写作与审核
     │   → BLIND：不读作者主旨的画面盲读
     │   → ALIGNMENT：主旨与证据机制对照
     │   → 剧本、来源和 manifest
     │
     ├─ 尚无完整素材：production.execute_production
     │   → verify_parent / load_reference_transfer
     │   → Calls.call + PLAN：资产、完整动作素材、剪辑覆盖机会
     │   → AliyunImages.generate：固定资产主图及素材起始画面
     │   → IMAGE_REVIEW：真实图片粗一致性审核，有限改图／候选选择
     │   → MiniMaxH3.generate：三路并发、各素材只提交一次
     │   → production.edit_sources
     │
     ├─ 全部素材已完成：production.finish_existing_media
     │   → 只恢复实际素材观察与剪辑，不构造图片／视频生成后端
     │   → production.edit_sources
     │
     └─ production.continue_music_coverage
         → 必要时向 Omni 提供音轨静音测量
         → 只调整音乐，不改变画面切片，不拉伸音乐
```

`--stage screenplay` 仅调用 `pipeline.execute`；`--stage reedit` 调用
`production.reedit_existing_media`，是对已完成素材的显式新续剪，不是另一条故事生产路线。

## 剪辑不是按生成顺序拼接

```text
production.edit_sources
  → editing.review_copy：真实素材的观看副本
  → Calls.call(WATCH)：实际可见状态、变化和可用信息
  → Calls.call(EDIT)：Omni 选择素材 ID、in/out、顺序、速度、效果和音乐
  → editing.compile_plan：核对实测源时间、重复区间和支持范围
  → editing.render：裁剪、执行效果、拼接和音乐
  → editing.edit_metrics / audio_measurements：程序测量反馈
  → Calls.call(FINAL_REVIEW)：Omni 观看实际输出而非只读编辑表
  → 最多一次替换编辑表，再观看／选择已有成片
```

新全链读取同一份 `reference_transfer`，不在剪辑末端再独立生成互不关联的参考解释。
旧产物缺少该字段时明确走兼容分支 `legacy_late_reference_call`，不改写历史来源。

## 谁做判断，谁做执行

| 工作 | 决策／执行主体 |
| --- | --- |
| 参考内容、主旨、段落功能、剪辑目的假设 | Omni；观察与假设分开记录 |
| 主题、故事、人物／道具／场景描述、动作与状态 | Omni；逐段传递已有前文 |
| 批评、有限修订与现有候选比较 | 独立模型请求；不是人工真值 |
| 图片是否保持粗身份、改图内容、较好候选 | Omni 看实际图片 |
| 素材起始条件、生成提示词和时长 | Omni；资产／视频后端执行 |
| 实际保留哪个区间及如何安排 | Omni 看真实生成素材后决定 |
| ID、字段、时间范围、文件 SHA、缓存和任务身份 | 程序确定性检查 |
| 最终裁剪、速度、支持的效果及音轨操作 | FFmpeg 执行；不替模型重新选片 |

通用提示词是程序协议，不是针对某条片子的人工答案。人工没有中途选题、接受草稿、
手写剧情、选图或选片接口。存储路径、凭据和支出授权是基础设施，不是故事输入。

## 核心数据及真实产物

| 文件 | 内容与下游用途 |
| --- | --- |
| `input_lineage.json` | 参考 SHA、媒体元数据、模型配置、代码和提示词 SHA |
| `prompts.json`、`calls/` | 实际请求、原始响应、格式检查与 token／时间 |
| `reference_reading_draft.json` | 首轮内容／剪辑理解，不被局部附加记录覆盖 |
| `reference_reading.json` | 联合理解、局部观察和不确定项 |
| `reference_transfer.json` | 同一参考记录的来源绑定及可迁移信息任务 |
| `routes.json`、`outline.json` | 自主主题选择与整体框架 |
| `asset_bible.json`、`U*.json`、`screenplay.json` | 固定资产、逐段动作与完整剧本 |
| `blind_reading.json`、`alignment_review.json` | 文本盲读及主旨对照，不冒充媒体核查 |
| `production/production_plan.json` | 资产、生成素材和覆盖机会；没有虚构的实际切片 |
| `asset_inventory.json`、`start_frame_inventory.json` | 真实图片文件、来源、审核和候选选择 |
| `image_jobs_aliyun/`、`video_jobs/` | 不可重复提交身份、响应、任务 ID、轮询及实测输出 |
| `source_inventory.json`、`source_observations.json` | 真实视频与 Omni 看片观察 |
| `edit_plan_*.json` | Omni 对实测素材的实际编辑决定 |
| `render_*/final.mp4`、`edit_metrics.json` | 可播放渲染与程序统计 |
| `final_review_*.json`、`style_transfer_result.json` | 实际成片审阅、风格呈现与缺口 |
| `production/result.json` | 被选中的最终文件、SHA、状态和限制 |

文本、素材与成片分别核查；有文本来源 ID 不等于对应动作已成功生成。
镜头表行数不等于真正切镜数；统计区分连续源片段与编辑连接，不测生成视频内部切镜。
请求 fps 有记录，服务端实际采样没有报告，不能声称已知实际取帧密度。

## 当前范围与兼容代码

执行器支持硬切、短淡入淡出、固定片段速度、灰度和灰度向已有原色渐显。
音乐仅从 Omni 定位的参考音乐区间裁剪／循环、调整增益和淡变，保持原速；不做干净 BGM 分离。
没有字幕动画、合成配音、复杂转场、自动精确节拍网格或所有电影语法效果。
未知手法或素材缺口留在候选限制中，不强行装饰或伪报成功。

`media_backends.ImageHelper` 与 `production.cloud_restore_lineage` 为历史接口失败／恢复记录的
兼容代码。默认全链后端是 `AliyunImages`，不会自动尝试 ImageHelper 或切换供应商。
`donor_prompts.py` 是本地复制并标注来源的基础协议，不在运行时导入原项目。

完整真实运行与研究历史分别在 `REAL_RUN_STATUS.md` 和 `REFERENCE_EDITING_CHAIN.md`，
不把实验产物、失败请求或参考故事答案复制进源码包作为新创作输入。
