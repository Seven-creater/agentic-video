# 剪辑知识与参考迁移 skill 调研

日期：2026-10-04。范围：调研现有 skill、剪辑资料及论文；未安装插件、改变模型、
修改运行代码或调用 GLM。原任务仍为 76/80 次请求、16 个唯一精看窗口、四次授权渲染。
本文件是后续设计依据，不是新运行授权或已实现能力。

## 结论与本项目已知事实

已有可借鉴的官方与开源 skill。最合适的组合是剪辑知识卡、模型自主精看、
可执行编辑操作以及成片复核；不能保证读文档后立即准确识别所有手法。

当前 `semantic_prompts.plan_prompt` 已允许多个不连续短片段、关键动作慢放、重复过程加速。
本轮实际 EDL 仍为五条原速片段。因此，“模型未被告知可以变速”不是准确的根因描述。
我们没有受控实验能证明 GLM 缺乏剪辑知识，或仅增加术语就能修复素材误认和审核问题。
实际记录见 [本轮报告](REFERENCE_LIBRARY_SEMANTIC_RUN_20261004.md)。

## 已核实的 skill

| 来源 | 实际可借鉴内容 | 与本项目的边界 |
| --- | --- | --- |
| [Z.ai 官方 video-agent-kit](https://github.com/zai-org/zcode-plugins/blob/main/plugins/video-agent-kit/README.md) | 全片时间戳抽帧、局部高 fps 复看、基础变速/切片/定格、timeline、预览、技术 QC、修复记录 | README 明确不负责素材库检索；工具返回图片让主 Agent 观察，不在工具内调用推理模型。不能直接当成 GLM 剪法识别专家 |
| [Qwen 官方 editing-director](https://github.com/QwenLM/Qwen-MM-Plugins/blob/main/src/capabilities/video-edit/skill/SKILL.md) | craft/looks/workflows/review 分层；参考复刻先全局看再局部看，保存证据，检查实际输出 | 含固定设计偏好、HyperFrames 路由和工具依赖；仅借用通用方法，不自动更换模型或全盘采用固定结构 |
| [ECC taste-distillation](https://github.com/affaan-m/ECC/blob/main/skills/taste-distillation/SKILL.md) 与 [taste-application](https://github.com/affaan-m/ECC/blob/main/skills/taste-application/SKILL.md) | 镜头边界、镜长分布、测量约束、准确裁切和输出测量 | 主要覆盖色彩与节奏统计，不能用相同镜长分布证明叙事、动作省略或高光选择正确 |
| [ffmpeg-video-editor](https://github.com/bryanwhl/ffmpeg-video-editor/blob/main/SKILL.md) | 切片、变速、转场、编码等操作示例 | 以命令知识为主；知道执行方法不等于知道应当如何剪 |

Z.ai 官方 marketplace 在检索时列出 video-agent-kit v0.4.3、作者 Z.ai、requiresPaidPlan=true。
这是 ZCode 市场元数据，不证明国内 Coding Plan 在当前 Codex 宿主中自动包含其全部服务。
[官方市场记录](https://github.com/zai-org/zcode-plugins/blob/main/marketplace.json)

当前 Python 只向视觉 MCP 传 `video_source/image_source` 与 `prompt`。
安装到 Codex 的 SKILL.md 不会自动成为 GLM 的输入。接入时要明确转发有关知识，
或让 GLM 输出观察请求，由执行器提供对应媒体，再把结果送回 GLM。
现有 GLM/Codex 分工保持不变，不能把主 Agent 看图选片默认为 GLM 创作。

## 最相关的论文

**VEU-Bench，CVPR 2025。** 用剪辑知识 ontology 组织特征与功能，覆盖镜头属性、
速度、切镜与转场，区分识别、推理和作用判断。§4.3.2–4.3.3 发现剪辑语境提示有帮助，
但过长或过度限制的提示可能降性能；会解释概念的模型仍可能认错画面。
研究对象没有验证本项目 GLM-5.3-Flash，不能直接迁移其效果数字。
[正式论文](https://openaccess.thecvf.com/content/CVPR2025/papers/Li_VEU-Bench_Towards_Comprehensive_Understanding_of_Video_Editing_CVPR_2025_paper.pdf)，
[全文](https://arxiv.org/html/2504.17828v1)

**Automatic Non-Linear Video Editing Transfer，CVPR 2021 workshop。**
从参考镜头提取构图、内容、运动、速度、亮度，检索素材并迁移样式。
§3.3.2 准确播放倍速仍依赖人工标注，采用每镜恒定值；不是完整自主速度曲线识别，
也没有解决异源主旨及角色事件重构。
[全文](https://arxiv.org/html/2105.06988v1)

**DIRECT，2026 年 4 月预印本；CutClaw，2026 年 3 月预印本。**
可借鉴表达需求到素材查询、细粒度裁切、反馈与回退。两者不等于已验证的
参考技法迁移，也不是可直接装给 GLM 的专业权重。
[DIRECT](https://arxiv.org/html/2604.04875v1)，[CutClaw](https://arxiv.org/html/2603.29664v1)

## 剪辑资料

[Yale Film Analysis：Editing](https://filmanalysis.yale.edu/editing/) 有定义和影片例子，
可用于连续性、动作匹配、省略剪辑及蒙太奇的识别依据。
省略剪辑是删去事件的一部分；不应把所有时间压缩都统一称为 jump cut。

[Adobe 时间重映射](https://helpx.adobe.com/ae_en/premiere/desktop/edit-projects/change-clip-speed/change-clip-speed-and-duration-using-time-remapping.html)
说明分段速度和关键帧的执行方法；它是工具说明，不证明模型可以从未知原片还原准确倍速。

[The Technique of Film and Video Editing，出版社目录](https://www.routledge.com/The-Technique-of-Film-and-Video-Editing-History-Theory-and-Practice/Dancyger/p/book/9781032849799)
按叙事清晰、戏剧强调、类型、连续性、节奏和声音组织剪辑知识。
本轮只核实出版社介绍和目录，没有阅读整书，不声称存在一份穷尽所有手法的统一清单。

## 针对本任务的设计建议，尚未实现

先建立一个按需读取的通用手册。每张知识卡记录：

`手法名称 → 可见识别条件 → 常见混淆/反证 → 表达目的 → 素材前提 → 编辑操作 → 输出检验`

不写本条参考的秒点、人物、动作答案、固定段落数或必须采用的技巧。
通用术语和异源示例属于领域知识；给出本条参考的正确切点和答案属于任务标注，
不得作为本次自主识别的输入。知识库允许 unknown/other，避免强行套标签。

建议先覆盖以下通用类别，而非一次塞入整部教材：

| 类别 | 自主观察问题 | 执行与验证重点 |
| --- | --- | --- |
| 时间省略／过程蒙太奇 | 哪些状态变化可见？是否跳过中间过程？ | 多个不连续短片段；保留能建立关系的必要画面 |
| 动作匹配／动作上切 | 相邻镜头中的动作阶段、方向是否对应？ | 同动作切点或有依据的衔接；不可凭名称认定匹配 |
| 动作与反应组织 | 行动后谁产生何种可见反应？ | 反应是否对应当前人物与事件，而非仅表情相似 |
| 慢放／加速／速度变化 | 局部运动是否有异常速度或变化？还有哪些解释？ | 参考速度先标估计；新素材倍速是实际执行参数，两者分开 |
| 长短镜头与信息揭示 | 每镜新增什么信息？何处停留、何处推进？ | 删除冗余但不删关键因果；不固定每镜同长 |
| 定格／重复／回放 | 连续画面是否停住或重现？作用是什么？ | 真正执行定格或回放，记录源时间与输出时间 |
| 转场／构图匹配 | 切镜之间的空间、形状、运动如何联系？ | 按素材和表达目的选择；不强制花式转场 |
| 声画关系 | 是否有可靠的声音观察或测量？ | 视觉 MCP 无音频核验时保留未知；ASR 不能证明 BGM 节拍 |

工作流应是：全片中性扫描 → GLM 提出手法假设及复看需求 → 按需读取知识卡 →
执行实际局部复看 → 形成有证据与不确定性的剪法记录 → 检索符合条件的素材 →
精看所选切片 → 写 EDL → 审核成片并将反证送回规划。

当前执行器已支持多个短片段、恒定 0.5–2 倍速和尾帧定格；
平滑连续速度坡道、复杂转场或 J/L cut 不能仅靠补文档声称已经实现。
要将知识卡的操作映射到真实支持能力，未支持的能力保持明确状态。

初步验证应分别考察知识调用、视觉识别、表达作用、实际执行和观看可读性，
不能以“手法名称数量增加”当作改善。对照设计需使用一致媒体、预算和评估标准，
不以更多观察成本冒充 skill 效果；运行时保持旧记录与预算规则，评估标注与创作输入分开。
先验证知识和观察协议，再决定是否开展新的完整剪辑实验。
