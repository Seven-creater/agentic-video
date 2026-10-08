# 通用视觉叙事精剪 skill 的 GLM 实测

2026-10-08，Asia/Shanghai。用户在教师示范交付后明确要求：
“下一步就可以迁移到glm，让glm试一下。”

## 本次范围与方法

GLM-5.3-Flash 使用 [通用 skill](../skills/visual-story-finecut/SKILL.md) 和
[决策卡](../skills/visual-story-finecut/references/decision-cards.md)，
对原 `render_0/final.mp4` 77.366667 秒粗剪自主划段、选择精看范围、选择切点与速度，
目标接近 21.933333 秒参考，不追求最短。
没有向模型提供教师视频、教师切点、教师电影剧情复盘或指定哪个动作应该保留。
仅空间黑边检测帮助可见性：自动得到 `[0,480,720,364]` 全内容视图；
画面主体 `[0,484,720,310]` 用于去字幕盲读。不是按电影内容预选时间。

完整参考有原不明请求 131，故整段视频不重新发送，也不换编码绕过。
初始参考上下文来自已收到的 GLM 060 原回复中的 reference / editing_reference 子对象，
绑定原 request/response SHA，明确保留其覆盖合同失败和模型估计局限。
模型可按缺项自行选参考局部补看；不能切碎整条参考伪装重放。
77 秒父视频则是独立输入，与旧不明 004/131/166 的媒体和范围均不同。

执行顺序为：完整父片原速静音观察 → 模型自主提出局部问题 → 连续短片与真实 PTS 图 →
完整 EDL → FFmpeg 成片 → 新上下文去字幕盲读 → 独立目标比较。
最多一次有具体实际输出反馈的局部改版，不自动启动下一轮。
原音轨仅在画面确定后按原速封装；不靠听觉缺失否定已经看清的画面。
源片缺少结果必须记录，不能以字幕、慢放或停帧创造结果。

## 接入与保护

在同一 `runs/library_reference_20261004/`、250 调用基线上追加
`visual_story_skill_trial_v1` 授权，未重建普通任务或重置 80。
原前 250 条调用、原 artifacts、旧文件 SHA 与三条 HTTP 日志已有字节前缀均绑定。
新 State/JS guard 只接受 `vss_*` 阶段，旧 microclip/Goal 权限不放宽。
桥接跳过所有旧队列，恢复仅检查新调用，004/131/166 状态不改变。
单 lane 对三条历史日志的 POST hash 都去重，SDK 自动重试仍关闭。
每次已知格式失败最多一次修复；pending / uncertain 不重发。
Goal 保持暂停，MiniMax 和抖音发现保持冻结。

授权与知识快照：`artifacts/visual_story_skill_trial_v1_001.json`、
`artifacts/visual_story_skill_trial_v1/baseline_state.json`、`knowledge/`。
模型请求、原回复、解析与失败仍进入原 `calls/`，沿用累计编号。
这一次的渲染与记录在 `artifacts/visual_story_skill_trial_v1/`，不是新任务目录。

新增入口：

```powershell
.venv-library\Scripts\python.exe -m omni_story.library.visual_story_trial --output runs/library_reference_20261004 --run
```

授权已登记，重复入口只允许同授权下的等待/已收到缓存，不自动创建下一轮。
真实执行时，需要同一 Codex 会话的官方 `mcp_launch` 连接，未改成独立套餐 HTTP 服务。

## 工程检查与首次实际进度

- 新状态、流程、JS guard 联合 67 项合成/媒体检查通过。
- Bridge/launcher 与旧 microclip、extension、independent、Goal、slot guards 相关回归 140 项通过；
  最终新增范围别名和桥接复测另有 39 项通过。
- 首次连接使用官方 MCP `@z_ai/mcp-server@0.1.5`，GLM-5.3-Flash，
  输出上限 131072、模型超时 600 秒，工具超时 660 秒，自动重试关闭。
- 251 `vss_observe` 已登记提交。此时尚未有新 GLM 成片，不能宣称 skill 迁移质量已通过。

以上测试验证程序行为，不是模型剪辑质量。
后续实际回复、成片及审查结论在本文件追加，不提前填成功。

## 实际停止：265 连接重置，尚无成片

本轮新增15次登记（251–265），其中251–264共14次均收到原始回复且有解析记录；
265为结果不明。没有 EDL、渲染或新 GLM 视频，不建立 skill 剪辑质量通过。
MCP/管线进程已结束，Goal仍暂停，没有266请求。

GLM自主划出五项表达贡献，选择训练5–9秒、手指38–41.5秒、
冲击波41.5–45秒、回归54–58秒四个精看范围，step=0.25秒。
每处连续短视频后提供真实源PTS图片页，最多六帧一页；实际帧间距按manifest记录，
不能把请求step当作每个间距恰好0.25秒。没有给它教师切点。

可观察到的进展与限制：

- 原速粗观察把训练称为“冲刺”；254/255图片观察识别了蛇形角色、失衡、
  空中翻转、下坠与落地，指出冲刺描述不符。触发接触的精确时刻仍未确认。
- 257/258/259记录手指、表情变化和长期存在的字幕，但没有确认精确收指触发，
  也不能把字幕出现时间当成发声瞬间。
- 260–263检查冲击出现与扩散；264完成回归54–58秒的连续片段观察。
  下一页图片请求265失去回复，尚未汇总最终EDL。
- 这些是模型观察，仍可能错误；没有把“提供了完整30fps代理”写成云端逐帧理解。
  实际API提交未含fps/num_frames参数，官方MCP没有本地视频抽帧环节。
  详细事实见新目录 `runtime_audit/`，云端内部采样策略仍无回执证据。

265的实际HTTP记录为一次POST，seq190，UTC01:54:13.565；UTC01:54:32.756
捕获 `TypeError: fetch failed`，cause=`ECONNRESET / read ECONNRESET`，没有HTTP回复。
随后工具错误显示 `library_mcp_retry_or_budget_blocked`，这是已发送job的重试被拦，
不证明80或套餐额度耗尽。保留原265 uncertain；不改为failed_known或退还计数。
原队列response、HTTP记录和 `stopped.json` 均保持，不能重发相同请求、媒体或观察谱系。

当前轮另收到外部主动中断。检查时上述网络停止已发生；
`controller_turn_interrupt.json` 只记录停止后续提交，不将265的ECONNRESET归因于中断。
恢复若被用户再授权，必须先处理新unknown的边界并保护251–265，
不能靠重编码、重启、新目录或放宽去重直接重放265。

## 用户继续后的恢复准备与启动前停止

用户再次明确“那就继续”。同任务追加 `vss_unknown_265_resume_v1`，冻结265调用前缀、
全部旧call文件清单/SHA、旧artifacts和日志字节前缀；仍沿用原未用渲染授权，不增加轮数。
流程已补跳过 `vss_detail_3_0`，其没有回复的状态显式保留；复用264正常短片结果，
之后只可看模型原已选择范围内55.5–57和57–58秒的独立帧页。
文字改写不算新的剪辑操作进展；不重复渲染相同执行表。

Python恢复34项/原state34项检查通过；JS恢复20项/原10项检查通过，
bridge等84项相关回归通过；本轮flow25项与新JS恢复20项联合45项通过。
但实际MCP启动前发现跨语言字段契约不一致：Python已固化artifact使用
`authorization_sha256` 和 `new_renders=0`，JS消费者期待
`original_authorization_sha256`、`reuse_original_unused_renders`，所以本地启动
抛 `library_mcp_visual_story_resume_scope_or_prefix_changed`，未发送新POST。
单边合成检查未发现这个真实生产者/消费者差异；需窄修消费者读取实际字段，
不改写已固化授权，并用真实grant做只读配置验证。

本轮随后收到主动中断，已设置mcp_stop并核对无后台管线/桥接进程。
累计仍265，零pending、零266、新渲染0；原265仍uncertain，旧请求均未重发。
恢复许可与部分代码保留，但不能声称已成功接通或产出新的GLM视频。

## 再次继续：实际授权的跨语言预检通过

用户再次明确“继续”，沿用原试验及已登记的265恢复许可。仅修JS消费者，
读取Python真实字段 `authorization_sha256`、`new_renders=0`、
`input_lock_sha256`、`goal_resumed=false`、`teacher_answers_forbidden=true`
及原progress_policy，不改写授权artifact，也不要求不存在的字段。
Python真实 `get_auth(force=True)` 和Node真实configuration/frozen265只读预检均通过。
Node返回原trial策略、单lane，265队列确认跳过；预检前后原授权、恢复授权、
ledger和265请求/started/unknown回复SHA不变。相关flow/原JS35项检查通过。
这修复本地接通错误，不是模型质量结论；此时仍265、无pending、无新成片。
新停止记录采用内容hash区分同计数的不同错误，旧stop文件不覆盖。

## 实际完成：77.37秒→21.9秒，唯一局部改版与有限交付

跨语言只读预检通过后，沿原试验继续266–274；没有重发265，也没有重建任务、
恢复Goal或扩增渲染次数。266/267完成原模型选择的回归范围中55.5–57、57–58秒独立帧页，
268自主生成第一份EDL并实际渲染。269无字盲读为partial，270目标审核partial并要求改版。
271完成唯一局部改版并渲染，272无字盲读仍partial；273收到原回复但JSON合同失败，
274唯一格式修复收到并解析，目标审核为pass、建议交付。原273回复和失败记录保留。

最终 `result.json` 为 `completed_with_model_review_limits`，选择 `render_1`。
累计274，试验新增24条251–274：23条received、265 uncertain，零pending。
274的pass是模型自评，不代表无字盲读通过、陌生观众理解通过或联合质量通过。
两版各渲染一次，未自动进入下一轮。管线和官方MCP均exit0，
`mcp_stop=visual_story_trial_settled`，未恢复Goal；当前会话`get_goal`返回null，没有活动Goal。
旧004/131/166/265全部不明历史不变，旧Goal暂停记录保留。

| 项目 | 实际结果 |
|---|---|
| 父粗剪 | 77.366667秒 |
| 固定参考 | 21.933333秒 |
| 两版画面成片 | 均21.9秒、1280×720、30fps、657帧 |
| 实际操作 | 9段不连续裁切，均1×；末段增加0.4秒/12帧真实尾帧停留 |
| 改版变化 | clip_5入点37.3→38.0；clip_6入点42.5→41.8，0.7秒从对峙分配至爆发前全景 |
| 其余执行段 | 7段编码SHA与全解码RGB相同，包括clip_8完整45帧 |
| 原版静音画面SHA | `ae4b4ccfa2a21c2ae6946eb1e90c55135676238812b74ac056eab51eb895fa88` |
| 选中版静音画面SHA | `f5ac92c767fa9ebb75c778f77d7ae6a45bd973c212eb4bd1d544116b0d872e67` |
| 封装音乐后的交付SHA | `63056eeaeaa5543f37ca4aa345fdeda5a64300e500e71628319059ceb8090768` |

视频与文件夹完整路径：

```text
C:\Users\29785\Desktop\omni-autonomous-screenplay\runs\library_reference_20261004\artifacts\visual_story_skill_trial_v1\delivery\glm_skill_finecut.mp4
C:\Users\29785\Desktop\omni-autonomous-screenplay\runs\library_reference_20261004\artifacts\visual_story_skill_trial_v1\delivery
```

音乐在画面选择完成后，以参考的原音轨按原速封装；两个GLM审核对象为物理无音轨代理。
独立CPU核验 `technical_audit_20261008/delivery_video_audio_CPU.json` 已通过：
交付657画面帧逐帧与选中静音render_1一致；音轨结束21.896009秒、解码时长21.896417秒，
中央同时间参考音频相关系数0.998910129，约4ms尾差，没有旧34秒候选那样的长段截断。
这验证媒体执行与音轨时间，没有试听认证、音乐节拍或声画艺术通过结论。

### 真实能力进展与限制

本次时长接近用户目标：模型能按段落贡献选择不连续片段、省略等待，
把77秒压至接近参考的22秒，并保留训练失衡—腾空—落地的动作变化及结尾积极回应。
它没有选择局部慢放或提速，不能说已经掌握全部教师技巧或精确高光时长控制。
唯一改版提供较完整的“平静全景→亮点→光环扩散”，但没有添加学习变化或对手结果的新画面。

模型审阅存在三处需要保留的矛盾，不能转写为真实成功修复：

- 269/270把输出18.3–19.8秒的熊猫现身读成奔逃的鹅，270归因于渲染错误。
  271/274随后声称已修复clip_8。但两版clip_8执行范围都为源57.4–58.9，
  编码SHA和全部45帧RGB相同；独立去字幕帧审阅两版均看到熊猫。
  因此没有证据表明FFmpeg错切或这个片段被修复，变化在模型解释而非执行素材。
- 274说“盲读可独立复述”否定、坚持、自我接纳、身份认可等完整主线。
  原272无字盲读仍为partial，记录11–17秒、18–21秒的因果缺口，
  没有独立读出这些文字承担的意思。目标审核已看计划和字幕，其解释不能回填到盲读。
- 274依据约1秒抽样怀疑clip_2入点提前0.5秒。
  CPU执行核验显示两版都按源6.5–9.0裁切，分段与最终成片解码RGB吻合；
  尚无真实证据支持裁点偏移，不以模型猜测认定执行错误。

独立CPU核验只验证计划绑定、解码帧、实际时长与代理，无模型语义通过含义。
证据保存在本试验 `technical_audit_20261008/render_0_CPU_execution.json` 和
`technical_audit_20261008/render_0_vs_1_CPU_pixels.json`。

独立画面审阅先冻结无字原版读法，再单独检查字幕；改版复看者因此不再是全新陌生观众。
实际联系表支持“受挫→后来从容对峙→大规模光效→主角现身、人群积极反应”的基本顺序，
但具体学习演变、手势与光环的完整因果、对手最终状态仍缺直接画面。
字幕补充身份、再来一次、自悟和亲子骄傲；中英字幕“当然/Nope.”本身还有语义冲突。
这不能建立纯画面准确主旨完全通过。

独立审阅是全时轴取样加局部密帧，不是正常速度连续陌生观众观影，
没有因此验证运动流畅性、准确节奏、慢动作质量或音乐对位。
冻结报告位于 `independent_cpu_review_20261008/picture_blind/` 和
`independent_cpu_review_20261008/revision_picture_only/`。

试验到此交付并停止，不以274自评pass抹掉272partial或以上矛盾，
不自动新轮、不重放unknown265、不把教师EDL注入下一次自主试验。

交付证据局限另存附加artifact
`runs/library_reference_20261004/artifacts/visual_story_skill_trial_v1/delivery_evidence_limits_20261008.json`，
联合质量状态为 `not_established`；不改原 `result.json`、模型评分或原回复。
本地原速代理与密帧不证明原生云端的内部抽帧策略，后者仍unknown。
