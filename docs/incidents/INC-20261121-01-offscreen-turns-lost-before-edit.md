# INC-20261121-01 参考里的画外台词进剪辑后被当成画内，要求对口型，旁白分支也永远走不到

| 项 | 内容 |
|---|---|
| 发现日期 | 2026-11-21 |
| 影响范围 | A 线通用对白剪辑（`approved_dialogue_preview` → `reviewed_sequence_edit` → `dialogue_mouth_*`），所有含画外参考台词的模板；错误成片（画外句必然判不过，整片只能是 `needs_review`），以及旁白片误入口型剪辑 |
| 状态 | 已修复并部署（发布 `scene_v1_schedule_980bf32d3381`），《不烧心》从零实跑验证中 |
| 相关提交 | e87caad / 3c77af4（分支 restore/general-reviewed-dialogue-1121） |
| 相关任务 | 历史草稿：`trials/fast_template_20261114_*_01_不烧心_*`、`fast_template_2026111[5-6]_*_17_狂飙吃鱼_*`、`*_21_薛甄珠吵架_*`；验证任务：`trials/fast_template_20261121_114805_01_不烧心_01_32cafd4a7159` |

## 1. 问题长什么样
- 服务器上 12 个历史模板任务的 `dialogue_script.json`，没有一行带 `on_screen` 字段（逐行统计：`(0, 25, ['<missing>'])` 等）。
- 参考台词本身有核验过的画外标记，例如《立刻有》`original_reference_contract.json` 第 1 句 `on_screen=False`，《不烧心》第 1、2、7 句（"老板老板""明天能不能订300桌""老板还是你稳啊"）为 `False`。
- 剪辑包 `bundle.json` 的每句 `on_screen` 全是空，经 `dialogue_mouth_bundle.py:97` 的默认值变成 `True`，评分 `dialogue_mouth_edit.py:863` 把这些句子全部当作"必须看见说话人动嘴"。
- `approved_dialogue_preview.py:31`（修复前）用 `row.get("on_screen", True)` 判断是否转纯旁白分支，因为字段从不存在，这个分支在任何模板上都不会触发。

## 2. 怎么查出来的
1. 接管时在服务器跟《立刻有》认可片：`full_verified/outcome.json` 逐句 8/9，未过的 l1 对应参考第 1 句（画外）+第 2 句。
2. 读 `approved_dialogue_preview.deliver()`，发现旁白判断依赖剧本行 `on_screen`；用服务器脚本统计所有 `fast_template_*` 的剧本文件，字段出现次数为 0。
3. `grep on_screen` 全库：只有 `reference_dialogue.py:386/457` 写参考台词的 `on_screen`，`joint_dialogue.py:742` 只在调度行上写 `reference_offscreen`，没有任何代码把它写回 `dialogue_script.json`。
4. 确认 `reference_turns_verified` 里的 `on_screen` 是核验后的结果：GLM 在参考帧里认出说话人嘴动时，`reference_dialogue.py:457` 会改成 `True`，剩下的 `False` 才是真画外。
5. 排除"剧本作者有意改成画内"的假设：剧本格式没有可见性字段，写剧本提示（`joint_dialogue.py:282`）只说画外句"可保留画外声音配对话对象倾听"。

## 3. 根因
直接原因：剪辑阶段只看剧本行的 `on_screen`，剧本行从来没有这个字段，于是默认全部画内。根本原因：画外标记只存在于参考台词（`reference_turns_verified`），而剧本行只保存了 `reference_line_ids`，剪辑入口没有按这些编号把核验结果带过来。属于代码问题（流程分支漏传字段，默认值掩盖了缺失）。

## 4. 解决方案
- `src/omni_story/library/reviewed_sequence_edit.py:107` 新增 `verified_turns()`，读取 `reference_turns_verified`；`:112` 新增 `heard_on_screen()`：一句剧本台词只有在它改编的**全部**参考台词都核验为画外时才算画外，混合句（如《立刻有》l1）仍按画内处理。
- `reviewed_sequence_edit.py:250`：剪辑前给每行 `setdefault("on_screen", heard_on_screen(...))`；随后原有逻辑在参考帧确认说话人时仍改为 `True`（`:266`）。剧本将来若显式写了 `on_screen`，以剧本为准。
- `approved_dialogue_preview.py:32`：旁白分支改用同一判断，纯画外/旁白参考转 `scheduled_preview`。
- 缺少 `reference_turns_verified` 的旧目录退回原默认（画内），不比修复前更差（3c77af4）。

通用性：判断只依赖参考片本身核验过的画内/画外，不涉及任何模板、角色或帧号。

## 5. 为什么不用其他方案
- 让写剧本的 GLM 在剧本里输出 `on_screen`：要改提示词和校验并重新生成所有草稿，且会让已锁草稿的哈希变化，旧任务无法改剪复用。
- 在剪辑包里一律放宽评分（画外句不计分）却不标记：剪辑模型看不到哪句是画外，仍会为画外句硬找嘴动镜头。
- 混合句也算画外：会放过原片里确实露脸说话的那部分，不符合参考。

## 6. 怎么验收
- 新增测试 `tests/test_verified_dialogue_route.py::test_a_line_is_off_screen_only_when_every_adapted_reference_turn_was`，服务器全量 `schedule_tests` 359 项通过（修复前 358 项）。
- 无新请求回放（`OMNI_REPLAY_ONLY=1`）：《立刻有》《华强买瓜》《马冬梅》三份历史草稿都能进入同一剪辑入口；《立刻有》剪辑输入重建仍为 9 句、545 帧、11 段素材；原成片口型重算仍为 358/363，另一人同时动嘴 0 帧。
- 用新代码重放《不烧心》历史草稿：25 句版中 l1、l2、l7 判为画外，正好对应参考的三句画外喊话；12 句版中 l2 判为画外。
- 真实服务器从零运行：`fast_template_20261121_114805_01_不烧心_01_32cafd4a7159`，结果待补。

## 7. 量化数据
历史草稿里"改编自纯画外参考台词"的剧本句（修复前全部被要求对口型）：
- 不烧心：3/25、1/12
- 狂飙吃鱼：3/10、3/14
- 华强买瓜：1/29、1/31（18 句版 0）
- 薛甄珠吵架：1/31、1/72（21 句、40 句版 0）
- 雪山救狐狸 0/22、马冬梅 0/10、粉色娇嫩 0/7；立刻有 0 句纯画外、1 句混合（l1）

修复前后的成片通过率、耗时：未测量，以《不烧心》实跑结果补充。

## 8. 经验与后续
- 默认值 `get(..., True)` 会把"字段从没写过"伪装成"画内"，新增跨阶段字段时应在交接点统计出现率。
- 遗留：《立刻有》l1 这类混合句仍按画内评分；纯旁白参考转入的 `scheduled_preview` 是旧调度路线，当前版本未实跑过该分支。
