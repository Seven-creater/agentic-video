# INC-20261027-07 修正次数用尽即整条任务失败:该交付的版本被丢掉

| 项 | 内容 |
|---|---|
| 发现日期 | 2026-10-27 |
| 影响范围 | 创作路线无人值守运行;浪费调用(已渲染成果作废)+ 无诊断停机 |
| 状态 | 已修复(2c288ff;B2/B3/D 项。B1 粗剪规划耗尽仍为干净停机——无物可交付,属既定策略) |
| 相关提交 | 2c288ff |
| 相关任务 | `.agent-team/tasks/U1_unattended.prompt.md`;`wt/pfix/reports/unattended_outcomes_u1.md`;排查清单 B 类(历史 13 个任务因 plan_N 耗尽失败,第一大死因) |

## 1. 问题长什么样
`model_protocol_repair_exhausted:<stage>` 一出现,整条任务直接失败:精剪修订耗尽时,已渲染且已审核的上一版被丢弃;粗剪配音时长修正耗尽时,已能出片的结果被丢弃;粗剪规划耗尽(历史 13 个任务)整条无产出。停机时没有任何结构化记录说明死在哪个阶段、哪个调用。

## 2. 怎么查出来的
排查清单 B 类列出三处耗尽点与可选降级;历史任务统计 plan_N 耗尽 13 例为第一大死因。用户决定(任务书 User policy):修复耗尽时"交付已渲染的最好版本并标 partial",无物可交付则精确诊断后停。

## 3. 根因
代码问题(流程分支,容错缺失):没有为"耗尽"设计出口;修复回路次数有限是合理的,但耗尽后的处置被简化为抛异常终止。

## 4. 解决方案
- B2 精剪修订耗尽(story_finecut.py:59/995):复用 `finecut_continuation` 的已知失败调用校验(`_known_protocol_failure`,支持 repair/repair2 链),交付最新已渲染且已审核版本,标 `partial=True`、`partial_reasons=['fine_revision_exhausted', ...审核问题]`,继续创作音频阶段,最终 `completed_partial`;不新增模型调用。
- B3 粗剪配音时长耗尽(clean_chain.py:469、story_context.py:399/414):带 `timing_problems` 继续(与一次修正后仍有问题的既有路径一致);仍放不下的句子记 `dropped_lines`+原因后丢弃,绝不截断语音;已丢弃句不再合成、不排程;原稿保留,字幕只用实际发声稿;partial 贯穿 fine context/audio/final。
- B1 粗剪规划耗尽:无渲染成果,不加兜底模型调用,改为精确诊断停机(见 D)。
- D 可诊断停机:顶层 runner(server_cli.py:351)try/finally 包住全部 worker 执行,任何终态写 `<task>/run_outcome.json`(run_outcome.py:75):outcome ∈ {delivered, partial, stopped}、status、final_video、partial_reasons;停机记录 stage、error、exception_type、raised_at(omni_story 内最深帧 file:line)、last_call_id、protocol_failure 路径;附 audio_measurement、调用与修正计数。
- 测试:`test_b2_exhausted_fine_revision_delivers_reviewed_render_partial`、`test_b3_exhausted_rough_timing_keeps_candidate_and_drops_unfitted_lines`、`test_d_worker_always_writes_run_outcome` 等。
通用性:出口只依赖"是否存在已渲染且已审核的版本"这一事实,与阶段内容无关。

## 5. 为什么不用其他方案
- 继续整条失败(现状):丢弃已付费成果,违背用户"交付最好版本"的决定。
- 耗尽后加一次兜底模型调用换一份新回复:违反"不自动重试/重发"的用户规则;明确不加。
- 截断语音硬塞进窗口:损害听感且语义残缺;选择丢句并如实记录。
- B1 也强行交付:规划阶段没有任何可交付物,只能精确停机(用户规则第 3 条)。

## 6. 怎么验收
同批 29 用例基线全败/当前全过(见 INC-20261027-05 第 6 节);B2/B3/D 各有专项测试,`test_d_exhausted_plan_stops_with_exact_call_and_traceback` 验证停机可定位到具体调用与 file:line。真实重跑:叶问等模板修后正常交付/部分交付(见 INC-20261028-03 修复后的 4 模板重跑)。

## 7. 量化数据
修复前:plan_N 耗尽历史 13 个任务失败(第一大死因,排查清单 B1)。修复后耗尽导致的整条失败次数未单独统计(可从 run_outcome.json 的 outcome=stopped + partial_reasons 分布统计)。

## 8. 经验与后续
- "耗尽"是常态而非异常,必须有三出口:交付、部分交付、可诊断停机;run_outcome.json 让每次失败都能定位到 file:line。
- 遗留:B1 的根因(规划阶段高频拒绝)仍需靠合同对齐类修复(INC-20261026-01、INC-20261027-03)持续压降。
