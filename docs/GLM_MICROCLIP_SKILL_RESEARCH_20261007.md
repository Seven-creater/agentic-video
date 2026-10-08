# GLM 小范围视频精剪 Skill 调研

调研日期：2026-10-07，Asia/Shanghai。状态：资料与本地接口核查、流程提案；尚未安装或实施新 skill，尚未进行这个流程的 GLM 质量实验。本轮没有模型请求或新渲染。已有账本仍为 207，`mcp_stop` 仍存在。

## 结论与任务范围

可以在当前 Codex 做导师提出的“小范围观察、精细截帧、迭代定位、裁剪验证”。Codex 的 skill 负责组织步骤和调用工具；GLM-5.3-Flash 负责视觉事实、需要补看的位置、关键瞬间和切点决策。流程步骤需要明确传给 GLM，不能只把 SKILL.md 安装在 Codex 后就假设 GLM 也读取了它。

ZCode 是 GLM 的官方 agent 客户端，内置 Flash 用于多模态任务；本项目继续用现有 Codex 会话连接官方视觉 MCP 即可研究这个方法，不必先更换客户端。这里不把 Codex 主模型描述为已切换到 GLM，也不把独立 Python 标准 API 当作套餐 MCP。

当前试验只证明现有方法精剪不足：34→31 秒，77.366667→76.2 秒。它没有证明 GLM 在更可控的精细观察下必然失败或必然成功。后续先验证局部能力，再决定是否扩大到整片。

依据：[OpenAI Skills](https://learn.chatgpt.com/docs/build-skills)、[Codex MCP](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)、[ZCode](https://zcode.z.ai/en)、[GLM-5.3-Flash](https://docs.z.ai/guides/vlm/glm-5.3-flash)。官方支持视觉和给出剪辑使用建议，不等于本任务的关键瞬间精剪已经通过实验。

## 本地已经核实的接口

- `omni_story/library/mcp_bridge.mjs` 固定官方视觉 MCP 与 `glm-5.3-flash`，支持 `analyze_image` 和 `analyze_video`。
- 已保存的 `runs/library_reference_20261004/mcp_tools.json` 是实际工具清单：图片入口只有单个 `image_source` 和 `prompt`；视频入口只有 `video_source` 和 `prompt`，没有 `fps`、多图数组或帧区间参数。网页工具名与本地安装版本有差异，实施应以实际清单为准。
- 本地 30 fps 代理文件不证明云端逐帧观看。没有核验云端抽帧策略。
- 最近的 `parent_cut_pipeline.py` 对每个 slot 进行一次视频决策，随后渲染和做两次实际输出审核；没有按 GLM 指出的缺项不断缩小范围、加密抽帧的观察过程。已有通用剪辑知识不能代替这一机制。
- bridge 的通用图片能力可以复用，但当前直接精剪的守卫只允许已有视频阶段。实际接入需要新增明确的图片观察阶段、媒体绑定和进度记录；不能修改已完成阶段或放开旧守卫来冒充新流程。

见[官方 Vision MCP](https://docs.z.ai/devpack/mcp/vision-mcp-server)。GLM 模型原生支持多图输入，与当前 MCP 单图工具的输入形状是不同层次。

## GitHub 候选：可以借鉴的组件

检查了原始 SKILL.md 及相关脚本。没有找到已验证“无对白电影片段 → 自主关键瞬间 → 精确边界 → 实际剪辑 → 可靠语义审核”的完整现成 skill。

| 候选 | 实际能力 | 对本项目的限制 |
| --- | --- | --- |
| [maxazure/video-editing-skill](https://github.com/maxazure/video-editing-skill/blob/main/SKILL.md) | [frame_grid.py](https://github.com/maxazure/video-editing-skill/blob/main/scripts/frame_grid.py) 提供真实解码帧、PTS、编号网格、单帧导出及收据核验；包含剪辑执行工具 | 很适合借鉴精确观察工具；没有完整的自主观察收敛策略。高光评分脚本主要依赖转录文本。README 声明 MIT，但本次未找到完整 LICENSE 文件，不能直接称为许可已完整核验 |
| [bsisduck/video-analyzer-skill](https://github.com/bsisduck/video-analyzer-skill/blob/main/SKILL.md) | 小范围、采样率、时间戳图与场景检测 | 自适应主要依据视频长度；没有语义缺项驱动的边界迭代或渲染闭环。Bash 工具需适配 Windows，GPL-3.0 代码复用需保留相应义务 |
| [kajisho5/ffmpeg-skill](https://github.com/kajisho5/ffmpeg-skill/blob/main/SKILL.md) | 机械裁剪、变速、定格、实际输出网格与前后对比 | 明确不负责语义瞬间选择；音量、时长代理不足以找到安静但重要的结果。MIT，可借鉴执行与复核组织方式 |
| [oxbshw/watch-skill](https://github.com/oxbshw/watch-skill/blob/main/skills/watch/SKILL.md) | 指定区间和帧、源时间映射、持久索引；[the-loop](https://github.com/oxbshw/watch-skill/blob/main/skills/the-loop/SKILL.md) 要求实际修改后再观察 | 默认密度仍会漏快速动作；循环主要提供观察和批评，调用者执行修改，不是已经训练好的语义剪辑器 |

这些是源码核查结果，没有在本机运行它们的测试或安装完整外部工作流。仓库活跃、测试文件或作者示例不能作为本项目成功证据。

智谱官方也有 [glmv-caption](https://github.com/zai-org/GLM-V/blob/main/skills/glmv-caption/SKILL.md) 和 [glmv-grounding](https://github.com/zai-org/GLM-V/blob/main/skills/glmv-grounding/SKILL.md)：前者描述媒体，后者主要定位对象和追踪。检查对应脚本，默认模型是 `glm-5v-turbo`，固定标准 API 地址；caption 的 SKILL.md 默认型号与脚本还不一致。它们不是本项目的精剪 skill，也不是现有 Coding Plan MCP 适配器，不能直接执行来替换提供方。

## 论文支持与不能照搬的部分

[Iterative Zoom-In: Temporal Interval Exploration for Long Video Understanding](https://arxiv.org/html/2507.02946v1) 将观察组织为时间区间搜索：每次在更小范围采样固定数量的帧，增加局部时间分辨率，保留跨区间帧描述。可以借鉴这种观察结构。

该论文的置信信号包含输出 token 的平均 log-probability；本项目 MCP 没有提供这个可用信号，模型口头声称“有信心”不能替代它。论文验证的是视频问答，不是动作峰值剪辑、可读性或 GLM 的实际成片效果。

[VideoTree](https://videotree2024.github.io/) 与 [VideoAgent](https://arxiv.org/abs/2403.10517) 也支持按当前任务决定继续观察的位置、逐步补足证据。它们不是现成编辑 skill。后一个 VideoAgent 与 arXiv:2403.11481 的同名记忆增强项目不同，不能混用标题和实验结论。

## 建议实现一个窄范围 Skill

下面是针对本任务的工程设计，尚未实测有效：

1. **局部概览与事实记录。** 输入一个可用 slot 及少量带编号、真实 PTS 的帧。GLM 描述可见动作和状态变化，分别记录文字证据与画面证据。原 slot 说明仍是可错导航。
2. **由 GLM 指出缺项。** 明确当前不能确定的动作、人物状态或结果，提出需要补看的小区间与具体问题。Codex 只验证并执行这个观察请求。
3. **加密观察候选及切点邻域。** 可从秒级采样逐步到数帧每秒，再在候选边界附近查看原始相邻帧。采样密度可调；每个小网格保持足够分辨率。GLM引用帧 ID，程序映射到实际时间，避免直接猜秒点。
4. **连续片段确认与微剪。** 连续原速短视频核对动作顺序、峰值及结果。GLM提出保留范围和有理由的局部变速／停留。单张高光姿态不能证明动作完成，场景变化也不等于叙事关键瞬间。
5. **实际输出审核。** 渲染局部结果后，先隐藏预设答案静音描述实际可见内容，再核验丢失信息、冗余、结果可读性和前后衔接。带字幕盲读不能证明纯画面可懂。慢放或停留必须有可见作用，不能仅靠操作名称判好。

“准备、动作变化、峰值、结果／反应”是观察维度，不是每段必须保留的四个镜头。身份、结果或关系需要更多时间时可加长，其他过程可省略；整体目标仍是清楚、精炼并保留主旨。

建议 skill 保持简短，脚本负责可靠抽帧、PTS映射和渲染，引用文档负责观察与微剪准则。GLM 的下一观察请求采用固定动作类型和本地校验，不能执行模型自由生成的 shell 命令。

## 进度与验证方式

每轮需要新增可用帧／相邻片段证据、解决一个明确缺项，或产生可测的剪辑差异。相同输入、相同问题、相同边界的重复回答不算进度；没有新证据或无法解决矛盾时停止，保留局限。格式错误仍最多一次修复，创作失败不能伪装成格式错误反复重试。

先在一段约 5–15 秒、没有未知请求阻断的可用片段上验证：GLM 能否定位真实关键动作、保住可见结果、说明省略内容，实际短片是否比原段精炼且可懂。和最近的一次性局部视频决策作对照；记录实际帧数、调用、时长及错误。窗口长度是提议的试验规模，不是规定剧情秒点。

这个局部能力通过后，再接两版父视频的 slot 顺序与整片审核。模型自评属于模型证据，最终仍需独立观看；不能在小实验失败后重新启动整片规划循环。

未来实际执行仍应在原任务内另记前向阶段和授权，保留旧输入锁、207条账本、004/131/166未知状态及所有原回复，不建新目录重置计数。本文不启动执行、不修改旧结果或授权更多渲染。
