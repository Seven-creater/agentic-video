**本次尚未修复：现有证据不足以确认作业失败的底层原因。**

分支为 `fix/reference-value-understanding`，HEAD 为 `412b4db`。未修改生产代码、未暂存、未提交；仅新增本中文核查报告。原有未跟踪文件 `uv.lock` 保留。

已独立核对 `evidence_04_少帅下飞机_11.txt`、`analysis_12.md`、
[黄金路径](../golden_path_heihu_98e9427.md)、前次运行 #10 的真实响应及当前源码。
指定证据包含 `run_outcome.json`、`chain_failure.json`、traceback 和调用状态汇总；
没有本次调用的原始失败详情、队列响应、agent 日志或 HTTP 日志。
搜索本地 `.agent-team` 以及项目目录后，也未找到属于本次运行的这些诊断文件。

**可以证实的直接原因与分析中的推断须分开。**

`glm_001_reference` 在 `opencode_provider._submit` 中未取得被接受的成功回复，
调用被记为 `failed_known`，随后抛出
`LibraryStopped('opencode_vision_job_failed_no_retry:glm_001_reference')`。
失败发生在 `pipeline.py:996` 的 `_submit` 内，尚未进入解析和校验回复的 `try` 块
（`pipeline.py:1001`）。因此本次没有证据证明参考内容、策略或 value 字段触发了拒绝。
也确实尚未检索影片库。

| `analysis_12.md` 的判断 | 代码及证据支持的边界 |
| --- | --- |
| `failed_known` 表明进程正常退出 | `opencode_provider.py:339` 只将 `returncode == -1` 视为超时未知；其他非零退出码也能进入 `failed_known`。没有本次 `exit.json`，不能断言退出码为 0。 |
| 0 token 证明没有请求或没有任何回复 | `state.py:278` 初始化 `usage={}`，只有成功路径 `complete_call` 填入用量；`fail_call` 不记录 HTTP 用量。`state.py:375` 汇总这些字段，并明确标注 `captured_responses_only_unknown_charges_not_zero`。0 不能证明没有 HTTP 请求、错误响应或费用。 |
| 作业/传输层吃掉了回复 | `opencode_provider.py:335-360` 同时覆盖无队列响应、队列错误和 HTTP 未恢复成功内容等情形。现有终态无法区分这些原因。 |
| #10 的 HTTP 恢复说明队列交接失灵 | #10 确实标有 `recovered_from_original_http=true`，但代码始终优先选 HTTP 捕获结果；该标记本身不能证明队列异常。 |
| 没有自动重提是本次代码根因，应加一次重试 | 这只是建议的恢复策略，并未定位导致本次作业失败的缺陷；而且直接违反本任务“无自动重试或重发”的约束。未采用。 |

黄金路径的参考回复已收到，随后通过参考字段、策略三键及枚举检查；
该路径使用 `reinterpret/mixed/action`。前次运行 #10 已收到的 `mixed/music` 组合
则暴露了音频能力校验缺口，现有 `creative_reference_strategy_contract` 已对其修复，
提示词与校验器共享同一 helper。已核对现存
`tests/fixtures/reference_music_capability/response.json` 与 #10 证据中的真实 envelope 完全相等。

运行 #11 在任何参考回复校验之前就停止；修改上述共享合同不能让一个没有可用回复的调用通过。
目前没有可依据黄金路径推广、且能修复这次失败的已证实规则缺陷。
因此未增加模板特例、未放宽任何门禁、未把作业错误伪装成可修复的模型内容 ValueError，
也未改变 library 模式或重发行为。

没有新增测试或 fixture：本次证据不包含可用于重现底层失败的原始回复/诊断文件。
凭终态伪造 `{returncode: 0}`、空 HTTP 日志或“首次失败、第二次成功”的 fixture
不能满足“使用真实文件，且每个新测试修改前失败”的要求。已有 #10 fixture 不需要重复复制。

要继续定位，需要同一次运行的 `calls/glm_001_reference/failure.json`、
`calls/glm_001_reference/agent/exit.json`、`agent/events.jsonl`、`agent/mcp_server.log`，
任务根目录的 `mcp_http*.jsonl`，以及存在时的
`mcp_queue/glm_001_reference.response.json`。未产生的文件也需明确注明不存在。
已请求这些文件的本地路径或脱敏内容；本报告写入时尚未收到。

**验收命令及真实输出。**

默认 uv 缓存目录受沙箱写权限限制，三个命令均改用
`UV_CACHE_DIR=/private/tmp/uv-e2e-cache`。pytest 的在线依赖解析另因网络权限失败，
最终设置 `UV_OFFLINE=1`，使用已缓存依赖执行。没有改动命令主体或测试代码。

```sh
export UV_CACHE_DIR=/private/tmp/uv-e2e-cache UV_OFFLINE=1
PYTHONPATH=src /Users/Admin/.local/bin/uv run --no-project --with pytest --with numpy python -m pytest -q -rf tests
```

退出码 1；真实输出末尾：

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
9 failed, 1601 passed, 104 skipped, 24 errors in 15.25s
```

实际比较脚本输出：

```text
FAILED subset of baseline: True
FAILED count: 9
New failures: []
```

9 个 FAILED 全在 `baseline_2879e5c.txt` 内。另有 24 个 setup ERROR，
逐项均为本地测试 HTTP server 绑定 socket 被沙箱拒绝：
`PermissionError: [Errno 1] Operation not permitted`。这不是全绿测试结果。
完整输出：[pytest_e2e.txt](/private/tmp/pytest_e2e.txt)。

```sh
export UV_CACHE_DIR=/private/tmp/uv-e2e-cache
PYTHONPATH=src /Users/Admin/.local/bin/uv run python -m omni_story.library.replay_check /Users/Admin/Desktop/agentic_video_all/.agent-team/replay_data --json /private/tmp/replay_e2e.json
```

退出码 1；真实汇总行：

```text
TOTAL | 143 | 6 | 21 | 33 | 0
```

列顺序为 `ok | regression | fixed | still_failing | unsupported`。
按 `(task, stage, call_id)` 对比基线 `replay_baseline_98e9427.json` 的实际输出：

```text
replay baseline summary: {'ok': 143, 'regression': 6, 'fixed': 21, 'still_failing': 33, 'unsupported': 0}
replay current summary: {'ok': 143, 'regression': 6, 'fixed': 21, 'still_failing': 33, 'unsupported': 0}
baseline ok/fixed rows: 164
new regressions or missing rows: 0
rows: []
```

既有 6 条 regression 仍在；基线 164 条 `ok/fixed` 没有变成 `regression/still_failing`，没有缺失记录。
完整输出：[replay_e2e.txt](/private/tmp/replay_e2e.txt)，
JSON：[replay_e2e.json](/private/tmp/replay_e2e.json)。

```sh
export UV_CACHE_DIR=/private/tmp/uv-e2e-cache
PYTHONPATH=src /Users/Admin/.local/bin/uv run python scripts/fault_check.py /Users/Admin/Desktop/agentic_video_all/.agent-team/replay_data --json /private/tmp/fault_e2e.json
```

退出码 1（审计还报告了其他类别的发现）；指定五项的真实输出：

```text
| E1 | 0 |
| M1_crash | 0 |
| M2_json | 0 |
| M2_crash | 0 |
| R1 | 0 |
```

完整输出：[fault_e2e.txt](/private/tmp/fault_e2e.txt)，
JSON：[fault_e2e.json](/private/tmp/fault_e2e.json)。

```sh
git diff --stat
```

实际 stdout 为空，退出码 0。生产代码及已跟踪文件无 diff；本报告为新增未跟踪文件，
因此不会计入该命令。验收结果只说明当前代码相对既有基线没有上述退化，不能证明运行 #11 已修复。
