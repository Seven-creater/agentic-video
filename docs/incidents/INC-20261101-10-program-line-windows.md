# INC-20261101-10 对白密集模板规划阶段台词时间窗反复算错，修复次数耗尽停机；改为程序排时间窗

| 项 | 内容 |
|---|---|
| 发现日期 | 2026-11-01 |
| 影响范围 | 创作路线 plan_0（粗剪规划）与精剪规划的 audio_draft 时间窗；对白密集模板（雪山救狐狸 16 句 / 38 秒）；致命停机 |
| 状态 | 已修复（f936f78），雪山 fix6 真实重跑验证中 |
| 相关提交 | f936f78 |
| 相关任务 | sb_02_雪山救狐狸07_11010245/…/fix5（失败），…/fix5/evaluations/fix6（验证） |

## 1. 问题长什么样
fix5（release 0ecdf57）停在 `model_protocol_repair_exhausted:plan_0`，三次规划各错一处：
- attempt 0 `plan:audio_line_window_outside_segments`：ad_09 手填 24.0–25.6 秒，所挂 seg_6 只到 24.0 秒；
- attempt 1 `audio_line_window_below_minimum`：ad_01 手填 0.2–2.8 秒（2.6 < 3.24 秒），而 seg_1 有 0.0–6.0 秒；
- attempt 2 `visual_library:range_outside_content`：模型把成片时间轴（0、4.6、6.2……）填进 source_in_s/out_s。

## 2. 怎么查出来的
用 `parse_model_json` 解析三份 protocol_failure.json 的 model_text，按 `segment_output_timeline` 算每组（相同 segment_ids）的片段跨度，与 `min_line_window_s` 之和比较（脚本见会话 scratchpad lw.py）。
- 三份回复的 16/16/15 句全部手填了 start_s/end_s。
- 除 attempt 0 的 seg_6（需求 5.04 秒，跨度 5.0 秒）外，所有组的需求都不超过跨度，失败全来自手算。
- GPT 回放还发现：attempt 1 排窗后的下一个错误是 `plan:storyboard_duration_out_of_range`，45.5 秒对上限 45.4992 秒，只超 0.0008 秒。attempt 2 正是为了压缩时长才混淆了两套时间轴（推断，依据是 attempt 2 总长恰好 37.9 秒，等于参考时长）。

## 3. 根因
- 直接原因：提示词允许并鼓励模型在 audio_draft 写成片时间轴上的显式 start_s/end_s，还要它自己按 EDL、帧数、锚点重算。对白一密，模型的心算就反复出错，修复反馈又要求它做更多时间轴算术。
- 根本原因：时间窗是可以确定计算的机械字段，却交给了模型。默认分配按字数比例（`line_windows.draft_line_windows`），不按每句的最低需求，可行的组也可能被拒。总时长区间的亚秒级超出同样是机械误差，却没有程序修正。
- 类型：提示词与校验设计问题（把机械字段交给模型），加上容错缺失。

## 4. 解决方案
- `src/omni_story/library/line_windows.py`：组内分配改为按需求分配，每句先拿到 `required_line_window_s`（有实测 TTS 回执就用回执，否则用下限），余量按字数分摊，末句取剩余。组不可行时退回原来的比例分配，让校验照常报出缺口。改为组级违规，给出 span、demand、deficit、可延长秒数和需要删减的字数；提示词、校验、反馈和 story_context 共用这一个解析器。
- `src/omni_story/library/plan_normalisation.py`：
  - `normalise_plan` 在粗剪和精剪两条路径都移除模型手填的 start_s/end_s，逐句记录 `program_line_window`；
  - 只在锚点单独造成不可行时才删锚点（`drop_start_anchor_for_line_window`）；
  - 缺口 ≤1.0 秒时，在同一条 usable_range 内按帧延长片段（`extend_segment_for_line_window`）；
  - 总时长超出或不足分镜区间 ≤1.0 秒时，从末段向前按帧裁剪或延长（`trim_for_storyboard_duration` / `extend_for_storyboard_duration`），整次试算全部满足才写回。
- 提示词（`line_window_prompt`）不再让模型写时间，只告诉它每组的跨度、需求和余量。
- 程序不改 text、mode、speaker、segment_ids，也不改素材选择。规则只依赖 EDL 与台词长度，不依赖具体模板。

## 5. 为什么不用其他方案
- 继续加强反馈、让模型重算：fix5 已经证明反馈越详细，模型越容易混淆两套时间轴，治标不治本。
- 放宽最小时间窗：违反"只拦截不可能窗口"的既有下限设计，会让配音被截断。
- 缺口大于 1 秒也自动延长或裁剪：这已经是选画面的创作决定，应交给模型（缩短台词或改挂片段）。
- 合并或缩短台词：改的是创作内容，程序不该做。

## 6. 怎么验收
- 新增 `tests/test_program_line_windows.py`，用三份真实回复和证据上下文回放：
  - attempt 1 排窗并裁剪 1 帧（seg_10 536.5→536.4667）后，完整通过规划校验；
  - attempt 0 剩下组级缺口 0.04 秒（usable_range 已用满），反馈提示删 1 个字；
  - attempt 2 的原片时间越界照常报错，没有被掩盖。
- 全量 pytest：9 failed，全部属于 baseline_2879e5c；历史回放 203 行，ok/fixed 降级 0；故障检查 E1、R1、M1_crash、M2_json、M2_crash 均为 0。Opus 与 GLM 各自复跑一遍。
- GLM 交叉审结论 pass，无缺陷。
- 已有断言改动 6 处，理由逐条写在 `.agent-team/out/s13/S13_fix.md`。
- 真实重跑：雪山 fix6（release f936f78），结果待补。

## 7. 量化数据
- fix5：plan_0 3 次调用全部失败，失败原因 100% 来自手填时间窗或时长误差。
- 回放三份回复：程序排窗后 1/3 直接通过（原来 0/3）。
- 时长误差 0.0008 秒，靠裁剪 1 帧（0.033 秒）消除。
- 真实任务的成功率待 fix6 补充；以后可统计 `program_line_window` 记录数和 plan 修复次数来衡量。

## 8. 经验与后续
- 能确定计算的字段（时间、帧、区间换算）一律由程序算，模型只做创作选择。
- 遗留风险：组缺口大于 1 秒、或 usable_range 已用满时，仍要靠模型缩短台词或改挂片段。
- attempt 2 这类"成片时间填进原片时间"的错误，程序无法确定地反推，仍会被拒。
