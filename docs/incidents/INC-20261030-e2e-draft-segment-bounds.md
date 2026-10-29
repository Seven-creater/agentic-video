**已修复规划期漏检台词越界，以及规划与渲染使用不同时间轴的问题；未提交。**

分支：`fix/reference-value-understanding`。独立核对了
`evidence_18_粉色娇嫩_12.txt`、只读分析 `analysis_13.md`、
[黑虎阿福黄金路径](../golden_path_heihu_98e9427.md)及当前源码。
原有未跟踪文件 `uv.lock` 和 `INC-20261030-e2e-reference-job-evidence-gap.md` 保留不动。

证据明确记录：最后收到 `glm_188_plan_0_repair`，粗剪渲染后在
`build_rough_context → validate_story_context` 因
`story_context:draft_outside_real_segments` 停止。旧代码在规划时只检查
`end_s-start_s` 的统计下限，没有落实提示词已有的“窗口须落在所挂首段至末段内”约束。
因此一个时长足够、位置错误的窗口仍能被接受，直到模型规划阶段结束后才被拒绝。

分析中的时间轴分歧也确实存在：旧 `segment_output_timeline` 连续累计秒数；
渲染器分别对运动帧数和尾部定格帧数取整后累计整数帧。
例如证据第三段长 2.5 秒，测试明确将 speed 变异为 1.008 后，名义时长为
2.480158730… 秒，30 fps 渲染只有 74 帧，即 2.466666666… 秒。
按名义终点填写的台词或临近尾端的起声锚点会越出实际边界。
这是确定性算术差异，不需要扩大 `1e-6` 容差解决。

**分析中的结论边界。** 证据第 115 行的完整回复被截断在 `seg_4`，没有
`audio_draft`，也没有完整 `plan_0.json` 或 `render_result.json`；本地未找到该次运行的完整产物。
可以核实 `seg_3` 从 1273.0–1276.0 改为 1273.5–1276.0，缩短 0.5 秒。
但不能确认具体哪句沿用了旧时间，也不能声称本次一定由帧量化触发。
可见前三段在 30 fps 下全部帧对齐，不能把“黄金路径恰好帧对齐”作为两次运行的充分差异解释。
分析称异常没有可操作字段也不准确：原来的后置异常已有 `violation`；真正遗漏的是规划期检查。
未采纳其渲染后 clamp 建议：现有证据没有证明越界仅为亚帧偏差，自动裁窗可能掩盖真实错位。

黄金路径在规划期已通过 `audio_line_window_below_minimum` 反馈修正台词预算；
其文档也说明根计划原文和是否带警告继续均未记录。此次扩展的是这条通用的台词窗口规则：
在接受计划前，同时验证“窗口足够长”和“窗口处于所挂片段的输出范围内”。
没有修改参考理解、选角、候选质量或其他与本次停机无直接因果关系的规则。

`line_windows.py` 中提示词和校验器继续共享 `draft_line_window_details`、窗口分配器及推导说明。
明细现在同时给出允许的起止边界；提供 fps 时，分配器使用渲染器共享的
`output_frame_counts`，分别取整运动与定格帧数，再累计输出时间。
粗剪传入计划 fps，精剪传入其实际输出 FPS；构建故事上下文时直接校验渲染产物的真实范围。

新增拒绝为 `ValueError('plan:audio_line_window_outside_segments')`，携带
`violation.path/value/expected/repair_hint`、全部越界行及实际片段范围。
越界属于结构错误，无法通过已有的统计下限警告降级路径放行。
保留原有后置门禁和 `1e-6` 容差，没有新增自动重试或重发，也没有调用在线模型、TTS 或重新运行付费创作。

规则只依赖任意 EDL 的源区间、speed、freeze、fps、片段引用与台词时间，
不依赖模板、人物、台词内容或影片位置。无 `audio_draft` 的 library 路线不增加检查；
携带 `audio_draft` 的调用共享相同缺陷，适用同一规则。
渲染器的帧计算公式保持原样，仅提取为共享函数。
原有 0.45 秒窗口测试改为断言其实际的 13/30 秒，因为该源区间在 30 fps 下确实只有 13 帧；
统计下限常数未变。

**证据与回归测试。** [fixture](../../tests/fixtures/e2e_draft_outside_segments.json)
逐字段复制证据中两版回复的完整前三段及 `run_outcome.json`，注明截断边界。
[新增测试](../../tests/test_draft_segment_bounds.py)中的台词、窗口、速度和定格变异均为显式探针，
没有补造缺失的原始回复。覆盖修订后的过期窗口、提前越界、24/30/60 fps 时间轴与提示词一致性、
粗剪规划入口的显式终点和锚点越界、精剪入口，以及修正后通过真实渲染上下文校验。

对 `git archive HEAD src` 导出的旧源码运行同一份新增测试，9 个用例全部失败。
旧源码加真实 EDL、探针窗口的复现实际输出：

```text
OLD preflight: accepted probe [4, 7]
Rendered seg_3: 4.0 6.5
OLD post-render: story_context:draft_outside_real_segments
```

修复后相关测试集实际输出：

```text
........................................................                 [100%]
56 passed in 1.68s
```

该结果证明已修复能导致本次终态的代码缺口；没有完整原始台词，尚不能逐句重放本次整跑，
也不将这些离线验收表述为已完成一次新的在线交付。

**验收命令与真实输出。** 默认 uv 缓存目录不可写，使用已有可写缓存，并禁止依赖联网解析：

```sh
export UV_CACHE_DIR=/private/tmp/uv-e2e-cache UV_OFFLINE=1
```

```sh
PYTHONPATH=src /Users/Admin/.local/bin/uv run --no-project --with pytest --with numpy python -m pytest -q -rf tests
```

退出码 1；输出末尾原文：

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
9 failed, 1610 passed, 104 skipped, 24 errors in 15.55s
```

9 个 FAILED 均属于指定基线。另有 24 个 setup ERROR，逐项均为本地 HTTP 服务绑定 socket
被沙箱拒绝：`PermissionError: [Errno 1] Operation not permitted`。不是全绿结果。
完整输出：[reference_bounds_pytest.txt](/private/tmp/reference_bounds_pytest.txt)。

```sh
PYTHONPATH=src /Users/Admin/.local/bin/uv run python -m omni_story.library.replay_check /Users/Admin/Desktop/agentic_video_all/.agent-team/replay_data --json /private/tmp/replay_e2e.json
```

退出码 1；原有 6 条 regression 仍存在。实际汇总（列为 ok / regression / fixed / still_failing / unsupported）：

```text
TOTAL | 143 | 6 | 21 | 33 | 0
```

完整输出：[reference_bounds_replay.txt](/private/tmp/reference_bounds_replay.txt)，
结果：[replay_e2e.json](/private/tmp/replay_e2e.json)。

```sh
PYTHONPATH=src /Users/Admin/.local/bin/uv run python scripts/fault_check.py /Users/Admin/Desktop/agentic_video_all/.agent-team/replay_data --json /private/tmp/fault_e2e.json
```

退出码 1（仍有其他类别的审计发现）；指定五项原文：

```text
| E1 | 0 |
| M1_crash | 0 |
| M2_json | 0 |
| M2_crash | 0 |
| R1 | 0 |
```

完整输出：[reference_bounds_fault.txt](/private/tmp/reference_bounds_fault.txt)，
结果：[fault_e2e.json](/private/tmp/fault_e2e.json)。

逐条对比基线的实际输出：

```text
FAILED subset of baseline: True
FAILED count: 9
New failures: []
replay baseline summary: {'ok': 143, 'regression': 6, 'fixed': 21, 'still_failing': 33, 'unsupported': 0}
replay current summary: {'ok': 143, 'regression': 6, 'fixed': 21, 'still_failing': 33, 'unsupported': 0}
baseline ok/fixed rows: 164
new regressions or missing rows: 0
rows: []
E1: 0
R1: 0
M1_crash: 0
M2_json: 0
M2_crash: 0
```

比较以 `(task, stage, call_id)` 定位记录：基线 164 条 ok/fixed 均保留，无新增退化或缺失。

```sh
git diff --stat
```

实际输出：

```text
 src/omni_story/library/active_finecut.py |  2 +-
 src/omni_story/library/contracts.py      |  6 +--
 src/omni_story/library/line_windows.py   | 63 +++++++++++++++++++++++---------
 src/omni_story/library/pipeline.py       |  2 +-
 src/omni_story/library/prompts.py        |  2 +-
 src/omni_story/library/render.py         | 10 +++--
 src/omni_story/library/story_context.py  |  3 +-
 src/omni_story/library/story_finecut.py  |  4 +-
 tests/test_mask_and_line_window.py       |  3 +-
 9 files changed, 64 insertions(+), 31 deletions(-)
```

`git diff --stat` 不包含未跟踪文件。本次还新增了上述 fixture、测试文件及本报告；未暂存、未提交。
`git diff --check` 无输出、退出码 0。旧源码测试输出：
[draft_bounds_before.txt](/private/tmp/draft_bounds_before.txt)。
