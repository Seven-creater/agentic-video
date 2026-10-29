本次已修复 line-window 下限与 plan 阶段交互导致的致命耗尽，未提交、未暂存。分支仍为 `fix/reference-value-understanding`，HEAD 仍为 `faa499b`。

真实故障与窗口推导：

从 `/Users/Admin/Desktop/agentic_video_all/.agent-team/qfs_fixture/plan_faa499b/` 的实际 response 提取了两份 repair 的完整 8 段布局和 7 句草稿，保存为 `tests/fixtures/plan_faa499b_line_windows.json`。两句均挂在 `seg_1`，其源范围为 825.5–827.5 秒、speed=1、无尾帧延长，因此输出时长为 2 秒。

| 回复 | 行 | spoken chars | len(text)，含标点 | 推导窗口 | 所需下限 |
| --- | --- | ---: | ---: | ---: | ---: |
| glm_187_plan_0_repair | l0：武林第一嘴，招式大过天。 | 10 | 12 | 2×12/26 = 0.923077 | 1.80 |
| glm_187_plan_0_repair | l1：看我绝招——'巨拳轰天锤'！ | 9 | 14 | 2×14/26 = 1.076923 | 1.62 |
| glm_188_plan_0_repair2 | l0：招式大过天。 | 5 | 6 | 2×6/18 = 0.666667 | 0.90 |
| glm_188_plan_0_repair2 | l1：看我绝招——巨拳轰天锤！ | 9 | 12 | 2×12/18 = 1.333333 | 1.62 |

分配权重采用 `len(text)`，下限采用 `sum(c.isalnum() for c in text)`；缩短首句会改变同组两句的分配。原逻辑没有把这些推导量告诉模型，且只报告首个失败行。

修改内容：

- `line_windows.py` 统一提供输出时间轴、逐行明细和单句推导说明。校验仍调用实际 `draft_line_windows` 得到窗口，prompt 的计算示例、已有草稿数值及错误明细均来自相同 helper。
- 计划失败的 `violation.offending_lines` 和 `diagnostics.line_window_warnings` 列出全部违规行，包括索引、ID、原文、spoken chars、含标点分配字数、起止时间、窗口、下限、segment IDs、每段输出起止时间和时长、推导说明及具体修复提示。repair feedback 保留全部行，不受普通反馈的 30 条/200 字截断影响；40 行测试覆盖这一点。
- 修复提示明确给出：改挂更长片段、在已看范围内延长片段、移到更早或更晚片段、填写显式窗口、缩短台词。粗剪、StoryFinecut 和 active finecut prompt 都包含同一推导说明。
- 已有 plan 合约允许 audio_draft 同时填写 `start_s/end_s`，并验证其为非负有限数值；`draft_line_windows` 会优先采用它们。prompt 与反馈现明确说明使用剪辑后输出时间轴上的绝对秒数，应位于所挂首段起点至末段终点。此次没有扩展 schema。
- 到达现有 repair limit 后，仅对已知 plan 阶段调用 `finecut_continuation._known_protocol_failure` 验证收到的原始调用、修复链和请求/响应哈希。随后只在本地暂缓这一条下限，重新执行整份原 validator；任何其他合约错误或工作流 guard 都保留原来的 `model_protocol_repair_exhausted:<stage>`。不追加模型调用、不重发请求。
- 仅剩此规则时接受最后回复，将全部 `line_window_warnings` 写入计划，并以 `line_window_exhaustion` artifact 绑定失败调用链；`run_outcome.json` 从经哈希校验的 artifact 收集警告。原 response、protocol_failure 保留，未伪造 parsed.json。支持 `plan_N`、嵌套 `finecut_N` 以及 StoryFinecut plan/revise 阶段。
- 已接收警告的计划可建立 story context，进入原有实测 TTS、一次 timing revision、仍无法容纳则通过 `dropped_lines` 交付 partial 的后备路径。警告本身不替代音频测量或内容交付判断。
- 下限仍为 `max(0.7, 0.18 * spoken_chars)`。相同 text、相同 voice 的有效 received-TTS 回执仍覆盖估算下限；其匹配与过滤规则未改变。

完整真实调用链的本地重放使用复制的三份完整回复、原 request 上下文及生产 plan validator，模型传输由测试 harness 代替。实际输出：

```text
real_reply_chain: accepted final reply; model_calls=3
warning_lines: [('l0', 0.666667, 0.9), ('l1', 1.333333, 1.62)]
first_repair_feedback: [('l0', 0.923077, 1.8), ('l1', 1.076923, 1.62)]
resume: identical=True; model_calls=3
all_paid_failures_preserved: True
run_outcome.warning_lines: ['l0', 'l1']
```

这不是服务器端重新执行视频任务；真实最后回复已在本地通过耗尽接收路径。完整 request 校验还确认，第一份 repair 暂缓下限后仍有身份锚点错误，最后一份 repair 已修复该错误，剩余只有两条窗口下限违规。

测试与验收：

所有命令只额外设置 `UV_CACHE_DIR=/private/tmp/question-first-search-uv-cache` 和 `UV_OFFLINE=true`，使用本机已有缓存。默认 uv 缓存不在可写范围，在线下载也受沙箱限制。

新增回归测试在 `git archive faa499b src` 导出的独立源码上运行，未切换或修改工作树。原版本与当前版本的实际摘要为：

```text
faa499b: 10 failed, 5 passed in 0.68s
当前版本: 15 passed in 1.53s
相关测试组: 247 passed, 24 skipped in 4.18s
```

原版失败的 10 项覆盖真实两份回复明细、粗剪与各精剪计划耗尽续跑、显式窗口提示、40 行完整反馈和嵌套精剪计划。5 项对照测试确认：其他违规（包括下限之后才暴露的违规与工作流 guard）仍然停止。续跑测试还核对零追加请求、原失败记录保留、artifact 不重复、run outcome 警告、实测 TTS、`rough_audio_timing_1` 及 dropped_lines/partial。

全量命令：

```sh
PYTHONPATH=src /Users/Admin/.local/bin/uv run --no-project --with pytest --with numpy python -m pytest -q -rf tests
```

实际输出末尾：

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
9 failed, 1574 passed, 104 skipped, 24 errors in 15.71s
FAILED subset of baseline_2879e5c: True
New FAILED: []
Removed FAILED: []
```

9 个 FAILED 与指定基线完全一致。全量测试并非全绿：另外 24 个 setup ERROR 均来自 `tests/test_tts_http.py:128` 的本地 TLS HTTP fixture，沙箱禁止 `socket.bind`，异常为 `PermissionError: [Errno 1] Operation not permitted`。没有改动测试以跳过这些限制。完整输出：[pfix_line_windows_full.txt](/private/tmp/pfix_line_windows_full.txt)。

Replay 命令：

```sh
PYTHONPATH=src /Users/Admin/.local/bin/uv run python -m omni_story.library.replay_check /Users/Admin/Desktop/agentic_video_all/.agent-team/replay_data --json /private/tmp/replay_w1.json
```

实际比较结果：

```text
before: {'ok': 143, 'regression': 6, 'fixed': 21, 'still_failing': 33, 'unsupported': 0}
after: {'ok': 143, 'regression': 6, 'fixed': 21, 'still_failing': 33, 'unsupported': 0}
classification/error changes: 0
```

203 行中没有新增回归或分类/错误码变化；6 条已知 regression 仍全部为 `audio_line_window_below_minimum`：

| 任务末级目录 | call_id | 窗口秒 | 下限秒 |
| --- | --- | ---: | ---: |
| v7_ab014be | glm_005_plan_0_repair2 | 6.0 | 6.84 |
| v5_8a71956 | glm_003_rough_audio_timing_1_repair | 2.0 | 2.16 |
| v4_6269974 | glm_005_plan_0_repair2 | 5.0 | 5.58 |
| v3_838f636 | glm_005_plan_0_repair2 | 5.0 | 7.2 |
| v3_838f636 | glm_014_chain_e2e_v1_fine_plan | 9.0 | 9.68 |
| full_tpl_xiaobiesan_20261026_v1 | glm_008_plan_0_repair | 2.0 | 2.16 |

变更仅增加计划窗口失败的诊断明细。Replay 继续执行严格 validator；耗尽后接收发生在执行层。完整结果：[replay_w1.json](/private/tmp/replay_w1.json)。

Fault check 命令：

```sh
PYTHONPATH=src /Users/Admin/.local/bin/uv run python scripts/fault_check.py /Users/Admin/Desktop/agentic_video_all/.agent-team/replay_data --json /private/tmp/fault_w1.json
```

从实际 JSON 提取的验收数值：

```text
{"E1": 0, "R1": 0, "M1_crash": 0, "M2_json": 0, "M2_crash": 0, "high": 0}
```

全部要求项均为 0。脚本因其他审计项而返回退出码 1，未把该退出码改写为成功；上述指标直接取自实际 findings。完整结果：[fault_w1.json](/private/tmp/fault_w1.json)。

`git diff --stat` 实际输出：

```text
 src/omni_story/library/active_finecut.py       |   7 +-
 src/omni_story/library/contracts.py            |   8 +-
 src/omni_story/library/finecut_continuation.py |  29 +++++++
 src/omni_story/library/line_windows.py         | 115 +++++++++++++++++++++----
 src/omni_story/library/pipeline.py             |   8 +-
 src/omni_story/library/prompts.py              |   4 +-
 src/omni_story/library/protocol_feedback.py    |   6 +-
 src/omni_story/library/run_outcome.py          |  10 ++-
 src/omni_story/library/story_context.py        |   5 ++
 src/omni_story/library/story_finecut.py        |   4 +-
 10 files changed, 169 insertions(+), 27 deletions(-)
```

上述命令只统计已跟踪文件。另外新增且未暂存：`tests/test_plan_line_window_exhaustion.py`、`tests/fixtures/plan_faa499b_line_windows.json` 及本报告。开始时已有的未跟踪 `uv.lock` 保留。`git diff --check` 通过，暂存区为空，HEAD 未改变。
