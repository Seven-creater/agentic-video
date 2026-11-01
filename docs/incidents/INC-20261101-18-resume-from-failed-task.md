# INC-20261101-18 修一次代码就整程嵌套重跑（film 模式白付 15–20 分钟检索精看）；断点续跑落地后又修两类真实拒绝

| 项 | 内容 |
|---|---|
| 发现日期 | 2026-11-01 |
| 影响范围 | 创作路线任务运维（server_cli/pipeline 启动与复用）；浪费调用 + 运维阻塞 |
| 状态 | 已修复（983c38c + 97dfa88 中的 S22b/S22c 修正）；两个低危边角遗留 |
| 相关提交 | 983c38c（--resume-from）、97dfa88（装饰读数识别 + settled 放行） |
| 相关任务 | 任务说明 .agent-team/tasks/S22_resume_from.prompt.md、S22b_S23.prompt.md（PART 0）、S22c_resume_settled_state.prompt.md；报告 out/s22、out/s23/S22c_fix.md；真实拒绝案例 sb_01_不烧心…/film2（停于 av_review）、sb_20_赵德汉…/film2（停于 fine_review） |

## 1. 问题长什么样
- 用户原话："每次遇到问题停下、修复、重跑浪费很多时间。能不能断点续跑：哪里报错修哪里，然后从断点续跑。"
- 此前每次代码修复后只能嵌套重跑：参考阅读（除非 --reference-cache）、素材检索与精看（film 模式 15–20 分钟）、规划全部重来——即使失败任务里已握有全部所需材料。离线回放显示很多已录失败 plan 回复在修复后的代码下本可直接通过（S14 fix6 第 2 次尝试、S15 fix7 第 0/1 次、S17 各回复）。
- 落地后暴露的两类真实拒绝（修复前）：① 参考读法被管线装饰过的任务（music 计时附加 `audio_delivery`、`--reference-strategy` 覆写）被一律拒续 `resume_from_accepted_reference_required`/误报 `resume_from_reference_seed_changed`；② 创作流程"干净停止"退出码为 0，job 状态是 `succeeded`，续跑被 `resume_from_failed_task_must_be_settled` 拒绝——不烧心 film2（run_outcome 停于 av_review）与赵德汉 film2（停于 fine_review）两个真实任务均如此。

## 2. 怎么查出来的
- 功能缺口：盘点既有部分机制（--reference-cache 哈希绑定复用参考、--reuse-plan 只复用最后一条 plan 回复且无效即停、TTS lineage、--rough-task、账本纪律"已收结果复用不重发"），确认没有任何路径能复用失败任务的窗口 + 全部 plan 尝试。
- 拒绝①：S22 审查（verdict fail，CONFIRMED）用离线探针实测：同一夹具 speech 计时获准，music 计时与 override 任务均被拒；根因在 `resume_config` 用 `parse(reply) == reference_reading.json` 判等，而 pipeline 写文件前会给读数加模型回复里没有的 `audio_delivery` 键（当时 pipeline.py:1644-1651）——判等永假。当时 14 份已录读法恰好都是 speech/action 计时，语料未暴露。
- 拒绝②：S22c 直接读两个真实任务的 job.json（state: succeeded）与 run_outcome.json（outcome: stopped），确认"worker 干净退出"≠"任务交付"。

## 3. 根因
- 功能缺口：流程问题——修复迭代频繁，但复用粒度只到"最后一条 plan 回复"，没有把失败任务当成检查点；根因是复用逻辑分散在多个标志里，缺一个统一的 checkpoint 概念。
- 拒绝①：代码问题——判等基准选错（拿"管线装饰后的存储值"对"原始回复 parse"），对装饰不透明；属校验过严。
- 拒绝②：代码问题——把 job 进程状态（succeeded=干净退出）当成任务创作结果（stopped/partial/delivered），两个语义混用；属流程分支。

## 4. 解决方案
- 983c38c 新增 `--resume-from <failed_task>`（隐含 --parent-task；仅创作任务；library/film 均可；question_first 三重防线拒绝）：
  1. 参考自动复用失败任务**已接受**的参考读法（同 reference_cache 检查）；
  2. 失败任务到达过 plan_0 及以后时，复用 watched_windows.json 与已收观察（同 --reuse-plan 视觉种子），跳过 coarse/zoom/search/精看；尚未看过任何窗口则带准确原因拒绝；
  3. 收集其全部已收 plan_0 / plan_0_repair / plan_0_repair2（含其自身的种子链），在**当前代码**的完整生产路径（normalise_plan + validate_current_plan）下从新到旧重验，取第一条通过者（记录所用 call 与"零次新 plan 调用"）；全不通过则用复用窗口发一次新 plan 调用、修复预算全新，并把此前各次尝试的当前违规按既有修复反馈措辞写进上下文；
  4. 下游照常（渲染、TTS lineage 复用、粗审、精剪、AV 审核、字幕），任何已录回复不重发；
  5. 记录 `resume_checkpoint` 工件（失败任务路径、失败阶段、复用的参考 call id / 窗口 sha / plan call id 或 new_plan、节省调用计数；pipeline.py:821-825 于 983c38c）；
  6. 输入锁记录 + 分离启动命令透传，容量恢复重启确定性一致。
- 97dfa88（S22b）：`_accepted_reference_matches`（server_cli.py:325）改为从失败任务自身记录识别已接受参考调用——`accepted_reference_reading` 工件（仅验证通过后写入、钉死 call_id）+ `protocol_failure.json` 排除被拒回复；比较前只剔除管线自己加的三类装饰（strategy/speech_form 用户覆写、music 计时 audio_delivery、fine_observation_failures 诊断标注），其余仍逐字节相等；"已看窗口但未到 plan_0"的拒绝提示改为如实描述。两种装饰场景各有测试。
- 97dfa88（S22c）：`resume_config`（server_cli.py:346）放行条件改为"worker 已结算且（job failed/stopped，或 job succeeded 且该任务 run_outcome.outcome ∈ {stopped, partial}）"；delivered → `resume_from_task_already_delivered`（:356）；运行中仍拒；未知 outcome 拒；其余检查全保留。
- 通用性：复用只经哈希绑定读失败任务自己的账本/工件，与模板无关；账本纪律（不重发、unknown/uncertain 绝不重放、完整性失败绝不转付费调用）全部沿用。

## 5. 为什么不用其他方案
- 扩展 --reuse-plan 而非新标志：它"只取最后一条、无效即停、无修复"，语义不同；复用会破坏其现有调用方。
- 让续跑重发已录模型调用取回状态：违反"已收结果复用、绝不重发"的账本纪律，重复付费。
- 把失败任务的中间产物直接拷贝进新任务：绕过哈希绑定与校验，身份/媒体溯源（sha 链）会断。
- 拒绝②改判 job 状态（把干净停止记成 failed）：改动波及全部任务类型与运维脚本，且 run_outcome 本来就是权威的创作结果。

## 6. 怎么验收
- 983c38c：新增 `tests/test_resume_from.py`（311 行，21 例）：三条已录 plan 中第 1 条在当前代码下变有效 → 续跑取它、渲染前零新模型调用；全无效 → 恰一次新 plan、窗口复用、无 coarse/search/精看调用；未到 plan 拒绝；question_first 拒绝；已录任务字节一致回放。真实证据（雪山 film1 三份回复）离线验证：全部通过，续跑会取 `glm_025_plan_0_repair2`，新增 plan 调用 0。审查确认 plan 复用走的是完整生产路径（含 source_id 重映射与 1280×720 归一化）且三重防线拒 question_first。
- S22b/S22c（97dfa88）：两种装饰读数场景 + succeeded+stopped 放行、succeeded+delivered 拒绝、运行中拒绝，续跑相关 34 项通过；默认风格任务配置与锁字节不变（硬编码 sha 测试把关）。
- 三门（各轮）：pytest FAILED 均与 baseline_2879e5c 逐条一致（983c38c 轮 9 failed/2337 passed；S22c 轮 2395 passed/8 failed 均基线内）；replay 基线 ok/fixed 降级 0（203→164 行口径随基线更新）；fault 五项全 0；录制文件字节不变（2789 个）。

## 7. 量化数据
- film 模式每避免一次整程重跑：省参考阅读 + 检索 + 精看约 15–20 分钟（任务说明口径），节省调用数记录在 resume_checkpoint（本记录未含具体数字）。
- 雪山 film1：3 份已录 plan 回复在当前代码下 3/3 可用，续跑新增 plan 调用 0。
- 真实拒绝修复前：2/2 干净停止任务（不烧心 film2、赵德汉 film2）无法续跑；修复后按 run_outcome 放行。
- 测试：+311 行（S22），S22b/S22c 增补后续跑相关 34 项。

## 8. 经验与后续
- 检查点续跑的判据要用"创作结果"（run_outcome）而非"进程结果"（job state）；对存储值做判等前先弄清它被管线装饰过什么。
- 低危遗留（S23 审查 findings，未修）：① plan 种子只收 plan_0/repair/repair2——picture_first 父任务在静音修订 plan_1 之后、粗剪 result.json 落盘前崩溃，子任务续跑会重复付费一次 plan_1（及可能 blind_1/check_1），正确性无影响；② `_accepted_reference_matches` 快路径 `parsed == reading` 不校验 parsed.json 来源即返回 True（被拒回复需与已接受读数逐字节相等才可能命中，实际不可达）。两者都可作后续小改进。
- 审查者备注：uncertain plan 会阻止兜底新调用（保守且有测试固化）；容量恢复要求 history 种子==检查点种子，属既有 --reference-cache 同款限制。
