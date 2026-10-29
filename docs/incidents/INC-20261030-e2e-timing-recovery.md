已在 `fix/reference-value-understanding` 工作树修复两处可复现的状态继承缺陷，未提交、未暂存。没有修改终审通过条件，也没有增加自动重试、重发或付费调用。

1. 证据与代码核对

证据中的 `glm_171` 将首段由 2.0 秒扩至 2.2 秒，首句显式窗口为 0–2.08 秒，回复记录同文本、同音色的 received TTS 实测 2.08 秒。原代码通过规划时长校验后，`story_context._build` 仍无条件继承粗剪的 `dropped_lines/timing_problems/partial_reasons`。后续 `synthesize_draft`、混音和字幕均跳过该句，因此有效的扩窗修复被旧状态吞掉。退出记录中只测到了其余三句，且保留了首句弃句原因，与该执行路径一致。

另有一处同类缺陷未被原分析指出：`StoryFinecut.finish` 先从粗剪汇总 `partial`，再将它写回刚构建的精剪上下文。因此，只修复 `_build` 仍会重新引入旧的部分完成原因。

原分析对终审的结论不能全部采纳。`glm_173` 明确说明起声缓解只是计划，尚未实际听审；它的 `partial/deliver` 不能证明 `glm_174` 发现的跳切已解决。`final_visual_review` 使用的代理由 `visual_story_trial.proxy` 显式移除音轨，属于视觉检查；已有 AV 审核也不能使失败的视觉硬门自动通过。证据中没有原始成片可供本次独立重审，因此不足以支持增加 minor 豁免、已知问题放行或将 `needs_revision` 降级成交付。回素材库和自动再剪属于新增流程，不是本次能证明必须修复的协议错误，且用户禁止增加自动重试。

`audio_measurement.passed=true` 只证明交给混音器的句子通过技术测量；原流程明确允许已弃句的 partial 候选，因此“完整草稿四句、测量三句”本身不足以证明测量函数有 bug。本次修复上游误弃句，让恢复的句子重新进入现有测量流程，没有改变测量阈值。

2. 修改与通用性

`story_context._reconsider_timing_drops` 仅重新审视长度不足导致的弃句。它按本次实际输出时间轴，通过既有 `draft_line_window_details` 计算窗口，并要求同文本、同音色的有效 received TTS 时长确实可容纳；没有回执、回执不匹配、窗口不足或仅有锚点冲突时，保留原排除状态。清理范围限于已解除的弃句及对应时间问题；其他排除、其他 partial 原因和无法解释的 partial 标记保留。新计划已删除的句子也不会继续携带旧的长度不足标记。

`StoryFinecut.finish` 改为合并当前精剪上下文与当前审核的 partial 状态，避免再次引入粗剪旧原因。

从 `required_line_window_s` 提取 `received_line_duration_s`，提示词的 `line_window_prompt`、规划校验器和弃句恢复均使用同一条回执/窗口计算链。原有统计下限、实测优先规则和数值容差均未改变；恢复后仍须通过现有 TTS 文件哈希校验、实际调度、混音测量、AV 审核和视觉终审。没有新增拒绝分支，也没有引入新的异常合同。

生产代码不含模板名称、角色、台词、镜头编号或固定时长。判据只有错误类型、当前输出窗口和绑定回执；更换标识、文本的回归用例也通过。library 模式没有新增行为，唯一涉及的共享回执 helper 是等价提取。

3. 证据夹具与修改前验证

真实 `glm_171`、`glm_173` 回复与 `run_outcome.json` 复制到 `tests/fixtures/e2e_visual_failure_20261029/`，只重新排版。该目录 README 记录证据 SHA-256，并说明粗剪上下文与音频占位文件是测试重建，不冒充原始媒体。

新增 4 个参数化测试实例，分别覆盖弃句恢复和收尾状态回写；同时检查零 TTS 重发、测量包含恢复句、回执匹配、无效回执、窗口不足、锚点冲突和其他 partial 原因。最终版本测试对 `git archive HEAD src` 导出的修改前代码全部失败，失败分别发生在“弃句仍存在”和“旧 partial 原因被写回”的断言。原代码运行 HTTP 测试也出现相同的 24 个本机端口绑定错误：

```text
FAILED tests/test_e2e_timing_recovery.py::test_received_revision_restores_fitted_line_without_tts_and_measures_it[False]
FAILED tests/test_e2e_timing_recovery.py::test_received_revision_restores_fitted_line_without_tts_and_measures_it[True]
FAILED tests/test_e2e_timing_recovery.py::test_finish_uses_current_context_instead_of_reapplying_rough_timing_flags[False]
FAILED tests/test_e2e_timing_recovery.py::test_finish_uses_current_context_instead_of_reapplying_rough_timing_flags[True]
4 failed, 1 passed, 24 errors in 2.14s
```

修改后的新增测试实际输出：

```text
....                                                                     [100%]
4 passed in 0.16s
```

完整修改前日志：`/private/tmp/e2e_timing_before.txt`。

4. 验收实际输出

默认 `uv` 缓存不可写且沙箱不能联网。已将现有缓存复制到 `/private/tmp/uv-e2e-cache`；所有验收命令均在以下环境变量下执行，未修改项目依赖：

```sh
export UV_CACHE_DIR=/private/tmp/uv-e2e-cache
export UV_OFFLINE=1
```

第一条命令：

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
9 failed, 1587 passed, 104 skipped, 24 errors in 15.18s
```

退出码 1；FAILED 集合与 `baseline_2879e5c.txt` 相同，新增 FAILED 为 0。24 个额外 setup error 来自 `test_tts_http.py` 绑定 `127.0.0.1` 时的 `PermissionError: [Errno 1] Operation not permitted`，已用修改前代码复现。没有更改或跳过这些测试，不能将本次全量测试写成全绿。完整日志：`/private/tmp/pytest_e2e.txt`。

第二条命令：

```sh
PYTHONPATH=src /Users/Admin/.local/bin/uv run python -m omni_story.library.replay_check /Users/Admin/Desktop/agentic_video_all/.agent-team/replay_data --json /private/tmp/replay_e2e.json
```

实际输出摘录：

```text
task | ok | regression | fixed | still_failing | unsupported
--- | ---: | ---: | ---: | ---: | ---:
TOTAL | 143 | 6 | 21 | 33 | 0
```

逐行比较 `replay_baseline_98e9427.json` 的 `(task, call_id)`，164 条基线 `ok/fixed` 记录全部保持通过，新增退化为 0；所有 203 条记录的分类均与基线相同。退出码 1 来自基线已有的 6 条 regression，没有新增。完整 JSON 和日志：`/private/tmp/replay_e2e.json`、`/private/tmp/replay_e2e.txt`。

第三条命令：

```sh
PYTHONPATH=src /Users/Admin/.local/bin/uv run python scripts/fault_check.py /Users/Admin/Desktop/agentic_video_all/.agent-team/replay_data --json /private/tmp/fault_e2e.json
```

故障检查实际输出摘录：

```text
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
| M2_miss | 1796 |
| R1 | 0 |
```

所要求的 E1、R1、M1_crash、M2_json、M2_crash 均为 0。脚本因其余审计发现退出 1；完整 JSON 和日志分别为 `/private/tmp/fault_e2e.json`、`/private/tmp/fault_e2e.txt`。

第四条命令及实际输出：

```sh
git diff --stat
```

```text
 src/omni_story/library/line_windows.py  | 11 +++++++----
 src/omni_story/library/story_context.py | 31 +++++++++++++++++++++++++++++++
 src/omni_story/library/story_finecut.py |  4 ++++
 3 files changed, 42 insertions(+), 4 deletions(-)
```

`git diff --stat` 不包含未跟踪文件：本次另新增测试文件、三份 JSON 夹具、夹具说明与本报告；它们均未暂存。原有未跟踪 `uv.lock` 未改动。`git diff --check` 无输出。

本次修复保证有效的精剪扩窗不会再被旧弃句状态静默丢弃；它不证明历史成片的跳切已经合格。未重跑线上创作、未重发任何历史请求，也未将原始 `needs_revision` 裁决改成通过。
