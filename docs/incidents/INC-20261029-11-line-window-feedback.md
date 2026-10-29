# INC-20261029-11 台词时长反馈不可操作:模型看不到派生窗口,修正越改越糟直至耗尽

| 项 | 内容 |
|---|---|
| 发现日期 | 2026-10-29 |
| 影响范围 | 粗剪计划的台词窗口校验与修正回路;致命停机(`model_protocol_repair_exhausted:plan_N`) |
| 状态 | 已修复(98e9427) |
| 相关提交 | 98e9427 |
| 相关任务 | 报告 `wt/pfix/reports/plan_line_window_exhaustion_zh.md`;审查 `out/W2_review.md`(verdict pass);真实回复:`.agent-team/qfs_fixture/plan_faa499b/`(fixture `tests/fixtures/plan_faa499b_line_windows.json`) |

## 1. 问题长什么样
第一次修正:`l0`(12 字含标点)推导窗口 2×12/26=0.923 秒 < 下限 1.80 秒被拒;模型按提示删字后,第二次修正窗口反而缩成 2×6/18=0.667 秒 < 0.90 秒——缩短文本同时改变同组两句的分配权重,窗口变小。模型看不到"窗口是按 len(text) 权重从所挂片段时长派生的"这一事实,修正两轮耗尽,任务失败。

## 2. 怎么查出来的
从 `qfs_fixture/plan_faa499b/` 提取两份 repair 的真实回复(8 段布局+7 句草稿)逐句推导:l0/l1 都挂 seg_1(源 825.5–827.5 秒、speed=1,输出恰 2 秒),分配权重 `len(text)`、下限按 spoken chars——原逻辑既不告知推导量,又只报第一个失败行。

## 3. 根因
代码问题(反馈不可操作):修正反馈不携带派生窗口、分配权重与各段可用时长,模型只能盲改;且"仅剩窗口违规"也要耗尽停机,而下游本来就有实测 TTS→timing 修正→dropped_lines 兜底链。

## 4. 解决方案(98e9427,plan_line_window_exhaustion_zh.md)
- `line_windows.py` 统一提供输出时间轴、逐行明细与单句推导说明;提示词的计算示例、草稿数值与错误明细同源。
- 计划失败的 `violation.offending_lines` 与 `diagnostics.line_window_warnings` 列出**全部**违规行:索引、ID、原文、spoken chars、含标点分配字数、起止时间、窗口、下限、segment IDs、每段输出起止/时长、推导说明与修复提示(改挂更长片段/延长片段/挪片段/填显式窗口/缩短台词);repair feedback 不受 30 条/200 字截断。粗剪、StoryFinecut、active finecut prompt 共用同一说明;显式 `start_s/end_s` 字段的语义(输出时间轴绝对秒)写明。
- 耗尽接受:到达修复上限且最后一份回复唯一剩余违规为窗口下限时,经 `_known_protocol_failure` 哈希绑定原始调用链后,本地暂缓这一条规则、完整重跑原 validator;任何其他错误照旧致命。接受的计划带全部 `line_window_warnings`,绑 `line_window_exhaustion` artifact,run_outcome 汇总警告;零追加模型调用、不伪造 parsed.json。支持 plan_N、嵌套 finecut_N 与 StoryFinecut plan/revise。
- 接受的计划照走既有兜底:实测 TTS → 一次 timing revision → 仍放不下则 dropped_lines/partial。
- 下限与回执例外不变(max(0.7, 0.18×字数))。
通用性:全部作用于派生窗口的结构信息,无模板特判。

## 5. 为什么不用其他方案
- 继续只报第一个失败行:真实回复已证明模型会朝错误方向改(删字反而更糟)。
- 放宽/取消下限:INC-20261029-06 已证明下限本身必要(短句 0.45 秒窗口必丢);问题在反馈不在阈值。
- 耗尽即停:下游兜底链(实测 TTS/timing 修正/dropped_lines)本就是为此设计的最终裁判,计划阶段的估算不该有终审权。
- 为解窗口而追加模型调用:违反不重发/不追加调用的规则;用本地重放原 validator 实现。

## 6. 怎么验收(plan_line_window_exhaustion_zh.md 实际输出)
- 新增回归测试:`git archive faa499b` 隔离源码 10 failed → 当前 15 passed(真实两份回复明细、粗剪与各精剪耗尽续跑、显式窗口提示、40 行完整反馈、嵌套精剪);相关测试组 247 passed, 24 skipped。
- 5 项对照测试确认其他违规(含下限之后才暴露的、工作流 guard)仍停止。
- 真实调用链本地重放(3 份完整回复+原 request+生产 validator):`real_reply_chain: accepted final reply; model_calls=3`、警告行 [('l0',0.666667,0.9),('l1',1.333333,1.62)]、首次反馈 [('l0',0.923077,1.8),('l1',1.076923,1.62)]、resume 相同零追加、`all_paid_failures_preserved: True`。
- 全量:`9 failed, 1574 passed, 104 skipped, 24 errors`(基线子集);Replay:143/6/21/33 与改前完全一致、0 分类变化;Fault:E1/R1/M1_crash/M2_json/M2_crash/high 全 0。W2 审查 pass。
- 真实运行:黑虎阿福参考完整交付——17.9 秒、11 句、太郎全程一致、纯 3D、TTS 补发(INC-20261028-02 的机制)首次真实触发成功(事实清单;服务器运行记录未归档)。

## 7. 量化数据
故障样本:l0 0.923<1.80 → 删字后 0.667<0.90(越改越糟);seg_1 输出 2 秒、7 句草稿。修复后:窗口违规从"必死"变为"带警告继续",真实链 3 次调用全部保留、零追加;黑虎阿福 17.9 秒/11 句交付。

## 8. 经验与后续
- 反馈必须给出模型无法自行推导的量(派生窗口、权重、每段时长)。
- 估算类校验(窗口下限)不应拥有终审权,实测层(TTS 回执/timing)才是 backstop——这是"接受+警告+兜底"模式的定型。
