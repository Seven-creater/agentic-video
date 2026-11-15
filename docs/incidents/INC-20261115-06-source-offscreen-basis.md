# INC-20261115-06 原声核对回复把依据写成“画外音”，并把画外的人标为说话人，选角前停止

| 项 | 内容 |
|---|---|
| 发现日期 | 2026-11-15 |
| 影响范围 | 原声说话人核对 `scene_casting.normalize_source_turns`；致命停机（选角前） |
| 状态 | 已修复，发布 `scene_v1_schedule_1793eb085328` |
| 相关提交 | 无 git |
| 相关任务 | 停止：`fast_template_20261115_053740_…`；续跑：`…_061941_…` |

## 1. 问题长什么样
`ValueError: source speaker must be one actually visible known actor`，位于 `scene_casting.py:64`，`validate_source_turns`。Qwen 核对 `vl_0aa53d1263f6ae57` 的回复中有一段：`visible` 只有两只小动物，`speaking` 却是盔甲角色，`basis` 写成 `off_screen_voice`。这个值不在允许的 mouth_motion、synchronous_gesture、none、uncertain 之内。

## 2. 怎么查出来的
在服务器上用同一份代码回放三份原声回复，只有 `0aa53` 不通过；逐段检查，找到上面这一段。另有一段把两个人标为同时说话，这种情况已由既有规则转成声音假设，不会报错。

## 3. 根因
已有的归一化规则只处理 none/uncertain 依据下的说话人，以及 mouth_motion/synchronous_gesture 依据下说话人不在画面内的情况，没有处理“自行编写的非画面依据”。这是对外部模型输出的容错缺失。

## 4. 解决方案
依据不在四种标准值之内时，如果画面人物和说话人都是已知演员编号，就把原依据记入 `source_basis_original`，依据改为 uncertain；随后既有规则把说话人转为 `heard_actor_hypotheses`，交给独立画面复核。这条规则通用：只靠听觉得到的说话人，不能当成画面中可见的说话。说话人编号未知时仍然拒绝，原始回复不改写。

## 5. 为什么不用其他方案
- 把 `off_screen_voice` 加为合法依据：画外音不能证明画面中的人在说话，会污染选角证据。
- 丢弃整段：会丢掉“听到盔甲角色的声音”这一有用线索。
- 重新请求 Qwen：需要再付费，而且模型可能再次写出非标准值。

## 6. 怎么验收
- 新增 `test_source_offscreen_basis.py`，共 3 项：用真实回复验证该段转为假设、说话人不越出画面、原始回复不改写；未知编号仍被拒绝；真实 mouth_motion 保持不变。
- 全量 145 passed。
- 续跑 061941 复用了已付费的 Qwen 回复（`source_speaker_reuse`），越过停止点进入选角。

## 7. 量化数据
- 修复前停在第 37 个请求。
- 修复后新增 Qwen 调用 0 次。

## 8. 经验与后续
外部模型的枚举字段可能被自行扩展，校验时应区分“可安全降级的值”和“必须拒绝的值”。
