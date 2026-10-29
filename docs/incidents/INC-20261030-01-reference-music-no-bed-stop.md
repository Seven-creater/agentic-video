# INC-20261030-01 参考策略 music×新语音×无音乐床未先校验,分派处硬停整跑(少帅下飞机,1 次调用零产出)

| 项 | 内容 |
|---|---|
| 发现日期 | 2026-10-30 凌晨(e2e 自动修复第 11 轮) |
| 影响范围 | 创作路线(reference_rough_skill_v1)参考理解→执行分派;致命停机——歌词驱动 MV 类参考未配音乐床时,1 次模型调用(21,073 prompt tokens)后整跑作废,连出题都未开始 |
| 状态 | 已修复(840ea5c);部署后同模板嵌套重跑结果待补 |
| 相关提交 | 840ea5c(修复);ea3fb17(2026-10-19 引入该停止点) |
| 相关任务 | 服务器 run `/home/ubuntu/apps/agentic-video/shared/jobs/e2e_04_少帅下飞机_10300329`(模板 04_少帅下飞机/01.mp4);证据 `out/e2e/evidence_04_少帅下飞机_10.txt`;分析 `out/e2e/analysis_11.md`;修复报告 `docs/incidents/INC-20261030-e2e-reference-music-capability.md`(fix_11 所链);审核 `out/e2e/review_11.md`(pass);闸门 `out/e2e/progress.log` round 11 行 |

## 1. 问题长什么样
run_outcome.json:`outcome/status: stopped`、`stage: visual_audio.music_source`、`error/error_code: audio_music_driven_work_requires_explicit_bed_with_new_speech`、`exception_type: LibraryStopped`、`raised_at: …/src/omni_story/library/visual_audio.py:85`、`model_calls: 1`、`repairs: 0`、`last_call_id: glm_001_reference`、`final_video: null`。chain_failure.json:`requests: 1`,21,073 prompt + 1,172 completion tokens,`automatic_restart: false`。progress.log:03:29:01 起、03:34:16 停(spent ¥0.0275)。
但 glm_001_reference 回复本身完全合规:`strategy={reinterpret, mixed, music}`,theme/visible_evidence×3/value_judgment/cultural_context/uncertainties 齐全,判断有据(speech_form:"歌词以第三人称旁白式抒情演唱……",value_judgment.evidence 引 ASR 歌词)。

## 2. 怎么查出来的
e2e 证据包逐字段核对(analysis_11.md,对照 8619a85 源码,traceback 行号与该版一致):
- `model_calls: 1` → 影片库从未被查询,排除"素材不足";strategy 三键存在(`pipeline.py:1482-1484`)与枚举合法(`contracts.py:475-488`)均通过,排除协议失败。
- 对照黄金路径 `docs/golden_path_heihu_98e9427.md`:黑虎阿福 `strategy={reinterpret, mixed, action}`,`pipeline.py:1544` music 分支根本不进,没有额外护栏,只是没踩进死角;本次 `timing_driver=music` 进分支,`pipeline.py:1546` 调 `music_source`,bed 仅在显式 `--audio-music-bed` 时配置(`server_cli.py:429-430`),本次为 None(由代码路径反证:未走 configured 分支)→ `visual_audio.py:82` 短路、84(`speech_mode!=none`)为真、85 raise。
- `LibraryStopped` 是 RuntimeError(`state.py:23`),且发生在 glm.call 成功返回之后的本地代码,不在 `_repair` 协议修复通道内(该通道只管校验器 ValueError);错误串也不告知两个既有逃生口(`--audio-music-bed`/`--reference-strategy`)。

## 3. 根因
代码问题(提示词与校验/执行不一致 + 校验缺口 + 容错缺失),非素材不足(库未查询;缺的是执行输入——CLI 音乐床——而非模板素材;按 theme_transfer_policy `pipeline.py:1573-1580` 音乐计时本不是必须交付物,下游对 `timing_driver` 零处引用)。
直接原因:模型对歌词驱动 MV 的诚实判断 `{music, mixed}` 落进"music×新语音×无床"这一执行器唯一不可交付的组合角(`music_source` 设计契约 `visual_audio.py:72-77`:有新语音必须显式供床,且系统不做源分离)。
根本原因:提示词(`pipeline.py:1501-1503`)让模型从 speech/music/action 自由选 timing_driver,却从未告知该组合在无床时不可交付;校验只查键存在+枚举,不可交付组合畅行无阻,直到执行分派才由 `visual_audio.py:85`(ea3fb17 引入)硬停收场——无降级、无回退、反馈不可操作,一次 21k tokens 的合规理解被整跑作废。
潜伏同角(本次未触发):参考阶段 `speech_mode=none` 过关(复用原声)后规划再加旁白,`clean_chain.py:674` 会带非空 audio_draft 再调 `music_source`,在同一析取上再次硬停。

## 4. 解决方案(840ea5c,6 个文件 +226/−2)
- `src/omni_story/library/contracts.py:491` 新增共享合同 helper `creative_reference_strategy_contract`:无显式音乐床且有新语音时 `timing_driver` 只允许 speech/action;有床或 `speech_mode=none`(对应原声复用路径)时全部取值开放。提示词与校验器共用同一允许矩阵,"提示词与执行不一致"从源头关闭。
- `src/omni_story/library/pipeline.py:1485` 在新 reference 回复校验中检查该矩阵:不可执行组合抛 `ValueError('creative_reference_music_requires_explicit_bed_with_new_speech')`,violation 附 `path($.strategy.timing_driver)/value(music)/expected/repair_hint`(要求保留参考描述、不得假报无语音或假定系统会分离音乐)——进入既有 `_repair` 协议修复通道,模型可自行改选可执行策略;并在缓存参考/显式用户覆盖合并完成后、音乐源分派前(`pipeline.py:1550`)再核验最终策略,有效覆盖不被模型原始 music 选择误拦。
- `src/omni_story/library/replay_check.py:104` 重建生产校验闭包时传入归档 config,回放忠实使用当时记录的床配置与用户策略。
- 新增 `tests/test_reference_music_capability.py`(7 项)与 fixture `tests/fixtures/reference_music_capability/response.json`(复制证据完整回复 envelope,内部 `result.content[0].text` 与证据逐字一致)。
- 通用性:谓词只含 `speech_mode × timing_driver × 是否显式配床` 三个任何参考都有的合同字段,不读角色/台词/影片/模板名;参考的音乐形式仍如实记录在 speech_form/visual_form(延续"主旨可迁移、表达方法另记"既有语义)。无自动改写、自动降级、新增重试;`music_source` 原硬停保留为纵深防御,原声混用保护、`music_audible/music_rhythm_status` 实测审核、音量门禁均未放宽。

## 5. 为什么不用其他方案
- 自动降级 music→speech 并写 artifact(analysis_11 主修复):无实测证明音乐对目标作品可有可无,直接改写持久化策略会掩盖目标变化、等于让记录替模型说谎;改为让模型在带 repair_hint 的协议修复里自己重选。未采用。
- `music_source` 对该组合返回 None 不 raise(analysis_11 修复 2):同样撤掉音乐要求且到该处已太晚;`speech_mode=none` 晚发角已有 `validate_audio_draft` 既有约束兜底(`contracts.py:1108-1109`,review_11 已核实),不为推测增加容错。未采用。
- contracts 里把 music×speech 组合判非法:MV 参考客观存在,配床时该组合恰恰合法可交付,静态判非法使参考无法被如实表征。
- 自动源分离参考音轨生成床:重能力、质量不可验证,违反既有设计承诺(`visual_audio.py:75` "never a separated/pure music claim")。
- 按模板/歌词特征特判:任务规范明令禁止,且谓词不碰内容即可闭合。

## 6. 怎么验收(均为真实输出,详见修复报告)
- 真实证据回归(新测试 7 项):改前 `7 failed in 1.44s`,改后 `7 passed in 0.10s`;覆盖真实回复×能力矩阵、新回复/缓存/显式覆盖三个入口、提示词与校验共用合同(有床/无床)、归档配置回放;用例内另断言动作策略与有效覆盖实际到达出题入口且未增加 reference 调用。
- 全量 pytest:`9 failed, 1601 passed, 104 skipped, 24 errors`——9 个 FAILED 与基线逐条相同;24 个 ERROR 为沙箱端口权限(未修改的 `tests/test_tts_http.py` 绑定 127.0.0.1 收 `PermissionError`,GLM 复核沙箱无此限制:1532 passed/197 skipped/9 failed,与 `baseline_2879e5c.txt` 一致)。
- replay_check:TOTAL ok 143/regression 6/fixed 21/still_failing 33;基线 164 条 ok/fixed 全保留、分类 0 变化;6 条 regression 为基线已有的 plan 阶段 `audio_line_window_below_minimum`,与 reference 无关。
- fault_check:要求的 E1/R1/M1_crash/M2_json/M2_crash 均为 0。
- GLM 审核 review_11:verdict=pass(1 个低严重度边缘发现,见 §8)。
- 机械闸门 progress.log round 11:`03:58:04 round 11: GLM review=pass`、`03:59:42 round 11: gates extra_failed=0 new_regressions=0 fault_bad=0`,随后 `DEPLOYED_840ea5c`。
- 部署后同模板嵌套重跑:已部署待跑,结果将写入 `out/e2e/progress.log`,下一轮补充本记录;未执行新的收费完整创作运行,不宣称已生成本参考对应成片。

## 7. 量化数据
- 修复前(该次运行):模型调用 1 次(21,073 prompt + 1,172 completion tokens)、repairs 0、花费 ¥0.0275、运行 03:29:01→03:34:16(约 5 分 15 秒)、成片 0。
- 修复后(本地):真实证据回归 7 失败→7 通过;全量 1601 通过/9 基线失败/24 沙箱错误;回放 0 新退化;fault 五项全 0。
- 部署后嵌套重跑对比(耗时/调用数/结局):未记录(进行中,结果写 progress.log 后补)。

## 8. 经验与后续
- 给模型的自由度必须与执行器能力对齐,且同一约束要在"提示词-校验器"两端用同一份合同表达(共享 helper + 静态测试断言 prompt 内嵌其 JSON,防漂移回退)。
- 描述性判断(参考是什么)与执行性决策(新作品怎么做)分开:降/改策略走协议修复让模型自己重选,不系统替它改写。
- 遗留 1(review_11 唯一发现,low,未修):运维显式传不可执行的 `--reference-strategy`(music 计时+有声+无床)时,`pipeline.py:1486` 将覆盖值代入新回复校验器,配置错误被归因为模型协议失败——空耗 1 次 repair 后以 `model_protocol_repair_exhausted:reference` 停止(改前是干净硬停、无额外调用)。建议 validator 内有 override 时跳过矩阵检查,由 `pipeline.py:1550` 循环外兜底;待后续小修。
- 遗留 2:部署后同模板嵌套重跑结果待补(progress.log)。
