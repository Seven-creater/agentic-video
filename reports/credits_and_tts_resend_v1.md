# 正文范围与 TTS 单次自动重发修复验证报告

分支 `fix/reference-value-understanding`，HEAD `2c288ff231c224f78291fc9fa8c507f2a80e2219`。代码由 GPT 编写，所有改动保留在工作树，未执行提交或其他 Git 写入。

正文范围：新增三个电影的范围数据、严格加载校验、启动前停止及输入锁快照；提示与校验共用 `content_range_for(source)`，通过同一个 `check_content_range` 拒绝不允许的原片区间。完全在范围外的片库候选不再提供，交叉窗口标出可用子区间。历史任务无快照时维持旧行为，已渲染父片使用输出时间。最终视觉审查保持原样。

TTS：仅原生传输的指定零字节 headers idle_timeout 签名可自动重发一次。复用现有原始字节归档及替代请求机器，发送前持久化任务账本；粗剪、精剪、共享缓存与恢复共用额度。人工授权路径的校验保留。headers idle 改为 20 秒，body idle 仍为 60 秒，总超时保持原值。run_outcome 关联两次尝试。

没有修改 audio_review.py、joint gate、B policies、F2。新增范围文件已通过 .gitignore 的精确例外纳入可交付文件；原先存在的未跟踪 uv.lock 保留。

## 新增测试：实际输出

基线由 `git archive 2c288ff | tar -x -C /private/tmp/v1_base` 导出，仅复制新增测试到基线后执行相同测试命令。44 个参数化用例全部在基线失败，当前全部通过。

```text
44 failed in 1.32s
44 passed in 0.87s
```

覆盖：范围文件有效/缺失/缺源/越界/非有限数；启动前停止与 raised_at；生产 5006–5010 秒错误和 revisit；有效区间；提示、候选过滤和部分窗口；输入快照与历史兼容；精剪的时间域区别；精确 TTS 签名、重发前归档和落账、第二行挂起、其他签名、失败后的两次尝试、恢复与共享缓存额度，以及 20 秒 header timeout。

附加 TTS 与创意路线定向检查：
```text
160 passed in 3.22s
```

## 全量测试：实际输出

执行用户指定命令；因沙箱禁止写默认缓存并禁止联网，环境增加 `UV_CACHE_DIR=/private/tmp/pfix_uv_cache UV_OFFLINE=true PYTHONPYCACHEPREFIX=/private/tmp/pfix_pycache`。uv 缓存为现有缓存的复制件，未安装新的依赖或改变测试筛选。

```sh
PYTHONPATH=src /Users/Admin/.local/bin/uv run --no-project --with pytest --with numpy python -m pytest -q -rf tests
```

```text
9 failed, 1331 passed, 103 skipped, 24 errors in 19.06s
FAILED subset of baseline_2879e5c.txt: True
Additional FAILED: []
```

全量并非全绿：9 个 FAILED 与给定基线集合相同；另外 24 个 ERROR 均发生在原有本地 TLS 服务夹具，沙箱拒绝 socket bind（Operation not permitted）。已有缺少 PIL 的失败仍保留。没有跳过或放松任何检查。

基线内 FAILED 列表：
```text
tests/test_clean_chain.py::test_deferred_rough_requires_actual_final_av_delivery[None]
tests/test_clean_chain.py::test_deferred_rough_requires_actual_final_av_delivery[joint_failed]
tests/test_clean_chain.py::test_deferred_rough_requires_actual_final_av_delivery[no_marker]
tests/test_clean_chain.py::test_deferred_rough_requires_actual_final_av_delivery[not_decoded]
tests/test_clean_chain.py::test_deferred_rough_requires_actual_final_av_delivery[not_delivered]
tests/test_clean_chain.py::test_stop_after_rough_reviews_actual_draft_once_and_never_calls_finecut[False]
tests/test_clean_chain.py::test_stop_after_rough_reviews_actual_draft_once_and_never_calls_finecut[True]
tests/test_server_jobs.py::test_guard_descriptors_are_validated_before_writing
tests/test_visual_library.py::test_same_appearance_does_not_merge_people_and_known_ids_keep_exemplars
```

## Replay：实际输出

```sh
PYTHONPATH=src /Users/Admin/.local/bin/uv run python -m omni_story.library.replay_check /Users/Admin/Desktop/agentic_video_all/.agent-team/replay_data --json /private/tmp/replay_v1.json
```

```text
TOTAL | 149 | 0 | 22 | 32 | 0
Changed rows versus 2c288ff: 0
```

逐行比较基线与当前 JSON：没有变化行，因此没有需要逐行解释的变化原因。历史任务没有范围快照，未增加限制。

## Fault check：实际输出

```sh
PYTHONPATH=src /Users/Admin/.local/bin/uv run python scripts/fault_check.py /Users/Admin/Desktop/agentic_video_all/.agent-team/replay_data --json /private/tmp/fault_v1.json
```

```text
{"E1": 0, "E2": 0, "M1_crash": 0, "M1_accept": 97, "M2_json": 0, "M2_crash": 0, "M2_empty": 0, "M2_many": 25, "M2_expected": 61, "M2_path": 164, "M2_miss": 1990, "R1": 0}
high=0
```

M2_many=25、M2_expected=61、M2_path=164、M2_miss=1990，全部与给定基线一致。M2_json、M2_crash、M2_empty 为 0；E1、R1、M1_crash、high 也为 0。脚本保留既有审计问题，正常写报告后退出码为 1。

## GLM 审查未完成

按本地 .agent-team/dispatch.sh 的 GLM 路由启动了只读审查，返回：
```text
API Error: Can't reach the API server — check your internet or DNS (ENOTFOUND)
```

该调用 `is_error=true`、`num_turns=1`，没有生成有效审查意见。受网络沙箱限制，本次不能声称已经满足“GLM reviews”。审查请求和错误原文见 `/private/tmp/pfix_glm_review.md` 与 `/private/tmp/pfix_glm_review.json`。

## git diff --stat：实际输出

```text
 .gitignore                                   |   7 +-
 src/omni_story/library/clean_chain.py        |  15 +-
 src/omni_story/library/contracts.py          |   7 +-
 src/omni_story/library/pipeline.py           |  32 ++++-
 src/omni_story/library/prompts.py            |   9 +-
 src/omni_story/library/replay_check.py       |   3 +
 src/omni_story/library/run_outcome.py        |   6 +
 src/omni_story/library/server_cli.py         |   8 ++
 src/omni_story/library/story_context.py      |   4 +-
 src/omni_story/library/story_finecut.py      |  10 +-
 src/omni_story/library/tts.py                |  25 +++-
 src/omni_story/library/tts_http.py           |   8 +-
 src/omni_story/library/tts_retry.py          | 205 +++++++++++++++++++++------
 src/omni_story/library/visual_audio.py       |   7 +-
 src/omni_story/library/visual_library.py     |   8 +-
 src/omni_story/library/visual_story_trial.py |   7 +-
 tests/test_v4_replay_regressions.py          |   6 +-
 17 files changed, 299 insertions(+), 68 deletions(-)
```

未跟踪文件不计入上述 Git 统计，以下新增交付文件仍位于工作树：

- `src/omni_story/library/content_ranges.py`
- `data/visual_library/three_films_v2/content_ranges.json`
- `tests/test_credits_and_tts_resend.py`
- 本报告 `reports/credits_and_tts_resend_v1.md`

`git diff --check` 退出码：0。

完整原始日志：`/private/tmp/new_tests_v1.txt`、`/private/tmp/base_tests_v1.txt`、`/private/tmp/full_tests_v1.txt`、`/private/tmp/replay_v1.json`、`/private/tmp/fault_v1.json`；机器可读核验汇总为 `/private/tmp/pfix_verification_summary.json`。
