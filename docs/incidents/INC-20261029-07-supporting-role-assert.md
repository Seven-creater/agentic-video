# INC-20261029-07 配角没被选角,裸断言把整个任务打断(B_trembling_victim 无 cast)

| 项 | 内容 |
|---|---|
| 发现日期 | 2026-10-29 |
| 影响范围 | question_first 的 questions 回复校验;致命停机 |
| 状态 | 已修复(4e8d2d9) |
| 相关提交 | 4e8d2d9 |
| 相关任务 | 真实回复 fixture:`tests/fixtures/question_first/heihu_questions_1013f88.json`(125 行)与 `heihu_reference_reading_1013f88.json` |

## 1. 问题长什么样
questions 回复只为反派选了角;`B_trembling_victim`(发抖的受害者)等配角没有出现在 casting 里。校验用裸 `assert all(role in data["casting"] for ...)`(question_first.py:416,当时行号)直接 AssertionError,任务崩溃。

## 2. 怎么查出来的
真实 heihu 回复(1013f88 运行)触发断言;对照 QUESTIONS 提示词——并未要求模型给每个角色都选具体演员。

## 3. 根因
代码问题(校验过严 + 裸断言):校验要求比提示词更严;配角(受害者、对手)本来就不需要固定演员;且用 assert 表达校验,失败即崩、无 violation 信息。

## 4. 解决方案(4e8d2d9)
配角可以不选角:beats 里出现而 casting 缺失的角色自动补空列表(`data["casting"][role] = []`),不再断言;仅要求至少有一个角色被选上(`assert any(data["casting"].values())`);未选角角色不生成固定锚点、不参与锚点过滤(faa499b 后与白名单联动:未知选角逐条删除记 `dropped_casting`,全部未选角才抛 `qfs_questions:no_valid_film_casting`)。
通用性:按"角色是否需要固定演员"的结构区分,与具体题目无关。

## 5. 为什么不用其他方案
- 强制模型给所有角色选角(改提示词再要一次调用):配角强行选角会制造假身份(方案文档已指出表外角色被硬套表内名的问题);空列表更诚实。
- 其余备选:未讨论。

## 6. 怎么验收
真实回复入库做 fixture(heihu_questions_1013f88.json 等 245 行 fixture 随提交);测试 +11 行断言配角空选角可过、全空才拒。专项验收输出未单独归档;行为由后续 C1 轮(faa499b)16 个新旧对照用例继续覆盖(`旧源码 a5d692d:16 failed → 当前 17 passed`)。

## 7. 量化数据
未测量(一次性崩溃;修复后该断言 0 复现)。

## 8. 经验与后续
- 裸 assert 不能用于模型回复校验:失败必须是带 violation 的可修复 ValueError 或有意的降级路径。
- "哪些角色必须有演员"要作为显式设计决定写进提示词与校验器(方案文档:选角先定人,但只对需要身份连续性的角色)。
