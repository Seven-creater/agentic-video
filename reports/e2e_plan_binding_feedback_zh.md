已在分支 `fix/reference-value-understanding` 修复计划身份绑定合同与修复反馈不完整的问题。未提交、未暂存，没有执行服务器任务，也没有新增自动重试或重发。改动前已有的未跟踪文件 `docs/incidents/INC-20261030-e2e-timing-recovery.md`、`uv.lock` 保留。

核对了指定证据 `evidence_01_不烧心_3.txt`、只读分析 `analysis_4.md` 与当前代码。证据中的停止点确为 `model_protocol_repair_exhausted:plan_0`，已接收 174 次调用，修复次数为 2。三次 plan 的拒绝依次为：

| 尝试 | 原始拒绝 | 核对结论 |
| --- | --- | --- |
| glm_172 | `plan:cast_entity_not_supported_by_observation` | 同一 cast 引用未确认身份的窗口角色；同一回复还保留了该角色的焦点绑定。 |
| glm_173 | 同上 | cast 改绑另一来源的角色，但仍沿用前一来源的实体 ID。fine 回复没有 entity_id，现有入库逻辑为它生成来源内的本地 ID，不能与原实体逐字相等。未确认焦点绑定也仍存在。 |
| glm_174 | `plan/focus_binding_confirmed_role:unknown_or_duplicate_ref` | cast 只留已支持的绑定后，才暴露前两轮未报告的焦点错误；五个 segment 又把原片时间改成了局部时间。 |

已验证的代码缺陷是：`protocol_feedback._plan` 原先没有扫描 cast 或 focus_role_bindings。cast 校验先于焦点、片段校验，第一条拒绝掩盖了独立错误；焦点拒绝本身也没有结构化 violation。提示虽要求身份已确认，却没有明确说明不同观测实体应拆成不同 cast，或提供可直接复制的合法绑定清单。

只读分析有两点不能直接采纳。第一，跨来源角色已有合法表达：分别使用各来源观测到的实体建立不同 cast 项，并保持相同音色，所以不是 schema 完全无法表达。第二，自动剪除 cast/focus、删除 cast_id 会替模型改变身份和故事选择；此次不采用该归一化方案。原有身份确认、实体相等、来源 SHA、usable_range、重叠事件、原片时间及焦点入选要求均保留，没有放宽检查或增加修复预算。

具体改动如下：

- `contracts.plan_field_contract(..., windows=...)` 成为提示与校验共用的绑定合同。候选只来自 watched 窗口中 `identity_confirmed is True` 的角色；cast 候选还必须携带观测实体 ID。合同说明不同实体须拆成不同 cast，焦点不必覆盖每个窗口，未确认绑定应由模型删除或重选。
- creative_audio 的实际计划提示调用同一 helper，列出可复制的 `{window_id, role_id, entity_id}` 和焦点候选。没有在普通 library 提示中新增动态清单；共享 cast/focus 校验的错误明细用于修复相同的身份引用缺陷。
- 抽出 `validate_focus_role_bindings`，保留原有判定顺序及错误字符串，为拒绝附加 `violation.path/value/expected/repair_hint`。cast 校验同样依据共享 helper 的合法候选判定，并在拒绝中列出相关窗口的候选。
- 反馈先检查完整绑定列表，保留重复项、结构等错误，再分别运行同一校验器检查各绑定，将局部索引映射回原始路径。这样一轮就能同时报告不同绑定错误，并继续原有片段时间、音频等扫描。没有新增网络调用、自动补写实体、剪除绑定或换算剪辑时间。

修复只依赖窗口状态、确认标志、实体 ID 和结构引用，不读取模板名称、角色名字、台词或影片片段。换成其他参考模板、任意角色 ID、任意来源数量时，都按同一份观测合同处理。合法 cast 拆分仍由模型选择，代码不推断跨窗口或跨来源身份。

证据夹具位于 `tests/fixtures/e2e_plan_bindings/`。唯一完整的 fine 回复直接解码保存；三份 plan 所在证据节均被截断到约 4000 字符，只复制完整顶层 JSON 字段，并记录 provenance。`audio_draft` 截断部分未补造。另两个缺失观测窗口为明确标注的测试上下文，只复现拒绝链已确认的身份状态；第二来源 SHA 的不可见后缀为合成值。不能据此声称已恢复完整视觉证据、完整配音稿或存在已通过全部交付检查的计划。

新增 7 个回归用例，全部使用上述真实回复字段或 fine 文件，覆盖前两轮同时报告 cast/焦点、末轮同时报告焦点与五处时间域错误、可修复 violation、合法拆分提示与候选、多个非法 cast 绑定，以及生产调用循环的原有停止边界和缓存不重发。将 HEAD 原始源码导出到 `/private/tmp/e2e_bindings_original_src`，不切换工作树，运行同一批测试的实际输出为：

```text
FAILED tests/test_e2e_plan_bindings.py::test_first_cast_rejection_also_reports_unconfirmed_focus[0]
FAILED tests/test_e2e_plan_bindings.py::test_first_cast_rejection_also_reports_unconfirmed_focus[1]
FAILED tests/test_e2e_plan_bindings.py::test_last_reply_reports_focus_and_all_source_time_errors_together
FAILED tests/test_e2e_plan_bindings.py::test_focus_rejection_has_repairable_violation_and_observed_options
FAILED tests/test_e2e_plan_bindings.py::test_contract_lists_exact_entities_and_explains_source_local_cast_split
FAILED tests/test_e2e_plan_bindings.py::test_feedback_reports_each_independent_cast_binding_in_one_pass
FAILED tests/test_e2e_plan_bindings.py::test_real_fields_get_complete_first_feedback_without_more_attempts_or_resends
7 failed in 1.34s
```

修改后的实际输出：

```text
.......                                                                  [100%]
7 passed in 0.68s
```

逐字段重新提取证据并与夹具比对，以及运行实际校验与诊断的输出：

```text
Fixture fields match evidence: True
attempt 0: plan:cast_entity_not_supported_by_observation
feedback paths: ["$.cast[0].bindings[1]", "$.focus_role_bindings[1]"]
attempt 1: plan:cast_entity_not_supported_by_observation
feedback paths: ["$.cast[0].bindings[1]", "$.focus_role_bindings[1]"]
attempt 2: plan/focus_binding_confirmed_role:unknown_or_duplicate_ref
feedback paths: ["$.focus_role_bindings[1]", "$.segments[0].source_in_s/source_out_s", "$.segments[1].source_in_s/source_out_s", "$.segments[2].source_in_s/source_out_s", "$.segments[3].source_in_s/source_out_s", "$.segments[4].source_in_s/source_out_s"]
```

以下为指定验收命令及真实输出摘录。环境仅额外设置 `UV_CACHE_DIR=/private/tmp/question-first-search-uv-cache`、`UV_OFFLINE=true`，使用已有本机依赖缓存。

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
9 failed, 1594 passed, 104 skipped, 24 errors in 16.38s
```

对指定基线逐项比较的实际输出：

```text
FAILED subset of baseline_2879e5c: True
New FAILED: []
Removed FAILED: []
```

命令退出码为 1，不能视为全绿。9 个 FAILED 与基线相同；24 个 setup ERROR 均由 `tests/test_tts_http.py` 的 TLS 服务器 fixture 绑定本地端口时触发 `PermissionError: [Errno 1] Operation not permitted`。没有修改或跳过这些测试。完整输出：[e2e_bindings_pytest.txt](/private/tmp/e2e_bindings_pytest.txt)。

```sh
PYTHONPATH=src /Users/Admin/.local/bin/uv run python -m omni_story.library.replay_check /Users/Admin/Desktop/agentic_video_all/.agent-team/replay_data --json /private/tmp/replay_e2e.json
```

```text
task | ok | regression | fixed | still_failing | unsupported
TOTAL | 143 | 6 | 21 | 33 | 0
```

使用 `(task, stage, call_id)` 唯一键逐行比较指定 replay 基线的实际输出：

```text
baseline summary: {'ok': 143, 'regression': 6, 'fixed': 21, 'still_failing': 33, 'unsupported': 0}
current summary: {'ok': 143, 'regression': 6, 'fixed': 21, 'still_failing': 33, 'unsupported': 0}
Protected ok/fixed rows: 164
New regression/still_failing/missing from protected rows: []
```

命令退出码为 1，因为脚本仍报告基线已有的 6 条 regression；164 条原 `ok/fixed` 记录均保持 `ok/fixed`。完整输出：[replay_e2e.txt](/private/tmp/replay_e2e.txt)，JSON：[replay_e2e.json](/private/tmp/replay_e2e.json)。

```sh
PYTHONPATH=src /Users/Admin/.local/bin/uv run python scripts/fault_check.py /Users/Admin/Desktop/agentic_video_all/.agent-team/replay_data --json /private/tmp/fault_e2e.json
```

```text
| 类别 | 命中 |
| --- | ---: |
| E1 | 0 |
| E2 | 0 |
| M1_crash | 0 |
| M1_accept | 90 |
| M2_json | 0 |
| M2_crash | 0 |
| M2_empty | 0 |
| M2_many | 9 |
| M2_expected | 61 |
| M2_path | 154 |
| M2_miss | 1794 |
| R1 | 0 |
```

要求的 E1、R1、M1_crash、M2_json、M2_crash 全为 0。脚本因其他审计发现返回退出码 1；这些无关项未扩大修复范围。完整输出：[fault_e2e.txt](/private/tmp/fault_e2e.txt)，JSON：[fault_e2e.json](/private/tmp/fault_e2e.json)。

```sh
git diff --stat
```

```text
 src/omni_story/library/contracts.py         | 87 +++++++++++++++++++++--------
 src/omni_story/library/pipeline.py          |  2 +-
 src/omni_story/library/protocol_feedback.py | 32 +++++++++++
 3 files changed, 97 insertions(+), 24 deletions(-)
```

`git diff --check` 通过。新增测试、夹具及本报告尚未跟踪，因此不出现在上述未暂存 tracked diff 统计中。

修复消除了已验证的提示/校验信息缺口，并未让原本非法的回复直接通过。真实最后回复仍会在原有两次修复预算后停止；回归测试确认再次读取已接收调用不会重发，原失败记录保持。此次没有把缺失的完整证据、模型下一次是否会正确修复，或实际视频交付结果当成已经验证的事实。
