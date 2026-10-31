# INC-20261031-01 规划手抄身份及来源标识反复失败，耗尽 plan_0 修复预算

| 项 | 内容 |
|---|---|
| 发现日期 | 2026-10-31 |
| 影响范围 | 创作路线粗剪与精剪规划；停机、浪费调用；所有参考模板 |
| 状态 | 已修复（工作树，未提交）；182 全部校验通过，183 因未知选材窗口继续拒绝 |
| 相关提交 | 基线 a53427b；本批按要求不提交 |
| 相关任务 | `.agent-team/qfs_fixture/plan_r1_bushaoxin/`，glm_182/183/184；用户另报告 5 次同类耗尽 |

## 1. 问题长什么样

初始三次真实回复依次报 `plan:cast_entity_not_supported_by_observation`、`plan:focus_binding_unknown_window`、`plan:dialogue_speaker_not_in_selected_segments`。第一版规范化后，182 仍报 `plan:window_source_mismatch`，183 仍报 `plan/focus_role_bindings:list_required`，184 报真实重复素材错误。搜索已有可用画面，规划却仍需要模型逐字抄写确定性的标识。

## 2. 怎么查出来的

读取真实 request、response、protocol_failure 和 `watched_windows.json`，逐项核对窗口来源、确认状态、原样 entity_id、规范名和观察证据。通过 `replay_check.nested_validator` 执行生产粗剪闭包；测试位于 `tests/test_plan_normalisation.py:35`。

182 的 `seg_8` 使用 `window_b068c254cdc29902`，窗口来源是 `src_cf2fec1024243517`，模型却填了 `src_24675c65978d5360`。来源可由已选窗口唯一确定，应机械回填。

183 的 `cast` 不存在，7 个选段全部使用未知窗口 `ref_movie`，原焦点绑定也是 `ref_movie/boss`。28 个已确认观察角色全部有 `identity_evidence`；因此不是证据文本缺失，而是 cast 候选为空、所用合法窗口数为 0。窗口和来源均来自参考片，不能自动替换成电影素材。

排查入口为 `src/omni_story/library/pipeline.py:2111`、`src/omni_story/library/story_finecut.py:485`；来源回填与身份推导分别在 `src/omni_story/library/plan_normalisation.py:66`、`:78`。

## 3. 根因

直接原因是第一版只处理 cast、focus 和对白挂载，没有回填窗口所决定的 source_id；焦点候选又依赖成功绑定的 cast，并把“某窗口唯一出现的 cast”误当成“该焦点人物”。根本原因仍是创作决策与机械引用簿记未完全分工。183 同时存在真实选材问题：没有任何所用窗口对应已确认观察，且无法辨明电影焦点人物。

## 4. 解决方案

共享模块在原校验前从已选窗口复制 `segments.source_id`；若 segment 已有 `source_sha256`，一并复制窗口值。未知窗口保持原值；不改素材范围和角色可见区间。每项变化写入 `plan_normalisations:{path,before,after,reason}[]`。

cast 只从实际使用窗口的确认观察推导，按原样 entity_id、qfs 规范名/别名和固定 cast.json 快照匹配，不跨不同 entity_id 合并 cast。焦点先解析明确的 cast_id、规范名/别名或观察 entity_id，再按原有效焦点声明确定身份；对每个所用窗口仅补唯一确认匹配，逐字复制观察证据。保留不同局部实体的原有效视觉焦点声明，但没有共同身份时不向新窗口外推，也不因该窗口恰好只有另一个 cast 就改绑人物。

合同 `src/omni_story/library/contracts.py:816` 强制要求观察自身具有 `identity_evidence`。本次不以 `state` 代填或改写观察；缺失时给出 `observation_identity_evidence_required` 诊断。没有确认匹配时保留 `focus_role_id`，由原校验拒绝；反馈包含 13 个有效窗口、28 行 `(window_id, role_id, entity_id, name)`，其中无可证名称/实体的值保持 null；另保留 11 个可绑定 cast 三元组和各 speaker 的可见片段。

对白继续按最近且容纳台词的可见段、当前/更早身份锚、合同允许的旁白依次处理。原付费回复与夹具保持原值；生产规范化结果单独保存。身份、范围、连贯性校验未修改，非创作 library 路径不启用规范化。

## 5. 为什么不用其他方案

- 不放松确认状态、实体匹配或证据必填：会把缺身份问题伪装成合法计划。
- 不清空 `focus_role_id` 来绕过非空绑定规则：会静默丢掉创作人物。
- 不把 `ref_movie` 映射到某电影窗口：没有唯一对应，属于替模型重选镜头。
- 不把 source_id 不一致称为独立创作错误：window_id 已唯一决定来源，可以确定性修复。
- 不要求不同窗口的原有效焦点声明共享一个局部 entity_id：原合同允许模型用视觉依据声明跨窗口焦点，局部 ID 不同不能单独否定该声明；程序也不能凭此新增未确认的关联。

## 6. 怎么验收

完整命令、原始输出与逐回复结果见 `reports/reference_value_plan_batch_p1_zh.md` 和同名 results JSON。专项测试共 28 项：本次修复前源码快照为 `10 failed, 18 passed`，修复后全部通过；原始 182/183 的新断言改前失败、改后通过。新增历史 `glm_006_plan_0_repair` 夹具，保证有效的局部实体焦点声明不回退。

182 经过来源和绑定规范化后通过完整生产校验；183 保留原非空错误，但反馈明确全部选段使用未知窗口，列出合法替代值；184 仅重复素材拒绝。测试同时覆盖无确认身份、缺证据、明确焦点不可漂移、source SHA 回填、幂等性、真实反馈载荷和 library 不变。

全量测试为 1685 passed、9 failed、104 skipped、24 errors。9 项 FAILED 全在指定基线中；24 项 ERROR 为沙箱禁止监听本地 socket。历史回放相对指定基线无 ok/fixed 回退。E1、R1、M1_crash、M2_json、M2_crash 均为 0。未调用模型、未在线重跑、未渲染新成片。

## 7. 量化数据

真实 182/183/184 分别记录 4、1、5 项规范化，最终为 PASS、未知窗口导致焦点无绑定、重复素材拒绝。183 的 28 个确认角色中缺证据数为 0，实际使用合法窗口数为 0，cast 数为 0。历史回放 203 行：ok 143、fixed 21、regression 6、still_failing 33，分类变化 0 行；线上新调用 0 次。实际耗尽率与费用改善未测量，需在线任务重跑验证。

## 8. 经验与后续

窗口决定的来源字段必须由程序复制；焦点补齐需要先确定人物，再找所用窗口的出现。原有效跨窗口声明与程序新增的身份关联应分别处理：保留前者，新增关联必须有规范身份依据。183 需要模型重新选择真实窗口和电影区间，现有信息不足以自动恢复。重复素材问题见 [INC-20261031-02](INC-20261031-02-repeated-source-footage.md)。
