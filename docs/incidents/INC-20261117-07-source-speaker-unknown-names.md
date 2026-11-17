# INC-20261117-07 窗口里没有确认的人物时，Qwen 自己给角色起名，原声说话人校验停机（02 A 线）

| 项 | 内容 |
|---|---|
| 发现日期 | 2026-11-17 |
| 影响范围 | 原声说话人核对（`scene_casting.normalize_source_turns` / `validate_source_turns`）；致命停机 |
| 状态 | 已修复（A 线 d8f704f，release 0c67c8b55fdd）；B 线暂停，未同步；实跑验证中 |
| 相关提交 | d8f704f（仅本地分支，未推送） |
| 相关任务 | `trials/fast_template_20261117_044344_02_雪山救狐狸_01_e5a516763632` |

## 1. 问题长什么样
`AssertionError:creative_stopped_before_usable_draft:source speaker must be one actually visible known actor`。

报错发生在换到章鱼烧摊组之后，核对窗口 vl_d81814b2681c1388 时，位置是 `scene_casting.py:64`。

## 2. 怎么查出来的
- 读 GLM 对这个窗口的看片结果（observe_6_repair 的 parsed.json）：`confirmed_actors: []`，每段的 `visible` 也都是空的。也就是说，这个窗口里没有确认任何已知人物。
- 读 Qwen 原声核对的请求：`people: {}`（已知人物名单为空）。
- 读 Qwen 的回复：它自己给角色起了名字，`visible: ["Hachiware","Chiikawa"]`，还写了 `speaking: ["Hachiware"]`、`basis: mouth_motion`。
- `validate_source_turns` 要求可见人物和说话人都必须在名单里，于是抛出 ValueError。这一步没有修复流程，异常一路传上去，整个任务停机。

## 3. 根因
校验和容错没有对齐。名单外的名字说明"认不出是谁"，按"不能猜身份"的原则，应该是"这一段没有可确认的说话人"，而不是让整个任务停下。原来的几个测试（未知说话人必须报错）锁定了"停机"这个行为，它们真正要保护的是"不能把未知名字猜成已知角色"。

## 4. 解决方案
- `normalize_source_turns` 新增一步：
  - 名单外的名字从 `visible` 去掉，记到 `unidentified_visible`；
  - 被标成说话人的名单外名字移到 `heard_actor_hypotheses`（只听到、不能确认是谁）；
  - 如果因此没有说话人了，`basis` 改为 `uncertain`，并记下 `source_claim_conflict`。
- 已知人物的判断不受影响；不是字符串的异常值照样校验失败。
- 不会给任何镜头凭空加上说话人，质量底线不变。

## 5. 为什么不用其他方案
- 让 Qwen 重答：这一步没有修复流程，要新加；而且名单为空时，重答也没有可选的人。
- 名单为空就跳过核对：只能覆盖"名单为空"这一种情况，名单不空但多写了名字的情况照样会停。
- 猜名字对应谁（比如按外观匹配）：违反"不能猜身份"的原则。

## 6. 怎么验收
- 新增 `test_source_unknown_names.py`，共 3 项：
  - 名单为空且 Qwen 自己起名时，不认说话人，也不停机；
  - 已知说话人旁边多出一个未知名字时，已知说话人保留；
  - 不是字符串的异常值照样报错。
- 改写了 3 个旧测试的断言，本意（不猜身份）不变：
  - `test_cast_candidate_switch` 的有歧义外观；
  - `test_source_turn_conflicts` 的未知说话人；
  - `test_source_offscreen_basis` 的未知说话人。
- 服务器全量测试：A 线 246 项全部通过。
- 真实续跑：02 A 线 053555，结果待补。

## 7. 量化数据
未统计其他任务中 Qwen 自己起名的频率。以后可以统计各次运行的 `unidentified_visible` 字段。

## 8. 经验与后续
- 模型回复里出现名单外的名字时，应该当作"不确定"处理，而不是"格式错误要停机"。
- 遗留：GLM 看片时这个窗口一个人物都没确认，是另一层的问题（身份识别）。目前只是不让它导致停机，这个窗口的镜头也不会被当成有人说话。
- B 线暂停，这个修复还没同步，以后恢复 B 线时要补上。
