# INC-20261027-04 配音时长阻塞句不可编辑:修正反馈指不出堵在哪,放不下的句子必然丢弃

| 项 | 内容 |
|---|---|
| 发现日期 | 2026-10-27 |
| 影响范围 | 创作路线粗剪与精剪的配音时长修正(timing revision);错误成片(丢句)与浪费调用(修正必败) |
| 状态 | 已修复(7da9891) |
| 相关提交 | 7da9891 |
| 相关任务 | `.agent-team/out/T1_blocker_report.md`;重放归档 kuangbiao / xiaobiesan 任务链 |

## 1. 问题长什么样
TTS 实测时长超过所在窗口(`tts_exceeds_window`)时,修正反馈只报"某句超窗",不说明是哪一句堵住了它、空闲窗口在哪;且被堵句(如 `dl_2` 被 `vo_3` 堵住)不在可编辑集合里,模型删改自己的句子也无法解开。典型:`dl_2` 可用 0 秒、被 `vo_3` 堵到 32.52 秒,`vo_3` 才是真正要改的句,但旧规则禁止编辑它。

## 2. 怎么查出来的
用真实任务链的录制回复做回放(`replay_check`),发现 `glm_003_rough_audio_timing_1_repair` 持续 still_failing,错误 `audio_draft_timing_unrelated_line_changed:vo_3`——模型改了 vo_3(实际阻塞句)却被判"改了不可改的句"。逐句推导窗口确认 vo_3 是阻塞源。新增 `scripts/timing_blocker_check.py`(137 行)对 26 个真实 timing 问题做确定性推演。

## 3. 根因
代码问题(反馈不可操作 + 权限集合错):①权限集合没有把"堵住别人的句"算进可编辑;②排程元数据(阻塞者、阻塞到几秒、空闲区间)已存在但没有进反馈。

## 4. 解决方案
粗剪与精剪共享草稿经 `synthesize_draft → schedule_draft` 记录阻塞句、cursor 和空闲窗口;`timing_editable_line_ids` 成为统一权限集合计算入口,提示词与校验器都用它;修正反馈点名阻塞行并列出空闲区间(`free_intervals`);阻塞句在粗剪与精剪都变为可编辑。旧字段、旧错误字符串、`validate_story_context` 保持不变。回放时按实测时长重建当前权限,不使用历史 editable_line_ids 作权限来源。
通用性:权限由排程结构(谁堵谁、哪里空)推导,与模板、台词内容无关。

## 5. 为什么不用其他方案
- 保持"受保护句不可改"的旧权限:与事实矛盾——排程上正是这些句在制造冲突;且 vo_3 类阻塞句修复后 26/26 可解。
- 直接丢弃放不下的句子(不做修正回路):丢句本可避免(把阻塞句缩短/挪窗即可放下);丢弃只保留为耗尽后的最后手段(见 INC-20261027-07 B3)。
- 其他备选:未讨论。

## 6. 怎么验收(T1_blocker_report 实际输出)
- 新增测试 16 passed(0.13s);`git archive 1f954ae` 隔离旧源码同一测试 16 failed(1.41s)。
- 全套 `9 failed, 1255 passed, 103 skipped, 24 errors`,FAILED outside baseline 为空(24 个 ERROR 均为沙箱 socket PermissionError)。
- 故障注入关键项 E1、R1、M1_crash、M2_json、M2_crash、high 全 0。
- 真实数据确定性推演:26 个 timing 问题 new_solvable=26、validator_accepted=26,其中 6 个旧规则下不可解。
- 回放:`{"ok": 149, "regression": 0, "fixed": 22, "still_failing": 32, "unsupported": 0}`;录制回复仅两行分类变化,均为改善(`audio_plan_v1` unsupported→ok、`rough_audio_timing_1_repair` still_failing→fixed,后者即 vo_3 阻塞句获同等编辑权限)。

## 7. 量化数据
26 个真实 timing 问题:旧规则不可解 6 个,修复后 26/26 可解且校验接受;回放 fixed 22 条、regression 0。修复前后的运行耗时/费用未测量。

## 8. 经验与后续
- 修正反馈必须携带足够的推导信息(阻塞者、空闲窗口),否则模型只能瞎改。
- 时序类问题的验收可以用"确定性理想修复"推演(不调 TTS、不评判文稿质量,只验证排程可行性),成本为零。
