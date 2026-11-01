# INC-20261101-17 cast 写成"观察行"形状无 cast_id，plan_0 三连败耗尽修复预算；cast 本可由已确认说话人机械推导

| 项 | 内容 |
|---|---|
| 发现日期 | 2026-11-01 |
| 影响范围 | 创作路线规划归一化（film/素材库模式带对白规划）；致命停机 + 浪费调用 |
| 状态 | 已修复（b1e8f62），三条真实回复回放通过 |
| 相关提交 | b1e8f62（与 S20 横屏画布同提交） |
| 相关任务 | sb_02_雪山救狐狸…/fix8/evaluations/theme1/evaluations/film1（release 5d1fb42，--material-search film）；证据 .agent-team/out/s21/evidence/（calls/glm_023..025 + watched_windows.json + search_0.json） |

## 1. 问题长什么样
- 三次 plan 尝试（glm_023_plan_0、glm_024_plan_0_repair、glm_025_plan_0_repair2）全部失败 `plan/cast/cast_id:text_required`，修复预算耗尽停机。
- 模型三次写的 cast 形状各不相同：`{"bindings":[…]}` / `[{"bindings": []}]` / 一列角色行 `{window_id, role_id, entity_id, identity_confirmed}`（窗口观察的形状），都没有 cast_id。
- 反馈里 `bindable_triples` 非空（INC-20261101-13 的窗口级实体编号已在）；每句对白都带 `speaker: {window_id, role_id}` 和 `voice`，旁白无 speaker——即预期 cast 已被说话人完整确定。

## 2. 怎么查出来的
读 .agent-team/out/s21/evidence/ 三份失败回复的 request.json/protocol_failure.json（含 model_text）、watched_windows.json、search_0.json：对照规划合同（`plan_field_contract(...)['cast']`、`validate_task_cast`）确认校验要求"每条 cast 项带非空 cast_id + 逐字照抄的 entity_id"，而三份回复没有任何一份能提供；同时逐句核对 speaker/voice，确认 cast 的全部信息（entity、出现窗口、音色）都可从已确认观察机械得到。失败形态不同、根因相同：缺一个从说话人到 cast 的程序推导。

## 3. 根因
- 直接原因：模型反复把窗口观察行/绑定表当成 cast 写，造不出校验要求的 cast_id 形状。
- 根本原因：代码问题（容错缺失/反馈不可操作）——cast 是可由结构化输入完全推导的字段（speaker→entity→voice），却没有程序兜底；修复反馈只说"cast_id 缺失"，不告诉模型（也不告诉程序）可以从哪里得到它。INC-20261101-13 已补上实体编号来源，本例是同一条身份链上"cast 形状"一环的缺口。
- 不是校验过严：cast_id/entity 绑定承担对白音色一致性检查，不能放宽。

## 4. 解决方案
- `plan_normalisation.normalise_plan` 在既有 cast/focus/speaker 记账之前新增推导（b1e8f62，写入点 plan_normalisation.py:394/398）：当 cast 缺失、不是"每项都带非空 cast_id 的对象列表"、或含无 cast_id 项时，从对白说话人重建：
  - 每个**已确认**（`identity_confirmed is True` 且带非空 entity_id）的不同实体一个条目；未确认/未知出现留给校验器，绝不编造；
  - cast_id 由 role_id 消毒生成、冲突加数字后缀（确定性、可续跑复现）；bindings = 该实体在 speaker 与段 role_ids 中的全部 (window_id, role_id) 出现；entity_id 逐字照抄；
  - voice = 该说话人台词所用音色（一实体多音色取第一个，交给既有音色一致性校验器报告，不改台词音色）；
  - 台词的 cast_id 仅在为空时填入；模型写的合法条目原样保留，只补缺；
  - 记录 `cast_derived_from_speakers`（before/after），幂等；不碰 speaker/text/voice/mode/segments；校验器未放宽。
- 门控：与横屏画布同键启用（新任务 output_canvas 存在时 `derive_cast=True`，pipeline 调用点传入），旧锁行为不变、回放字节不变。
- 通用性：只依赖"speaker 指向已确认窗口观察"这一结构，对 library/film 各检索模式、任何电影成立。

## 5. 为什么不用其他方案
- 继续靠修复反馈让模型自己写对：三连败证明反馈不可操作，每次浪费 3 次调用后停机。
- 放宽 cast 校验（允许无 cast_id）：对白音色一致性检查失效，错误成片风险。
- 为未确认角色也推导条目：会编造身份，违反"不编造身份"的既有规则（INC-20261029-07 同理）。
- 跨窗口按外形合并成同一 cast 条目：易把不同角色并错；实体粒度交给观察层的 entity_id（INC-20261101-13 已定）。

## 6. 怎么验收
- 三份真实失败回复带证据上下文回放：glm_023 / glm_024 / glm_025 全部 PASS，cast=6，画布同步归一化 1280×720；审核者确认 `replay_check.nested_validator` 提取的是真实校验闭包（非放宽版）。
- 新增 `tests/test_cast_from_speakers.py`（171 行）：合法 cast 不动；部分合法补齐；未确认 speaker 不建条目；同外形两窗口 → 两个 cast 条目；幂等。
- 三门（与 S20 同跑）：pytest 9 failed / 2316 passed / 106 skipped（FAILED 与基线逐条相等）；replay 与基线零差异、ok/fixed 降级 0；fault 五项全 0。
- 真实任务续跑回执：未记录（三条回复回放即当时离线验证手段）。

## 7. 量化数据
- 修复前：该任务 plan_0 三次尝试 3/3 失败于同一 `plan/cast/cast_id:text_required`，任务停机。
- 修复后：同一证据 3/3 通过，cast 各 6 条；零新增模型调用。
- 测试：+171 行。每任务节省的修复调用：3 次（本证据形态）。

## 8. 经验与后续
- 凡"可由结构化输入完全推导"的字段（cast、编号、时间窗换算——见 INC-20261101-10/11 同类处理），应当由程序机械生成并留痕，把修复预算留给真正的创作决策。
- 身份链（观察 entity_id → cast → 台词 cast_id → 音色）每新增一环，都要检查上一环的输出形状是否够用；INC-20261101-13 补了来源，本例补了形状，属同一条链的两次补齐。
- 遗留：模型若继续写观察形状的 cast，现在会被推导兜住；但"模型到底该写什么形状"的提示示例与推导结果并存，长期看可把 cast 从模型必写字段降级为可选（须另立任务评估对音色选择的影响）。
