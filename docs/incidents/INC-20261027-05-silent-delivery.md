# INC-20261027-05 叶问打十个成片全程无声(-91 dB)却判合格交付

| 项 | 内容 |
|---|---|
| 发现日期 | 2026-10-27 |
| 影响范围 | 创作路线无人值守交付;错误成片(整片纯数字静音)且质量门放行 |
| 状态 | 已修复(2c288ff,任务 U1 的 S1 项;顺带修复联合质量门死代码 F1) |
| 相关提交 | 2c288ff |
| 相关任务 | 服务器任务 `full_tpl_yewen_20261027_v1`(参考:叶问打十个 02.mp4,release 7da9891);任务书 `.agent-team/tasks/U1_unattended.prompt.md`;报告 `wt/pfix/reports/unattended_outcomes_u1.md`;审查 `out/U2_review.*` |

## 1. 问题长什么样
成片 `story_finecut.mp4` 与全部音轨(voice / music_ducked / mix_raw / mix / candidate / proxy)实测均为 -91 dB,即纯数字静音。该参考的策略为 `timing_driver=action`、`speech_mode=none`:既无新配音(`audio_draft` 为空),也无背景乐。Qwen 声画审核仍返回 `music_audible: true`、`music_rhythm_status: matches`、"全程伴有史诗感…管弦乐"(幻觉,见 INC-20261027-06),于是 `content_delivery_ready=True`、`joint_quality_gate=True`,静音片被当成合格品交付。

## 2. 怎么查出来的
服务器真实任务核对(任务书 S 节逐条列出证据):`visual_audio.music_source`(当时 ~38 行)仅在 `timing_driver=='music'` 时返回原声;action 驱动且无新语音时返回 None;源音频按设计被移除,混音器只能产出全零样本。同时发现精剪级 `joint_quality_gate` 恒为 False 的死代码问题(叶问运行音频侧 joint=True、精剪级却报 False,排查清单 F1)。

## 3. 根因
代码问题(流程分支):参考声轨床只服务 music 驱动;"动作驱动+无新语音"这一组合落入无人处理的空档——既不配新语音、也不给背景声,而交付链上没有任何实测响度检查兜底(这是 INC-20261027-06 的根因)。直接原因:`music_source` 的驱动分支不完备;根本原因:交付质量完全依赖模型审核结论,无客观测量。

## 4. 解决方案
- `visual_audio.music_source`(visual_audio.py:25/40/71):无新语音时,action/speech/music 任意驱动均返回完整参考声轨作背景床;保留既有停止码(`audio_music_driven_work_requires_explicit_bed_with_new_speech`、`audio_music_driven_reference_has_no_soundtrack`);rough 路径共用同一函数(clean_chain.py:651)。参考床要求 `music_audible=True`,不再只对 music 驱动要求。
- 联合质量门(story_finecut.py:50):joint 为 True 当且仅当"fine 审 deliver + 音频 ready + technical verified + 量测通过 + 非 partial",删除创作路线错误依赖的死条件;产出 completed / completed_partial / 既有 needs 状态。
- 新测试:`test_s1_action_without_speech_uses_reference_soundtrack`、`test_s1_action_reference_bed_requires_audible_music`、`test_f1_joint_true_exactly_for_full_pass`。
通用性:按"驱动 × 是否有新语音"的完备组合表处理,任何参考模板同样成立。

## 5. 为什么不用其他方案
- 保持"仅 music 驱动给原声":直接原因即在此分支空档;无新语音时参考原声是唯一可用背景。
- 有新语音的 action 作品也自动铺参考原声:保留既有停止码(要求显式床),不擅自混入可能冲突的原声——沿用户既定策略。
- Qwen 审核修正回路:按任务书明确 out of scope(不改 audio_review.py),由实测门(INC-20261027-06)解决。
- F2(声画审核后的配音修订回路):任务书明确不加。

## 6. 怎么验收(unattended_outcomes_u1.md 实际输出)
- 新增 29 用例:`git archive 7da9891` 基线 29 failed(0.63s)→ 当前 29 passed(1.27s)。
- 全量:`9 failed, 1284 passed, 103 skipped, 24 errors`,FAILED 为基线子集(Additional FAILED 为空)。
- Replay:TOTAL 149 / 0 / 22 / 32 / 0,11 行差异逐行列明且均为诊断增强或前批既有效果。
- Fault check:E1=R1=M1_crash=M2_json=M2_crash=high=0。
- 全零样本与 -91 dBFS 输入在单元测试中判 silent。
- 真实服务器重跑(叶问):混音峰值 -5.2 dB、音乐 -8.2 dB(数字出处:事实清单,服务器重跑记录未归档)。

## 7. 量化数据
修复前:成片与全部音轨 -91 dB(纯静音)。修复后重跑:混音峰值 -5.2 dB、音乐 -8.2 dB。叶问之后一次交付 54.5 秒、平均 -16.1 dB、峰值 -3.2 dB(出处同上:事实清单)。

## 8. 经验与后续
- "驱动 × 语音"的组合表必须完备,任何空档都是潜在静音/无声交付。
- U2 审查曾指出配套遗留:丢弃台词后字幕阶段要用 `mixable_draft`(否则给不存在的语音烧字幕);当前代码 clean_chain.py:797 字幕已使用 `mixable_draft(final_story)`,该遗留已随 2c288ff 最终版修复。
