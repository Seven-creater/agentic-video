本轮已修复 GLM / Opus 指出的三项问题，工作树 HEAD 仍为 `c3beebb`，没有提交、没有暂存。以下内容替代上版报告；完整逐句文本、回执路径和两组分类比较见 [f1_replay_changes.json](f1_replay_changes.json)。

`min_line_window_s(text)` 现为 **`max(0.7, 0.18 × spoken_chars)`**，由具名常量 `MIN_LINE_WINDOW_S`、`SPOKEN_CHAR_SECONDS` 生成，删除固定 padding。函数 docstring 记录本轮给定的服务器审计：190 条唯一收到的 TTS，秒/字 p05 约 0.16–0.24、p50 约 0.25；1–4 字最短 0.72 秒，5–8 字最短 0.96 秒。以 `max(0.7, a+b·n)` 比较实测时长，`0.35+0.30n` 误拒 175/190，`0.3+0.22n` 为 78，`0.3+0.20n` 为 41，`0.2+0.20n` 为 28，`0+0.20n` 仍有 10。这些是用户提供的服务器统计，本机 replay 归档可去重到 72 组 text/voice，不能把两者混称为同一数据集。

该规则只作为不可能窗口的硬下限，临界窗口继续由实测 timing revision 处理。汉字、字母和数字计数，标点和空白不计；无文本或 `mode=none` 免除此下限。`摔惨了。` 下限 0.7 秒，仍拒绝真实 17.05–17.50 秒窗口，violation 的 `path=$.audio_draft[0]`、`value≈0.45`、`expected=0.7`，repair_hint 要求缩短文本或扩大窗口。8 字句 `绝学压箱底——轰天锤！` 下限 1.44 秒，20 字句为 3.6 秒。

同文本、同音色的已收到 TTS 继续以实际时长覆盖统计下限，向上或向下均如此。粗剪的 `validate_current_plan` 现在把 `context['received_audio_for_edit']['actual_tts_lengths']` 传入 `contracts.validate_plan(received_tts=...)`，再传给 `validate_audio_draft`。replay 适配器也绑定原始请求 context，使用相同生产校验分支。模型回复不能自造回执绕过检查。

新增接线测试直接覆盖 2.8 秒窗口、原文 `让开——这不是你们的仗！`、Ethan、实际 TTS 2.16 秒：粗剪 PLAN 接受。同一个测试将回执改为 3.16 秒，必须以 expected=3.16 拒绝，证明回执确实传入；另用 20 字、估算 3.6 秒但实际 2.16 秒的原文测试向下覆盖。rough/fine timing、精剪 PLAN 和独立 audio PLAN 继续共用同一 helper 与提示词常量。

新影片的黑边测量改为：内容范围内 9 个时间点，每点解码 12 张原生尺寸灰度帧；全宽每一列寻找最后一个亮度大于 24 的像素下缘，先跨帧取该列最深下缘，再取所有列的中位数作为 picture bottom。没有再对左右边缘条带取空间最大值。测量记录保存采样时间、帧数、列 p25 / median / p75、IQR、阈值及方法 `ffmpeg_gray_column_median_v2`，按影片 SHA 缓存；旧 side-strips 记录不会当作 v2 测量直接复用。

使用稳健列离散度 **IQR=p75−p25**，超过帧高 2% 时抛 `ValueError: question_first:subtitle_bar_column_spread_too_large`；底部黑边小于帧高 5%，或没有可识别画面时抛 `ValueError: question_first:subtitle_bar_below_five_percent_or_no_picture`。缓存的黑边几何也检查 5% 下限。少于 25% 宽度的角落水印属于离群列，不改变稳定水平画面边界的中位数或 IQR。合成测试使用 200×100、真实 picture bottom=88、右下水印占 20% 宽且深入第 98 行的帧数组，遮罩仍从 y=0.88 开始、h=0.12；另验证暗采样帧、4% 黑边、无黑边、40% 宽覆盖导致的大离散度及缓存复用。

测量限制：假设影片具有稳定水平黑边，且多数列在采样中能看见真实画面边缘。覆盖达到 25% 的字幕/台标、变化画幅、长期极暗边缘可能使结果不可靠；大面积一致覆盖也可能避开 IQR 检查，需人工核验，不能将该路径当成任意水印或画内字幕定位器。

三部 KFP 默认优先使用完整 SHA 绑定的已验证 library 数值，不经过新测量：

| 影片 | SHA 前缀 | 原生尺寸 | picture bottom | 遮罩 x / y / w / h / color |
| --- | --- | --- | ---: | --- |
| KFP1 | `24675c65978d5360` | 1920×1080 | 950 | 0 / 0.8796296296296297 / 1 / 0.12037037037037036 / #000000 |
| KFP2 | `5f9ca95e53664193` | 1920×1080 | 950 | 同上 |
| KFP3 | `cf2fec1024243517` | 1920×1080 | 950 | 同上 |

证据仍是 `full_tpl_kuangbiao_fish_20261026_v1/watched_windows.json` 的 library 原生帧记录。本机 PATH 无 ffmpeg / ffprobe，未重新测量或渲染真实影片。服务器脚本仍可使用：

```sh
PYTHONPATH=src python scripts/check_subtitle_bars.py --shot-cache /实际/shot_cache
# 新编码没有验证值时自动测量；--measure 可显式强制复核。
# --film /实际/影片.mkv 可重复指定。
# --write-cache 仅在新任务固定 cache SHA 之前写入 shots.json。
```

question-first 仍使用整窗、全宽、纯黑底部遮罩，不请求模型生成遮罩；library 模式遮罩路径保持原行为。

本次验收使用以下实际命令。沿用可写离线 uv cache，未改变测试和校验逻辑：

```sh
export UV_CACHE_DIR=/private/tmp/uv-pfix-cache UV_OFFLINE=1 PYTHONPATH=src
/Users/Admin/.local/bin/uv run --no-project --with pytest --with numpy python -m pytest -q -rf tests/test_mask_and_line_window.py tests/test_finecut_continuity.py tests/test_finecut_speaker_check.py tests/test_question_first.py tests/test_replay_check.py tests/test_creative_route_batch.py
/Users/Admin/.local/bin/uv run --no-project --with pytest --with numpy python -m pytest -q -rf tests
/Users/Admin/.local/bin/uv run python -m omni_story.library.replay_check /Users/Admin/Desktop/agentic_video_all/.agent-team/replay_data --json /private/tmp/replay_f1.json
/Users/Admin/.local/bin/uv run python scripts/fault_check.py /Users/Admin/Desktop/agentic_video_all/.agent-team/replay_data --json /private/tmp/fault_f1.json
git diff --stat
```

新增及改动测试真实输出（完整日志 `/private/tmp/tests_f1_followup_targeted.txt`）：

```text
336 passed, 2 skipped in 3.33s
```

包含新文件的 28 项测试，以及所有本次改动的测试文件。粗剪 closure 接线测试补上 context，QFS 合成缓存 fixture 改为 v2 测量记录。

全量测试真实输出末尾（`/private/tmp/tests_f1.txt`）：

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
9 failed, 1539 passed, 104 skipped, 24 errors in 14.68s
```

逐项与 `/Users/Admin/Desktop/agentic_video_all/.agent-team/out/baseline_2879e5c.txt` 做集合比较，真实输出：

```text
新增 FAILED: []
消失 FAILED: []
```

因此 FAILED 是指定基线的子集（实际完全相同）。24 个 setup error 均为 TLS 本地服务器 fixture 的 `socket.bind` 被沙箱拒绝：`PermissionError: [Errno 1] Operation not permitted`；上轮同环境也有这些错误。Pillow 缺失造成的失败已在上述基线内。此处未宣称全量测试全绿。

replay 已重跑当前代码，也从 `git archive HEAD` 导出未修改的 c3beebb 到 `/private/tmp/pfix-f1-head-followup`，以 `PYTHONPATH=/private/tmp/pfix-f1-head-followup/src` 重跑原版。输出列为 ok / regression / fixed / still_failing / unsupported：

```text
TOTAL | 149 | 0 | 22 | 32 | 0  # c3beebb 原版
TOTAL | 130 | 19 | 17 | 37 | 0  # 上版审查失败版本（本轮修改前保存的 JSON）
TOTAL | 143 | 6 | 21 | 33 | 0  # 本轮修复
```

共 203 行，unsupported=0。下面列出**相对 c3beebb 的全部 7 个分类变化行**，以及**相对审查失败版本恢复的全部 17 行**。二者没有省略。任务别名对应完整相对 replay_data 路径：

| 别名 | 任务相对路径 |
| --- | --- |
| T01 | `full_tpl_kuangbiao_fish_20261026_v1/evaluations/silent_fix_1dcc63c/evaluations/v3_838f636/evaluations/v4_6269974/evaluations/v5_8a71956/evaluations/v7_ab014be/evaluations/creative_2879e5c/evaluations/creative_53c4328/evaluations/creative_3f9c1eb/evaluations/creative_5f5bb4a/evaluations/creative_1f954ae` |
| T02 | `full_tpl_kuangbiao_fish_20261026_v1/evaluations/silent_fix_1dcc63c/evaluations/v3_838f636/evaluations/v4_6269974/evaluations/v5_8a71956/evaluations/v7_ab014be/evaluations/creative_2879e5c/evaluations/creative_53c4328/evaluations/creative_3f9c1eb/evaluations/creative_5f5bb4a/evaluations/creative_1f954ae/skill_finecut/creative_audio` |
| T03 | `full_tpl_kuangbiao_fish_20261026_v1/evaluations/silent_fix_1dcc63c/evaluations/v3_838f636/evaluations/v4_6269974/evaluations/v5_8a71956/evaluations/v7_ab014be/evaluations/creative_2879e5c/evaluations/creative_53c4328/evaluations/creative_3f9c1eb/evaluations/creative_5f5bb4a` |
| T04 | `full_tpl_kuangbiao_fish_20261026_v1/evaluations/silent_fix_1dcc63c/evaluations/v3_838f636/evaluations/v4_6269974/evaluations/v5_8a71956/evaluations/v7_ab014be/evaluations/creative_2879e5c/evaluations/creative_53c4328/evaluations/creative_3f9c1eb` |
| T05 | `full_tpl_kuangbiao_fish_20261026_v1/evaluations/silent_fix_1dcc63c/evaluations/v3_838f636/evaluations/v4_6269974/evaluations/v5_8a71956/evaluations/v7_ab014be/evaluations/creative_2879e5c/evaluations/creative_53c4328` |
| T06 | `full_tpl_kuangbiao_fish_20261026_v1/evaluations/silent_fix_1dcc63c/evaluations/v3_838f636/evaluations/v4_6269974/evaluations/v5_8a71956/evaluations/v7_ab014be/evaluations/creative_2879e5c` |
| T07 | `full_tpl_kuangbiao_fish_20261026_v1/evaluations/silent_fix_1dcc63c/evaluations/v3_838f636/evaluations/v4_6269974/evaluations/v5_8a71956/evaluations/v7_ab014be` |
| T08 | `full_tpl_kuangbiao_fish_20261026_v1/evaluations/silent_fix_1dcc63c/evaluations/v3_838f636/evaluations/v4_6269974/evaluations/v5_8a71956` |
| T09 | `full_tpl_kuangbiao_fish_20261026_v1/evaluations/silent_fix_1dcc63c/evaluations/v3_838f636/evaluations/v4_6269974` |
| T10 | `full_tpl_kuangbiao_fish_20261026_v1/evaluations/silent_fix_1dcc63c/evaluations/v3_838f636` |
| T11 | `full_tpl_xiaobiesan_20261026_v1/evaluations/regress_ab014be/evaluations/creative_2879e5c/evaluations/creative_53c4328` |
| T12 | `full_tpl_xiaobiesan_20261026_v1/evaluations/regress_ab014be/evaluations/creative_2879e5c` |
| T13 | `full_tpl_xiaobiesan_20261026_v1/evaluations/regress_ab014be` |
| T14 | `full_tpl_xiaobiesan_20261026_v1` |

相对 c3beebb，6 行 `ok → regression`，1 行 `fixed → still_failing`，共同原因是 `audio_line_window_below_minimum`。为避免只看首次报错，表中列出每行**所有被拦截句子**；下标对应 `$.audio_draft[i]`，单位秒。实测列依同一顺序列出全归档同 text/voice 的时长，完整来源路径保存在 JSON 的 `archive_tts_evidence`。

| 任务 | call_id | 分类变化 | 下标：窗口 < 当前下限 | 实际 TTS |
| --- | --- | --- | --- | --- |
| T04 | `glm_003_rough_audio_timing_1_repair` | fixed → still_failing | [3]：2.9 < 7.52 | [3]：7.52 |
| T07 | `glm_005_plan_0_repair2` | ok → regression | [5]：6 < 6.84；[6]：2 < 2.34 | [5]：11.36；[6]：3.76 |
| T08 | `glm_003_rough_audio_timing_1_repair` | ok → regression | [7]：2 < 2.16 | [7]：3.6 |
| T09 | `glm_005_plan_0_repair2` | ok → regression | [4]：5 < 5.58；[7]：2 < 4.32 | [4]：7.04/8.96；[7]：5.44/5.52 |
| T10 | `glm_005_plan_0_repair2` | ok → regression | [1]：5 < 7.2；[2]：1 < 1.8 | [1]：9.76；[2]：2.08 |
| T10 | `glm_014_chain_e2e_v1_fine_plan` | ok → regression | [0]：9 < 9.68；[1]：4.761905 < 4.88 | [0]：9.68；[1]：4.88 |
| T14 | `glm_008_plan_0_repair` | ok → regression | [5]：2 < 2.16 | [5]：2.96 |

这 11 句均未发现能装入对应窗口的实测回执。3 行在当前请求或同任务结果中有超窗证据；另外 4 个 PLAN 行在该任务自身结果中没有同版文本回执，但其他归档中存在同文本、同音色的 TTS，且同样超窗。跨任务证据仅用于这次验收审计，没有注入生产校验作为输入回执，也不能据此断言这些 PLAN 回复后来实际丢句。归档未记录对应 dropped_lines，不把 timing_problems 等同于已丢弃。

全归档核对真实输出（相关汇总也写入 JSON）：

```text
global distinct received text+voice: 72
all-archive ok -> regression rejected lines with fitting actual TTS: []
```

两条原先以 3.05 秒门槛误拒的真实 `让开——这不是你们的仗！`，窗口 2.8 秒、实际 2.16 秒，现已恢复为 ok：T01、T05 的 `glm_002_rough_audio_timing_1`。

相对本轮开始时的严格版本，以下 17 行恢复原分类：13 行 `regression → ok`，4 行 `still_failing → fixed`。共同原因是旧的典型语速预算改为较低硬下限，临界时长由后续实测处理；恢复通过不代表已保证 TTS 放下。表中保留旧首次拒绝位置和数值，方便逐行对应上版报告。

| 任务 | call_id | 本轮分类变化 | 旧首次拒绝（窗口 < 旧门槛） |
| --- | --- | --- | --- |
| T01 | `glm_002_rough_audio_timing_1` | regression → ok | `$.audio_draft[4]`：2.8 < 3.05 |
| T02 | `glm_001_audio_plan_v1` | regression → ok | `$.audio_draft[4]`：0.9 < 1 |
| T03 | `glm_005_rough_audio_timing_1_repair2` | still_failing → fixed | `$.audio_draft[4]`：2.6 < 3.05 |
| T04 | `glm_004_rough_audio_timing_1_repair2` | still_failing → fixed | `$.audio_draft[5]`：5.5 < 6.05 |
| T05 | `glm_002_rough_audio_timing_1` | regression → ok | `$.audio_draft[4]`：2.8 < 3.05 |
| T06 | `glm_002_rough_audio_timing_1` | regression → ok | `$.audio_draft[4]`：1.4 < 2.45 |
| T07 | `glm_006_rough_audio_timing_1` | regression → ok | `$.audio_draft[4]`：1.2 < 1.25 |
| T09 | `glm_006_rough_audio_timing_1` | regression → ok | `$.audio_draft[0]`：12 < 14.15 |
| T09 | `glm_016_chain_e2e_v1_fine_plan` | regression → ok | `$.audio_draft[0]`：8 < 11.75 |
| T10 | `glm_006_rough_audio_timing_1` | regression → ok | `$.audio_draft[0]`：9 < 10.25 |
| T11 | `glm_003_rough_audio_timing_1_repair` | regression → ok | `$.audio_draft[4]`：5 < 5.75 |
| T12 | `glm_002_rough_audio_timing_1` | regression → ok | `$.audio_draft[4]`：3.5 < 4.25 |
| T12 | `glm_011_chain_e2e_v1_fine_plan` | still_failing → fixed | `$.audio_draft[0]`：3 < 3.65 |
| T13 | `glm_006_plan_0_repair` | regression → ok | `$.audio_draft[2]`：5 < 8.45 |
| T13 | `glm_019_chain_e2e_v1_fine_plan` | regression → ok | `$.audio_draft[1]`：6 < 6.35 |
| T13 | `glm_024_chain_e2e_v1_fine_revise_repair2` | still_failing → fixed | `$.audio_draft[1]`：6 < 6.35 |
| T14 | `glm_009_rough_audio_timing_1` | regression → ok | `$.audio_draft[5]`：2 < 2.15 |

fault check 已实际完成，分类计数原样摘录（完整 `/private/tmp/fault_f1.txt`、`/private/tmp/fault_f1.json`）：

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

从 JSON 统计的验收项真实输出：

```text
{"E1": 0, "R1": 0, "M1_crash": 0, "M2_json": 0, "M2_crash": 0}
high_findings: 0
high_root_causes: 0
```

fault 脚本退出码为 1，来自表中其余非零诊断类别；用户指定的五项及 high 均为零。replay 退出码也为 1，因为仍有上述 6 个真实拒绝行，并非工具崩溃。

`git diff --check` 退出码 0，无输出。`git diff --stat` 真实输出：

```text
 src/omni_story/library/audio_contracts.py |  5 +++++
 src/omni_story/library/contracts.py       | 23 +++++++++++++++++++----
 src/omni_story/library/pipeline.py        | 31 +++++++++++++++++++++++++++++--
 src/omni_story/library/prompts.py         |  4 +++-
 src/omni_story/library/question_first.py  |  3 +++
 src/omni_story/library/replay_check.py    |  2 +-
 src/omni_story/library/story_context.py   | 21 +++++++--------------
 src/omni_story/library/story_finecut.py   |  4 +++-
 src/omni_story/library/visual_audio.py    | 14 ++++++++++++--
 tests/test_creative_route_batch.py        |  2 +-
 tests/test_finecut_continuity.py          |  7 +++++--
 tests/test_finecut_speaker_check.py       |  2 +-
 tests/test_question_first.py              |  8 ++++++--
 13 files changed, 95 insertions(+), 31 deletions(-)
```

`git diff --stat` 不显示未跟踪文件；未为取得统计而暂存。未跟踪的本次实现、脚本、测试与报告为 `src/omni_story/library/line_windows.py`、`src/omni_story/library/subtitle_bar.py`、`scripts/check_subtitle_bars.py`、`tests/test_mask_and_line_window.py`、`reports/f1_replay_changes.json`、`reports/f1_mask_line_window.md`。原有未跟踪 `uv.lock` 不纳入本次实现。没有在本机重跑服务器任务的 TTS 合成或最终视频渲染，没有提交。
