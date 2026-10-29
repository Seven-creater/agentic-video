**参考策略能力校验修复报告（2026-10-30）**

工作树分支：`fix/reference-value-understanding`。未提交、未暂存。只修复已核实的策略合同缺口。

**根因与黄金路径对照**

已独立核对 `evidence_04_少帅下飞机_10.txt`、只读分析 `analysis_11.md`、
[黄金路径](../golden_path_heihu_98e9427.md)及源码。证据只有一次已收到的 reference 调用，策略为
`reinterpret/mixed/music`；异常位置为 `visual_audio.music_source`，错误为
`audio_music_driven_work_requires_explicit_bed_with_new_speech`。原代码让这个组合通过参考校验，随后在
没有 `audio_music_bed` 时要求给新语音配置音乐床而硬停。它尚未进入影片检索，不能归因于素材不足。

黄金路径使用 `reinterpret/mixed/action`，通过相同的“三键齐全＋枚举有效”通用策略合同，
随后跳过音乐分派。并不存在让黄金路径通过音乐分支的额外规则；它没有走该分支。
本修复扩展这一既有合同：除字段有效，策略还必须与当前运行的音频输入相容。
同时延续既有“主旨可以迁移、表达方法分别记录”的规则，明确参考的音乐形式仍如实记录在
`speech_form/visual_form`，新作品的执行策略由可用能力和同一主旨共同决定。

分析的直接因果链成立；其自动 `music→speech` 降级和 `music_source` 返回 `None` 的建议未采用。
没有实测证明音乐对目标作品可有可无，直接改策略或撤掉音乐要求会掩盖目标变化。
所谓 `speech_mode=none` 后规划新增旁白的情形，已有 `validate_audio_draft` 对 `audio_draft=[]` 的约束；
证据也未走到该阶段，因此未为这一推测增加容错。

**改动与通用性**

- [contracts.py](../../src/omni_story/library/contracts.py:491) 新增共享合同 helper
  `creative_reference_strategy_contract`；提示词、校验器均使用它生成的允许矩阵。
  无显式音乐床且有新语音时，`timing_driver` 只允许 `speech/action`；其余组合保持原有取值。
- [pipeline.py](../../src/omni_story/library/pipeline.py:1485) 在新 reference 回复校验中检查该矩阵，
  并在缓存参考／显式用户策略覆盖完成后、音乐源分派前再核验最终策略。
  有效的用户覆盖不会被模型原始 `music` 选择误拦截。
- 新拒绝为 `ValueError('creative_reference_music_requires_explicit_bed_with_new_speech')`，
  附带 `violation.path/value/expected/repair_hint`，指出 `$.strategy.timing_driver`、实际 `music`、
  当前语音模式和允许值；修复提示要求保留参考描述，不能假报无语音或假定系统会分离音乐。
- [replay_check.py](../../src/omni_story/library/replay_check.py:104) 重建生产校验闭包时传入归档配置，
  让回放使用当时记录的音乐床与用户策略。

规则只依赖 `speech_mode`、`timing_driver`、是否显式配置音乐床，不读取角色、台词、影片或模板名称。
覆盖全部 4 种语音模式、3 种计时模式及有床／无床配置。没有自动改写参考、自动降级、
新增重试或重发逻辑；原有协议修复机制保持不变。`music_source` 的原声混用保护、
音乐可听性与节奏审核、音量实测门禁均未放宽。library 模式、旧参考默认值和原有缺键处理保持原样。

**真实证据测试**

[response.json](../../tests/fixtures/reference_music_capability/response.json) 复制证据中完整真实回复 envelope，
仅重排 JSON 缩进，内部 `result.content[0].text` 原文保留；已与原证据解析结果逐项比对相等。
[测试](../../tests/test_reference_music_capability.py) 共 7 项：真实回复与能力矩阵、
新回复／缓存／显式覆盖三个入口、提示词与校验共用合同的有床／无床两种配置、归档配置回放。
另在用例内检查动作策略和有效覆盖实际到达出题入口，且没有增加 reference 调用。

修改前输出：

```text
7 failed in 1.44s
```

修改后输出：

```text
7 passed in 0.10s
```

**验收命令与真实输出**

受沙箱写权限和网络限制，三个命令运行前统一设置下列环境；命令主体按要求原样执行：

```sh
export UV_CACHE_DIR=/private/tmp/uv-e2e-cache UV_OFFLINE=1
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
9 failed, 1601 passed, 104 skipped, 24 errors in 15.34s
```

`FAILED` 全为所给基线的 9 项，无新增。全量测试并非全绿：另有 24 项初始化 `ERROR`，
均来自未修改的 `tests/test_tts_http.py` 在 `ThreadingHTTPServer(('127.0.0.1', 0), ...)` 绑定端口时
收到沙箱 `PermissionError: [Errno 1] Operation not permitted`。未绕过该限制或改测试掩盖错误。
完整日志：`/private/tmp/reference_music_pytest.txt`。该命令退出码为 1。

```sh
PYTHONPATH=src /Users/Admin/.local/bin/uv run python -m omni_story.library.replay_check /Users/Admin/Desktop/agentic_video_all/.agent-team/replay_data --json /private/tmp/replay_e2e.json
```

```text
task | ok | regression | fixed | still_failing | unsupported
TOTAL | 143 | 6 | 21 | 33 | 0
```

逐行比较 `(task, stage, call_id)`，基线 164 条 `ok/fixed` 全部保留，203 行分类全部与基线一致。
其中 6 条 `regression` 为基线已有问题，因此命令仍退出 1；无新增退化。
完整输出：`/private/tmp/reference_music_replay.txt`。

```sh
PYTHONPATH=src /Users/Admin/.local/bin/uv run python scripts/fault_check.py /Users/Admin/Desktop/agentic_video_all/.agent-team/replay_data --json /private/tmp/fault_e2e.json
```

实际分类计数原文：

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

验收要求的 E1、R1、M1_crash、M2_json、M2_crash 均为 0。
脚本因仍存在其他审计发现而退出 1；未将退出码描述为成功，也未扩大修改范围去修无关问题。
完整输出：`/private/tmp/reference_music_fault.txt`。

实际基线比较输出：

```text
pytest FAILED: 9
FAILED subset of baseline: True
new FAILED: []
baseline ok/fixed rows: 164
new regressions from baseline ok/fixed: 0
changed row categories: 0
required fault counts: {"E1": 0, "R1": 0, "M1_crash": 0, "M2_json": 0, "M2_crash": 0}
fixture equals evidence reply: True
```

```sh
git diff --stat
```

```text
 src/omni_story/library/contracts.py    | 28 ++++++++++++++++++++++++++++
 src/omni_story/library/pipeline.py     | 11 ++++++++++-
 src/omni_story/library/replay_check.py |  2 +-
 3 files changed, 39 insertions(+), 2 deletions(-)
```

`git diff --check` 通过。上述 stat 只统计已跟踪文件；新增测试、fixture 目录及本报告均保留为未跟踪文件，未暂存。
开始工作时已有的 `uv.lock` 和 `docs/incidents/INC-20261030-e2e-timing-recovery.md` 未修改。

未执行新的收费完整创作运行，也没有重发原调用；当前验证覆盖合同、执行分派前的拦截／可行路径、
历史回放和故障注入，不宣称已生成本次参考对应的最终成片。
