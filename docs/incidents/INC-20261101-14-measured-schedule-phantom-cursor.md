# INC-20261101-14 实测 TTS 调度按草稿顺序排：装不下的台词留下"幽灵占位"，连累时间更早的核心台词被丢

| 项 | 内容 |
|---|---|
| 发现日期 | 2026-11-01 |
| 影响范围 | 创作路线实测配音调度（story_context.schedule_draft）与精剪审核时间轴；错误成片（核心台词丢失）+ 浪费调用 |
| 状态 | 已修复（276a22f），真实重跑回执待补 |
| 相关提交 | 276a22f |
| 相关任务 | sb_20_赵德汉藏钱_11010204/evaluations/theme1/evaluations/film1/evaluations/film2（release 5d1fb42，--material-search film）；证据 .agent-team/out/s18/evidence/ |

## 1. 问题长什么样
- 赵德汉 film2 停在 fine_review `needs_material_reselection`，审核 `missing_core_meaning` 写明两句核心台词因 `tts_exceeds_window` 被丢：line_1（开场旁白）、line_2（"一分不敢花"）。
- story_context.json：audio_draft 顺序 line_1 窗口 2.0–6.08（seg_1+seg_2，narration，实测 4.4 s），line_2 窗口 0.0–2.0（seg_1，dialogue，实测 1.2 s）。
- dropped 记录：line_1 available 4.08 < 4.4；line_2 available 0、`blocked_until_s 6.4`——line_2 时间上更早、只是草稿序更晚，被失败的 line_1 挡死。
- 附带发现：fine review 给 line_5..line_7 的音频预览窗口仍用 55 s 粗剪时间轴，而精剪成片约 31 s。

## 2. 怎么查出来的
读 evidence 里的 plan_0.json、rough_audio/story_context.json、rough_handoff.json、skill_finecut/{plan_0,input,result}.json 与 calls/glm_024..026（fine plan/blind/review），定位到 `src/omni_story/library/story_context.py` 的 `schedule_draft`（旧实现约 :414）：按草稿顺序迭代 + 单调游标；装不下的行**仍被追加进 `scheduled` 并推进 `cursor = finish`**（幽灵占位）；非锚定行在自己的段跨度内不会前移。复核确认 line_1 本可从 1.2 s（line_2 之后，seg_1 起点为 0.0）起声、5.6 s 结束 ≤ 6.08。精剪时间轴问题定位到 `story_finecut.py`：修复前到 `finish()`（当时 :779）才重建精剪 context，审核先看到了粗剪窗口。

## 3. 根因
- 直接原因：调度顺序用草稿序而不是起声时间序；失败行占位并推进游标，使时间更早的后句被"幽灵区间"挡住。
- 根本原因：代码问题（流程分支/容错缺失）——调度器把"排不进"当成"排进了但很晚"处理，占位语义错误；同时非锚定行缺少段内前移能力。实测时长权威本身没错，错在排布算法。
- 次要原因：精剪审核复用了粗剪时间轴的窗口元数据，审核意见（预览窗口超窗）失真。

## 4. 解决方案
- `story_context.py:465`（276a22f）`schedule_draft` 重写：按各行预期起声时间稳定排序（同刻按草稿序），结果回写原草稿顺序；装不下的行不进占用时间轴、不推进游标；非锚定行溢出时可前移到"最晚可容纳起点"，下限为 max(前一放行尾, 首段起点)（revised 模式不早于自己首段起点），记录 `measured_slide_earlier(line, from, to)`；仅旁白（无 speaker、无锚点）溢出 ≤1.0 s 时可延伸进无人占用且在总时间轴内的空闲时间，记录 `narration_overrun(line, seconds)`；对白永不越段、锚定行维持严格行为。全部记录进 context（如 `schedule_adjustments`），幂等。
- 已录上下文续跑不被改写：`story_context.py:433` `_schedule_recorded_draft` 保留旧规则（与旧 `schedule_draft` 逐字相同），无 `measured_schedule` 记录请求的旧文件回放字节不变；`clean_chain.py`（当时 :637）本地读取门避免重跑时重复调度 + write_once 冲突。
- `story_finecut.py:677`（276a22f）`review()` 先用 `fine_context(plan, result)` 把窗口映射到精剪时间轴再送审核，粗剪预览元数据从继承白名单剔除；仅文本与音色完全匹配的回执才用于实测调度。
- 通用性：规则只依赖"行—段—实测时长"结构，与具体模板/电影无关；实测时长仍是权威，文本/音色/分段一律不动。

## 5. 为什么不用其他方案
- 放宽窗口或截短台词：违反"实测时长权威、不改内容"的用户规则，治标不治本。
- 丢弃失败行后整体重排多轮：复杂且仍可能把早期行挤出；按时间序单遍 + 前移已覆盖证据场景。
- 允许对白也越段延伸：会破坏"对白落在所挂段内"的既有校验（曾致 INC-20261030-02 整跑报废）。
- 精剪审核沿用粗剪窗口再换算：反馈不可操作，审核者看到的窗与实际成片不符。

## 6. 怎么验收
- 新增 `tests/test_measured_schedule.py` + 3 份 `s18_*.json` 证据夹具：证据场景回放后 line_2 0–1.2、line_1 前移至 1.68–6.08，零丢弃，后续行不变。规格示例给的 1.2–5.6 与"最晚可容纳起点"规则矛盾，实现采用后者（审核确认与用户裁定一致）。
- 单元覆盖：草稿序≠时间序、失败行不占位、旁白 ≤1 s 越界进空闲、对白/锚定不越、幂等、续跑文件字节不变；另做 6000 例随机 fuzz（乱序草稿、随机制造失败行、measured/revised 双模式），不变量全部成立。
- 既有测试仅改 `test_free_intervals_include_gaps_and_all_overlapping_lines` 一处：原断言依赖"草稿序=时间序"这一被修的错误行为。
- 三门：pytest 9 failed / 2274 passed / 104 skipped（FAILED 全在 baseline_2879e5c，24 errors 为沙箱禁端口绑定）；replay ok 143 / regression 6 / fixed 21 / still_failing 33，基线 ok/fixed 降级 0；fault E1/R1/M1_crash/M2_json/M2_crash 全 0。
- 真实重跑：未记录（待后续任务回执补）。

## 7. 量化数据
- 修复前：证据任务 7 句台词丢 2 句核心（line_1、line_2，占核心台词 2/2），整任务停在 fine_review。
- 修复后：同一证据 0 句丢弃；line_2 0–1.2 s、line_1 1.68–6.08 s。
- 精剪时间轴失真：审核窗口基于 55 s 粗剪，实际精剪约 31 s；修复后审核即见精剪时间轴。
- 本任务浪费调用数：未记录（evidence 未给出总调用数）。

## 8. 经验与后续
- 调度/排布类代码要区分"放置失败"与"放置很晚"：失败行占位是这类"后句被连累"问题的通用形态。
- 任何给审核者的预览元数据必须与被审对象同一时间轴，否则审核意见不可操作。
- 遗留：审核阶段新增的 `fine_context` 构建与 finish 阶段同源，理论上有相同的校验抛错面（失败模式与旧 finish 相同，不新增风险）；无 handoff 的 fallback 读取靠 video/reference/音频回执 sha 兜底，无整体 sha 校验（审核判非阻塞）。
