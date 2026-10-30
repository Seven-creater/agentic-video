# 参考理解与最小可见事实：通用批次 R1（中文报告）

工作树分支 `fix/reference-value-understanding`；起止 HEAD 均为 `93fd5d44c5f2727ca60d784eb20743c7ca2e7adf`。未提交、未暂存。原有未跟踪 `uv.lock` 保留。

本批把 questions / close / 规划 / 两处审核对齐到同一原则：GLM 选可用镜头，剪辑与 TTS 重赋意义。画面只承担配音无法伪造的人物关系、动作类别和镜头需要；原片故事、台词与声音不构成约束。没有重新发送夜跑请求，也没有把历史负评改成通过。

## A–C：改动与边界

- `reference_requirements.py` 的 minimal_visual_contract 同时用于提示词和校验。must_see 仅允许 `who_relation`、`action_class`、`shot_need`、`mouth_open_needed`；前 3 项上限分别为 48、16、24 个 Unicode 字符，最后一项必须为 bool。关系额度容纳两个角色标签和一个关系，动作额度容纳一个短动词类，镜头额度容纳一个取景指令；不允许把小剧本塞入单值。该长度约束不是语义分类器，是否确为最小要求仍须 GLM 按共同原则判断。
- 道具、手势、场地、结果、情绪和转变放 nice_to_have；台词、动机、主题放 dub_can_carry。未知键、超长值、错误类型产生带路径和 hint 的 violation；questions 没有修复轮，所以验证入口完整搬入 nice_to_have、记录 normalised_must_see，不截字、不崩溃、不补写硬条件。旧字段也只保留为可选信息。
- close 按 `minimal_checks` 的最小项核验，全部成立即可 usable；nice_to_have_score 仅参与排序，评分格式错误归零并留痕。旧 equivalents 仍可读，但不再要求逐字或等价复演；错误的 equivalent_id 也仅规范化并记录未匹配，不丢弃可用镜头。mouth、anchor、3D 约束仍有效；partial 可继续精看，却不能消除 material gap。
- fine-cut target 和 creative AV 调用方共用 meaning_transfer_contract，判断“实际画面 + 配音”是否传达 intended_takeaway。换人、换场地、换调度、对白改旁白本身都不是 missing_core_meaning。AV 中的 `picture_audio_coherence` 独立检查实际声画归属与矛盾；明确 contradictory/unverifiable 不能交付，精剪目标审核同样拒绝带未解决矛盾的 deliver。
- 旧 next_action / missing_core_meaning 一致性合同保留；历史回复缺新 coherence 字段时仍用既有协议，不反向重写档案。`audio_review.py` 的请求和传输没有改动。

## D：五份耗尽证据和八类拒绝

取证于 `.agent-team/out/e2e/` 的 `不烧心_3`、`雪山救狐狸_4`、`粉色娇嫩_14`、`马冬梅_16`、`马冬梅_17`：共提取 15 条 plan 失败和 1 条 fine 失败。原文本有长度截断，夹具只解码完整顶层字段；没有补造缺失的观察或台词。`不烧心_4` 的 questions 前 3 个完整 beat 及完整 AV 审核也存入夹具。

| 拒绝码 | 出了什么问题 | 本批处理（原规则继续拒绝真实违规） |
|---|---|---|
| `plan:cast_entity_not_supported_by_observation` | 一个 entity 被用于实际不同来源/不同实体或未确认出场 | 利用当前 HEAD 已有绑定校验，规划合同新增 `bindable_triples`，每项明示 window_id、role_id、entity_id、identity_confirmed=true；仅复制真实选择，不按同名或同音色合并。 |
| `visual_library:range_outside_content` | source 绝对秒数改成 local 秒数，或选到正文外 | `plan_source_range_contract` 计算正文 ∩ 窗口 ∩ 单条 observed usable_range 的绝对秒数；拒绝反馈列 allowed_source_ranges，保留原错误字段与数值。 |
| `plan:segment_role_missing_overlapping_event_evidence` | usable_range 角色并集被用于没有该角色事件的子段 | 保留 HEAD 已有严格相交规则和 per-role 事件表；规划者同时拿到合法范围与事件表，可删无证据角色或重选实际有事件的子段。 |
| `plan/confirmed_roles:unknown_or_duplicate_ref` | 引用未确认、未知或重复的窗口局部角色 | 保留 refs 校验；继续列 confirmed_role_ids、overlapping_role_ids、role_event_ranges。 |
| `plan/focus_binding_confirmed_role:unknown_or_duplicate_ref` | 焦点指向未确认角色，修复后又把它加回来 | 保留逐窗口 allowed_bindings；不必绑定所有窗口，空列表表示该窗口没有合法焦点。 |
| `plan:range_not_supported_by_fine_observation` | 所选区间跨 usable_range，或角色集合无同条范围支持 | 提示、验证、多问题反馈共用范围匹配 helper，范围保留 usable_range_index；不合并、扩大或平移计划去猜授权。 |
| `plan:dialogue_speaker_voice_changed` | 同一实体已建立音色后来变化 | 三处原拒绝都返回具体 audio_draft 路径、allowed_voices 和 hint；反馈扫描其他句，真实观察身份优先于错误 cast 声明。 |
| `fine:entity_source_namespace_required` | fine 从其他来源已看角色复制 entity | 修复生产上下文：fine_identity_context 仅提供同 source_sha256 的角色，带同一 fine_entity_contract。原 qfs `SHA:qfs_ASCII` 生成格式已合法，不需要放宽 validator；外源 ID 仍拒绝，不自动换前缀。 |

修复提示要求只改 violations 及必要依赖，其他时间、ID、绑定保留。没有增加修复预算或自动改写 EDL；同一角色规范身份、3D-only、正文、单条可用范围、子段角色事件和说话者可见/合理画外锚点约束继续生效。

## 测试：先在 HEAD 失败，再通过

新增 `tests/test_minimal_reference_requirements.py` 共 33 个场景，使用真实问题/负评/协议失败和明确标记的受控上下文。原 HEAD 源码通过 `git archive HEAD src` 解到只读回放用临时目录，运行相同测试；29 个行为回归失败，4 个已有质量保护对照通过。修复后 33 个全部通过；含已有真实身份/事件/审核一致性测试的相关定向套件为 181 个通过。

原始输出：

```text
29 failed, 4 passed in 0.62s
181 passed in 0.63s
```

保护对照包括：真实 `不烧心` 割裂负评仍拒绝交付；错误嘴动、不同锚点、高可选评分下的非 3D 候选仍不合格；真实不受观察支持的 cast 仍拒绝；已确认角色没有重叠事件、跨多条 usable_range、片尾范围继续拒绝。新增明确 coherence 状态还防止“主题 matches 但画音 contradictory”交付。

仅更新 3 类旧断言以适应新合同：不再要求旧 equivalents/结果硬条件；partial 虽可精看仍报告 material gap；正文越界反馈从单一 bounds 增强为正文及可用范围选择表，仍保留原越界字段和错误码。原 `test_reference_value_understanding.py` 完整保留。

## 验收命令与全量测试原输出

当前沙箱不允许写用户级 uv 缓存。已将现有缓存复制到可写临时目录，统一导出以下环境变量后原样执行用户给定的三条命令；没有修改项目依赖或测试跳过规则：

```sh
export UV_CACHE_DIR=/private/tmp/reference-value-93fd5d4-uv
export UV_OFFLINE=1
PYTHONPATH=src /Users/Admin/.local/bin/uv run --no-project --with pytest --with numpy python -m pytest -q -rf tests
PYTHONPATH=src /Users/Admin/.local/bin/uv run python -m omni_story.library.replay_check /Users/Admin/Desktop/agentic_video_all/.agent-team/replay_data --json /private/tmp/replay_r1.json
PYTHONPATH=src /Users/Admin/.local/bin/uv run python scripts/fault_check.py /Users/Admin/Desktop/agentic_video_all/.agent-team/replay_data --json /private/tmp/fault_r1.json
```

全量 pytest 原始摘要及全部 FAILED：

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
9 failed, 1657 passed, 104 skipped, 24 errors in 16.79s
```

集合核验实际输出：

```text
FAILED_current=9
FAILED_baseline=9
new_FAILED=0
removed_FAILED=0
FAILED_current <= FAILED_baseline: True
```

24 个 ERROR 是 `tests/test_tts_http.py` 在 setup 建立本地 TLS HTTP server 时触发 `PermissionError: [Errno 1] Operation not permitted`（沙箱禁止监听端口）；原 HEAD 同样复现 24 个 ERROR。9 个 FAILED 原样属于指定基线，原因涉及环境缺 ffprobe / Pillow、Linux 专用 server_jobs，以及既有 audio_measurement 测试替身缺字段。全量测试并未全绿，以上没有隐藏或改为 skip。

## 历史回放及每行变化

`/private/tmp/replay_r1.json` 实际摘要（与 `replay_baseline_98e9427.json` 相同）：

```json
{
  "ok": 143,
  "regression": 6,
  "fixed": 21,
  "still_failing": 33,
  "unsupported": 0
}
```

```text
baseline_rows=203
current_rows=203
new ok/fixed -> regression/still_failing=0
category_changes=0
changed_payload_rows=14
missing_rows=0
```

下面逐项列出全部 14 行载荷变化；分类、passed_now 和拒绝码未变。`L`=已有 HEAD 相对旧基线的台词窗口允许区间/推导反馈；`C`=已有 HEAD 的来源隔离 cast hint；`R`=本批新增正文与单条观察范围表、source 时间域和 usable_range_index。精确 task 全路径、每个变化字段的 before/after 均在 [reference_value_replay_changes_r1.json](reference_value_replay_changes_r1.json)，不把基线到 HEAD 的既有变化冒认为本批修复。

| 任务（唯一后缀；fish/xb 指两个回放根） | call_id | 分类前后 | 变化 | 变化叶字段数 |
|---|---|---|---|---:|
| `fish/v7_ab014be` | `glm_003_plan_0` | still_failing → still_failing | L, C | 8 |
| `fish/v7_ab014be` | `glm_004_plan_0_repair` | still_failing → still_failing | C | 1 |
| `fish/v7_ab014be` | `glm_005_plan_0_repair2` | regression → regression | L, C | 14 |
| `fish/v4_6269974` | `glm_003_plan_0` | still_failing → still_failing | C, R | 22 |
| `fish/v4_6269974` | `glm_004_plan_0_repair` | still_failing → still_failing | C | 1 |
| `fish/v4_6269974` | `glm_005_plan_0_repair2` | regression → regression | L, C | 14 |
| `fish/v3_838f636` | `glm_004_plan_0_repair` | still_failing → still_failing | C | 1 |
| `fish/v3_838f636` | `glm_005_plan_0_repair2` | regression → regression | L, C | 14 |
| `fish/v3_838f636` | `glm_014_chain_e2e_v1_fine_plan` | regression → regression | L | 23 |
| `fish/full_tpl_kuangbiao_fish_20261026_v1` | `glm_005_plan_1` | still_failing → still_failing | C | 1 |
| `fish/full_tpl_kuangbiao_fish_20261026_v1` | `glm_006_plan_1_repair` | still_failing → still_failing | L, C | 50 |
| `xb/regress_ab014be` | `glm_005_plan_0` | still_failing → still_failing | C, R | 23 |
| `xb/full_tpl_xiaobiesan_20261026_v1` | `glm_007_plan_0` | still_failing → still_failing | C | 1 |
| `xb/full_tpl_xiaobiesan_20261026_v1` | `glm_008_plan_0_repair` | regression → regression | L, C | 8 |

该离线回放验证历史 JSON 与当前合同，不会证明新提示词已经改变线上模型选择或成片质量。既有 6 个 regression 与 33 个 still_failing 未在本批绕过校验消除。

## 故障演练原输出

从本次 fault_check 原始表中摘录要求的 5 项：

```text
| E1 | 0 |
| M1_crash | 0 |
| M2_json | 0 |
| M2_crash | 0 |
| R1 | 0 |
```

实际覆盖 validator_attempts=9984、E1_attempts=3090、E1_aggregate_attempts=480、M1_attempts=5959、R1_attempts=100。脚本退出码为 1，原因是它还报告其他既有审计发现（例如 M1_accept、M2_miss）；不能把要求的五项为零写成全部审计问题清零。完整 JSON 位于 `/private/tmp/fault_r1.json`。

## 事故记录

按用户指定的 incident-record 技能，以两个根因分别记录并更新索引：

- [INC-20261030-06](../docs/incidents/INC-20261030-06-minimal-visible-meaning-transfer.md)：视觉硬条件与意义迁移混淆。
- [INC-20261030-07](../docs/incidents/INC-20261030-07-plan-valid-choice-tables.md)：规划合法选择和来源上下文缺口，含八类拒绝逐项说明。

## git diff --stat 原输出

未执行 git add；以下为原命令输出，它按 Git 行为不包含未跟踪的新文件：

```text
 docs/incidents/README.md                    |   2 +
 src/omni_story/library/contracts.py         | 119 +++++++++++++++++++++-------
 src/omni_story/library/pipeline.py          |  30 ++++---
 src/omni_story/library/protocol_feedback.py |  32 +++++---
 src/omni_story/library/question_first.py    |  74 ++++++++++-------
 src/omni_story/library/story_finecut.py     |  19 +++--
 src/omni_story/library/visual_audio.py      |  20 ++---
 tests/test_credits_and_tts_resend.py        |   7 +-
 tests/test_question_first.py                |  11 +--
 9 files changed, 215 insertions(+), 99 deletions(-)
```

本批另新增以下未跟踪文件（均为交付内容，保留为未暂存状态）；本报告本身也为新增文件：

- `src/omni_story/library/reference_requirements.py`（90 行）
- `tests/test_minimal_reference_requirements.py`（297 行）
- `tests/fixtures/reference_value_20261030/README.md`（9 行）
- `tests/fixtures/reference_value_20261030/bushaoxin_av_review.json`（33 行）
- `tests/fixtures/reference_value_20261030/bushaoxin_questions.json`（105 行）
- `tests/fixtures/reference_value_20261030/protocol_failures.json`（2962 行）
- `reports/reference_value_replay_changes_r1.json`（1284 行）
- `docs/incidents/INC-20261030-06-minimal-visible-meaning-transfer.md`（50 行）
- `docs/incidents/INC-20261030-07-plan-valid-choice-tables.md`（58 行）

`git diff --check` 无输出，HEAD 和分支未变。没有新线上端到端成片验证；用户提供的旧夜跑、回放和本地测试是本批实际验证范围。
