C1 选角与风格硬约束修复报告（未提交）

已在 `fix/reference-value-understanding` 修复两处 question-first 质量失败；HEAD 仍为 `a5d692d`。没有提交，也没有暂存文件。原有未跟踪的 `uv.lock` 保留。

新增 [cast.json](/Users/Admin/Desktop/agentic_video_all/.agent-team/wt/pfix/data/visual_library/three_films_v2/cast.json)，包含要求的 18 个真实角色、规范中文名、中英文别名、影片归属，以及按原片 SHA 绑定的二维排除区间。太郎 / 大龙 / 残豹 / Tai Lung 统一为“太郎”。第二部中乌龟大师的闪回与李山的短暂出场也纳入影片归属；[角色出场资料](https://en.wikipedia.org/wiki/List_of_Kung_Fu_Panda_characters)与[官方第二部角色表](https://www.dreamworks.com/movies/kung-fu-panda-2)供复核。区间严格采用用户提供的 film 1 `[34, 165]`、film 2 `[21, 120]`，均记录 `verified_by: "Opus frame samples 2026-10-29"`；没有推测补充中段二维区间。

[film_cast.py](/Users/Admin/Desktop/agentic_video_all/.agent-team/wt/pfix/src/omni_story/library/film_cast.py)按 content ranges 的方式读取并校验文件，将内容与文件 SHA 固定到 `input_lock.configuration.film_cast`。resume 从输入锁取值，即使当前文件被修改或删除，也保持原名单和排除区间。新任务文件缺失、未设置素材库根目录、旧任务缺少锁定名单，均以 `visual_library:cast_missing_or_invalid:...` 明确停止。library 模式不加载名单，不添加风格过滤。

[question_first.py](/Users/Admin/Desktop/agentic_video_all/.agent-team/wt/pfix/src/omni_story/library/question_first.py)现有以下行为：

- questions 提示词展示锁定白名单，明确禁止选用参考视频自身人物；`target_style=3d` 写入配置和 questions。无效选角逐条删除并记入 `dropped_casting`，角色可保留为 `[]`；所有角色均未选角时仅抛出 `ValueError('qfs_questions:no_valid_film_casting')`。保留原回复回执，复跑不重发。
- 选角的名字、别名和影片归属采用白名单值；close-look 的 `who` 和生成的锚点名使用同一名单归一。未选角角色不生成固定锚点，未知人物和非 3D 候选也不能建立锚点。
- skim 选区跳过已知二维区间；close-look 片段扩边后仍重叠二维区间时不送检；最终窗口转换再次检查区间重叠（含两秒扩边），并硬性拒绝 `2d_stylized` / `mixed`。过滤先于 usable / partial 回退选择，二维 usable 不会遮住合法的 3D partial。
- 风格过滤后没有合法窗口的 must-see 节拍写入 `question_first_gaps`；[pipeline.py](/Users/Admin/Desktop/agentic_video_all/.agent-team/wt/pfix/src/omni_story/library/pipeline.py:1622)保存缺口后以 `question_first:material_gap` 停止，不进入补看、计划或渲染。

测试使用用户指定命令；沙箱不允许默认 uv 缓存写入且无法连接 PyPI，因此仅增加以下环境变量，复用已有离线依赖：

```sh
export UV_CACHE_DIR=/private/tmp/qfs-uv-cache
export UV_OFFLINE=true
```

新增行为回归测试将同一份测试和数据文件放入 `git archive a5d692d` 的源码副本执行；旧版本源码未经修改。筛选条件为 `-k 'film_cast or film_style or (pipeline_injects and True)'`，真实结果为：

```text
旧源码 a5d692d：16 failed, 1 passed, 174 deselected in 1.00s
当前源码：
.................                                                        [100%]
17 passed, 174 deselected in 1.61s
```

16 个行为回归用例全部旧版失败、新版通过，涵盖真实黑虎/Hak Foo 回复删除、合法太郎归一、全无效清晰报错、三个 close-look 别名、两种非 3D 风格拒绝、合法 3D partial 回退、两部已知二维区间、文件锁定与 resume、文件缺失、Runner 提示词/锚点接线、pipeline 缺口停止。另一个 library 兼容性用例在旧版和新版都通过。旧测试中允许 mixed / 2D 转窗口的断言已按硬规则调整；跨窗口身份测试和窗口上限测试改用足量 3D fixture，保留原验证强度。

全量测试的真实输出（退出码 1）：

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
9 failed, 1559 passed, 104 skipped, 24 errors in 14.73s
FAILED subset baseline: True
NEW FAILED: []
```

FAILED 与 `/Users/Admin/Desktop/agentic_video_all/.agent-team/out/baseline_2879e5c.txt` 的 9 项完全相同。另有 24 项 ERROR 均发生在本地 HTTP server fixture 的 `socket.bind`，错误为 `PermissionError: [Errno 1] Operation not permitted`，属于当前沙箱端口限制；未修改这些测试。完整输出：[c1_full_tests.txt](/private/tmp/c1_full_tests.txt)。

Replay 的真实输出（退出码 1，来自已知的 6 条回归）：

```sh
PYTHONPATH=src /Users/Admin/.local/bin/uv run python -m omni_story.library.replay_check /Users/Admin/Desktop/agentic_video_all/.agent-team/replay_data --json /private/tmp/replay_c1.json
```

```text
TOTAL | 143 | 6 | 21 | 33 | 0
列顺序：ok | regression | fixed | still_failing | unsupported
full replay rows identical: True
row_count: 203 203
changed_rows: 0
```

与本次修改前的 `a5d692d` 比较，203 行完整结果完全相同，新增回归 0，状态或错误变化 0。6 条已知回归仍全部为 `audio_line_window_below_minimum`：

| 任务尾段（其余为 kuangbiao） | call_id | 字段 | 窗口秒数 / 要求秒数 |
| --- | --- | --- | --- |
| v7_ab014be | glm_005_plan_0_repair2 | $.audio_draft[5] | 6.0 / 6.84 |
| v5_8a71956 | glm_003_rough_audio_timing_1_repair | $.audio_draft[7] | 2.0 / 2.16 |
| v4_6269974 | glm_005_plan_0_repair2 | $.audio_draft[4] | 5.0 / 5.58 |
| v3_838f636 | glm_005_plan_0_repair2 | $.audio_draft[1] | 5.0 / 7.199999999999999 |
| v3_838f636 | glm_014_chain_e2e_v1_fine_plan | $.audio_draft[0] | 9.0 / 9.68 |
| xiaobiesan 原任务 | glm_008_plan_0_repair | $.audio_draft[5] | 2.0 / 2.16 |

原始 JSON：[replay_c1.json](/private/tmp/replay_c1.json)；修改前：[replay_c1_before.json](/private/tmp/replay_c1_before.json)；逐行变化列表：[c1_replay_changes.json](/private/tmp/c1_replay_changes.json)（`[]`）。

Fault check 的真实提取结果如下。脚本退出码 1，因为仍报告其他既有 medium / low 审计项；要求的五个故障类别与 high 均为 0。

```sh
PYTHONPATH=src /Users/Admin/.local/bin/uv run python scripts/fault_check.py /Users/Admin/Desktop/agentic_video_all/.agent-team/replay_data --json /private/tmp/fault_c1.json
```

```json
{
  "E1": 0,
  "R1": 0,
  "M1_crash": 0,
  "M2_json": 0,
  "M2_crash": 0,
  "high": 0
}
```

共 9984 次验证，root causes 为 7 个 medium、1 个 low。完整结果：[fault_c1.json](/private/tmp/fault_c1.json)。这些离线检查没有重新运行真实模型或最终视频渲染，不能据此声称新成片已通过 final_visual_review。

`git diff --check` 无输出。`git diff --stat` 的真实输出为：

```text
 .gitignore                               |   1 +
 src/omni_story/library/pipeline.py       |   2 +
 src/omni_story/library/question_first.py | 124 ++++++++++++----
 tests/test_question_first.py             | 240 +++++++++++++++++++++++++++++--
 4 files changed, 322 insertions(+), 45 deletions(-)
```

普通 `git diff --stat` 不包含未跟踪的新文件。新增文件另列：`data/visual_library/three_films_v2/cast.json`（32 行）、`src/omni_story/library/film_cast.py`（94 行）、本报告。文件均未暂存；没有 commit。
