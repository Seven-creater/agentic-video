# INC-20261101-20 原片概览 coverage_s 回显字段：模型写"抽样帧范围"而非请求页，三页九次全拒致整任务停机

| 项 | 内容 |
|---|---|
| 发现日期 | 2026-11-01 |
| 影响范围 | 原片检索（film 模式，S16 起大量使用，762d7f2）概览页校验与旧录制重载路径；致命停机 + 浪费调用 |
| 状态 | 已修复（738193d）；遗留下游续跑绑定风险未修（见第 8 节） |
| 相关提交 | 738193d（修复）；762d7f2（film 模式功能引入，非事故） |
| 相关任务 | sb_01_不烧心_11010232/…/theme1/evaluations/picfirst1（release 97dfa88，--material-search film --creative-style picture_first，9 次调用后停机）；历史同症状：赵德汉 film1（3 页中 2 页修复、其中 1 页到 repair2）、雪山 film1（2 页到 repair2）、不烧心 film1（1 页修复） |

## 1. 问题长什么样
- picfirst1 的 film 运行 9 次调用后停机：`no_valid_coarse_evidence_available_for_search`。三个概览页（overview_978d5360 / _53664193 / _24243517，各 + _repair + _repair2）9 次回复**全部**失败 `coarse_does_not_match_requested_page`。
- 典型样本：请求 coverage `[34.0, 4991.0]`（正文内容范围，提示词明说"coverage_s必须原样照抄[34.0, 4991.0]"），回复 `coverage_s: [171.7, 4853.3]`——恰好是首/末抽样帧时刻；`source_id` 正确、事件都落在抽样时刻上。
- 同样的提示词字节在更早的 film 运行里就反复需要修复轮（赵德汉 film1、雪山 film1、不烧心 film1），是稳定的回显（echo）行为而非偶发。

## 2. 怎么查出来的
对比请求与回复：source_id 匹配、事件时刻合法、唯独 coverage_s 是"模型看到的抽样帧范围"而非"请求页范围"，而校验要求逐字等于请求页 → 判 `coarse_does_not_match_requested_page`。再翻此前 film 运行的修复历史（上表三任务），确认同一形状反复出现——定位为回显字段的语义分歧，而非某一页的偶发噪声。概览逐页路径本身是既有 library 检索流程的复用，film 模式（S16，feature，commit 762d7f2）开始高频使用后该缺陷被放大为整任务停机。

## 3. 根因
- 直接原因：模型把"我看到的范围"（首末抽样帧时刻）写进 coverage_s，而不是照抄请求页；校验零容差逐字比对 → 拒绝。
- 根本原因：代码问题（提示词与校验不一致/容错缺失）——对**回显字段**，提示词要求"照抄"，校验要求"逐字相等"，但都没有为"合法等价写法"（页内抽样范围）提供归一化；模型自然写观测值，于是每次都要烧 2 次修复轮，3 页 × 3 次尝试全废后整任务停机。
- 不是数据问题：回复内容本身（source_id、事件时刻）完全可用，废掉的只是形状。

## 4. 解决方案
- 新增 `contracts.normalize_coarse_coverage`（contracts.py:868 于 738193d）：当 `source_id` 匹配且回复的 `coverage_s` 是落在请求 `[start, end]`（±1 s 容差）内的二元数值列表（如抽样帧范围）时，校验前将其改写为请求范围，并记录归一化 `coarse_coverage_echo(requested, written)`。
- 接线两处（pipeline.py:1499、:1552 于 738193d）：实时校验路径（`_adaptive_coarse` → observe().validate）与旧录制 coarse 重载路径（原 `coarse_reply_does_not_match_original_media` 检查处）都先归一化——录制回复重载时同样归一化，录制文件不改写，回放逐字节不变。
- 仍拒绝：source_id 不同、coverage 越出请求页、事件不在抽样时刻。幂等（written == requested 时跳过）。
- 通用性：规则只看"页内二元范围"这一形状，与影片/页码/模板无关；对历史需要修复轮的各 film 任务同样成立。

## 5. 为什么不用其他方案
- 放宽校验为"区间相交即可"：会放过真正看错页/看错片的回复（source 混淆），错误成片风险。
- 只改提示词更强调"照抄"：历史三任务已证明提示词强调无效，模型自然写观测值。
- 把 coverage_s 从回复里删掉、由程序填充：改动面大（回放、下游 coarse_index 消费方都要动），且保留模型自报范围可作交叉证据。
- 改写已录回复文件：违反录制不可改纪律；改为重载时同样归一化即可保持回放一致。

## 6. 怎么验收
- 新增回归测试 `tests/test_coarse_coverage_echo.py` 23 项全过：帧范围回显 → 接受、coverage 置为请求页、留痕；越界/错 source → 拒；事件偏离抽样时刻 → 拒；已录归一化回复重载 → 结果一致。
- 三门：pytest 2418 通过、8 失败均在 baseline_2879e5c；replay 164 个基线 ok/fixed 项零降级；fault E1/R1/M1_crash/M2_json/M2_crash 全 0。变更 contracts.py +15、pipeline.py +3、测试 +164，无 uv.lock。
- 真实重跑：未记录（修复后 film 概览页回执待补）。
- 审查（verdict fail，1 条 CONFIRMED）：核心逻辑正确（枚举的拒绝项、幂等、录制不改写、重放一致均核实），但发现一个由本改动引入的下游破坏，未随 738193d 修复——见第 8 节。

## 7. 量化数据
- 修复前：证据任务 3 个概览页 × 各 3 次尝试 = 9/9 全拒，9 次调用零产出停机；历史 film 运行中同症状修复轮：赵德汉 film1 需 2 页修复（1 页到 repair2）、雪山 film1 两页到 repair2、不烧心 film1 一页修复。
- 修复后：同形状回复 1 次即接受（23 项测试断言），每页省 2 次修复调用；按 3 页计每任务省 6 次。
- 回放/故障：基线 ok/fixed 降级 0；fault 五项 0。

## 8. 经验与后续
- **遗留风险（CONFIRMED，未修）**：归一化后的行（coverage_s=请求页 + coarse_coverage_echo 记录）写入 coarse_index.json，而 `calls/<id>/parsed.json` 仍存原始回显回复；`finecut_continuation.py:271` 与 `goal_feedback_continuation.py:423`（738193d）对二者做**字节级相等**绑定——任何接受了回显页的运行，一旦进入 goal/finecut 续跑轮次会硬停 `finecut_continuation:coarse_model_binding_missing`。改动前该绑定成立正是因为校验器从不改写值。修复方向（两处绑定改比较归一化视图，参照 `_matches_received_fine`；或 coarse_index 存原始行、使用点归一化）超出当时允许文件清单，738193d 未包含，当前 HEAD 仍在。**需要用户决定**：扩权另开任务修复，或在续跑消费方任务中顺带处理。
- 回显字段（要求照抄的字段）要在提示词、校验之外再备一层形状归一化：模型对"照抄"的服从率是经验值，零容差 + 无归一化 = 每页必烧修复轮。
- 新检索模式（S16 film）上线后，旧路径里"靠修复轮兜住的低频缺陷"会因调用量上升变成整任务停机；模式落地时应顺带清点历史修复轮里反复出现的失败形状。
