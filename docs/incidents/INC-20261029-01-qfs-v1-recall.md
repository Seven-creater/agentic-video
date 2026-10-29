# INC-20261029-01 带题找素材 v1 四缺陷:整页校验失败、等价画面逐字匹配、题目照搬细节、无同角色锚点

| 项 | 内容 |
|---|---|
| 发现日期 | 2026-10-29(实验 qfs_heihu_20261029 监控) |
| 影响范围 | question_first 实验脚本(scripts/question_first_search.py);漏检("吃瘪"节拍零候选)+ 人物前后不一致 |
| 状态 | 已修复(v2 实验脚本重跑验证;逻辑随后由 cf9a1aa 接入生产。无独立提交号——v2 修复保留在实验脚本,随 cf9a1aa 入库) |
| 相关提交 | cf9a1aa(集成);v2 修复本身无独立提交 |
| 相关任务 | `.agent-team/out/qfs_monitor/glm_summary.md`、`v2_glm_summary.md`;任务书 `tasks/Q1_question_first.prompt.md`、`tasks/Q3_qfs_fix.prompt.md` |

## 1. 问题长什么样
v1 总耗时 2:09:56,161 次调用 11 次失败;找到"喊叫"片段,"吃瘪"零候选。监控发现:
- 8/136 张宫格整页丢失(2 张 JSONDecodeError、6 张 AssertionError)——一格坏(空 beats、重复 n、未知 beat id)即整页 36 格报废,形成盲区;
- close 的"等价画面"判定用逐字包含匹配(`data["equivalent"] in job["beat"]["equivalents"]`),模型一转述即失败(吃瘪 5 个候选挂了 2 个);
- 题目照搬参考细节:comeuppance 的 must_see 写成"瘫坐地上,捂着腿,废品堆"这类字面描述;
- 同一雪豹反派在各候选里叫"太郎/大龙/残豹(大龙)/牛角大块头反派/A_boastful_villain",`shared_cast_hints` 为空;更严重的是 close_007 把主角阿宝标成"可充当反派"(理由自认随后被师父击退);close_011 与 close_017 同一镜头被两个节拍重复征用且角色名不一致;
- 失败以 AssertionError 为主(9/11),`issues` 为空列表,损伤不进问题清单。

## 2. 怎么查出来的
只读监控两份 report.json(glm_summary/v2_glm_summary 逐阶段核对调用数、失败、候选时间戳换算与 bounds);Opus 看静帧复核候选;Q3 任务书把根因归为 7 项(上述 4 项 + 跨节拍路由、画风字段、缓存/CLI)。

## 3. 根因
代码问题(校验过严 + 提示词缺陷 + 机制缺失):①校验把"可恢复的格级瑕疵"升级为"整页丢弃";②等价判定无归一/无编号;③must_see 混入参考的具体画面细节(应写功能);④没有角色锚点机制,身份靠模型每次即兴命名。

## 4. 解决方案(Q3 七项,实验脚本层)
① skim 逐格过滤:保留每张合法格,坏格记 `dropped_tiles`(带原因);空 beats 只是"无关";JSON 解析前先剥代码围栏、取最外层对象;仍不重发。② equivalents 编号 E1..En,回答 `equivalent_id`;纯文本按归一(去空白标点)或包含匹配;再不匹配记 `satisfies_unmatched`、保留 verdict 不废调用。③ QUESTIONS 提示词改为:must_see 只写可见的功能性条件(谁、动作类别、结果类别),参考细节进 equivalents;保留"喊叫"节拍 `mouth_open_needed=true` 断言。④ 角色锚点:引入该角色的节拍按序精看后,取置信度最高的 usable 候选定锚(存静帧+规范名);后续同角色节拍的 skim 排序优先该角色名格、close 提示带 `anchor_name` 且并发送锚点静帧,回答 `same_character_as_anchor`;false 则降 partial(reason `identity_mismatch`);报告记 `cast_anchors`。⑤ close 增答 `also_satisfies`,核验通过的片段路由给其他节拍(标 `routed_from`)。⑥ close 增答 `style: 3d|2d_stylized|mixed`,报告偏好 3d、标记 2d_stylized(KFP1 开场 34–125 秒 2D 不得与 3D 混剪)。⑦ 缓存与 CLI:`--cache-dir`、4 解码线程、`--skip-questions-from`(仅显式给文件才复用)、`--reuse-skim`(按签名)。
通用性:全部作用于"题目-格子-候选-锚点"的结构关系,零影片特判。

## 5. 为什么不用其他方案
- 失败调用重发:明确排除(不重发是配额安全设计,Q3 原文 "Still NO resend of any request")。
- 整页丢弃换"宁缺毋滥":8 页盲区直接造成漏检无法排除——逐格抢救的下界严格更优。
- 等价匹配只放宽为子串:仍敌不过转述;编号+归一+包含三层递进后仍保留 `satisfies_unmatched` 记录而非失败。
- 其余备选:未讨论。

## 6. 怎么验收(v2 重跑 + 监控报告)
v2(qfs_heihu_v2_20261029):总耗时 8:45.20(缓存复用,快约 14.8 倍);155 次调用、1 次失败(film2_sheet8 KeyError);两个旧缺口(villain_shouting、comeuppance)均找到 usable,且最优候选是同一支 17.5 秒片段(KFP1 2438–2455.5 秒,太隆:0–4 秒连击张嘴 + 15–17 秒被点穴击飞)——因果衔接在同片段内成立,不依赖跨片段身份判断;`cast_anchors` 1 条、`shared_cast_hints` [[1,"太隆"]]。遗留观察(监控报告第 5 节):候选全部出自 KFP1(二、三部是否真无素材无法排除)、close_001 路由判定自相矛盾(2D 段 usable/partial 文本矛盾)、8/18 次 close ok 却零候选(判 no 细节不落库)、same_character_as_anchor 仅 3/10 片段有值——这些问题随后在生产集成与 INC-20261029-10/12/13 中继续处理。

## 7. 量化数据
v1:161 次调用、11 次失败(skim 8、close 3)、7796.1 秒(shots 解码 7233.5 秒占 92.8%);三部 29476 帧、4878 关键帧、136 张宫格。v2:155 次调用、1 次失败、524.9 秒(shots 0.083 秒);节拍 4→3(questions 只生成 3 个);dropped_tiles 记录 8 张 sheet 共 68 条(v1 未记,无法对比)。共享缓存:`shared/data/shot_cache_kfp_v1`。

## 8. 经验与后续
- 结构化校验的失败粒度要最小化:格级瑕疵格级丢,页级 salvage 保底。
- "同一支片段答多题"(also_satisfies)与锚点机制是人物一致性的两根支柱。
- 真正的耗时瓶颈在本地解码而非模型调用(v1 模型调用墙钟不足 10 分钟)——缓存解码收益 14.8 倍。
