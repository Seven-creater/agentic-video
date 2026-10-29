# INC-20261028-01 黑虎阿福成片混入功夫熊猫 3 片尾演职员表(Jack Black 名字卡)

| 项 | 内容 |
|---|---|
| 发现日期 | 2026-10-27(任务 u3_heihu02),修复落提交 2026-10-28 |
| 影响范围 | 素材库检索→规划→精剪全链;错误成片(片尾字卡入片) |
| 状态 | 已修复(c296937) |
| 相关提交 | c296937 |
| 相关任务 | 服务器任务 `u3_heihu02`(release 2c288ff);任务书 `.agent-team/tasks/V1_credits_tts.prompt.md`;报告 `wt/pfix/reports/credits_and_tts_resend_v1.md`;审查 `out/V2_review.*` |

## 1. 问题长什么样
成片 22–29 秒出现 "Jack Black"、"Angelina Jolie" 等演员名字卡(墨迹风格背景)。来源:粗剪规划选了 KFP3 源 `src_cf2fec1024243517` 的 5006–5010 与 5021–5024 秒(窗口 vl_375f00a8c821d094,4980–5040)——该区间是片尾:5002 秒"剧终/THE END"之后即演员字卡。最终画面审查抓到(`no_credits: false`,needs_visual_revision)。

## 2. 怎么查出来的
最终视觉审查报出 credits;反查规划段的源时间落在 5000+ 秒。Opus 对三部电影逐帧核实正片区间(以源时间计):KFP1(片长 5529.5 秒)正片 [34.0, 4991.0](0–33 为梦工厂 logo+片名卡,4992+ 白闪、THE END、credits);KFP2(5423.9 秒)[21.0, 4869.0];KFP3(5694.7 秒)[66.0, 4999.0]。根因确认:素材库没有"正片区间"的结构概念,检索、规划、校验接受源内任意区间。

## 3. 根因
数据问题(素材库缺正片区间元数据)叠加代码问题(校验漏洞):任何窗口都可能被提供、被选、被通过;唯一拦住它的是最后一道人工视觉审查。

## 4. 解决方案
- 新增 `<visual_library_dir>/content_ranges.json`(schema `content_ranges_v1`,按源 sha256 记 content_start_s/end_s、excluded 区间、verified_by);仓库内置 `data/visual_library/three_films_v2/content_ranges.json`(上述三组值)。
- 加载器严格校验(有限数、0≤start<end≤片长、每个库内源必须存在);启动(`--creative-audio --visual-library`)时文件缺失/无效/缺源,在任何模型或付费调用之前停止:`LibraryStopped('visual_library:content_ranges_missing_or_invalid:<detail>')`,进入 run_outcome.json。
- 加载的区间连同文件 sha256 记入任务 state/input,resume 与 replay 用记录值;历史任务无快照时保持旧行为(不破坏回放)。
- 单一 helper `content_range_for(source)` 与同一检查用于所有选源处:目录/检索提供(区间外的窗口不再提供、跨界窗口给出可用子区间)、检索回复校验、粗剪规划校验、精剪 observe/plan/revise 与 story_finecut(区分源时间域与已渲染父片的输出时间域)、各阶段提示词(逐源写明正片区间、禁止 logo/片名卡/credits)。violation 精确到 `$.segments[i].source_in_s`,expected 为 `[content_start_s, content_end_s]`。
- 最终视觉审查保留为兜底,不作第一道防线。
- 新测试 `tests/test_credits_and_tts_resend.py`。
通用性:区间是逐源数据,新片只要核实一次即纳入;检查与模板无关。

## 5. 为什么不用其他方案
- 只靠最终视觉审查兜底:已证明是"事后发现",成片已错;审查保留但不依赖。
- 按窗口打黑名单:粒度错——窗口由预标注生成,正片区间才是片源的客观属性;黑名单无法覆盖跨界窗口的部分可用性。
- 缺文件时降级继续跑:会在无保护状态下再次选出 credits;选择调用前硬停(与"素材不足就停"的既定方向一致)。
- 其他备选:未讨论。

## 6. 怎么验收(credits_and_tts_resend_v1.md 实际输出)
- 新增 44 参数化用例:`git archive 2c288ff` 基线 44 failed → 当前 44 passed;附加定向检查 160 passed。
- 全量:`9 failed, 1331 passed, 103 skipped, 24 errors`,FAILED 与基线完全相同。
- Replay:TOTAL 149 / 0 / 22 / 32 / 0,相对 2c288ff 变化行 0(历史任务无区间快照,不加限制)。
- Fault check:E1=R1=M1_crash=M2_json=M2_crash=high=0,M2 系列与基线一致。
- 用例覆盖生产真实数字:粗剪规划校验拒绝 u3_heihu02 的 5006–5010 段。

## 7. 量化数据
修复前:1 个真实任务(u3_heihu02)成片 22–29 秒混入 credits;修复后该区间在提供/检索/规划/精剪四处均被拒(测试断言)。正片区间数据:KFP1 [34,4991]、KFP2 [21,4869]、KFP3 [66,4999]。

## 8. 经验与后续
- 片源的结构性事实(正片/片头/logo)必须进数据文件,不能依赖模型避开;逐帧核实一次,全链复用。
- 该模式后来复用于 2D 段排除(INC-20261029-10 的 stylized_ranges)与人物白名单(cast.json)。
