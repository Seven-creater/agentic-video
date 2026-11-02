# INC-20261102-09 剪辑时长没有约束：119 秒的参考剪出 153 秒成片，审核每修订一次就更长

| 项 | 内容 |
|---|---|
| 发现日期 | 2026-11-02 |
| 影响范围 | 默认创作风格 picture_aware 的所有任务（粗剪规划与精剪）；错误成片（拖沓，不像剪辑） |
| 状态 | 已修复（4d27aef；真实任务验证待做） |
| 相关提交 | 4d27aef |
| 相关任务 | prod_27_发现三个问题_11021100/evaluations/rule2（release 7acad6e） |

## 1. 问题长什么样
用户看了 rule2 成片：「2分半的时长也太长了吧，我们做的是剪辑」。参考 `01.mp4` 时长 119.4 s；三轮粗剪 render_0 105.6 s（14 段、13 句）→ render_1 150.0 s（18 段、10 句）→ render_2 157.0 s（21 段、22 句）；最终成片 153.0 s。用户此前认可的一条（default3）只有 54 s。

## 2. 怎么查出来的
1. 服务器 ffprobe 各轮 render 与成片时长，确认每次静音审核修订都在加镜头和台词。
2. 搜索时长约束：粗剪只有一条固定上限 180 s——`src/omni_story/library/pipeline.py:2708`（`render_capabilities.max_duration_s: 180`）、2799/2809/2822/2855（`max_duration_s=None if progress_driven else 180`）、`contracts.py:1386/1546`；进度路线提示词甚至写"总成片时长和segments数量不设固定上限"。picture_aware 的规划提示（`prompts.picture_first_plan_prompt`）对时长只字未提。
3. GLM 实现时进一步查出：最终成片出自精剪 `story_finecut.py`，配音模式下 `duration_contract()` 返回 `maximum_duration_s=None`，提示写"maximum_output_frames为null表示没有硬总时长上限。无需装入原参考音轨或接近参考时长"、"参考时长仅为节奏风格提示"。

## 3. 根因
设计缺口：分镜复刻被放弃时（ea3fb17 教训），连"0.8–1.2 倍参考时长"的硬合同一起拿掉了，之后默认路线只剩 180 s 的兜底上限，提示词也不告诉模型这是剪辑。模型按审核意见修问题时倾向"补镜头、补交代"，程序没有任何力量把它拉回来。属于流程约束缺失（代码问题），与模板、电影无关。

## 4. 解决方案（用户 2026-11-02 决定："和参考视频的时间差不多就行了…可以比参考视频更长一些也行"）
- `contracts.picture_aware_edit_max_s(ref) = min(180, max(30, round(1.25×ref, 1)))`，仅用于 picture_aware；不设下限。119.4 s 参考 → 149.2 s。
- 粗剪（`pipeline.py`、`prompts.py`）：上限放进规划上下文 `edit_length_budget`，提示词、`render_capabilities`、`validate_plan`（总和与逐帧取整两处）、`normalise_plan` 用同一个数；模板中"不得超过180秒""不设固定上限"改写为该上限；追加"时长合同"：这是剪辑，参考 X 秒讲清的内容我们用相当时长也能讲清，允许略长，删冗余镜头。修订轮追加"改写台词或更换/缩短镜头修复，不得靠新增镜头或加长定格"。超限走既有修复回路（新增 `repair_hint`），程序不自动剪。
- 精剪（`story_finecut.py`）：从本任务记录推出父粗剪的上限（S34 artifact + 选中轮次的规划提示带标记 `picture_aware_edit_length_v1`），时长合同 `maximum_output_frames = floor(edit_max_s×30)`，提示改为"与参考相当、允许略长、不得超过上限"，校验超限抛 `story_finecut:edit_length_budget_exceeded_<N>_seconds` 走既有修复回路。
- 兼容（统一规则）：每条回复只按它当次实际收到的提示所写明的时长规则判定（带标记 → 新上限；不带 → 原规则），覆盖新调用、续跑复用的旧请求（CodexMCP 保留旧提示、`_resume_plan`、received_plan 复用）、角色纠错 `correct_plan`、精剪细化 `validate_refinement`；修复提示同规则。粗剪定稿时把被接受请求的规则向前记入 `result.json.edit_length_budget`（null = 原规则），精剪与任意层续跑只读该记录，不倒查提示。有上限时一律按逐帧取整后的总帧数检查。旧记录与其他风格（classic / picture_first / storyboard / adaptive）字节级不变。上限另写入 artifact `picture_aware_edit_length_budget`。

## 5. 为什么不用其他方案
- 恢复 0.8–1.2 倍硬合同（含下限）：下限曾导致素材不足时反复失败，用户也只要求"差不多、可略长"。
- 固定 60/90 s 上限：用户选择以参考时长为准，长参考需要更多时间表达。
- 只改提示词不校验：模型在修订时仍会加长，已被 rule2 证明。
- 程序自动截短：会切掉台词或关键镜头，违背"内容由模型决定、程序不补写"的原则。

## 6. 怎么验收
- 新增 `tests/test_edit_length_limit.py`（39 项：38 通过，1 项需 ffmpeg 本机跳过）：上限数值；157 s 规划被拒、140 s 通过、classic 仍按 180；提示含参考时长与上限、无"180秒"、修订提示含"不得靠新增镜头"；旧记录不套新上限；精剪合同 4476 帧通过/4477 帧拒绝；无上限时精剪合同与提示逐字不变；以及下述 7 项审核问题各自的复现用例。
- GPT 三轮交叉审核：第 1 轮 fail 5 项（`--resume-from` 复用规划时精剪丢上限〔高〕、两层续跑丢上限、保留旧提示却按新上限判、按秒求和 149.14 s 但逐帧 4477 帧=149.233 s 漏检、修复提示泄漏到其他路线）；第 2 轮 fail 2 项（精剪细化与角色纠错沿用旧请求仍套新上限）；第 3 轮 pass，7 项全部重新复现确认已修。
- 全量 pytest 8 failed 全在基线内（2760 passed）；回放 203 行与基线逐行一致（ok 143 / regression 6 / fixed 21 / still_failing 33）；故障演练 E1/R1/M1_crash/M2_json/M2_crash 均为 0。
- 用 rule2 服务器真实记录核对精剪推导所需字段：`result.json.selected_round=2` 且在精剪前写入；`plan_2_repair2` 的提示含完整原提示。
- 真实任务验证：待部署后用同一参考重跑。

## 7. 量化数据
- 修复前 rule2：参考 119.4 s，粗剪 105.6 → 150.0 → 157.0 s，成片 153.0 s（参考的 1.28 倍），修订让时长增加 49%。
- 修复后：上限 149.2 s；真实结果待重跑。

## 8. 经验与后续
- 放弃一个"形式合同"时，要单独检查其中是否有用户仍然需要的约束（这里是时长）。
- 遗留：rule2 每轮规划都用满 2 次格式修复（plan_N_repair2），规划一次通过率低，值得单独查。
- 遗留：若实测配音总长本身超过上限，精剪只能删句，可能停机；真实重跑时观察。
