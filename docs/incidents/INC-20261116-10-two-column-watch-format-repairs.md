# INC-20261116-10 带题两栏看片后，回复格式被大量打回，补画面时修复次数用尽停机

| 项 | 内容 |
|---|---|
| 发现日期 | 2026-11-16 |
| 影响范围 | scene_v1 素材浏览静音看片（question_first_coarse 引入 goal_fit 之后）；浪费调用，最终致命停机 |
| 状态 | 已修复（release scene_v1_schedule_abf9482725f8），薛甄珠续跑中 |
| 相关提交 | 未提交 |
| 相关任务 | 薛甄珠 fast_resume_20261116_135649 |

## 1. 问题长什么样
`model_protocol_repair_exhausted:material_scene_browser_blind_vl_660dc1b40a3ae78f`。这条线路中共 38 次静音看片，其中 17 次需要第一次修复，8 次需要第二次修复。报错分布：`visible_facts[#] must be a plain string` 13 次，`interaction[#] ...` 8 次，`goal_fit[#]: start_s/end_s ... start<end` 3 次。

## 2. 怎么查出来的
统计了全部 blind 回复中写成对象的事实条目，键的组合有：`fact+second` 51 次、`description+second` 34 次、`content+second` 17 次、`fact+index+time` 7 次、`fact+id+s` 7 次、`desc+idx+time` 7 次、`description+fact_index+time_s` 6 次，等等。原来的 `timed_text` 只认 `second/time_s/start_s/end_s` 和 `desc/description/text/fact` 这几个键，时间也必须是数字。goal_fit 有一处 `start_s=end_s=16`，是"瞬间定格"，原校验要求开始必须早于结束，因此被拒。

## 3. 根因
第二栏要求填写 `fact_index`，模型于是倾向给第一栏的每条事实加编号和时间，写成对象。格式归一化没有跟上这种新形态，导致内容本身正确的回复被反复打回重写；单一瞬间也被误判为时间无效。属于"提示词与校验不一致"。

## 4. 解决方案
- `timed_text`：文本键增加 `content`，时间键增加 `time` 和 `s`，时间值也接受字符串（如 "0-2秒"）。编号键（`index/idx/id/fact_index`）的值必须是整数，处理时直接丢弃，因为列表里的位置本身就是编号。处理后统一写成"时间秒：内容"，不丢失任何内容。键名未知或值不清楚的，仍然打回。
- `validate_goal_fit`：允许 `start_s == end_s`。
- 提示词不改。续跑时，之前因格式被打回、但内容正确的回复，按新规则可以直接复用，不用重新请求。

## 5. 为什么不用其他方案
- 在提示词里强调"必须写纯字符串"：会改变提示词，导致已收到的所有看片回复都要重新请求；而且同样的格式漂移以前也发生过，靠提示词约束不住。
- 去掉 fact_index：这样就无法校验第二栏的说法有没有依据。

## 6. 怎么验收
`test_blind_visual_protocol.py` 新增 2 项（编号对象和不同键名都能无损展开；非整数编号或未知键仍被拒）。原有那项"字符串时间应被拒"的测试，按新的、有意放宽的行为改为"列表时间应被拒"。服务器全量测试 209 项通过。

## 7. 量化数据
修复前：38 次看片，修复 25 次（占 66%）。修复后：待续跑后统计。

## 8. 经验与后续
每次改动输出格式（比如新增一栏），都应该用真实回复统计一遍格式形态，再确定归一化规则。
