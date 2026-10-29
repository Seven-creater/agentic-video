已在 `fix/reference-value-understanding` 工作树修复两个由证据确认的问题，未提交、未暂存。开始时已存在的未跟踪 `uv.lock` 保持原样。

逐项核对了 `analysis_2.md`、`evidence_01_不烧心_1.txt` 和实际代码。第 164 个调用是首个 fine 窗口；证据记录 163 个 received 和 1 个 failed_known，异常从 `OpenCodeMCP._submit` 传播至 fine 循环。该循环原本只跳过协议修复耗尽的窗口，导致一个已知未成功的独立观察作业终止整个 creative 流程。证据没有该作业的 HTTP/events/failure 明细，不能确定供应商返回了什么状态，也不能把资源警告认定为直接原因。

另一个可复现问题是 `glm_163_qfs_close_18` 的 `tts_needed: null` 被裸断言拒绝。真实回复是 `verdict=no`、`also_satisfies=[]`，拒绝结论完整；对照回复 161、162 使用 `[]`，均通过。代码检索也确认 `tts_needed` 在此模块之外没有决策消费者，空列表本来就合法。将明确的 null 规范为 [] 只表示“没有提供 TTS 补充内容”，不生成台词、不提高候选等级、不改变角色、风格、时间与可见性条件。

实际修改如下：

- `pipeline.py` 仅在 workflow 为 `reference_rough_skill_v1`、错误码匹配、台账 call_id 匹配、状态为 failed_known、调用名属于当前 fine 作业或其协议修复时，记录窗口失败并继续其他窗口。记录包括来源 SHA、原片范围、请求 SHA、call_id 和 `not_watched_no_EDL_permission`。失败窗口不会进入 `watched_windows`，失败记录写入参考上下文，供后续规划与审查读取。
- 已记录的同 digest 失败仍由真实 `_submit` 拒绝重发；fine 循环只消化已确认的失败。uncertain/submitted、预算错误、完整性错误和其他作业错误继续传播。库模式不进入此恢复分支。原有 `no_watched_window_available_for_planning` 闸门已覆盖全部窗口失败，无需增加或放宽闸门。
- `question_first.py` 新增 `close_tts_contract()`，提示词与校验器共用 expected/repair_hint。null 规范为 []；缺失字段、字符串、数字、对象或非字符串数组仍拒绝，异常为 ValueError，带 `violation={path,value,expected,repair_hint}`。收到无效回复后沿用现有落账与诊断路径，不自动重发。

这两个修复只依赖工作流、独立观察窗口、台账状态和字段语义；生产代码不含该模板、人物、台词、影片片段或失败调用 ID。所有模板都经相同的窗口观察与 close schema 路径处理，因此修复可泛化。

没有照搬只读分析中的其他建议：已有无素材闸门；当前证据不足以授权 uncertain 结果的跳过；失败发生在首个 fine 窗口，尚无可验证的成片可作部分交付，因此不修改 run_outcome；也不放宽 who、时间等其他字段。没有新增自动重试、重发或自动重启，传输提供者和 MCP guard 未改动。

真实证据 JSON 位于 `tests/fixtures/e2e_fine_failure_20261029/`，包含三条完整 close 回复及 run_outcome、chain_failure，来源与测试中的合成范围写在同目录 README。fine 原始请求未随证据提供，测试以真实错误码与分类绑定合成独立窗口和真实 LibraryState 台账，不伪造供应商响应。

新增 5 个回归用例，覆盖三条真实 close 回复的落账重用、null 规范化、共享提示契约、可修复的类型错误、已知失败后继续其他窗口、台账重入不重发、失败范围传入规划及全失败闸门；同一用例内还验证未知状态、无关错误和库模式不会获得跳过权限。在临时源码副本中用 HEAD 的两个原始文件运行最终测试，真实输出为：

```text
5 failed in 0.39s
```

修复后的相同测试真实输出为：

```text
.....                                                                    [100%]
5 passed in 0.19s
```

以下验收均实际执行。由于默认 uv 缓存目录不允许写入，且环境禁止联网，先设置已有离线缓存；不修改项目依赖：

```sh
export UV_CACHE_DIR=/private/tmp/uv-pfix-cache
export UV_OFFLINE=true
```

```sh
PYTHONPATH=src /Users/Admin/.local/bin/uv run --no-project --with pytest --with numpy python -m pytest -q -rf tests
```

真实输出末尾（退出码 1）：

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
9 failed, 1583 passed, 104 skipped, 24 errors in 15.02s
```

9 个 FAILED 与给定基线完全一致。24 个 ERROR 均为本地 HTTP/TLS 测试在初始化监听 socket 时遇到 PermissionError；在相同环境对未修改 HEAD 排除新增测试重跑，真实输出为：

```text
9 failed, 1578 passed, 104 skipped, 24 errors in 14.76s
```

逐例对照结果为：

```text
FAILED outside supplied baseline: []
ERROR setup cases, current/pristine: 24 24
ERROR setup cases outside pristine: []
Baseline ok/fixed rows newly regression/still_failing/missing: []
Replay rows baseline/current: 203 203
```

```sh
PYTHONPATH=src /Users/Admin/.local/bin/uv run python -m omni_story.library.replay_check /Users/Admin/Desktop/agentic_video_all/.agent-team/replay_data --json /private/tmp/replay_e2e.json
```

真实汇总输出（退出码 1，因为归档已有 regression）：

```text
task | ok | regression | fixed | still_failing | unsupported
--- | ---: | ---: | ---: | ---: | ---:
TOTAL | 143 | 6 | 21 | 33 | 0
```

当前和基线的 203 行分类一致；原为 ok/fixed 的 164 行无新增 regression/still_failing，也无缺行。

```sh
PYTHONPATH=src /Users/Admin/.local/bin/uv run python scripts/fault_check.py /Users/Admin/Desktop/agentic_video_all/.agent-team/replay_data --json /private/tmp/fault_e2e.json
```

真实输出中的指定指标（脚本因其余审计发现退出 1，JSON 正常写出）：

```text
| E1 | 0 |
| M1_crash | 0 |
| M2_json | 0 |
| M2_crash | 0 |
| R1 | 0 |
```

本次审计共执行 9984 次验证，其中 E1 3090 次、R1 100 次。其他审计类别仍有发现，未将它们误报为通过或扩大本次修复范围。

```sh
git diff --stat
```

真实输出：

```text
 src/omni_story/library/pipeline.py       | 28 ++++++++++++++++++++++++++++
 src/omni_story/library/question_first.py | 19 ++++++++++++++++---
 2 files changed, 44 insertions(+), 3 deletions(-)
```

`git diff --stat` 不显示未跟踪文件：另新增 `tests/test_e2e_fine_failure.py`、fixture 目录中的 5 个 JSON 与 README，以及本报告；未暂存这些文件。验证输出保存在 `/private/tmp/pfix_e2e_pytest.txt`、`pfix_e2e_pristine_pytest.txt`、`pfix_fine_red.txt`、`pfix_fine_green.txt`、`pfix_e2e_replay.txt`、`pfix_e2e_fault.txt`、`pfix_e2e_comparison.txt`。

未运行真实付费创作或重启失败任务；离线验证证明已知失败的控制流可继续并保留证据缺口，不证明供应商故障消失或已有端到端成片交付。
