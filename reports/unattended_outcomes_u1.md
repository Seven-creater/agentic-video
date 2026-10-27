# 无人值守创作路线修复报告（U1）

工作树：`fix/reference-value-understanding`；起点/当前 HEAD：`7da9891f451cf27f6a0541a6c983d4502cc53957`。未提交、未暂存。原有未跟踪 `uv.lock` 保留。

## 逐项实现与测试

| 项目 | 修改与位置 | 新测试 |
| --- | --- | --- |
| S1 | [visual_audio.py:25](/Users/Admin/Desktop/agentic_video_all/.agent-team/wt/pfix/src/omni_story/library/visual_audio.py:25)、[visual_audio.py:40](/Users/Admin/Desktop/agentic_video_all/.agent-team/wt/pfix/src/omni_story/library/visual_audio.py:40)、[visual_audio.py:71](/Users/Admin/Desktop/agentic_video_all/.agent-team/wt/pfix/src/omni_story/library/visual_audio.py:71)：无新语音时，action/speech/music任意驱动均选完整参考声轨；保留有新语音/参考无音轨的既有停止码。参考床要求music_audible=True；非music驱动不额外要求节拍matches。rough路径共用music_source（[clean_chain.py:651](/Users/Admin/Desktop/agentic_video_all/.agent-team/wt/pfix/src/omni_story/library/clean_chain.py:651)）。 | [test_unattended_outcomes.py:35](/Users/Admin/Desktop/agentic_video_all/.agent-team/wt/pfix/tests/test_unattended_outcomes.py:35) `test_s1_action_without_speech_uses_reference_soundtrack`；[test_unattended_outcomes.py:47](/Users/Admin/Desktop/agentic_video_all/.agent-team/wt/pfix/tests/test_unattended_outcomes.py:47) `test_s1_action_reference_bed_requires_audible_music` |
| S2 | [audio_measurement.py:7](/Users/Admin/Desktop/agentic_video_all/.agent-team/wt/pfix/src/omni_story/library/audio_measurement.py:7)：阈值单一来源；mix.wav、最终mux、music/voice stem记录peak/RMS；逐句按[start_s,actual_end_s]记录voice RMS。[audio_render.py:183](/Users/Admin/Desktop/agentic_video_all/.agent-team/wt/pfix/src/omni_story/library/audio_render.py:183)写receipt；[visual_audio.py:46](/Users/Admin/Desktop/agentic_video_all/.agent-team/wt/pfix/src/omni_story/library/visual_audio.py:46)先量测再决定是否调用AV，失败进入needs_audio_revision；[visual_audio.py:813](/Users/Admin/Desktop/agentic_video_all/.agent-team/wt/pfix/src/omni_story/library/visual_audio.py:813)写audio_result。[audio_measurement.py:45](/Users/Admin/Desktop/agentic_video_all/.agent-team/wt/pfix/src/omni_story/library/audio_measurement.py:45)记录review_contradicts_measurement及fields。 | [test_unattended_outcomes.py:51](/Users/Admin/Desktop/agentic_video_all/.agent-team/wt/pfix/tests/test_unattended_outcomes.py:51) `test_s2_all_zero_and_minus_91_db_stems_are_silent`；`test_s2_failed_measurement_never_calls_av_review`（mix/line/music）；`test_s2_review_claims_contradict_silent_measurement`；`test_s2_normal_mix_passes_and_records_windows`；[test_unattended_outcomes.py:281](/Users/Admin/Desktop/agentic_video_all/.agent-team/wt/pfix/tests/test_unattended_outcomes.py:281) `test_s2_silent_candidate_reaches_non_delivered_fine_branch`；`test_s2_mix_receipt_contains_measurement_for_stems_and_mux` |
| B1 | [contracts.py:130](/Users/Admin/Desktop/agentic_video_all/.agent-team/wt/pfix/src/omni_story/library/contracts.py:130)共用字段合同与[contracts.py:152](/Users/Admin/Desktop/agentic_video_all/.agent-team/wt/pfix/src/omni_story/library/contracts.py:152) repairable ValueError；cast/range/caption/audio_draft/audio_relation均附path/value/expected/repair_hint。[pipeline.py:1966](/Users/Admin/Desktop/agentic_video_all/.agent-team/wt/pfix/src/omni_story/library/pipeline.py:1966)保留rough对白规则，[pipeline.py:1981](/Users/Admin/Desktop/agentic_video_all/.agent-team/wt/pfix/src/omni_story/library/pipeline.py:1981)提示同一合同；[protocol_feedback.py:294](/Users/Admin/Desktop/agentic_video_all/.agent-team/wt/pfix/src/omni_story/library/protocol_feedback.py:294)修正reference前缀plan错误的误导向。无新模型fallback。 | [test_unattended_outcomes.py:228](/Users/Admin/Desktop/agentic_video_all/.agent-team/wt/pfix/tests/test_unattended_outcomes.py:228) `test_b1_audio_contract_shared_by_prompt_validator_and_feedback`；[test_unattended_outcomes.py:379](/Users/Admin/Desktop/agentic_video_all/.agent-team/wt/pfix/tests/test_unattended_outcomes.py:379) `test_b1_evidence_rejection_names_field_and_allowed_values`（cast/range/caption）；[test_unattended_outcomes.py:345](/Users/Admin/Desktop/agentic_video_all/.agent-team/wt/pfix/tests/test_unattended_outcomes.py:345) `test_d_exhausted_plan_stops_with_exact_call_and_traceback` |
| B2 | [story_finecut.py:59](/Users/Admin/Desktop/agentic_video_all/.agent-team/wt/pfix/src/omni_story/library/story_finecut.py:59)与[story_finecut.py:995](/Users/Admin/Desktop/agentic_video_all/.agent-team/wt/pfix/src/omni_story/library/story_finecut.py:995)捕获准确revision耗尽stage，保留最新已render且已review版本；带partial和fine_revision_exhausted/原review问题继续creative audio，最终completed_partial。复用[finecut_continuation.py:108](/Users/Admin/Desktop/agentic_video_all/.agent-team/wt/pfix/src/omni_story/library/finecut_continuation.py:108)的已知失败call校验，兼容原有repair/repair2链，次数未改。 | [test_unattended_outcomes.py:142](/Users/Admin/Desktop/agentic_video_all/.agent-team/wt/pfix/tests/test_unattended_outcomes.py:142) `test_b2_exhausted_fine_revision_delivers_reviewed_render_partial` |
| B3 | [clean_chain.py:469](/Users/Admin/Desktop/agentic_video_all/.agent-team/wt/pfix/src/omni_story/library/clean_chain.py:469)同样复用已知失败校验，不补发；[story_context.py:399](/Users/Admin/Desktop/agentic_video_all/.agent-team/wt/pfix/src/omni_story/library/story_context.py:399)保留timing_problems与dropped_lines/reason，[story_context.py:414](/Users/Admin/Desktop/agentic_video_all/.agent-team/wt/pfix/src/omni_story/library/story_context.py:414)只送可用句到mixer。[story_context.py:327](/Users/Admin/Desktop/agentic_video_all/.agent-team/wt/pfix/src/omni_story/library/story_context.py:327)与[story_context.py:374](/Users/Admin/Desktop/agentic_video_all/.agent-team/wt/pfix/src/omni_story/library/story_context.py:374)跳过已丢弃句的TTS和排程；原稿仍保留，字幕只使用实际发声稿。partial贯穿fine context/audio/final。 | [test_unattended_outcomes.py:167](/Users/Admin/Desktop/agentic_video_all/.agent-team/wt/pfix/tests/test_unattended_outcomes.py:167) `test_b3_exhausted_rough_timing_keeps_candidate_and_drops_unfitted_lines`；[test_unattended_outcomes.py:297](/Users/Admin/Desktop/agentic_video_all/.agent-team/wt/pfix/tests/test_unattended_outcomes.py:297) `test_b3_dropped_rough_lines_mix_remaining_audio_and_finish_partial`；[test_unattended_outcomes.py:332](/Users/Admin/Desktop/agentic_video_all/.agent-team/wt/pfix/tests/test_unattended_outcomes.py:332) `test_b3_dropped_lines_are_not_synthesized_or_scheduled` |
| F1 | [story_finecut.py:50](/Users/Admin/Desktop/agentic_video_all/.agent-team/wt/pfix/src/omni_story/library/story_finecut.py:50)：fine next_action=deliver、audio content ready、technical verified、measurement passed且非partial时joint才为True。删除创作路线错误依赖的“blind/target必须pass、无problems、无limitations”条件；原静音盲审缺音频限制不再阻塞最终AV通过。[story_finecut.py:718](/Users/Admin/Desktop/agentic_video_all/.agent-team/wt/pfix/src/omni_story/library/story_finecut.py:718)产出completed/completed_partial/既有needs状态；chain传递状态。 | [test_unattended_outcomes.py:187](/Users/Admin/Desktop/agentic_video_all/.agent-team/wt/pfix/tests/test_unattended_outcomes.py:187) `test_f1_joint_true_exactly_for_full_pass`（full pass/fine/ready/technical/measurement/partial六种） |
| D | [server_cli.py:351](/Users/Admin/Desktop/agentic_video_all/.agent-team/wt/pfix/src/omni_story/library/server_cli.py:351)包住全部worker准备与执行，finally只从[run_outcome.py:75](/Users/Admin/Desktop/agentic_video_all/.agent-team/wt/pfix/src/omni_story/library/run_outcome.py:75)写run_outcome.json；用contracts.json_safe。记录delivered/partial/stopped、status、video、partial_reasons、可用量测、调用与repair计数、已有cost。异常取omni_story内最深traceback file:line与stage/error/type/last_call_id/protocol_failure；正常质量停止另有决策位置。其他已知repair耗尽若已有render，也通过同一失败校验保留最佳已生成版本（[run_outcome.py:32](/Users/Admin/Desktop/agentic_video_all/.agent-team/wt/pfix/src/omni_story/library/run_outcome.py:32)），无render则准确停止。 | [test_unattended_outcomes.py:203](/Users/Admin/Desktop/agentic_video_all/.agent-team/wt/pfix/tests/test_unattended_outcomes.py:203) `test_d_worker_always_writes_run_outcome`（三终态）；`test_d_exhausted_plan_stops_with_exact_call_and_traceback`；[test_unattended_outcomes.py:361](/Users/Admin/Desktop/agentic_video_all/.agent-team/wt/pfix/tests/test_unattended_outcomes.py:361) `test_d_exhausted_review_delivers_existing_render_without_model_call` |

正常停止与异常停止都不重发请求。`audio_review.py`、`tts_retry`及模型format-repair次数未修改；未添加F2循环。

## B1：census与实际归档核对

指定census计数与本地replay归档不是同一完整样本。实际查遍`calls/*/protocol_failure.json`，六类只找到8份：cast=0、range=2、draft=2、caption=0、relation=3、dialogue=1。不能声称验证了不存在的call。

| 拒绝码（简写） | census / 可核对归档 | prompt与repair反馈结论 | 本批修复 |
| --- | ---: | --- | --- |
| cast_entity_not_supported_by_observation | 11 / 0 | 无对应failure可逐call核对；当前代码原已有“确认角色+精确entity_id”规则。 | 合同共享、反馈保留实际observed entity与confirmation，增加专用hint；不放宽。 |
| range_not_supported_by_fine_observation | 8 / 2 | 两份prompt均明确同一usable_range；反馈虽含诊断表，path却是错误码或无关speaker。 | segments精确路径、source区间、允许的单条范围与role_ids、修复hint。 |
| plan/audio_draft:list_required | 7 / 2 | 列了逐项字段，未明确顶层非空数组；repair无字段violations或专用hint。 | audio_draft数组/非空/none时[]写入共享合同；精确path与hint。 |
| caption_event_outside_selected_range | 4 / 0 | 无对应failure；当前提示仅说明引用入选范围事件，校验仍严格要求实际重叠。 | 同一合同说明重叠；violation指向event_indices并列实际允许索引；新增拒绝测试。 |
| plan/audio_draft/audio_relation:list_required | 4 / 3 | 上下文有数组简例，正文直接列枚举；repair把错误码当path，未定位具体字段。 | 明确对象数组、逐segment覆盖、允许relation枚举；具体字段path与hint。 |
| reference_dialogue_form_missing_from_audio_draft | 3 / 1 | prompt解释对白形式但未明确至少一句mode=dialogue；repair误扫reference，要求theme等无关字段。 | 保留rough专用规则，明确至少一句，修正feedback阶段分派。 |

逐call原始路径、提示摘录与repair字段见 [审计JSON](unattended_plan_audit_u1.json)。

## 验收命令与真实输出

默认uv缓存不可写且沙箱无法连PyPI；使用已有离线缓存，只增加环境变量：

```sh
export UV_CACHE_DIR=/private/tmp/reference-value-uv-cache-pfix
export UV_OFFLINE=1
```

FFmpeg/FFprobe不在PATH；S1按要求使用probe_media monkeypatch，S2对已解码numpy样本与流程做确定性测试，没有伪称真实mux端到端已执行。

基线构建和同一份新测试：

```sh
mkdir -p /private/tmp/u1_base
git archive 7da9891 | tar -x -C /private/tmp/u1_base
cp tests/test_unattended_outcomes.py /private/tmp/u1_base/tests/test_unattended_outcomes.py
# 基线工作目录下：
PYTHONPATH=src /Users/Admin/.local/bin/uv run --no-project --with pytest --with numpy python -m pytest -q -rf tests/test_unattended_outcomes.py
# 当前工作目录下同一命令：
```

```text
29 failed in 0.63s
.............................                                            [100%]
29 passed in 1.27s
```

全测：

```sh
PYTHONPATH=src /Users/Admin/.local/bin/uv run --no-project --with pytest --with numpy python -m pytest -q -rf tests
```

```text
FAILED tests/test_clean_chain.py::test_deferred_rough_requires_actual_final_av_delivery[None]
FAILED tests/test_clean_chain.py::test_deferred_rough_requires_actual_final_av_delivery[no_marker]
FAILED tests/test_clean_chain.py::test_deferred_rough_requires_actual_final_av_delivery[joint_failed]
FAILED tests/test_clean_chain.py::test_deferred_rough_requires_actual_final_av_delivery[not_decoded]
FAILED tests/test_clean_chain.py::test_deferred_rough_requires_actual_final_av_delivery[not_delivered]
FAILED tests/test_clean_chain.py::test_stop_after_rough_reviews_actual_draft_once_and_never_calls_finecut[True]
FAILED tests/test_clean_chain.py::test_stop_after_rough_reviews_actual_draft_once_and_never_calls_finecut[False]
FAILED tests/test_server_jobs.py::test_guard_descriptors_are_validated_before_writing
FAILED tests/test_visual_library.py::test_same_appearance_does_not_merge_people_and_known_ids_keep_exemplars
9 failed, 1284 passed, 103 skipped, 24 errors in 12.17s
FAILED subset of baseline: True
Additional FAILED: []
ERROR count: 24
PermissionError count: 24
```

24个ERROR全部发生于本地HTTP测试server建立socket，被沙箱拒绝；9个FAILED与指定baseline相同，包含既有Linux-only/Pillow依赖与旧fixture失败。未改这些范围外行为。

Replay：

```sh
PYTHONPATH=src /Users/Admin/.local/bin/uv run python -m omni_story.library.replay_check /Users/Admin/Desktop/agentic_video_all/.agent-team/replay_data --json /private/tmp/replay_u1.json
```

```text
TOTAL | 149 | 0 | 22 | 32 | 0
{"ok": 149, "regression": 0, "fixed": 22, "still_failing": 32, "unsupported": 0}
```

与`replay_baseline_1f954ae.json`相比11行不同，下节逐行列出；通过/拒绝规则未放宽。

Fault check：

```sh
PYTHONPATH=src /Users/Admin/.local/bin/uv run python scripts/fault_check.py /Users/Admin/Desktop/agentic_video_all/.agent-team/replay_data --json /private/tmp/fault_u1.json
```

```json
{
  "E1": 0,
  "E2": 0,
  "M1_crash": 0,
  "M1_accept": 97,
  "M2_json": 0,
  "M2_crash": 0,
  "M2_empty": 0,
  "M2_many": 25,
  "M2_expected": 61,
  "M2_path": 164,
  "M2_miss": 1990,
  "R1": 0
}
```

额外执行计数：`{"M2_rejections": 5682, "M2_feedback_items": 9360}`。

`high=0`。脚本exit=1表示仍有已记录的medium/low审计项，不是运行崩溃；E1/R1/M1_crash/M2_json/M2_crash均0。M2全部数字如上保留。

数字静音验证：全零数组peak/RMS记录为有限JSON底值-120 dBFS并判silent；输入-91 dBFS样本同样判silent。阈值原样记录：whole/stem peak < -45 dBFS，line RMS < -50 dBFS。

## Replay逐行差异

表内任务使用路径末段便于阅读，完整task路径及changed_fields见 [差异JSON](unattended_replay_differences_u1.json)。

| # | task末段 | call id | category | 原因 |
| ---: | --- | --- | --- | --- |
| 1 | `creative_audio` | `glm_001_audio_plan_v1` | unsupported → ok | 7da9891已有audio_plan_v适配器；本批未改replay_check。 |
| 2 | `creative_3f9c1eb` | `glm_003_rough_audio_timing_1_repair` | still_failing → fixed | 7da9891已有阻塞句同等编辑权限；本批未改该规则。 |
| 3 | `v7_ab014be` | `glm_003_plan_0` | still_failing → still_failing | 对白形式拒绝保持，增加repair_hint。 |
| 4 | `v7_ab014be` | `glm_004_plan_0_repair` | still_failing → still_failing | audio_relation保持拒绝，定位到具体.audio_relation并列对象数组结构、允许枚举；诊断共用字段合同。 |
| 5 | `v4_6269974` | `glm_003_plan_0` | still_failing → still_failing | 范围拒绝保持，增加segments路径、完整源区间、同条usable_range的允许区间与role_ids；诊断共用字段合同。 |
| 6 | `v4_6269974` | `glm_004_plan_0_repair` | still_failing → still_failing | audio_relation保持拒绝，定位到具体.audio_relation并列对象数组结构、允许枚举；诊断共用字段合同。 |
| 7 | `v3_838f636` | `glm_004_plan_0_repair` | still_failing → still_failing | audio_relation保持拒绝，定位到具体.audio_relation并列对象数组结构、允许枚举；诊断共用字段合同。 |
| 8 | `full_tpl_kuangbiao_fish_20261026_v1` | `glm_005_plan_1` | still_failing → still_failing | audio_draft缺失仍拒绝，增加字段路径、非空数组要求与必填字段；诊断共用字段合同。 |
| 9 | `full_tpl_kuangbiao_fish_20261026_v1` | `glm_006_plan_1_repair` | still_failing → still_failing | 拒绝和violation保持，仅execution_contract改为共用cast/audio_relation字段合同。 |
| 10 | `regress_ab014be` | `glm_005_plan_0` | still_failing → still_failing | 范围拒绝保持，增加segments路径、完整源区间、同条usable_range的允许区间与role_ids；诊断共用字段合同。 |
| 11 | `full_tpl_xiaobiesan_20261026_v1` | `glm_007_plan_0` | still_failing → still_failing | audio_draft缺失仍拒绝，增加字段路径、非空数组要求与必填字段；诊断共用字段合同。 |

前两行改善来自起点7da9891已有的replay adapter/阻塞句权限修复，相对较旧1f954ae基准显现，本批没有改replay_check或该权限规则；其余9行仍拒绝，只增强violation/diagnostics。

## 差异统计与未执行项目

真实`git diff --stat`（未暂存新文件不会显示）：

```text
 src/omni_story/library/audio_render.py         | 11 +++-
 src/omni_story/library/clean_chain.py          | 64 +++++++++++++++----
 src/omni_story/library/contracts.py            | 85 ++++++++++++++++++++------
 src/omni_story/library/finecut_continuation.py | 15 ++++-
 src/omni_story/library/pipeline.py             | 14 ++---
 src/omni_story/library/protocol_feedback.py    |  9 ++-
 src/omni_story/library/server_cli.py           | 23 +++++++
 src/omni_story/library/story_context.py        | 32 +++++++++-
 src/omni_story/library/story_finecut.py        | 61 +++++++++++++-----
 src/omni_story/library/visual_audio.py         | 78 ++++++++++++++++++++---
 10 files changed, 322 insertions(+), 70 deletions(-)
```

新增源文件和测试均留在工作树、未git add：

- `src/omni_story/library/audio_measurement.py`：52行。
- `src/omni_story/library/run_outcome.py`：137行。
- `tests/test_unattended_outcomes.py`：409行。

本报告及两个审计JSON亦是未跟踪文件。`git diff --check`无输出（通过）。

未执行真实服务器叶问任务、真实TTS/Qwen请求或真实ffmpeg渲染：沙箱无ffmpeg且无外网，本批采用授权的unit/monkeypatch验证；没有自动重试或重送。census中未出现在给定replay_data的cast/caption等历史call无法逐份核实，已明确标注缺样，并对当前严格validator补测试。S1/S2/B1/B2/B3/F1/D代码和本地验收均已完成。未提交。
