# INC-20261030-02 规划期不查"台词窗落在所挂段内",渲染后才以 draft_outside_real_segments 报废整跑(粉色娇嫩,188 次调用零产出)

| 项 | 内容 |
|---|---|
| 发现日期 | 2026-10-30 凌晨(e2e 自动修复第 13 轮) |
| 影响范围 | 创作路线(reference_rough_skill_v1)粗剪规划→渲染→story_context 构建;致命停机——计划通过校验并完成渲染后,台词窗口越界才暴露,188 次模型调用(1,029,260 prompt + 131,200 completion tokens)零产出 |
| 状态 | 已修复(61dca10);部署后同模板嵌套重跑结果待补 |
| 相关提交 | 61dca10(修复);出险版本为部署 release `aa47a93_creative_840ea5c`(该缺口引入提交未记录——plan 期从未有过此检查) |
| 相关任务 | 服务器 run `/home/ubuntu/apps/agentic-video/shared/jobs/e2e_18_粉色娇嫩_10300421`(模板 18_粉色娇嫩/01.mp4,04:21:49 启动);证据 `out/e2e/evidence_18_粉色娇嫩_12.txt`;分析 `out/e2e/analysis_13.md`;修复报告 `docs/incidents/INC-20261030-e2e-draft-segment-bounds.md`(fix_13 所链);审核 `out/e2e/review_13.md`(pass);闸门 `out/e2e/progress.log` round 13 行 |

## 1. 问题长什么样
run_outcome.json:`outcome/status: stopped`、`stage: story_context.validate_story_context`、`error: story_context:draft_outside_real_segments`、`exception_type: ValueError`、`raised_at: …/src/omni_story/library/story_context.py:71`、`model_calls: 188`、`repairs: 3`、`last_call_id: glm_188_plan_0_repair`、`final_video: null`。chain_failure.json:`requests: 188`,1,029,260 prompt + 131,200 completion tokens,`automatic_restart: false`。traceback:`server_cli.py:608 → _run_task:473 → clean_chain.py:646 execute(build_rough_context)→ story_context.py:324 → _build:290 → validate_story_context:71 raise`。
死亡点在模型退场之后:glm_187_plan_0 因 `plan:cast_entity_not_supported_by_observation` 被拦,glm_188 修复版通过 validate_plan——其中 seg_3 由 `window_96bebc1edbe94d24, 1273.0–1276.0`(3.0 s)改为 `window_c7c8f8867dc61a17, 1273.5–1276.0`(2.5 s),其后所有段输出时间整体左移 0.5 s,cast_suppliant 绑定同步换窗——随后渲染成功,`build_rough_context` 死于台词窗越界。证据中 glm_187/188 回复均在 seg_4 中途截断,`audio_draft` 原文不可见,具体越界句未记录。

## 2. 怎么查出来的
证据包逐字段核对(analysis_13.md,对照 840ea5c 源码,traceback 行号与部署 release 一致):
- 排除法锁定触发机制:`_build` 里台词窗口由 `draft_line_windows` 重算,对不自报时间的句子,按比例分配在构造上闭包于组跨度内(首句取组起点、末句取组终点,恰好取等号通过)——这类句子不可能触发;触发者必为模型自报的 `start_s/end_s`(原样采用)或 `start_anchor` 把游标推过组终点(plan 期只验 `offset < 名义段长`,渲染量化把段长缩短后游标可越界)。
- plan 期校验链逐环核对:旧 `validate_audio_draft` 对自报时间只查"是数字"(contracts.py:1116-1118);旧 `validate_draft_line_windows` 只查窗口下限(line_windows.py:162-167);"台词窗落在所挂首段起点至末段终点内"这条提示词明教的不变量,全链唯一执行点是渲染后的 `validate_story_context`(story_context.py:62-71),容差 1e-6 s。
- 两套时间轴:plan 演算用未量化的名义连续秒轴(line_windows.py:87-100),渲染器按 `round(时长×fps)` 整帧量化累计(render.py:242-259);1e-6 s 容差远小于一帧(1/30 s≈0.033 s),也小于系统其余浮点比较的惯例容差 0.001 s(contracts.py:141、:911-913)。
- 修复代理独立复核的证据边界:证据可见前三段(2.0/2.0/2.5 s)在 30 fps 下全部整帧对齐,故不能断言本次必由帧量化触发;但"修订 EDL 后沿用旧时间坐标"这一机制类成立——seg_3 缩短 0.5 s 即时间轴整体平移,任何未按新 EDL 重算的自报窗口即越界。具体越界句因证据截断无法本地核实;服务器任务目录 `plan_0.json` + `render_0/render_result.json` 可复核(后置 error.violation 含越界句 id 与期望区间,story_context.py:68-70)。
- 旧码复现(修复代理,`git archive HEAD src` 导出 + 显式探针窗口):`OLD preflight: accepted probe [4, 7]` / `Rendered seg_3: 4.0 6.5` / `OLD post-render: story_context:draft_outside_real_segments`——先固化了 bug。

## 3. 根因
代码问题(校验缺口/提示词与校验不一致 + 两套确定性算术不同轴 + 容错缺失),非素材不足——6 个节拍全部有已看窗口支撑,slots `coverage.gaps` 全空(证据 95/115 行);b1 候选只能 partial 是 equivalents 机制按设计处理的质量降级,不停机。三处缺陷叠加,缺一不会死:
- (a) 提示词教了规则("audio_draft 每句已允许同时填写 start_s/end_s……须落在所挂首段起点至末段终点内",line_windows.py:48-49),plan 期校验链上没有任何一环执行它。
- (b) 规划/演算用名义秒轴、渲染用整帧量化轴,两轴系统性不一致;后置检查以量化轴为准且容差 1e-6 s,任何非帧对齐的计划都让自报窗口系统性漂移越界。
- (c) plan 一旦通过即渲染,`clean_chain.py:646` 的 `build_rough_context` 不在任何修复循环内,裸 `ValueError` 一路穿到 server_cli;错误里写好的 repair_hint 没有任何模型能看到。同一违规若在 plan 期发现就是一次标准 `_repair` 协议失败(机制同 golden 的 `audio_line_window_below_minimum`)。
直接诱因(推断):glm_188 修复 cast 违规时被要求整份计划重发,改写 EDL(seg_3 缩短 0.5 s)后部分自报窗口沿用修复前时间坐标,越出渲染后实际跨度;受证据截断所限,越界句 id 与数值未记录。

## 4. 解决方案(61dca10,9 个文件 +64/−31,另新增 fixture 与测试文件)
核心原则:在 plan 期、用渲染器自己的算术检查渲染后才执行的不变量,让它变成可修复的协议违规;两条确定性计算之间的分歧不在渲染后杀整跑。
- `src/omni_story/library/render.py:204` 提取共享 `output_frame_counts`(运动帧与定格帧分别 `round(×fps)` 取整),渲染器与规划期同源;渲染 provenance 直接取 `compile_library_plan` 的 `compiled["segments"]`(render.py:556)。规划期算出的边界与渲染后边界是同一份算术,构造上不可能再发散。
- `src/omni_story/library/line_windows.py:144` `draft_line_window_details` 增 fps 参数:明细同时给出 `allowed_start_s/allowed_end_s`(按 `segment_output_timeline(segments, fps=fps)` 量化轴);:175 `validate_draft_line_windows` 增加跨度检查,越界抛 `ValueError('plan:audio_line_window_outside_segments')`(:178-190),violation 附 `path/value/expected/repair_hint` 与全部越界行(`offending_lines`)——进入既有 `_repair` 协议修复通道,无新增自动重试。
- `src/omni_story/library/contracts.py:1165-1167` `validate_audio_draft` 传入 fps 调用新检查;仅在计划含 `audio_draft` 时生效,library 路线不受影响(review_13 核实,contracts.py:1063)。粗剪入口 `pipeline.py:2134` 传计划 fps,精剪入口 `prompts.py:128` 传实际输出 FPS;story_context 构建改用渲染行校验真实范围(与既有后置检查同一语义)。
- 既有 0.45 s 窗口测试改为断言实际 13/30 s(该源区间 30 fps 下确实只有 13 帧),统计下限常数未变;提示词与校验器继续共享 `draft_line_window_details`、窗口分配器与推导说明,演算示例与校验同轴。
- 通用性:谓词只含任意 EDL 都有的 `source_in_s/source_out_s/speed/freeze_tail_s/fps`、片段引用与台词窗口,不读模板/角色/台词内容;`1e-6` 容差、后置 `story_context:draft_outside_real_segments` 门禁、窗口下限常数全部保留(新检查是收紧,不是放宽);未调用在线模型、TTS,未重跑付费创作。

## 5. 为什么不用其他方案
- 渲染后 clamp 归一化(analysis_13 修复 2,防御纵深):现有证据没有证明越界仅为亚帧偏差,自动裁窗可能掩盖 0.5 s 级真实错位;plan 期拦截落地后该路径不可达作为主修,未采用。
- 放宽后置 1e-6 容差到一帧级:治标——只把越界阈值从 1e-6 改成一帧,漏掉 0.5 s 级的真实错位,两轴分歧依旧。
- 禁止模型自报 start_s/end_s:删能力而非堵漏洞;违规由 plan 期拦截并 repair,能力保留。
- 越界时 defer 降级带警告放行(finecut_continuation.py:139 路径):review_13 核实——带警告放行的计划仍会死在 `validate_story_context` 的无条件包含性检查上,拒绝反而把死亡点提前到可观测、可修复处。
- 针对粉色娇嫩/seg_3/具体角色做特判:任务规范明令禁止,且谓词不碰内容即可闭合。

## 6. 怎么验收(均为真实输出,详见修复报告)
- 新增回归测试 `tests/test_draft_segment_bounds.py` + fixture `tests/fixtures/e2e_draft_outside_segments.json`(逐字段复制证据中两版回复的完整前三段与 run_outcome.json,注明截断边界;台词/窗口/速度/定格变异均为显式探针,未补造缺失原始回复):对旧源码 9 个用例全部失败,修复后通过(先固化 bug 再修)。
- 相关测试集修复后:`56 passed in 1.68s`。
- 全量 pytest:`9 failed, 1610 passed, 104 skipped, 24 errors`——9 个 FAILED 与基线逐条相同;24 个 ERROR 均为沙箱禁止绑定 socket(`PermissionError: [Errno 1] Operation not permitted`),非全绿但无新增失败。
- replay_check:TOTAL ok 143/regression 6/fixed 21/still_failing 33,与基线汇总完全一致;基线 164 条 ok/fixed 全保留,新退化或缺失 0 条。
- fault_check:E1/R1/M1_crash/M2_json/M2_crash 均为 0。
- GLM 审核 review_13:verdict=pass、findings=[](2 条非阻断观察见 §8)。
- 机械闸门 progress.log:`05:15:55 round 13: GLM review=pass`、`05:17:34 round 13: gates extra_failed=0 new_regressions=0 fault_bad=0`,随后 `DEPLOYED_61dca10`。
- 部署后同模板嵌套重跑:待跑,结果写入 `out/e2e/progress.log` 后于下一轮补充本记录;原始 audio_draft 被截断,无法逐句重放本次整跑,不将离线验收表述为已完成一次新的在线交付。

## 7. 量化数据
- 修复前(该次运行):模型调用 188 次(1,029,260 prompt + 131,200 completion tokens)、repairs 3(2 次 fine + 1 次 plan)、成片 0;04:21:49 启动,停机时刻未记录(证据与 progress.log 均无该 run 的结束时间戳),花费未记录。
- 修复后(本地):新增 9 用例 9 失败→9 通过;相关测试集 56 通过;全量 1610 通过/9 基线失败/24 沙箱错误;回放 0 新退化;fault 五项全 0。
- 部署后嵌套重跑对比(耗时/调用数/结局):未记录(进行中,结果写 progress.log 后补)。

## 8. 经验与后续
- 两条确定性算术(规划演算与渲染量化)必须同源:本次直接共享 `output_frame_counts`,浮点运算顺序一致,从构造上消灭分歧,而不是靠放大容差吸收。
- "渲染后才检查的不变量"值得逐条审计:凡提示词教了而校验器没查的规则迟早被模型踩中;plan 期可算的检查都应提前到 plan 期并接入 `_repair`,让带 repair_hint 的反馈有人能看到。本次是 golden §3 原则("把违规从杀跑改成可修复反馈")在这条不变量上的漏网。
- 遗留 1(review_13 非阻断观察):粗剪规划 context(`pipeline.py:2018`)不含 fps,故 `pipeline.py:2134`/`prompts.py:128` 的 `fps=context.get('fps')` 在粗剪调用点实际恒为 None;当前演算示例(2.0 s)在任意 1–60 fps 下均整帧对齐、无实质分歧,但若将来给粗剪 context 加 `segments` 而不加 `fps`,明细块会按名义轴计算而校验器按量化轴——需后续留意。
- 遗留 2:证据截断(glm_187/188 回复停在 seg_4)使具体越界句 id 与数值未记录;服务器任务目录 `plan_0.json` + `render_0/render_result.json` 可一键复核。
- 遗留 3:部署后同模板嵌套重跑结果待补(progress.log)。
