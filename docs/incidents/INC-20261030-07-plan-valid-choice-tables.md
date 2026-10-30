# INC-20261030-07 plan 修复缺可复制的合法选择表，跨来源身份与时间域修复振荡直至耗尽

| 项 | 内容 |
|---|---|
| 发现日期 | 2026-10-30 |
| 影响范围 | 所有模板的 fine 身份上下文及 plan_0；5 次夜跑修复耗尽，浪费调用、致命停机 |
| 状态 | 已修复（工作树；未提交，未进行新的在线重跑） |
| 相关提交 | 基础 HEAD `93fd5d4`；已有绑定与事件反馈来自此前修改，本批补齐缺口，无新提交 |
| 相关任务 | `evidence_01_不烧心_3`、`evidence_02_雪山救狐狸_4`、`evidence_18_粉色娇嫩_14`、`evidence_19_马冬梅_16`、`evidence_19_马冬梅_17` |

## 1. 问题长什么样

上述五次均以 `model_protocol_repair_exhausted:plan_0` 结束。证据含跨来源 cast 合并、未确认角色绑定、所选子段没有对应角色事件、把原片绝对秒数改为从 0 开始的局部秒数、跨 usable_ranges 选段、同一实体改音色，以及 fine 复制其他来源 entity 前缀。

## 2. 怎么查出来的

提取五份文本中 16 条 protocol_failure（15 条 plan、1 条 fine）及完整顶层模型字段。对缺失 watched_windows 的地方明确使用受控测试上下文；同时复用已有真实 fine 事件间隙夹具和不烧心跨来源绑定夹具。

检查 `src/omni_story/library/contracts.py:173`、第 207 行、第 790 行及第 1202 行的范围、身份、音色校验；追踪 `src/omni_story/library/pipeline.py:1915` fine 上下文来源和第 2149 行规划合同。没有应用 `round18_unreviewed.patch`；只将其作为待核参考查看。

## 3. 根因

可见角色、稳定实体和所选子段角色不是同一概念。旧 fine 上下文扁平混入其他来源已看角色，诱导复制外源 entity；旧 plan 虽已有部分角色/绑定反馈，但没有由正文与单条 observed usable_range 共同计算的绝对秒数表。时间、身份、音色的反馈粒度不一致，使一次修复可能引出另一种失败。修复范围指令也过于宽泛。

## 4. 解决方案

| 拒绝码 | 原因与通用修复 |
|---|---|
| `plan:cast_entity_not_supported_by_observation` | 把一个 entity 绑定到实际不同实体或未确认出场。保留原拒绝；复用已存在的 allowed_bindings，并增加含 identity_confirmed 的 bindable_triples 给所有创作规划。只可复制已确认三元组，不按同名、服饰或同音色合并。 |
| `visual_library:range_outside_content` | 修复把 source 秒数整体改成 local，或选到正文外。新增 plan_source_range_contract，计算正文、窗口、单条 usable_range 的交集；拒绝时列出这些绝对秒数选择，保留原错误路径及数值。 |
| `plan:segment_role_missing_overlapping_event_evidence` | usable_range 的角色并集被误用于无该角色事件的子段。保留此前已实现的严格相交校验和每角色事件表；与新增范围表同时交给规划者，允许删除无证据角色而非伪造事件。 |
| `plan/confirmed_roles:unknown_or_duplicate_ref` | 局部角色被当作已确认或跨窗口有效。保持原 refs 规则；反馈继续列 confirmed_role_ids、overlapping_role_ids、role_event_ranges。 |
| `plan/focus_binding_confirmed_role:unknown_or_duplicate_ref` | 焦点绑定指向未确认角色。保留按窗口的 allowed_bindings；无需绑定每个窗口，空选择表示该窗口不可绑定，不能用声明覆盖观察。 |
| `plan:range_not_supported_by_fine_observation` | 选段跨多条 usable_range，或角色集合不受同一条支持。规划、校验和多问题反馈共用 matching_plan_source_ranges；每条候选保留 usable_range_index，禁止合并范围取得授权。 |
| `plan:dialogue_speaker_voice_changed` | 已建立实体/角色的配音音色变化。三个原拒绝位置都给出具体 audio_draft 路径、allowed_voices 和 hint；多问题反馈扫描其他句，观察身份优先于无证据 cast 声明。 |
| `fine:entity_source_namespace_required` | 马冬梅 fine 把另一部片的熊猫锚点复制到本片。生产方 fine_identity_context 只提供本来源角色，并提供同一 fine_entity_contract；校验规则仍要求原 source_sha256 + 合法 ASCII 局部 ID，拒绝时列本源锚点或提示省略未核验 entity。没有改前缀冒认同一实体。 |

所有 format repair 被明确限制为 violations 及必要依赖，不整体改写时间域或顺手加绑定。预算不增加，未自动改写 EDL，也未把修复耗尽当作通过。

## 5. 为什么不用其他方案

- 不扩大正文、窗口或 usable_range：这会引入片头片尾或未看素材。
- 不放开 source namespace、identity_confirmed 或事件相交：会给身份穿帮授权。
- 不自动给 plan 整体加减 source offset：真实 EDL 是编辑决策，混合时间域不能靠猜测转换。
- 不增加修复轮数：更多相互矛盾的重写不能消除合同信息缺口。
- 不盲贴未审核 patch：本批依据当前 HEAD 和真实夹具逐项验证。

## 6. 怎么验收

见 `reports/reference_value_understanding_r1_zh.md`，包括命令原文、完整 FAILED 列表、回放每一行变化清单和 fault 指标原输出。五份耗尽记录均验证选择表可复制至同一校验器；真实跨源 cast、未确认角色、无重叠事件和正文外范围仍被拒绝。回放 203 行，无原 ok/fixed 变成 regression/still_failing。

## 7. 量化数据

五份文本记录提取 16 条协议失败。与 `replay_baseline_98e9427.json` 比较，203 行分类全部不变（143 ok、21 fixed、6 原有 regression、33 原有 still_failing）；14 行诊断载荷有变化，逐行见报告。E1、R1、M1_crash、M2_json、M2_crash 全为 0。新在线规划成功率、节省调用数及真实耗时未测量。

## 8. 经验与后续

可修复不仅意味着拒绝有 hint，还意味着模型事前拿到与校验同源的有限合法选择。不得用计划声明制造观察证据。原回放仍有既存失败，未在本批放宽规则消除；线上是否减少耗尽需后续真实重跑测量。语义根因见 [INC-20261030-06](INC-20261030-06-minimal-visible-meaning-transfer.md)。
