已在 `fix/reference-value-understanding` 工作树完成修复，未提交。改动限于 Question-first 检索与其流水线入口；`material_search=library` 路径未改。

独立核对证据与代码后，确认主要缺陷是将“某个检索节拍缺候选”直接升级为整条运行终止。证据中的 b1 为 `candidates: []`，但 b2 的 `close_006` 已是 `usable / equivalent / 3d`，有效源时间为 284–291 秒。原来的无条件 gap 门在连续观察和规划前停止，导致已有的 `reference.question_first_gaps`、`slots.coverage.gaps`、规划与评审上下文无法用于处理这类部分缺口。

修复改为使用现有 `to_search_result` 的实际窗口筛选结果判断是否还有素材可观察。有合格窗口时进入正常连续观察、规划及评审，同时保留缺口；没有合格窗口时仍抛原来的 `question_first:material_gap`。风格、时间、内容区间、排除区间和成片质量检查保持原样，身份仍须实际确认才能授权绑定。真实 b1/b2 夹具验证了修复后只观察 b2 窗口，b1 缺口仍出现在规划的 reference、顶层 gaps 和 slots 中；原有全部候选为二维风格的停止用例继续通过。此处改变的是检索阶段的终止条件，未将缺口判为已满足，也不保证最终叙事评审通过。

另一项可直接复现的缺陷是带锚点回复中的 `same_character_as_anchor: null` 被裸断言丢弃。`glm_165_qfs_close_26` 的 JSON 完整，主结论是 `no`，`also_satisfies` 为空，此时身份值不参与候选授权。对正向候选，现有 `apply_close_constraints` 本来就有 `identity_unverified → partial` 分支，但上游断言使该分支不可达。

现在保留显式 `null`：负向结论继续为 `no`，有锚点且身份未确认的正向候选至多为 `partial`，不能取得已确认实体绑定。错误类型或带锚点时缺失字段仍被拒绝，改用带 `path / value / expected / repair_hint` 的 `ValueError`；批处理记录保留其 violation。提示词和验证器的错误契约共同调用 `close_identity_contract()`。四条真实回复的保留数量由修改前的 3/4 变为 4/4，四条仍全部为 `no`；两次读取同名收据只发生四次原始 HTTP 桩调用，没有重发。

这些规则只依赖节拍、实际合格窗口和身份确认状态，不匹配模板、角色、台词、主题词或影片时间点，因此适用于不同参考模板。身份调整保留了现有保守降级和最终观察授权边界，终止条件调整则有真实可用候选及完整缺口传递的回归验证依据。

提供的分析中，部分缺口被提前终止、`null` 被断言丢弃这两项得到证据支持。证据虽列出 12 条空断言，但只有其中一条的完整失败回复可核对，不能据此认定其余 11 条同因，更不能推断它们会补齐 b1。所有低置信度 skim 也不足以证明素材不存在；因此没有增加 `verified_absent` 判定或二次检索，没有新增自动重试、重发或模型调用机制。

已复制四份完整 `response.json` 和能完整解析的 b1/b2 报告片段到 `tests/fixtures/question_first/`，并附来源说明。原证据的 questions/report 被截断，夹具没有补造缺失部分。新增四个用例在修改生产代码前全部失败；其中非法身份用例只变异真实回复的一个字段。修改后 Question-first 测试结果为 `194 passed, 1 skipped in 3.24s`。

验收使用指定命令。默认 uv 缓存目录不可写，执行前设置了 `UV_CACHE_DIR=/private/tmp/uv-pfix-cache`、`UV_OFFLINE=1`，其余命令保持原样。下面粘贴真实输出节选，完整输出在对应临时文件中。

修改前的定向回归（[完整输出](/private/tmp/qfs_e2e_red.txt)）：

```text
FAILED tests/test_question_first.py::test_pipeline_injects_search_zero_then_runs_normal_observations[evidence_partial]
FAILED tests/test_question_first.py::test_evidence_close_no_with_null_anchor_is_retained_without_resend
FAILED tests/test_question_first.py::test_evidence_null_identity_only_supplies_partial_unconfirmed_candidate
FAILED tests/test_question_first.py::test_evidence_invalid_identity_has_shared_repair_contract_without_resend
4 failed, 191 deselected in 0.99s
```

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
9 failed, 1578 passed, 104 skipped, 24 errors in 15.28s
```

退出码为 1，九项 FAILED 与指定基线完全一致。另有 24 项 `tests/test_tts_http.py` 初始化错误，均为沙箱拒绝本机 socket 监听的 `PermissionError`。为核实来源，额外从原始 HEAD `98e9427` 只读归档到临时目录，用同一命令执行全量测试：

```text
9 failed, 1574 passed, 104 skipped, 24 errors in 15.19s
```

这 24 个错误的测试项与修改后逐项相同。没有为使测试通过而修改网络检查、跳过规则、依赖或不相关业务逻辑。[修改后完整输出](/private/tmp/qfs_e2e_pytest.txt)，[原始 HEAD 完整输出](/private/tmp/qfs_e2e_pristine_pytest.txt)。

```sh
PYTHONPATH=src /Users/Admin/.local/bin/uv run python -m omni_story.library.replay_check /Users/Admin/Desktop/agentic_video_all/.agent-team/replay_data --json /private/tmp/replay_e2e.json
```

```text
task | ok | regression | fixed | still_failing | unsupported
TOTAL | 143 | 6 | 21 | 33 | 0
```

逐项比较指定回放基线的实际输出：

```text
pytest FAILED outside supplied baseline: []
Pristine HEAD / changed worktree setup errors: 24 / 24; same test cases
replay protected rows: 164
replay protected-row regressions: []
replay category changes: []
```

回放退出码为 1，因为仍报告基线已有的六条 regression；所有行的分类与基线一致。[回放 JSON](/private/tmp/replay_e2e.json)，[完整输出](/private/tmp/qfs_e2e_replay.txt)。

```sh
PYTHONPATH=src /Users/Admin/.local/bin/uv run python scripts/fault_check.py /Users/Admin/Desktop/agentic_video_all/.agent-team/replay_data --json /private/tmp/fault_e2e.json
```

```text
| 类别 | 命中 |
| E1 | 0 |
| M1_crash | 0 |
| M2_json | 0 |
| M2_crash | 0 |
| R1 | 0 |
```

这五项均满足验收条件。脚本退出码为 1，因为完整审计仍报告其他类别的问题，未将其作为本次修复范围。[故障检查 JSON](/private/tmp/fault_e2e.json)，[完整输出](/private/tmp/qfs_e2e_fault.txt)。

```sh
git diff --stat
```

```text
 src/omni_story/library/pipeline.py       |   8 +-
 src/omni_story/library/question_first.py |  36 ++++++---
 tests/test_question_first.py             | 122 ++++++++++++++++++++++++++++---
 3 files changed, 144 insertions(+), 22 deletions(-)
```

以上是实际 `git diff --stat`，不包含未跟踪的五份 JSON 夹具、夹具来源说明及本报告。进入工作树时已存在的未跟踪 `uv.lock` 仍保留；没有暂存或提交。本次只进行了离线验证，没有重跑收费或远程创意任务，也未宣称实际成片已经通过质量评审。
