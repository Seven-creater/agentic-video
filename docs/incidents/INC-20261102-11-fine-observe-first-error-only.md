# INC-20261102-11 精剪观察：11 段局部检查有 7 段超过 6 秒，每次修复只点名第一段，三次用完后只交出粗剪

| 项 | 内容 |
|---|---|
| 发现日期 | 2026-11-02 |
| 影响范围 | 所有走 skill_finecut 的创作任务的精剪观察阶段；错误成片（只交付粗剪，无精剪、无终审） |
| 状态 | 已修复（d1ea7f4；真实任务验证待做） |
| 相关提交 | d1ea7f4 |
| 相关任务 | prod_27_发现三个问题_11021100/evaluations/rule2/evaluations/len1/evaluations/len2（release 3b289bc） |

## 1. 问题长什么样
`run_outcome.json`：`outcome: partial`，`partial_reasons: ["model_protocol_repair_exhausted:chain_e2e_v1_fine_observe"]`，`final_video` 指向粗剪 `render_1/final.mp4`（139 s）。三次调用都报 `local_inspection_max6s_max36_requested_frames_shrink_range_or_raise_step`。模型请求了 11 段局部检查（step 0.2 s），其中 7 段超过 6 秒：[45,52] [52,62] [62,75] [79,90] [103,113] [113,122] [131,139]。

## 2. 怎么查出来的
读三次 `protocol_failure.json` 与修复请求 `request.json` 末尾：第一次修复的 violations 只有 `$.inspect[1]` `[14.0, 24.0, 0.2]`，第二次只有 `$.inspect[3]` `[30.0, 38.0, 0.2]`；模型每次只改被点名的那一段（14–24 拆成 14–19、19–24；30–38 改成 30–36），其余超长段从未被指出。定位 `src/omni_story/library/visual_story_trial.py` `inspect_check`：逐行检查，第一处超限即抛。提示词本身写明"每个局部按≤6秒且时长/step_s≤36的批次"，模型初稿仍违反。

## 3. 根因
校验只报第一个违规，多元素列表的修复要逐个耗费修复次数；7 段超长、只有 2 次修复机会，必然耗尽。与 INC-20261102-10 同类（反馈不完整），属代码问题，与模板、电影无关。

## 4. 解决方案
`visual_story_trial.inspect_check`：逐行检查拆为 `_inspect_row_check`；第一处违规的位置与错误名保持不变（其后的其他类型错误不改变首个错误）；若有多段超出 6 秒/36 帧预算，全部写入 `violation.offending_groups`，每段带 `duration=…s, frames=…` 与"拆成多个连续的≤6秒批次或缩短范围"的修复说明，`protocol_feedback.diagnose` 已有的 offending_groups 分支一次列出。只有一段超限时反馈与原来逐字一致。

## 5. 为什么不用其他方案
- 程序自动把长范围切成 6 秒批次：会让局部检查调用数翻倍（本次 11 段将变约 20 段），是否值得看由模型决定。
- 增加修复次数：违规数不定，治标不治本。
- 通用地对所有校验器"收集全部错误"：各校验器结构不同，逐个按真实失败修更稳；已修 regions（INC-10）与本处。

## 6. 怎么验收
- 新增 `tests/test_inspect_all_overbudget.py`（5 项，用本次 11 段真实范围）：一次列出全部 7 段及时长/帧数；单段超限反馈逐字不变；首个错误名与顺序不变；后续畸形行（如 target=[] 引发 TypeError）不能覆盖首个错误；预算内通过。
- GPT 交叉审核：第 1 轮 fail 1 项（只捕获 ValueError，后续行的 TypeError 等会覆盖首个超预算错误），已改为首错之后任何异常都只跳过。
- 全量 pytest 8 failed 全在基线内（2775 passed）；回放 203 行与基线逐行一致；故障演练 E1/R1/M1_crash/M2_json/M2_crash 均为 0。
- 真实任务验证：待部署后 `--resume-from len2` 只重做精剪。

## 7. 量化数据
- 本次：14 次调用中 3 次耗在精剪观察，最终只交付 139 s 粗剪。
- 修复后第一次修复即可看到全部 7 段超长范围。

## 8. 经验与后续
- 本次粗剪时长 127 s → 139 s，均在新上限 149.3 s 内（INC-20261102-09 生效）；字幕区域沿用上级缓存，INC-20261102-10 的修复未被真实触发。
- 规划阶段仍常用满两次格式修复（本次 plan_1：`plan:range_not_supported_by_fine_observation`、`audio_line_window_below_minimum`），可能同样存在"只报第一个"的问题，值得逐个排查。
