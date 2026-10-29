# INC-20261029-13 精看回复 same_character_as_anchor=null 撞裸断言,整条回复被丢弃(12/30 次)

| 项 | 内容 |
|---|---|
| 发现日期 | 2026-10-29 夜(e2e 第 1 轮证据) |
| 影响范围 | question_first 精看(close)回复处理;浪费调用——模型已看过并给出结论的回复被整条丢弃,且连带丢失 also_satisfies 路由,减少其他节拍的候选供给 |
| 状态 | 已修复(5c44d97) |
| 相关提交 | 5c44d97 |
| 相关任务 | 证据 `.agent-team/out/e2e/evidence_01_不烧心_0.txt`(`glm_165_qfs_close_26` 等 12 条);fixture `tests/fixtures/question_first/glm_165~168_*.json` 与 `material_gap_20261029_README.md` |

## 1. 问题长什么样
`glm_165_qfs_close_26`:合法 JSON、verdict "no"、also_satisfies 空,唯一"异常"是 `same_character_as_anchor: null`;job 带锚点(reasoning 自述 required_anchors 含"平先生")。回复被 `AssertionError:`(空消息)整条丢弃、记 `received_unusable`,永不重发。同批 12 条(约 30 次 close 的 40%)同签名丢弃;对照 glm_166/167/168 带锚点回复 bool(true/true/false)则校验通过、拒因具体可信。

## 2. 怎么查出来的
unknown_calls 12 条逐条对照;glm_165 的完整回复可核对,直接归因:带锚点 + null 锚点字段 → question_first.py:445-446(当时)裸 assert。断言三重自相矛盾:①与 CLOSE 提示词不一致(提示词说"无法确认按 false");②与约束施加器不一致(`apply_close_constraints` 本有 None→`identity_unverified`→partial 分支,上游断言使其在有锚点时不可达——校验比下游更严);③与决策路径不一致(verdict=no 且 also_satisfies 空时该字段不参与任何判断)。

## 3. 根因
代码问题(校验过严 + 裸断言):把"决策不消费的字段"当成硬性格式要求;且强制了提示词自己都没要求的取值。

## 4. 解决方案(5c44d97,e2e_material_gap_fix_zh.md)
- 保留显式 null:负向结论(verdict=no)继续为 no;有锚点且身份未确认的正向候选至多为 `partial`(走既有 `identity_unverified` 分支),不能取得已确认实体绑定。
- 错误类型(非 null 的非法值)或带锚点时缺失字段仍拒绝,但改用带 `path / value / expected / repair_hint` 的 ValueError;批处理记录保留 violation。
- 提示词与验证器共用 `close_identity_contract()`。
- 四条真实回复保留数 3/4 → 4/4,四条结论仍全部为 no;两次读同名收据只发生 4 次原始 HTTP 桩调用,零重发。
通用性:schema 层原则(按决策路径收缩必填、coerce 优先于丢弃),对任何阶段成立。

## 5. 为什么不用其他方案(analysis_1 方案 B 各项)
- ①null 交给 identity_unverified→partial 分支(或按提示词语义 coerce false):已采纳(保留 None)。
- ②字段要求按决策路径收缩(verdict=no 且 also_satisfies 空时不强制 bool 锚点字段):已采纳。
- ③丢弃仅保留给结构性损坏且每条必须带具体 reason:部分采纳——非法值/缺字段已带 violation;12 条中其余 11 条因证据包无完整回复,无法逐一归因,未扩大处理。
- 对失败调用重发:违反不重发规则,排除。

## 6. 怎么验收
4 个新增用例先红后绿:`test_evidence_close_no_with_null_anchor_is_retained_without_resend`、`test_evidence_null_identity_only_supplies_partial_unconfirmed_candidate`、`test_evidence_invalid_identity_has_shared_repair_contract_without_resend`、`test_pipeline_injects_search_zero_then_runs_normal_observations[evidence_partial]`(真实 b1/b2 结构);Question-first 194 passed, 1 skipped;全量/replay/fault 与 INC-20261029-12 第 6 节相同;review_1 确认 4 份 fixture 与证据逐字一致、`http.call_count==4` 无重发。

## 7. 量化数据
修复前:不烧心运行 12/约 30 次 close 回复被丢弃(40%),其中可归因 1 条(glm_165);修复后:该类回复保留 4/4(测试),丢弃仅剩结构性损坏;候选集合单调不减(对证据重放)。

## 8. 经验与后续
- 校验的必填集合要等于"决策实际消费的字段集",提示词语义(无法确认按 false)与校验必须同源。
- 丢弃必须可归因(带 reason),空 AssertionError 让排查只能靠猜。
- 与 INC-20261029-12 合看:丢弃造成的候选损失是 material_gap 误停的加重因素。
