# INC-20261027-06 Qwen 声画审核对静音成片幻觉判好("有管弦乐、节奏吻合")

| 项 | 内容 |
|---|---|
| 发现日期 | 2026-10-27 |
| 影响范围 | 创作路线声画审核(Qwen);错误成片(静音片获合格结论并交付) |
| 状态 | 已修复(2c288ff,任务 U1 的 S2 项:实测响度门 + 审核与实测矛盾即判不就绪) |
| 相关提交 | 2c288ff |
| 相关任务 | `full_tpl_yewen_20261027_v1`;`.agent-team/tasks/U1_unattended.prompt.md`;`wt/pfix/reports/unattended_outcomes_u1.md` |

## 1. 问题长什么样
给 Qwen 的代理视频是纯数字静音(-91 dB),审核回复却是 `music_audible: true`、`music_rhythm_status: matches`,并描述"全程伴有史诗感…管弦乐"。静音片由此通过质量门(与 INC-20261027-05 叠加)。

## 2. 怎么查出来的
叶问任务的审核回复与音轨实测对照:全部音轨 -91 dB 与"有管弦乐"直接矛盾。结论:不能信任模型对音频的任何断言。

## 3. 根因
外部服务问题(模型幻觉)叠加代码问题(容错缺失):审核结论无客观交叉验证;且静音候选根本不该送审——送审既花钱又制造幻觉机会。

## 4. 解决方案
- 新增 `src/omni_story/library/audio_measurement.py`:阈值单一来源——整混峰值 < -45 dBFS 判静音,句窗 RMS < -50 dBFS 判该句静音;mix.wav、最终 mux、music/voice stem 记录 peak/RMS;逐句按 [start_s, actual_end_s] 记录 voice RMS,写入回执与 audio_result。
- 门先于审核调用:测出静音(mix/任一句/该有声而无声的床)则 `content_delivery_ready=False`,走既有非交付分支(`needs_audio_revision`,reason 如 `audio_measured_silent` / `audio_line_silent:<id>` / `audio_music_bed_silent`),不发起 Qwen 调用。
- 交叉核对:审核声称 `music_audible: true` 而音乐 stem 实测静音、或 `speech_clear: true` 而全部语音窗静音时,记 `review_contradicts_measurement`(audio_measurement.py:45)并判审核不就绪。
- 测试:`test_s2_all_zero_and_minus_91_db_stems_are_silent`、`test_s2_failed_measurement_never_calls_av_review`、`test_s2_review_claims_contradict_silent_measurement`、`test_s2_normal_mix_passes_and_records_windows` 等。
通用性:阈值与"哪个 stem 该有声"由作品类型决定,不由模板决定。

## 5. 为什么不用其他方案
- 给 Qwen 审核加格式修正回路:排查清单 A7 已注明"修正需再调 Qwen,要用户拍板";实测门直接消除送审场景,更省钱。
- 信任审核、事后抽查:静音片已真实交付,证明事后不可靠。
- 全部交人工听片:与无人值守目标冲突。

## 6. 怎么验收
同 INC-20261027-05 第 6 节(同一批 29 用例、全量、replay、fault 输出)。静音判定单元测试覆盖全零数组与 -91 dBFS 输入;`test_s2_failed_measurement_never_calls_av_review` 断言测静音后零 Qwen 调用。

## 7. 量化数据
修复前:静音候选仍送 Qwen 且获"matches"结论。修复后:静音候选 0 次 Qwen 调用(测试断言);叶问重跑混音峰值 -5.2 dB、音乐 -8.2 dB(事实清单)。审核幻觉发生率未测量(可在服务器统计 `review_contradicts_measurement` 出现次数)。

## 8. 经验与后续
- 任何"模型对物理量的断言"都要有实测交叉核对;矛盾时以实测为准。
- 该模式后来推广为通用原则:GLM 认人、时长估算等均有客观校验层。
