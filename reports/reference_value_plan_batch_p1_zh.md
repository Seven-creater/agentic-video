# 通用规划批次 P1：来源与焦点绑定跟进

分支 `fix/reference-value-understanding`，基线 HEAD `a53427b`。本次在已有未提交改动上继续修复；**没有提交、没有线上模型调用或媒体重渲染**。采用 [karpathy-guidelines](/Users/Admin/.codex/skills/karpathy-guidelines/SKILL.md) 和任务指定的 [incident-record](/Users/Admin/Desktop/agentic_video_all/.claude/skills/incident-record/SKILL.md)。

## 三份真实回复的最终结果

| 回复 | 最终结果 | 判断与可操作反馈 |
|---|---|---|
| glm_182 | **PASS** | `seg_8.source_id` 从已选窗口回填为 `src_cf2fec1024243517` 后，通过完整生产规划校验；4 项规范化、5 个焦点绑定。 |
| glm_183 | 保留 `plan/focus_role_bindings:list_required` | 真正的选材问题：7 个片段全部引用未知 `ref_movie` 和参考片 source，cast 缺失，所用合法窗口数为 0。保持 focus_role_id，不替换镜头；反馈列出 13 个合法窗口、28 行确认角色及 11 个 cast 三元组。 |
| glm_184 | **仅** `plan:repeated_source_footage` | `seg_3/seg_6` 都用同源 219–223 秒，重叠 4 秒；反馈列出冲突和 20 条空闲替代范围。额外执行后续对白形式、说话者身份校验，均 PASS。 |

使用生产 `pipeline.execute/validate_current_plan` 闭包，将原始夹具重新经过规范化和全部规划校验，真实输出：

```text
glm_182_plan_0.json normalisations=4 result=PASS focus_bindings=5
glm_183_plan_0_repair.json normalisations=1 result=plan/focus_role_bindings:list_required focus_bindings=0
183 root_cause={"cast_count": 0, "used_known_window_count": 0, "unknown_window_ids": ["ref_movie"], "confirmed_observation_count": 28, "confirmed_observations_missing_identity_evidence": 0}
183 valid_window_ids=13 bindable_rows=28 bindable_triples=11
glm_184_plan_0_repair2.json normalisations=5 result=plan:repeated_source_footage focus_bindings=3
184 conflicts=[{"segment_ids": ["seg_3", "seg_6"], "source_id": "src_24675c65978d5360", "overlap_s": 4}]
184 free_alternative_ranges=20 remaining_dialogue_validation=PASS
```

原始回复保持不变。规范化计划和实际 `protocol_feedback.diagnose` 载荷分别存于 `/private/tmp/glm_18*_normalised.json`、`/private/tmp/glm_18*_feedback.json`；完整诊断与审计变更一并写入 `reports/reference_value_plan_batch_p1_results.json`。复现脚本为 `/private/tmp/p1_real_replies_runner.py`，运行环境 `PYTHONPATH=src:tests`。

## 根因与修复边界

**来源标识。** `plan_normalisation._segment_sources` 从 window_id 对应的窗口复制 source_id；segment 若带 source_sha256，也复制窗口的 SHA。每项回填写入 plan_normalisations，重复执行不新增审计项。未知窗口与素材区间保持原值，由原校验拒绝；反馈新增 valid_window_ids 和 unknown_segment_window_ids。来源不一致是可机械修正的问题，上一版报告将其归为独立创作问题不准确。

**焦点人物。** 183 的空列表不是因为确认观察缺 identity_evidence：28 个确认角色的证据均非空。它没有 cast，旧逻辑的 cast_entities 候选为空，而且所有所用 window_id 都不存在，无法匹配电影人物。不能从“老板”的叙事职责推断成阿宝或平先生，更不能把参考片 0 秒等区间映射到电影窗口。

通用填充现在先解析 focus_role_id 指定的 cast_id、cast.json 规范名/别名或观察 entity_id，也可由原有效焦点声明确定身份。对每个所用窗口，只选该人物唯一的 identity_confirmed 角色，并逐字复制观察证据。不能因某窗口只出现另一个 cast 就把焦点改成该人物。原有效的跨窗口视觉焦点声明仍保留；局部实体 ID 不同不足以否定原声明，也不能用于向未声明窗口自动外推。cast 的单实体约束不变。

`contracts.validate_fine` 明确要求观察自身有 identity_evidence，因此不允许仅用 state 替代缺失证据。本次不修改合同或观察，反馈以 focus_binding_blockers 报 `observation_identity_evidence_required`。没有任何确认匹配时保留 focus_role_id 和既有 validator 错误；bindable_rows 列出 `(window_id, role_id, entity_id, name)`。无可证姓名/实体的值为 null，不由外形猜名。

**重复素材。** 保留严格大于 0.5 秒的同源重叠拒绝，不自动删除、缩短或重选段落。184 的 free_alternative_ranges 包含窗口、来源、角色和 usable_range_index，例如 `window_7360119fed755b9c` 的 4898.5–4907.5 秒、`window_0245e2602a666d92` 的 3718.6–3721.2 秒。它们从允许表扣除已占用原片产生，实际反馈测试确认完整保留。

粗剪和精剪继续使用同一个规范化模块；对白迁移、身份锚与旁白回退规则不变。`contracts.py` 没有修改；身份、范围、连贯性要求未放松，非创作 library 路径不启用规范化。

## 回归测试：改前失败、改后通过

专项共 28 项，较上批增加 10 项，并加强真实回复断言：182 整体通过、183 精确失败原因和合法表、184 唯一重复错误与真实反馈。新增覆盖来源/SHA 回填、焦点 cast_id 与规范别名/观察实体匹配、禁止焦点漂移、缺证据诊断、有效局部焦点保留、幂等性。历史 ok 的 `full_tpl_xiaobiesan_20261026_v1/evaluations/regress_ab014be/glm_006_plan_0_repair` 另有真实夹具防回退。

修改前将完整 src 复制到 `/private/tmp/p1_followup_before/src`，用相同最终测试文件运行：

```text
10 failed, 18 passed in 3.78s
```

失败项包括原始 182/183 新断言及机械来源、身份匹配、焦点一致性和证据诊断；184 的重复拒绝与 library 对照本来就通过。修复后原始输出：

```text
............................                                             [100%]
28 passed in 3.77s
```

## 指定验收命令及真实输出

命令按规格运行，仅前置 `UV_CACHE_DIR=/private/tmp/pfix_uv_cache UV_OFFLINE=1`：默认缓存不可写且沙箱网络受限，复用已有离线缓存。

```sh
PYTHONPATH=src /Users/Admin/.local/bin/uv run --no-project --with pytest --with numpy python -m pytest -q -rf tests
PYTHONPATH=src /Users/Admin/.local/bin/uv run python -m omni_story.library.replay_check /Users/Admin/Desktop/agentic_video_all/.agent-team/replay_data --json /private/tmp/replay_p1.json
PYTHONPATH=src /Users/Admin/.local/bin/uv run python scripts/fault_check.py /Users/Admin/Desktop/agentic_video_all/.agent-team/replay_data --json /private/tmp/fault_p1.json
```

全量测试原始 FAILED 列表和摘要：

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
9 failed, 1685 passed, 104 skipped, 24 errors in 26.05s
```

对 `/Users/Admin/Desktop/agentic_video_all/.agent-team/out/baseline_2879e5c.txt` 比较，FAILED 超出基线为 0。全套并非全绿：24 项 `tests/test_tts_http.py` setup ERROR 均由 `ThreadingHTTPServer(('127.0.0.1', 0), ...)` 的 `PermissionError: [Errno 1] Operation not permitted` 引起，没有跳过或改写这些测试。全测退出码 1；完整日志 `/private/tmp/p1_tests.txt`。

回放原始 TOTAL，列顺序 `ok | regression | fixed | still_failing | unsupported`：

```text
TOTAL | 143 | 6 | 21 | 33 | 0
```

相对指定 `replay_baseline_98e9427.json`，逐行比较 `(task, call_id, stage)` 的最终分类：

```json
{
  "FAILED_outside_baseline": [],
  "changed_rows": [],
  "ok_or_fixed_regressions": 0,
  "new_pass_rows": []
}
```

无 ok/fixed 回退，最终变化行 `[]`，新增历史 PASS `[]`。基线已有 6 行 regression，回放退出码仍为 1。真实 182 是此次单独复测新增 PASS，三份新夹具不在该历史 replay_data 中，不能计作历史回放新增 PASS。完整日志 `/private/tmp/p1_replay.txt`、JSON `/private/tmp/replay_p1.json`。

故障检查所需五项实际结果：

```json
{"E1": 0, "R1": 0, "M1_crash": 0, "M2_json": 0, "M2_crash": 0}
```

五项全部为 0。fault 命令退出码 1，另报告 M1_accept=90、M2_many=9、M2_expected=61、M2_path=158、M2_miss=1768；这些不属于本次要求归零的五项，没有将退出码当成成功隐去。完整日志 `/private/tmp/p1_fault.txt`、JSON `/private/tmp/fault_p1.json`。

## git diff --stat

最终实际输出：

```text
 docs/incidents/README.md                    |  2 ++
 src/omni_story/library/pipeline.py          | 40 +++++++++++++++++++----------
 src/omni_story/library/protocol_feedback.py |  3 +++
 src/omni_story/library/story_context.py     | 15 +++++++++++
 src/omni_story/library/story_finecut.py     | 19 ++++++++++++++
 tests/test_finecut_speaker_check.py         |  2 ++
 tests/test_replay_check.py                  |  2 +-
 7 files changed, 68 insertions(+), 15 deletions(-)
```

git 默认不统计未跟踪文件。因此本次跟进的共享模块、新测试、真实夹具、两份 incident 和两份报告仍需结合 `git status --short` 审阅；均未暂存或提交。原有 `uv.lock` 未改动。`git diff --check` 无输出。

## Incident 与既有证据限制

已更新两份中文记录及 `docs/incidents/README.md` 索引：

- `docs/incidents/INC-20261031-01-plan-mechanical-identifiers.md`：来源回填、183 根因、焦点匹配与验收数据。
- `docs/incidents/INC-20261031-02-repeated-source-footage.md`：184 的唯一重复拒绝、20 条替代范围和后续校验结果。

雪山真实 `fine_plan_0.json` 仍仅含不相交的 chain_parent 区间，缺实际父片 render_result/story_context。原测试中的父片映射明确标注 synthetic，不能当作该线上运行的真实溯源回执。这个上批限制仍在；本次没有线上重跑或声称完成雪山真实溯源验收。
