# INC-20261101-15 最终状态自相矛盾：修订后真实成片全过仍报 stopped、final_video 为空，且停止分支回退 77 s 粗剪

| 项 | 内容 |
|---|---|
| 发现日期 | 2026-11-01 |
| 影响范围 | 创作路线最终状态汇总（clean_chain 顶层）与精剪结算；错误成片（明明已交付却不报交付/交付错文件）+ 浪费审核调用 |
| 状态 | 已修复（32af5fb），真实重跑回执待补 |
| 相关提交 | 32af5fb |
| 相关任务 | sb_01_不烧心_11010232/…/theme1/evaluations/film1/evaluations/film2（release 5d1fb42，--material-search film）；证据 .agent-team/out/s19/evidence/ |

## 1. 问题长什么样
- run_outcome：`outcome: stopped`、`status/error: needs_audio_revision`、stage av_review、`final_video: null`（raise 于 release 5d1fb42 的 clean_chain.py:862）。
- 但同一份 chain_result：finecut.creative_audio `status: delivered`、joint_quality_gate true、technical_delivery_verified true、content_delivery_ready true、dropped_lines []、audio_measurement passed；对**实际混音成片**的 AV review：theme_alignment `matches`、picture_audio_coherence `coherent`、missing_core_meaning []、repair_at `none`；final_visual_review `pass`；final_subtitled.mp4 已存在（77 s）。
- 即：后段（fine revise → 创作配音 → 实际 AV 审核 → 最终视觉审核）全部通过，顶层却沿用修订前的旧精剪结论报停且不给成片。
- 附带：两次 fine review 协议失败 `story_finecut:picture_audio_coherence_invalid`。

## 2. 怎么查出来的
读 evidence 的 chain_result.json、run_outcome.json、result.json、skill_finecut/{result,plan_0}.json、creative_audio/{audio_result,review_v0,audio_progress}.json、final_visual_review/ 与 calls/glm_020..023，逆着状态推导链查：`StoryFinecut.run()` revise 后执行指纹未变时保留旧 review 进 `finish()`（当时 story_finecut.py:1126–1146）→ `fine_audio_joint()` 要求旧 `target.next_action == deliver`（当时 :58–64），旧 revise 使 gate 为 false → `finish()` 产出 needs_audio_revision（当时 :844–847）→ 顶层直接继承（当时 clean_chain.py:763–781、812–814），最终视觉审核通过也不更新 → `run_outcome.py:81-87` 把 false gate 映射为 stopped 并置空 final_video。又核对了旧停止分支的媒体回退 `fine.get('candidate_video', parent['path'])`：缺 candidate_video 时回退 **77 s 粗剪**，而 AV 审核针对的是 **45.5 s 精剪**。

## 3. 根因
- 直接原因：顶层最终状态取自修订前的精剪裁决（stale），没有随"实际交付媒体"的最新裁决（实际 AV review、音频交付门、最终视觉审核）重导。
- 根本原因：代码问题（流程分支）——同一任务里存在两代裁决（修订前 fine review、修订后实际成片审核），没有任何一处规定"以最新、以实际媒体为准"，旧裁决静默胜出。按代码自身设计，fine review `partial` 允许交付后再审真实混音成片，因此后续实际媒体裁决才是权威。
- 次要原因：停止分支的媒体回退链缺精剪输出，会把错误时长/版本的文件当最终片。
- 两次协议失败：模型写 `picture_audio_coherence="coherent_with_issues：…"`，校验要求精确枚举 `coherent`/`contradictory`/`unverifiable`；提示合同（reference_requirements.py:80）已列明枚举，属模型违规而非提示/校验矛盾，未改行为、未改写历史回复。

## 4. 解决方案
- 新增统一交付检查 `visual_audio.py:43`（32af5fb）`delivered_audio_ready`：一次性核对 status=delivered、JQG、content_delivery_ready、technical_delivery_verified、audio_measurement.passed、无 partial 标记、review 为 dict 且 content_ready（matches/coherent/无矛盾/repair_at=none）。
- `story_finecut.py:60`（32af5fb）`fine_audio_joint` 与 `clean_chain.py:772-783` 顶层汇总均改用该结果；最终视觉审核失败仍在 clean_chain.py:829 否决交付。该停仍停：任何一门失败落入 needs_audio_revision / needs_material_reselection。
- 媒体回退链改为 `candidate_video → final_video → audio.final_video → parent`（clean_chain.py:783，32af5fb）：停止的运行保留精剪输出，字幕继续生成至 final_subtitled.mp4，不再退回粗剪。
- 通用性：判据只看"实际交付媒体上的最新裁决 + 交付门"，与模板无关；录制任务 resume 时 chain_result.json 原样返回，不回写。

## 5. 为什么不用其他方案
- 维持"fine review partial 一票否决"：与代码既有设计矛盾（partial 后明确安排了修订与实际成片审核），会把可交付任务全部判停。
- 在 run_outcome 处放宽映射：只治报告不治根，顶层状态与门控仍不一致。
- 改写两次协议失败的历史回复：违反"录制不可改"纪律，且违规在模型侧。

## 6. 怎么验收
- 新增 `tests/test_final_status.py` 17 例：证据案例 → `chain.status=completed`、`run_outcome.outcome=delivered`、final_video=final_subtitled.mp4；12 个负向用例（av_fails/av_unverifiable/av_revision/av_selection/av_theme/visual_fails/audio_partial/audio_partial_flag/joint_failed/not_ready/not_decoded/measurement_failed）全部断言 stopped 且 final_video=None。
- 证据外分支行为变化均有参数化用例固化：粗剪 partial（非 deferred）+ 实际成片全过 → 交付（旧：正是本次矛盾形态）；fine partial + AV 过 → 停（旧：completed_partial 仍报 final_video，违反"JQG false/partial 不得交付"）。
- 三门：pytest 9 failed / 2291 passed / 104 skipped（FAILED 全在基线；24 errors 为沙箱禁端口）；replay ok 143 / regression 6 / fixed 21 / still_failing 33，基线 ok/fixed 降级 0；fault 五项全 0；录制 JSON 字节不变 2789 个文件。
- 真实重跑：未记录（待补）。

## 7. 量化数据
- 证据任务：实际精剪 45.5 s、被回退的粗剪 77 s、已存在成片 77 s（final_subtitled.mp4）——修复前顶层报 stopped 且 final_video=null，修复后同一证据判 delivered。
- 行为变化（审核者复核）：粗剪 partial+实际全过 → 由 stopped 变 delivered；fine partial+AV 过 → 由"仍报 final_video"变 stopped。
- 测试：+17 例（12 负向）。花费对比未测量（修复对象是状态汇总，不改变调用量）。

## 8. 经验与后续
- 多轮裁决并存时必须显式声明权威顺序："实际交付媒体上的最新裁决"优先于任何中间裁决；新增审核阶段时同步检查顶层汇总读的是哪一代。
- 非阻塞遗留（审核者观察）：①partial-停路径里字幕与最终视觉审核仍会执行（多花一次审核调用，结果仍正确报停）；②HEAD 既有问题：chain_result.json 缺失的崩溃窗口里，缓存分支对旧形态 needs_audio_revision 结果读 `result['candidate_video']` 会 KeyError，本次未触碰。
- 两次 `picture_audio_coherence_invalid` 属模型枚举违规，枚举已在提示合同中，无需改提示；若再现频繁，可考虑机械截断到首个枚举词（须另立任务评估）。
