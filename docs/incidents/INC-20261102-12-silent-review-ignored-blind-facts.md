# INC-20261102-12 静音审核没对照盲看事实：盲看已写"卷轴是空白的"，"我接，回去就一件件改"仍判合格，直到精剪审核才拦下

| 项 | 内容 |
|---|---|
| 发现日期 | 2026-11-03 |
| 影响范围 | picture_aware 默认路线的粗剪内容硬门（INC-20261102-08）；画面与台词矛盾漏到精剪，任务停机不交付 |
| 状态 | 已修复（8a7b1ee；真实任务验证待做） |
| 相关提交 | 8a7b1ee |
| 相关任务 | prod_27_发现三个问题_11021100/evaluations/rule2/evaluations/len1/evaluations/len2 及其 evaluations/fine1（release eb3cc22） |

## 1. 问题长什么样
fine1 `run_outcome.json`：`outcome: stopped, stage: fine_review, error: needs_material_reselection`。精剪审核：`missing_core_meaning: ["整改承诺与'问题解决'的收尾被实际可见的空白卷轴和学员失望反应直接否定…"]`，`next_action: reselect_material`。而 len2 的粗剪静音审核两轮 `content_acceptable: true, must_fix: []`（得分 93.33、93.75），其中 ad_13「……我接。回去就一件件改。」（103–113 s）判 `ok`，证据只引用素材库窗口描述"B双手接过卷轴"。同一次审核的盲看已写：91–103 s"大熊猫打开卷轴，发现里面是空白的，表情从期待转为困惑"、104–118 s"大熊猫拿着空白卷轴，困惑地看着周围"。

## 2. 怎么查出来的
读 fine1 `skill_finecut/result.json` 与精剪审核回复；读 len2 `silent_review.json` 的 blind_reading.evidence 与 verdict.lines；读 `src/omni_story/library/prompts.py` `silent_review_prompt`：盲看整体放在 context.blind_reading，每句只带 segment_ids，段落证据是素材库窗口观察（原片窗口），模型逐句判定时没有把同一时段的盲看事实拿来对照。同一参考在 rule2 第一次运行时这处矛盾曾被拦下，说明判定依赖模型是否自发对照，不稳定。

## 3. 根因
提示词结构问题：实际粗剪的盲看事实与逐句判定分离，判定证据默认来自原片窗口描述，剪辑后画面的实际结果（空白卷轴）被忽略。属于提示与上下文组织缺陷（代码问题），与模板、电影无关。

## 4. 解决方案
`prompts.silent_review_prompt`（content_check 新提示）：程序按时间把盲看 evidence 中与该句 [start_s, end_s] 重叠（或点时间落在区间内）的事实挂到该句的 `blind_facts`；提示要求先对照 blind_facts：台词所说的行动或结果与之相反（如台词说接受、完成、解决、成功，盲看为空白、拒绝、失败、困惑或相反结果）判 contradiction，判 ok 须说明为何不冲突，已看素材描述不能替代实际粗剪所见。旧提示（legacy、content_check=False）字节不变；已有审核记录的轮次按旧格式重建提示（`blind_facts=False`），新审核记录 `line_blind_facts: true`；`prompt_bytes` 与该标记取自实际作答的请求（`_resume_silent_reply` 的 used_prompt / `_settled_request_prompt`），复用旧回复时如实记录旧字节数。

## 5. 为什么不用其他方案
- 程序用关键词自动判矛盾（如"空白"对"整改"）：只对个别词有效，无法通用。
- 精剪发现矛盾后自动回到粗剪重做：成本高，且应在粗剪内容硬门就拦住。
- 再加一轮独立审核：多花调用，且同样依赖模型自发对照。

## 6. 怎么验收
- 新增 `tests/test_silent_review_blind_facts.py`（5 项，用本次真实盲看事实）：ad_13 带上 104–118 s 空白卷轴事实、ad_12 带上 91–103 s；旧提示不含 blind_facts；缺失或无时间的证据不报错；`blind_facts=False` 与修复前提示逐字节一致；读取实际作答的请求提示。
- GPT 交叉审核三轮：第 1 轮 fail（旧记录在原目录重启时 prompt_bytes 2438→2917，报 `silent_review_recorded_check_changed`）；第 2 轮 fail（旧回复已收到但记录未落盘时被误记为新格式）；第 3 轮 pass，两类复现均保留实际 2438 字节、无新增调用。
- 全量 pytest 8 failed 全在基线内（2780 passed）；回放 203 行与基线逐行一致；故障演练 E1/R1/M1_crash/M2_json/M2_crash 均为 0。
- 真实任务验证：待部署后从头重跑（续跑会复用同一渲染的旧审核，验证不到）。

## 7. 量化数据
- len2：静音审核 2 轮 0 处矛盾；精剪审核 2 处核心矛盾（100–106 s、107–123 s）；fine1 共 12 次调用后停机，无成片。

## 8. 经验与后续
- 精剪审核作为最后一道防线起了作用（没交付讲错内容的片子），但应在粗剪内容硬门就修掉。
- 逐句判定要把"实际成片所见"放在每句旁边，不能只给原片窗口描述。
