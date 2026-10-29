# INC-20261029-12 单节拍缺候选即全链终止:可交付状态被"素材不足"停机(不烧心,168 次调用零产出)

| 项 | 内容 |
|---|---|
| 发现日期 | 2026-10-29 夜(e2e 自动修复第 1 轮) |
| 影响范围 | question_first 生产流水线;致命停机——已有 usable 候选的运行被 `question_first:material_gap` 终止,168 次调用、约 75 万 prompt tokens 零产出 |
| 状态 | 已修复(5c44d97) |
| 相关提交 | 5c44d97 |
| 相关任务 | 证据 `.agent-team/out/e2e/evidence_01_不烧心_0.txt`;分析 `out/e2e/analysis_1.md`;修复报告 `wt/pfix/reports/e2e_material_gap_fix_zh.md`;审查 `out/e2e/review_1.md`(pass) |

## 1. 问题长什么样
参考"不烧心"的运行 run_outcome.json:`error: "question_first:material_gap"`、`raised_at: pipeline.py:1627`、`repairs: 0`、`model_calls: 168`。report 里 b1 节拍 `candidates: []`,但 b2 已有 `verdict:"usable"`、`satisfies:"equivalent"` 候选(close_006,3d,有效源时间 284–291 秒);约 30 次 close 调用丢 12 次(40%)。整链零交付。

## 2. 怎么查出来的
e2e 证据包(不烧心运行)逐字段核对:unknown_calls.json 12 条 `received_unusable, error:"AssertionError:"`;skim 全部命中 low 置信;代码定位 pipeline.py:1626-1627 无条件 raise。代码走查发现注释"Carry retrieval gaps to the existing story/AV review"所述降级通路(slots.gaps、visual_coverage_contract、评审上下文)真实存在但在 raise 之后永不可达——死代码。

## 3. 根因
代码问题(流程分支为主因):任一 needs_visuals 节拍无 eligible 候选即全链终止,而系统自身处处为"素材不完美"设计了近似路径(equivalents、partial、dub_can_carry、slot 缩减),唯独这道门把它们全部短路。诱因:检索覆盖不足(skim 小图全 low、close 判 no 后无二次检索,gap 的 reason 无法区分"核实过不存在"与"没搜到");加重因素见 INC-20261029-13(12 条回复被整条丢弃)。

## 4. 解决方案(5c44d97,e2e_material_gap_fix_zh.md)
- 删除无条件 raise:改用 `to_search_result` 的实际合格窗口筛选结果判断——有任一合格窗口时进入正常连续观察、规划与评审,同时保留缺口(b1 缺口仍出现在规划的 reference、顶层 gaps 与 slots 中);全部节拍都无合格窗口时仍抛 `question_first:material_gap`(素材不足就停,是既定方向)。
- 风格、时间、内容区间、排除区间与成片质量检查全部原样;缺口不被判为已满足,也不保证最终叙事评审通过。
- 未做 verified_absent 判定与二次检索:证据只有 1/12 条失败回复可完整核对,不能认定其余 11 条同因,低置信 skim 也不足以证明素材不存在;不新增自动重试、重发或模型调用机制。
通用性:只依赖"节拍-合格窗口"结构,零模板/角色/台词特判。

## 5. 为什么不用其他方案(analysis_1 方案 C 及其他)
- gap 归因二分(verified_absent / coverage_limited)+ 一轮有界二次检索(方案 C):证据不足(12 条被丢回复内容未入证据包,无法归因;低置信≠不存在),且会新增模型调用机制;未实施。
- 全部节拍 gap 也强行继续:无任何可观察素材时无物可交付;保留停止(素材不足就停)。
- 缺口判为已满足/直接放行渲染:语义造假;缺口如实传递给规划与评审。
- 其余:未讨论。

## 6. 怎么验收(e2e_material_gap_fix_zh.md 实际输出)
- 新增 4 用例改前全红(`4 failed, 191 deselected`),修后 Question-first `194 passed, 1 skipped in 3.24s`。
- 全量:`9 failed, 1578 passed, 104 skipped, 24 errors`(与基线逐条相同;原始 HEAD 98e9427 隔离复跑对照 24 个 ERROR 同源)。
- Replay:TOTAL 143/6/21/33,164 条保护行(ok/fixed)0 退化、分类 0 变化;Fault:E1/M1_crash/M2_json/M2_crash/R1 全 0。
- 真实 b1/b2 夹具:修复后只观察 b2 窗口,b1 缺口贯穿规划上下文;全部候选为 2D 风格的停止用例继续通过。
- review_1 审查 pass:两处放宽均有实测依据、无模板特判、无重发;提示词变更导致的旧收据 `recorded_request_changed` 属请求钉住的安全设计。

## 7. 量化数据
修复前:168 次模型调用、repairs 0、约 75 万 prompt tokens、零交付(证据 run_outcome/analysis);close 调用丢失 12/约 30(40%)。修复后:同类运行进入规划评审路径(离线回放验证);真实服务器重跑对照未记录(截至事实清单)。

## 8. 经验与后续
- "终止门"必须有可达的降级路径;注释声称的行为要与实现对账(死代码即缺陷)。
- 遗留:检索覆盖不足(skim 低置信、无二次检索)仍在——本修复让缺口不再致命,但没有提高召回;是否补二次检索待后续证据与用户拍板。
