# 参考理解到剪辑：同一条链的研究与代码落实

日期：2026-10-02。范围：独立仓库的代码与离线验证，不重新运行付费模型。

## 1. 已定位的问题

用户的要求是输入参考视频，Omni 自主理解、创作、组织资产和素材，最后剪辑成片。
剪辑不是末端才新增的另一份任务：参考片如何安排信息，应在参考理解时形成记录，
写故事和准备素材时就保留可实现这种表达的机会；实际入出点必须等素材生成后决定。

现有第二条参考片运行 `runs/reference_7682750673951198510_001` 的只读核对结果：

- 参考片实测 37.533333 秒，五段源素材合计 64.791666 秒，旧成片实测 45.666667 秒。
- 12 行剪辑表实际上对应 5 段连续源素材，约保留源时长的 70.48%。相邻行从同一素材
  的上一结束点接着播放，速度与效果不变，不能把这算成新切镜。此统计只分析编辑器
  的连接，不统计生成视频内部的镜头切换。
- 旧生产路径在素材观察后另做 `REFERENCE_EDITING`，写故事和规划素材时没有共享这份
  剪辑观察。旧成片审核主要查叙事和粗节奏，未逐项核实参考方法是否在输出中出现。
- 原混音把 17.5 秒音乐片段播放一次后补静音。第 36 次请求由 Omni 决定原速循环；
  重混音保留原画面码流。预算随后耗尽，新混音没有另做模型看片。

这些事实足以说明旧结果不能称为剪辑风格已迁移；但保留率或时长比例本身并不能证明
剪辑质量差，较长的情绪停留也可能合理。本次不把上述数字升级成固定质量门槛。

五段素材 SHA 均与提交记录相符，父 manifest 验证通过。最新旧成片 SHA256：
`9c38470da2e6103abf6d4aecc01306444905dfd98a4518cfba9b0c23a30fe36c`。
本轮只读核对与返回旧缓存前后均为 36 次生产请求，没有重生成、重选片或改写旧 run。

## 2. 官方 skill 与论文：具体借鉴与边界

### Qwen 官方视频剪辑 skill

核对仓库 [Qwen-MM-Plugins](https://github.com/QwenLM/Qwen-MM-Plugins/tree/ea955c9ed0f0359dc660c5a4f5b2c877d4436812/src/capabilities/video-edit/skill)，
固定 commit `ea955c9ed0f0359dc660c5a4f5b2c877d4436812`；仓库许可证 Apache-2.0。
完整阅读的文件包括 `SKILL.md`、`workflows/style-replication.md`、
`workflows/vlog-multi-source.md`、`craft/pacing-rhythm.md`、`craft/music-beat-sync.md`、
`review/source-review.md` 和 `review/final-review.md`。

- [风格迁移](https://github.com/QwenLM/Qwen-MM-Plugins/blob/ea955c9ed0f0359dc660c5a4f5b2c877d4436812/src/capabilities/video-edit/skill/workflows/style-replication.md)：
  全局扫描后局部检查，保存带时间的观察，并在输出效果时间点验证。用于联合参考记录、
  局部观察和成片方法对照，不照搬其精确复刻要求及 HyperFrames 执行体系。
- [多源自主剪辑](https://github.com/QwenLM/Qwen-MM-Plugins/blob/ea955c9ed0f0359dc660c5a4f5b2c877d4436812/src/capabilities/video-edit/skill/workflows/vlog-multi-source.md)：
  先理解真实素材，再决定可用段落和排列。用于素材观察与实际源入出点，不把整条素材
  当作不可拆分单元，也不把剪辑表行数当镜头数。
- [节奏](https://github.com/QwenLM/Qwen-MM-Plugins/blob/ea955c9ed0f0359dc660c5a4f5b2c877d4436812/src/capabilities/video-edit/skill/craft/pacing-rhythm.md)
  与 [最终审阅](https://github.com/QwenLM/Qwen-MM-Plugins/blob/ea955c9ed0f0359dc660c5a4f5b2c877d4436812/src/capabilities/video-edit/skill/review/final-review.md)：
  工具测量和媒体观看互补，审阅绑定实际输出，修改音画后旧审阅不能直接继承。

本项目没有安装或直接调用这套外部 skill，也没有复制其代码。迁移的是可核查的操作
方式，落在本仓库通用提示词与 FFmpeg 工具中。没有照搬强制标题／正文效果、固定切镜
密度、精确卡点或遇到未知效果就停工的规定。音乐峰值不是已证明的节拍同步；本版未
实现 beat-grid、J/L cut、多面板、遮罩或复杂动画，不能声称这些已经学习或复现。

### Agent-based Video Trimming（AVT）

[论文](https://arxiv.org/html/2412.09513v1)将真实视频结构化、过滤低价值区间、组织故事并
评估实际成片。核对 [源码](https://github.com/ylingfeng/AVT/tree/7186a79fe0b843260051c8633f858bd8597dfeb8)，
固定 commit `7186a79fe0b843260051c8633f858bd8597dfeb8`；完整阅读 `tools/get_story.py`、
`tools/get_video.py`：选择结果映射回源时间区间，再裁剪和排列，不只是按上传顺序拼接。
本项目借鉴可用瞬间、重复／无信息过程与实际区间的分离；不采用其固定粗切片、低帧率、
依赖包和失败后使用全部素材的策略。该工作不保证参考风格迁移或本项目故事正确。

### Shot-by-Shot

[论文](https://arxiv.org/html/2504.01020v1)利用相邻镜头上下文及电影语法形成描述。
核对 [仓库](https://github.com/Jyxarthur/shot-by-shot/tree/cf832444d1b9f7914b68a9534f8f04f2985ad50b)
commit `cf832444d1b9f7914b68a9534f8f04f2985ad50b` 的项目说明和 `film_grammar/README.md`。
本项目借相邻画面和状态变化的观察方式；它主要解决音频描述，不是自主创作剪辑的
效果证明。没有下载其电影语法组件权重或声称整套方法无需额外模型。

### VEU-Bench 与定位边界

[VEU-Bench](https://arxiv.org/html/2504.17828v1)把剪辑识别、功能推理和判断区分开。
因此本版区分观察、目的假设、计划和实际输出审阅，不让写了效果名称等于复现了效果。
这是一项评测研究，不是现成的生成算法。
[VEBench](https://arxiv.org/html/2605.03276v1)进一步分别测试片段选择和时间定位，说明
术语识别不能替代可用区间定位。本版仍把 Omni 时间码标为模型估计，不报告没有人工
标注支撑的定位准确率。两项研究均不能作为本项目已有效的证据。

## 3. 已实现的控制流

```text
pipeline.execute(reference)
  → Loop.call(REFERENCE)：同次原声原字观看，内容＋剪辑观察
  → 至多两次模型提出问题的 REFERENCE_LOCAL 原片局部观看
  → reference.build_transfer：只派生与绑定来源，不补写语义
  → ROUTES → OUTLINE → SEGMENT：始终携带同一 reference_transfer
  → 剧本与固定资产描述
  → production.execute_production / PLAN：编辑方法对应素材覆盖机会
  → 原 Aliyun 图像／改图 API → MiniMax H3 素材
  → production.edit_sources / WATCH：观看真实素材及关键状态变化
  → EDIT：实际源入出点、信息取舍、顺序、粗节奏、可支持效果
  → editing.compile_plan / render：验证范围并执行，不替模型选片
  → FINAL_REVIEW：实际成片＋同一参考记录＋工具测量
  → 至多一次 Omni 重剪；必要时选择现有最佳成片，保留限制
```

全片参考调用默认请求 2 fps，局部与实际素材／成片请求 4 fps。记录请求 fps、媒体 SHA
与时区间；服务端没有报告实际采样帧，因此明确记为未知，不能拿请求 fps 证明覆盖。
局部输出附入联合记录，原首轮理解另存；程序不自行解决相互矛盾的模型结论。

同一链允许分阶段执行和不同专业提示词，但不是各自生成互不关联的参考解释：
`reference_transfer.json` 的原片 SHA、理解 SHA 和实际内容在下游校验。
纯画面盲读故意不接收作者意图和参考答案，避免“知道答案后看懂”的自证；这不割裂链路。

## 4. 数据与执行范围

- `reference_reading.json`：原片内容、观众理解、逐段剪辑观察、方法、目的假设、未知项。
  方法数量开放，允许空列表，无必选叙事角色／剪辑术语。
- `reference_transfer.json`：共享来源和相对信息任务；精确模型观察范围留在参考区。
- `production_plan.json/editing_intentions`：各方法的素材覆盖机会或缺口。此时不存在
  实际素材切片，不能预排入出点。
- `source_observations.json`：实际源视频的变化、结果、停顿和限制。提示词中的预期
  行为不能冒充已发生的行为。
- `edit_plan_*.json/style_mapping`：Omni 实际选择及方法的应用、调整、不可用或未知。
- `render_*/edit_metrics.json`：连续素材段、编辑器连接、保留源时长、片段时长和与参考
  时长的比例；只是反馈数据，不含“必须删到某百分比”的规则。
- `final_review_*.json/style_review`：Omni 观看输出后定位可见／调整的方法，未见与不明
  单列；`style_transfer_result.json` 绑定被看片 SHA，不提升参考假设为真值。

执行器支持硬切、短淡入淡出、固定片段速度、灰度、灰度向已有原色渐显，以及原速
音乐裁剪／循环／淡入淡出。颜色渐显只改变已有像素饱和度，不创造彩色物体或剧情。
复杂转场、字幕动画及精确音乐同步未实现时可以继续交付有限制成片，不能虚报迁移成功。
旧接口和原始 run 保留：缺少共享记录的旧父版本明确走 `legacy_late_reference_call`，
不会凭新代码把历史结果改标为联合分析。

## 5. 验证与尚未实测的部分

64 个本地测试通过，`git diff --check` 无差异格式错误；测试包括：

- 同一参考记录贯穿写作、素材规划、素材观看、编辑和成片审阅；新链没有额外全片
  `REFERENCE_EDITING` 调用；来源被改写时阻断。
- 局部问题由合成模型返回，不包含人工剧情或首轮预期结论；局部实际媒体保留音轨。
- 空／未知方法可以继续，未复现风格的可播放输出保留为有限制候选。
- 连续 12 行仍计 5 段素材，不伪报 11 次切镜；灰度渐显实际 FFmpeg 输出和时间范围正确。
- 任务与请求复用、不重发不确定 POST、不绕过预算、成片缓存（含有限制候选）安全返回。

合成模型响应只是程序测试，绝不是真实 Omni 风格迁移效果。原 donor 提示词和
`SOURCE_PROVENANCE.json` 未改；新提示词版本与运行代码各自留档。
没有训练、安装第三方工程、下载新权重或自动生成新的付费素材。

当前历史运行生产侧 36 次请求已耗尽。本次新增付费调用 0，未用新 run 重置预算。
代码已完成，新的真实选片／风格对照尚未运行：后续实测需用户明确授权新的有界模型
调用范围，并只复用现有五段素材。不能把本地通过写成“新片剪辑效果已通过”。
