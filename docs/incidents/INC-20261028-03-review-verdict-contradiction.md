# INC-20261028-03 精审结论自相矛盾(revise/deliver 却 core meaning 缺失)被判"核心缺失"停机

| 项 | 内容 |
|---|---|
| 发现日期 | 2026-10-28 |
| 影响范围 | 创作路线精剪审核裁决;致命停机——4 个重跑模板中 3 个(叶问、尼克狐、自己吓自己)因此停下 |
| 状态 | 已修复(0152f74) |
| 相关提交 | 0152f74 |
| 相关任务 | `.agent-team/out/auto_v1/Y1_fix.md`、`out/auto_v1/Y2_review.md` |

## 1. 问题长什么样
精审回复出现内部矛盾:`next_action=revise` 或 `deliver`,但 `missing_core_meaning` 非空(即自认核心含义缺失)。校验未检查二者一致性,代码按"核心缺失"处理,判为 `needs_material_reselection`——直接停机,不进修正。重跑 4 个模板里 3 个(叶问、尼克狐、自己吓自己)命中;黑虎阿福一致、放行。

## 2. 怎么查出来的
4 模板重跑的 run_outcome/审核回复对照:三条停机的回复均为"revise/deliver + 非空 missing"形态;审核校验器走查确认无一致性检查。

## 3. 根因
代码问题(校验漏洞):契约允许两个字段组合出矛盾语义;校验器只查各自格式,不查组合一致性,矛盾被 silently 当成最严重的一档(核心缺失)处理,而这一档没有修正回路。

## 4. 解决方案
- 提示词与验证器共用一个 helper(`review_verdict_contract()`,story_finecut.py:627、636-643):非空 `missing_core_meaning` 仅允许 `reselect_material`;空缺项仅允许 `deliver`/`revise`。
- 不一致时拒绝,violation 写明路径与期望(`review_verdict_contract`),走既有协议修复分支(pipeline.py:992 起)让模型改一致,而非直接停机;三个合法组合(deliver+空、revise+空、reselect+非空)全部仍被接受。
- 真实服务器回复做 fixture:叶问、尼克狐形态被拒,黑虎阿福一致放行。
通用性:一致性是 schema 级契约,与具体影片无关。

## 5. 为什么不用其他方案
- 按字段优先级择一解释(如永远信 missing):已在真实数据上造成 3/4 误停——矛盾本身就该被退回修正。
- 收窄为仅在 story_context 非空(创作路线)时启用校验:Y2 审查列为可选,未采纳——无条件规则更简单,legacy 路径的偏离回复进入修复回路属可接受行为(Y2 标注为 info 级知悉项)。

## 6. 怎么验收(Y1_fix/Y2_review 实际输出)
- 新增测试 6 项:c296937 基线 6 failed → 当前 6 passed。
- 全量:`9 failed, 1337 passed, 103 skipped, 24 errors`,FAILED 为基线子集。
- 历史 12 条精审回复(含 repair/repair2)重扫:deliver+空 4、revise+空 5、reselect+非空 3,新规则 0 误拒(Y2 独立复核一致)。
- Replay:ok=149、regression=0、变化行 0;Fault:E1=R1=M1_crash=M2_json=M2_crash=high=0。
- 真实重跑:修后叶问、尼克狐均完成交付(事实清单;服务器重跑记录未归档)。
- Y2 审查结论 pass(两条 info:legacy 路径行为变化知悉;yewen 原始 JSON 未取得,fixture 为形状一致的合成回复)。

## 7. 量化数据
修复前:4 模板重跑 3 个因此停机;历史精审 12 条中矛盾形态 0 条(即该矛盾是新出现的回复形态,历史误拒 0)。修复后:历史 0 误拒,叶问/尼克狐交付。

## 8. 经验与后续
- 相互依赖的裁决字段必须做组合一致性校验,且不一致要退回修正而不是择一解释。
- 用真实服务器回复做 fixture 是防"合成用例过于干净"的关键;yewen 原始 JSON 待归档后可替换合成用例。
