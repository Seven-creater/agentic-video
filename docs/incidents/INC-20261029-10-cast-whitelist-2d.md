# INC-20261029-10 黑虎阿福被选进角色表当反派,成片混入 2D 梦境画面

| 项 | 内容 |
|---|---|
| 发现日期 | 2026-10-29 |
| 影响范围 | question_first 选角与画风过滤;错误成片(参考视频人物入片、2D/3D 混剪穿帮)→ 最终画面审核停机(needs_visual_revision) |
| 状态 | 已修复(faa499b) |
| 相关提交 | faa499b |
| 相关任务 | 报告 `wt/pfix/reports/c1_cast_style.md`;审查 `out/C2_review.md`(verdict pass) |

## 1. 问题长什么样
黑虎阿福/Hak Foo(参考视频自身的人物)被 questions 选进 casting 当反派;最终画面审核判 `needs_visual_revision`(framing_ok=false、edit_coherence_ok=false)。Opus 看帧确认:成片 2–9 秒与 17.5 秒是 KFP1 的 2D 水墨梦境画面,10–16 秒才是 3D 吊桥——2D/3D 混剪造成"画风断裂"(尼克狐审核此前也提过同类问题)。

## 2. 怎么查出来的
最终画面审核结论 + Opus 逐帧目检(2–9 秒/17.5 秒 2D、10–16 秒 3D 吊桥);反查 casting 来源确认选了参考自身人物;反查候选窗口确认 2D 段未被过滤。

## 3. 根因
代码问题(校验缺失,两处):①选角没有白名单边界,模型可以选参考视频自身的人物或任意名字;②画风没有硬约束,2D 梦境段(已知区间)与 3D 正片可以同时成为候选(v2 实验已暴露:close_001 的 2D_stylized 段在 villain_shouting 下被判 usable)。

## 4. 解决方案(faa499b,c1 报告)
- 新增 `data/visual_library/three_films_v2/cast.json`:18 个真实角色、规范中文名、中英文别名、影片归属;太郎/大龙/残豹/Tai Lung 统一为"太郎"。按原片 SHA 绑定 2D 排除区间,严格采用用户给定的 film 1 [34,165]、film 2 [21,120],记 `verified_by: "Opus frame samples 2026-10-29"`,不推测补充中段区间。
- 新增 `src/omni_story/library/film_cast.py`:按 content_ranges 方式加载校验;名单与文件 SHA 固定进 `input_lock.configuration.film_cast`,resume 只读锁定副本;文件缺失/无库根/旧任务无锁定名单时明确停止(`visual_library:cast_missing_or_invalid:...`);library 模式不加载、不加风格过滤。
- questions 提示词展示锁定白名单并明确禁止选用参考视频自身人物;`target_style=3d` 写入配置与 questions;无效选角(如黑虎阿福/火云邪神)逐条删除记 `dropped_casting`,角色可保留 `[]`;全部无效才抛 `qfs_questions:no_valid_film_casting`。
- 名字、别名、影片归属用白名单值归一(heihu fixture 中残豹→太郎且 films 由 [3] 纠正为 [1]);close 的 `who` 与锚点名同表归一。
- 画风硬规则:skim 选区跳过已知 2D 区间;close 片段 ±2 秒扩边后仍重叠 2D 区间即不送检;最终窗口转换再查(含 2 秒扩边);硬拒 `2d_stylized`/`mixed`;过滤先于 usable/partial 回退选择(2D usable 不会遮住合法 3D partial);过滤后无合法窗口的 must-see 节拍写 `question_first_gaps` 并以 `question_first:material_gap` 停止(该停止门自身的问题见 INC-20261029-12)。
通用性:白名单与区间是逐片数据+结构过滤,新片补数据即适用;无角色/台词特判。

## 5. 为什么不用其他方案
- 只在提示词里劝阻(不选参考人物/别混 2D):软约束已被真实输出证伪;需要硬边界。
- 事后靠最终画面审核抓画风断裂:那就是本次的发现路径——审核只能拦下,拦下即停机重来,成本最高。
- 对 2D 排除区间做推测补全(中段区间):资料明确"没有推测补充中段二维区间",只用逐帧核实过的值。

## 6. 怎么验收(c1 报告实际输出)
- 新增行为测试在 `git archive a5d692d` 隔离源码上:`16 failed, 1 passed` → 当前 `17 passed`(涵盖真实黑虎/Hak Foo 回复删除、太郎归一、全无效报错、close 别名归一、两种非 3D 拒绝、合法 3D partial 回退、两部 2D 区间、文件锁定与 resume、文件缺失、pipeline 缺口停止)。
- 全量:`9 failed, 1559 passed, 104 skipped, 24 errors`,FAILED 与基线完全相同;Replay:203 行与 a5d692d 完全一致(6 条已知 `audio_line_window_below_minimum` 回归不变);Fault:E1/R1/M1_crash/M2_json/M2_crash/high 全 0。
- C2 审查 pass:白名单 NFKC 归一精确匹配、18 角色归一键无碰撞、stylized_ranges 与 content_ranges 对应、所有窗口产生路径均被风格检查覆盖无旁路、时域无混用、篡改 cast.json 后 resume 稳定。

## 7. 量化数据
白名单 18 个角色;2D 排除区间 2 段(KFP1 [34,165]、KFP2 [21,120]);heihu 真实回复 2 个无效选角被删(黑虎阿福、火云邪神)。修复后黑虎阿福参考的成片:17.9 秒、11 句、太郎全程一致、纯 3D(事实清单,见 INC-20261029-11)。

## 8. 经验与后续
- 身份类约束(谁能被选)与画风类约束(3D only)都必须是数据驱动的硬边界;提示词只负责引导。
- 锁定进 input_lock 保证 resume 不被外部文件改动影响——与 content_ranges 同一模式。
