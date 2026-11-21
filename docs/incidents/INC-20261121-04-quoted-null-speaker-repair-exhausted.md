# INC-20261121-04 GLM 把"看不出说话人"写成带引号的 "null"，报错不点名字段，三次回复都被拒，任务开跑 9 分钟就停下

| 项 | 内容 |
|---|---|
| 发现日期 | 2026-11-21 |
| 影响范围 | 参考读取阶段"核对每句谁在说"（`reference_dialogue.review_exchange_speakers`），所有有两名以上角色、含画外台词的模板；致命停机（`model_protocol_repair_exhausted`） |
| 状态 | 已修复并部署（发布 `scene_v1_schedule_0784b757663a`），《不烧心》从零实跑验证中 |
| 相关提交 | a59d5e6（分支 restore/general-reviewed-dialogue-1121，未推送） |
| 相关任务 | 出错：`trials/fast_template_20261121_140048_01_不烧心_01_32cafd4a7159`；验证：`trials/fast_template_20261121_141617_01_不烧心_01_32cafd4a7159` |

## 1. 问题长什么样
- 22:00 用发布 `601d45ae82de` 从零发起《不烧心》，22:09 停下：`AssertionError:creative_stopped_before_usable_draft:model_protocol_repair_exhausted:material_scene_turn_lineframes_1`。
- 三份回复（原回复、修订 1、修订 2）的报错都是同一句：`确认说者须引用本行两张不同的实帧，含口部动作及听者依据`。
- 回复里第 1、2 句（"老板老板""明天能不能订300桌"，参考里是画外喊话）写的是 `"speaker":"null"`，带引号；cue 写着"未见说话者入画，无法确认谁在喊"。

## 2. 怎么查出来的
1. 读 `run_outcome.json` 和三个 `calls/glm_01[1-3]_*/protocol_failure.json`，拿到报错和 GLM 原文。
2. 读服务器生效代码 `reference_dialogue.py`（与本地逐字节一致）的 `confirm`：一个 `if` 里合并了四个条件，只给一句报错。第一个条件 `r.get('speaker') is not None and r['speaker'] not in characters` 对字符串 "null" 成立，所以报的是"两张实帧"，真正的问题（speaker 不是角色名）没被说出来。
3. 对比三份回复：GLM 每次修订都在改帧号（例如第 2 句从空数组改成两帧），speaker 一直是 "null"，修订提示没能引导它改对。
4. 提示写的格式是 `"speaker":"角色或null"`，null 放在引号里，GLM 照样写成字符串是自然的读法。
5. 扫描服务器全部任务：这一步共有 7 次格式失败，7 次都是带引号的 null，涉及 3 个任务（`e2e_hq_20261121_fresh01`、`fast_template_20261119_164247_23_唐探立刻有`、本次《不烧心》）；20 份回复里 7 份含带引号的 null。

## 3. 根因
直接原因：校验把字符串 "null" 当成一个不存在的角色名。根本原因：提示词与校验不一致（示例把 null 写在引号里，校验只认 JSON null），加上反馈不可操作（四个条件共用一句报错，没指出是 speaker 字段）。属于代码问题。

## 4. 解决方案
提交 a59d5e6，`src/omni_story/library/reference_dialogue.py`：
- 新增 `given_speaker()`：speaker 是字符串 "null"、"none" 或空串，且不是已有角色名时，按 JSON null（看不出说话人）处理。逐句帧板检查 `confirm` 和单句视频检查 `check_one` 都先经过它。
- `confirm` 拆成按字段报错：speaker 不是角色名、frames 不是本行帧号、确认说者少于两张不同帧，各报各的原因并带上当前值。
- 提示词不改，已有任务的请求哈希不变。

通用性：只改这个检查的字段解析和报错，不涉及具体模板、角色或台词。

## 5. 为什么不用其他方案
- 改提示词示例、把 null 写在引号外：改变请求哈希，已有任务续跑时要重新请求；而且模型仍可能写成字符串，校验侧兜住才稳。
- 只改报错、不接受字符串 null：GLM 改对要多花一次修订，修订用完照样停。
- 提高修订次数：同一句含糊的报错重复三次也没改对，加次数只会多浪费调用。

## 6. 怎么验收
- 测试：`test_reference_exchange_rechecks_the_real_line_without_prior_speaker` 增加一条：speaker 写成 "null" 时校验通过，并改为 None。服务器 `schedule_tests` 361 项通过。
- 无新请求回放（`OMNI_REPLAY_ONLY=1`）：把本次三份原始回复交给旧、新两版的真实 `review_exchange_speakers`。旧版 `601d45ae82de` 三份全拒；新版 `0784b757663a` 三份全收，第 1、2 句判为看不出说话人（None），第 3–6 句为老板，和参考的画外喊话一致。
- 真实服务器从零运行：22:16 发起 `fast_template_20261121_141617_01_不烧心_01_32cafd4a7159`，结果待补。

## 7. 量化数据
- 服务器上这一步的格式失败 7 次，全部由带引号的 null 引起；20 份回复中 7 份含带引号的 null（35%）。
- 本次停机前用掉 13 次 GLM 请求、约 9 分钟。
- 修复后这 7 次失败按新校验都会被接受（推断：其余 6 次没有逐一回放，其中带引号 null 之外的字段未核对）。

## 8. 经验与后续
- 一个 `if` 合并多个条件只报一句话，会让修订回路"越修越偏"；每个字段应单独报错并带当前值。
- 提示里的 JSON 示例要和校验口径一致；null、数字这类非字符串值不要写在引号里。
- 遗留：其他检查也可能有同样的"示例带引号的 null"，这次没有全库排查。
