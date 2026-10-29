# 标准路线：黑虎阿福完整交付运行（代码 98e9427，question_first 新路线）

| 项 | 内容 |
|---|---|
| 任务目录 | `qfs_full_heihu_20261029_b/…/evaluations/98e9427`（服务器路径见证据原文，本文只引相对段） |
| 代码版本 | 98e9427（路线相关提交区间 `cf9a1aa…98e9427`，共 10 个提交） |
| 路线 | `--material-search question_first`，workflow `reference_rough_skill_v1`，creative_audio 开启 |
| 结果 | outcome=delivered；成片 `final_subtitled.mp4` 17.9 s（参考 18.2 s）；11 句台词；单一选角"太郎"贯穿全片；候选全部纯 3D |
| 证据 | `.agent-team/out/e2e/golden_heihu_evidence.txt`（脱敏截断版；下称"证据"）。本文事实均取自证据原文或本仓库代码；推断单独标注；查不到的写"未记录" |

读法约定：时间取自证据 call timeline 与 state.json（UTC，HH:MM:SS）；耗时=完成−提交；代码引用为 `文件:行号`（仓库根相对路径）；"调用次数"指 GLM 账本调用（qfs 直连与 MCP 同记一个账本，编号 glm_001–glm_192），声画审核另走 omni 模型，TTS 不在 GLM 账本。

## 1. 全程路线表

总览（按真实调用时间线；GLM 调用共 192 次 = glm_001–glm_192，无空号）：

| # | 阶段 | 调用 | 起止 | 耗时/跨度 | 自动修正 |
|---|---|---|---|---|---|
| 1 | 参考理解 | 1（glm_001_reference） | 12:34:16–12:35:38 | 82 s | 无 |
| 2 | 出题 | 1（glm_002_qfs_questions） | 12:35:38–12:36:05 | 27 s | 无 |
| 3 | 粗看 | 136（qfs_skim_*） | 首个完成 12:36:05，末个完成 12:40:51 | 跨度约 4 min 46 s | 无（坏回复只丢单张，见 #3.3） |
| 4 | 精看 | 30（qfs_close_0…29，glm_139–168） | 12:40:57–12:44:34 | 跨度约 3 min 37 s | 无 |
| 5 | 窗口观察 | 15 = 13 窗口 + 2 修复（glm_169–183） | 12:44:41–13:00:45 | 跨度约 16 min 04 s | 2 次（见 1.5） |
| 6 | 规划 | 3 = 1 + 2 修复（glm_184–186） | 13:02:18–13:06:47 | 跨度约 4 min 29 s | 2 次（见 1.6） |
| 7 | 粗剪渲染 | 0（本地 ffmpeg） | 13:06:47 后、13:08:23 前 | 墙钟未记录 | 无 |
| 8 | 配音（粗稿 TTS 合成） | 0 次 GLM；TTS 若干次（次数未记录） | 13:08:23 之前完成 | 未记录 | 1 次零字节补发（见 1.9） |
| 9 | 配音时长 | 1（glm_187_rough_audio_timing_1） | 13:08:23–13:09:08 | 45 s | 无（该调用本身就是修正） |
| 10 | 精剪观察·规划·渲染·盲看·审核 | 4（glm_188–191）+1 次本地渲染 | 13:09:25–13:14:47 | 跨度约 5 min 22 s | 无 |
| 11 | 配音（最终混音，复用缓存） | 0 次 GLM/TTS（全缓存） | 13:14:47 后约 5 s 内 | 未记录 | 无 |
| 12 | 声画审核 | 1（omni qwen3.8-omni-flash） | 13:14:52.709–13:15:15.645 | 22.9 s | 无 |
| 13 | 字幕烧录 + 最终画面审核 | 1（glm_192_final_layout_review_32596252a61b55ec） | 字幕约 13:15:15–13:15:33（推断）；审核 13:15:33.114–13:16:19.197 | 审核 46.1 s | 无 |
| 14 | 交付 | 0 | 13:16:19 后 | 未记录 | 无 |

用户清单中的"配音"在实际时间线上分两段：粗稿 TTS 合成发生在"配音时长"调用之前（先实测才可能发现超窗），最终混音发生在精剪审核之后（creative_audio 复用缓存台词）。本表如实拆开。

### 1.1 参考理解
- 负责：`src/omni_story/library/pipeline.py:1520`（`execute` 内 `glm.call('reference', …)`；提示词拼装 `pipeline.py:1488-1519`；校验 `contracts.validate_reference`，`pipeline.py:1477-1486`）。
- 调用：1 次；12:34:16–12:35:38（82 s）。
- 输入：参考片 `template_hunter/inbox/15_黑虎阿福/02.mp4`（时长 18.2 s，sha256 75474b91…，证据 creative_audio 段）+ 本地 ASR 文字（`reference_asr.json`，内容未记录）。
- 产出：`reference_reading.json`。证据未含原文；可从下游引用还原的字段：`theme`="热血打斗与'喊招式名'式幽默的结合"、`intended_takeaway`（可迁移的是"喊名—出招"动作而非具体角色）、`speech_form`（角色第一人称喊话为主、无旁白式叙述）、`visual_form.evidence` 3 条（4–9 s / 13–14 s / 17–18 s）、`strategy`={adaptation_mode: reinterpret, speech_mode: mixed, timing_driver: action}（均见 av_review 请求头 `reference_goal`/`strategy`）、`visible_evidence` ≥5 条（由出题反推，见 1.2）。
- 判据：`validate_reference` 要求 value 字段齐全；creative 路线必须有 `strategy` 三键（`pipeline.py:1482-1484`）。后续出题还要求 `visible_evidence` 非空（`question_first.py:559-561`）。

### 1.2 出题
- 负责：`src/omni_story/library/question_first.py:851`（`QuestionFirstRunner.run` → `transport.call('qfs_questions', …)`）；节拍骨架 `reference_beats` `question_first.py:557-563`（每条 visible_evidence 生成一个稳定节拍）；校验 `validate_reply` `question_first.py:404-495`。
- 调用：1 次；12:35:38–12:36:05（27 s）。
- 输入：参考理解全文 + 已核验的影片演员表（`film_cast`，来自 input_lock 配置）+ target_style=3d。
- 产出：`question_first/questions.json`：5 个节拍 b1–b5（每个含 `must_see`、5 条 `equivalents`、`dub_can_carry`、`roles`、`reference_evidence`）；选角 `casting`：A_boastful_villain→太郎（别名 大龙/大龍/残豹/殘豹/Tai Lung，films=[1]，rough_part="影片后段与主角对决的打斗段落"）；`dropped_casting`=[]；`target_style`="3d"。
- 判据：节拍 id 必须按 visible_evidence 顺序对应（`question_first.py:413`）；选角必须是影片演员表内的名字（`qfs_questions:no_valid_film_casting`，`question_first.py:445`）；`visible_evidence` 必须存在（`question_first.py:561`）。

### 1.3 粗看
- 负责：`question_first.py:862-868`（每个影片 contact sheet 一题 `qfs_skim_{film}_{index}`）；并发执行 `_run_jobs` `question_first.py:815-842`（DirectTransport，max_parallel=8）；校验 `validate_skim` `question_first.py:346-391`。
- 调用：136 次；首个完成 12:36:05、末个完成 12:40:51（约 4 min 46 s）。
- 输入：各影片 tile 网格 sheet（媒体为 sheet 图）+ 各节拍 id/must_see/equivalents。
- 产出：`question_first/skim.json`（每 sheet 一份 tile→节拍 命中，含 confidence 与 who）。单张坏回复只丢该 sheet 并记入 `unknown_calls.json`，不重发不致命（`question_first.py:822-838`，提交 c3beebb）。本次 `unknown_calls.json` 内容未记录（证据未含）。
- 判据：`validate_skim` 要求 tile 编号能映射回 sheet 的真实 tile、节拍 id 合法；破 JSON 走 tile 抢救（`salvage_tiles` `question_first.py:101`），内层 tile 不得当整份回复（c3beebb）。

### 1.4 精看
- 负责：`question_first.py:873-911`（`run` 的 close 循环）；区域合并排序 `select_regions` `question_first.py:217-255`；切片制作 `make_clip` `question_first.py:275-280`（640 宽、8 fps、≤20 s 的 mp4）；选角锚点 `update_cast_anchors` `question_first.py:497-530`；跨节拍路由 `route_candidates` `question_first.py:533-553`；缺料判定 `material_gaps` `question_first.py:937-949`。
- 调用：30 次（qfs_close_0…qfs_close_29，glm_139–glm_168）；12:40:57–12:44:34（约 3 min 37 s）。
- 输入：每个命中区域一个切片（clip_window=命中区间前后各扩 4 s、上限 20 s，`question_first.py:258-265`）+ 节拍 must_see + 当前锚点。
- 产出：`question_first/report.json`：逐节拍候选（关键字段 verdict/satisfies/equivalent_id/in_s/out_s/who/mouth_moving/style/confidence/also_satisfies/tts_needed/film_in_s/film_out_s/film/title/clip/still）+ `cast_anchors`（太郎锚点在此建立）+ `question_first_gaps`。证据只含 b1 前 4 个候选：close_001（film 3715.5–3718.0 s，medium）、close_002（4268.7–4270.4 s，high，also b3）、close_003（4495.5–4500.0 s，high，also b2/b3/b4）、第 4 条（切片内 4.0–8.5 s，high，also b1/b3/b4，film_in/out 未记录——原文截断）。b2–b5 候选与 gaps 明细未记录（report.json 截断）。
- 判据：候选 verdict ∈ {usable, partial} 且 style=3d 才可入选窗口（`candidate_window` `question_first.py:925-934` 硬性 3D 过滤，faa499b）；锚点一旦建立后续节拍不得换角（`question_first.py:507` 注释与逻辑）；所有需画面节拍至少有一个可用候选，否则记入 `question_first_gaps`，若连一个可观察窗口都给不出则整跑停止（`pipeline.py:1647-1649` `question_first:material_gap`）。

### 1.5 窗口观察（fine）
- 负责：合成 search_0 `to_search_result` `question_first.py:967-1010`（在 `pipeline.py:1735` 作为 `search_0` 记录，不买新调用）；逐窗口观察循环 `pipeline.py:1849-1973`（fine 调用在 `pipeline.py:1930`）；字幕黑边测量与遮罩 `subtitle_bar.py:73/142`（经 `pipeline.py:68-76` 挂到窗口）；校验 `contracts.validate_fine` `contracts.py:650-714` + 实体绑定 `bind_observed_entities` `question_first.py:1022`。
- 调用：15 次 = 13 个窗口 + 2 次格式修复；12:44:41–13:00:45（约 16 min 04 s）。
- 输入：13 个观察窗口（`candidate_window`：候选 film_in/out 前后各扩 2 s、上限 90 s，`question_first.py:925-934`），每个带 question（节拍 id+must_see）与 `qfs_identity`（太郎锚点实体，源 sha 命名空间）。
- 产出：`watched_windows.json` 13 条：12 个来自 src_24675c65978d5360（4266.7–4272.4、2434.0–2442.5、682.5–687.5、410.5–422.5、409.5–414.5、656.0–661.0、2434.0–2444.0、3599.0–3607.0、4359.5–4368.5、2453.0–2465.0、4493.5–4502.0、3713.5–3720.0，单位 s），1 个来自 src_5f9ca95e53664193（2236.0–2241.0 s）。每条含 observation（roles/events/usable_ranges）、`question_first_subtitle_bar` 与遮罩。
- 自动修正：2 次。glm_172（window_51fb153113d70124）回复违反 `fine:usable_role_missing_event_evidence`（`contracts.py:710`：usable_ranges 引用了没有事件证据支撑的角色）→ glm_173 修复（12:48:30–12:49:23）；glm_176（window_55538f454ac2c94b）违反 `fine:usable_range_event_does_not_overlap`（`contracts.py:705`：可用区间与所引事件区间无重叠）→ glm_177 修复（12:54:20–12:55:00）。修复只重发该窗口一次（`_repair` 后缀），再失败则该窗口不计入已看（`pipeline.py:1941-1946`）。
- 判据：`validate_fine` 全量通过（window_id/source_id 对齐、事件与可用区间在窗口内、角色身份证据、实体命名空间、黑边遮罩测量一致 `contracts.py:653-657`）。

### 1.6 规划（粗剪计划 plan_0）
- 负责：`pipeline.py:2090-2192`（`validate_current_plan` + `correct_plan`/`glm.call('plan_0', …)`）；合同校验 `contracts.validate_plan`（含 cast 合同 `validate_task_cast` `contracts.py:717-757`，`plan:duplicate_cast_entity` 在 `contracts.py:733`）；台词窗下限 `line_windows.validate_draft_line_windows` `line_windows.py:159-180`（下限 max(0.7, 0.18×spoken_chars) s，`line_windows.py:7-23`；有同文同声 received_tts 回执时按实测时长放行，`line_windows.py:70-81`）。
- 调用：3 次；13:02:18–13:06:47（约 4 min 29 s）。前一段 13:00:45–13:02:18（约 93 s）无 GLM 调用；推断为导航拼图（contact sheet）构建（`pipeline.py:1974-1978`），证据无直接记录。
- 输入：参考理解（含 question_first_gaps）、13 个已看窗口（含 usable_ranges/事件/实体）、slots（节拍任务）、台词与镜头联合合同、渲染能力表。
- 产出：`plan_0.json`（证据未含原文）。可从下游还原：8 个 segment（seg1–seg8，0–17.9 s，见 `rough_audio/selection.md`）+ 11 句 audio_draft（修订后时间见 §2.4）+ cast（太郎绑定）。渲染执行指纹未与其他计划重复（否则 `pipeline.py:2233-2236` 会停止）。
- 自动修正：2 次。glm_184 回复违反 `plan:duplicate_cast_entity`（cast 里同一 entity 出现两条）→ glm_185 修复（13:04:09–13:05:18）；glm_185 回复违反 `audio_line_window_below_minimum`（某句台词推导窗口 < max(0.7, 0.18×字数) s；98e9427 的反馈会列出每句的推导窗口与所挂镜头）→ glm_186 repair2（13:05:18–13:06:47）。glm_186 的回复是完全通过、还是"仅剩台词窗违规时带警告继续"（98e9427 允许，警告记入 `line_window_exhaustion` 并最终出现在 run_outcome）：未记录（证据未含 library_state 与警告清单）。
- 判据：`validate_plan` 通过（含 cast 绑定只能用 identity_confirmed 的角色、`reinterpret_rough_requires_silent_source_audio`（`pipeline.py:2104-2105`）、台词形式与说话者校验 `pipeline.py:2106-2109`）；进入渲染还需执行变化非空。

### 1.7 粗剪渲染（render_0）
- 负责：`pipeline.py:2252-2256`（`render.render_library_video`，creative 分支带 caption_reflow、不烧字幕、fps 30）；creative_audio 下渲染后直接写 `result.json`（status=rough_planned，`review_kind`=not_reviewed_until_audio_delivery）并返回（`pipeline.py:2257-2270`）——粗剪阶段不买内容审核调用，审核延迟到配音后。
- 调用：0 次 GLM；本地渲染。墙钟耗时未记录（时间线上介于 13:06:47 与 TTS/13:08:23 之间）。
- 输入：plan_0（8 段 EDL，audio_mode=silent）。
- 产出：`render_0/final.mp4`：17.9 s、fps 30、audio_mode silent、sha256 8df17ea6…、逐段 provenance（`render_0/render_result.json`）。该文件即后续链路的 chain_parent。
- 判据：`measured_duration_s`=17.9 s 与计划一致；后续 `clean_chain.py:605` 校验字节未变。

### 1.8 配音时长（rough_audio_timing_1）——先有实测才有此步
顺序说明（代码事实）：粗稿 TTS 合成在前（`clean_chain.py:658` `story_context.synthesize_draft`），`schedule_draft`（`story_context.py:367-396`）用实测时长排表；只有出现 `timing_problems`（超窗/锚点重叠）才调 `revise_rough_audio_timing`（`clean_chain.py:661` → `visual_audio.revise_draft_timing` `visual_audio.py:112-184`），修完用缓存重合成变更句（`clean_chain.py:663`）。
- 调用：1 次（glm_187_rough_audio_timing_1）；13:08:23–13:09:08（45 s）。
- 输入：story_context（含全部 11 句草稿、received_tts 实测时长、timing_problems 及每句的阻塞句/空闲区间）、粗片静音代理。
- 产出：`rough_audio/draft_timing_revision.json`（证据未含原文）；修订后的最终台词表写入 `rough_audio/script.md`（11 句，时间 0.00–17.30 s，见 §2.4）。修订只允许改 editable_line_ids 的 text 与 start_s/end_s，voice/mode/segment_ids/speaker 逐字段保持（`visual_audio.py:133-158`）。
- 判据：修订回复通过 `revise_draft_timing` 校验（无关句逐字段一致、可编辑句窗口 ≥ 下限）；重排后 `timing_problems` 为空（本次为空——若仍超窗则 `drop_timing_lines` 丢句转 partial，`story_context.py:399-411`；修尽仍失败走 `clean_chain.py:480-485` 的 exhausted 分支）。

### 1.9 配音（TTS 合成与补发）
- 负责：`story_context.synthesize_draft` `story_context.py:313-348`（逐句调 `tts.synthesize_line` `tts.py:432-448`，内容+音色一致的句子跨阶段复用缓存）；阿里 TTS 零字节表头超时的"每任务最多一次"自动补发：`tts.py:438-448` → `tts_retry.automatic_retry_once`（账本 `tts_automatic_resend.json`，`tts_retry.py:261` 起）；粗稿技术预览混音 `clean_chain.py:667-678`（`audio_render.mix_speech` `audio_render.py:293`）。
- 调用：TTS 次数与耗时未记录（不在 GLM 账本；证据未含 TTS 状态目录）。本次发生 1 次零字节补发且成功——该事实来自 `docs/incidents/INC-20261028-02-tts-zero-byte-resend.md`（提交 c296937，在 cf9a1aa 之前）的验收记录："首次真实触发：98e9427 那次黑虎阿福运行中，补发成功"；补发落在哪一句、耗时多少：未记录（`tts_automatic_resend.json` 不在证据内）。
- 产出：`rough_audio/tts/<utt_*>/state.json`（每句 received + 实测时长）+ `rough_audio/story_context.json`（received_tts、segments）；`rough_audio/preview.mp4` 技术预览（未含在证据）。
- 判据：每句拿到有限正数 `actual_duration_s`（`story_context.py:342-344`）；排表后无残留 timing_problems（§1.8）。

### 1.10 精剪（观察·规划·渲染·盲看·审核）
- 负责：`story_finecut.execute_finecut` `story_finecut.py:1057`（由 `clean_chain.py:758` 调用）→ `StoryFinecut.run` `story_finecut.py:942`：观察 `story_finecut.py:987`（PREFIX='chain_e2e_v1_fine_'，`story_finecut.py:19`）、规划 `story_finecut.py:1014`、渲染 `story_finecut.py:564`、盲看 `story_finecut.py:602`、对照审核 `story_finecut.py:656`；裁决一致性合同 `story_finecut.py:24-30`。
- 调用：4 次 GLM + 1 次本地渲染：
  - fine_observe（glm_188）13:09:25–13:10:15（50 s）——看的是粗片全片静音代理，自主划分信息段；本次无 inspect 局部抽看（`skill_finecut/inspection_results.json`=[]）。
  - fine_plan（glm_189）13:10:15–13:11:28（73 s）——产出 `skill_finecut/plan_0.json`：8 段 clip_1–clip_8（全部 source_id=chain_parent，即在父片输出时间轴上重切；source_crop=[0,132,1920,816] 统一裁掉上下黑边条，来自 `parent_layout` 的 picture_crop，`story_finecut.py:559`），段边界 0–1 / 1–4 / 4–5.9 / 5.9–6.9 / 6.9–10.9 / 10.9–12.9 / 12.9–14.9 / 14.9–17.9 s（末 4 段边界由 selection.md 段界推出；plan_0.json 原文截断于 clip_5）。
  - 渲染：`skill_finecut/render_0/final.mp4` 17.9 s、fps 30、silent、sha256 0a8e035…（墙钟未记录；位于 13:11:28–13:11:51 之间，推断）。
  - fine_blind（glm_190）13:11:51–13:13:45（114 s）——无上下文盲读静音成片。
  - fine_review（glm_191）13:13:50–13:14:47（57 s）——对照参考目标审实际成片。
- 自动修正：无（无 _repair 调用，也无 revise 二版：审核裁决为 deliver，可由"无 revise 调用且链路交付完成"推出；review 回复原文未记录）。
- 判据：观察/规划过各自的 observation_check/plan_check；盲读与对照审核过 `review_check`（`story_finecut.py:569-584`，问题须带时间证据）；对照审核 `next_action`=deliver 且 `missing_core_meaning`=[]（裁决一致性合同校验 `story_finecut.py:634-655`）；渲染时长 17.9 s 与父片一致。

### 1.11 配音（最终混音，复用共享草稿）
- 负责：`visual_audio.VisualAudio.run` `visual_audio.py:716`；共享草稿路径 `visual_audio.py:734-767`（selection/finecut 已看过遮罩像素，直接携带粗稿台词，不再买 observe/plan——本次因此没有任何 audio_plan_v* 调用）；台词复用合成 `perform` `visual_audio.py:745-755`；混音 `mix_speech` `visual_audio.py:806`。
- 调用：0 次 GLM、0 次新 TTS（全部命中缓存，`story_context.py:329-334` 的回执复用）；耗时未记录（约在 13:14:47–13:14:52 之间完成）。
- 输入：精剪成片（0a8e035…）+ 11 句已合成台词 + 故事上下文。
- 产出：`skill_finecut/creative_audio/candidate_v0.mp4`（6,593,580 字节；sha256 7bde28d…）+ 混音回执与 `audio_measurement`（§2.5）。
- 判据：混音后实测通过（`audio_measurement.passed`）；无残留 timing_problems（否则 `visual_audio.py:781-801` 转needs_audio_revision）。

### 1.12 声画审核（av_review_v0）
- 负责：先实测后送审 `review_measured_candidate` `visual_audio.py:47-70`（实测静音的候选不送 AV 模型，并做"审核结论 vs 实测"矛盾检查 `audio_measurement.review_contradictions`）；送审 `review_final_video` `visual_audio.py:187-271` → `audio_review.review_audio_video` `audio_review.py:135`（policy `actual-audio-video-review-v1`，`audio_review.py:21`）。
- 调用：1 次（qwen3.8-omni-flash，非 GLM 账本）；提交 13:14:52.709302，收到 13:15:15.644779（elapsed 22.912 s）；usage：prompt 12,124 tokens（含 audio 121 / video 10,694）、completion 470、total 12,594 tokens。
- 输入：candidate_v0.mp4（>内联上限，先转 `actual_av_proxy_v0.mp4` 音拷贝画代理，`visual_audio.py:195-213`；代理未含在证据）+ reference_goal（theme/intended_takeaway/speech_form/visual_form/visual_goals 5 条）+ strategy。
- 产出：`creative_audio/review_v0.json`：`apparent_story`（完整复述雪豹打斗+喊招式名+结尾"人先躺平了"反转）、`theme_alignment`="matches"、`speech_clear`=true、`music_audible`/`music_rhythm_status`=null（非音乐驱动）、`missing_core_meaning`=[]、`repair_at`="none"、`issues`=[]、`limitations`=[]。
- 判据：`content_ready`（`visual_audio.py:26-40`：实测通过 + 无矛盾 + repair_at=none + 缺义为空；music_expected=false 时不要求 music_audible）→ `content_delivery_ready`=true、`joint_quality_gate`=true、status=delivered（`visual_audio.py:810`）。

### 1.13 字幕烧录 + 最终画面审核
- 负责：字幕烧录 `clean_chain.py:793-802`（`audio_render.subtitle_final_video` `audio_render.py:29`，单本地化字幕政策）→ `final_subtitled.mp4`；全画布终审 `clean_chain.py:803-810` → `final_visual_review.review_final_layout` `final_visual_review.py:12-80`（stage 名 `final_layout_review_<sha 前 16 位>`，`final_visual_review.py:60`；代理必须无 ROI 裁剪，`final_visual_review.py:20-21`）。
- 调用：1 次 GLM（glm_192）；提交 13:15:33.113513，完成 13:16:19.196750（46.1 s）；usage：prompt 7,376、completion 271（含 reasoning 119）、total 7,647 tokens。字幕烧录本身无调用、耗时未记录（推断位于 13:15:15–13:15:33 之间，依据代码顺序）。
- 输入：`final_subtitled.mp4`（sha256 32596252a61b55ec…）全片正常速度代理。
- 产出：`final_visual_review/32596252a61b55ec/review.json`：status=pass，framing_ok/subtitle_ok/no_credits/no_distortion/edit_coherence_ok 全 true，issues=[]，`full_canvas_preserved`=true，5 条 visual_notes（上下黑边正常、字幕固定清晰无重叠、无演职员名单、00:07/00:10 白闪为打斗特效不构成遮块、镜头按"豹王出场→连招→乌龟点穴→倒地"连贯）。
- 判据：五项全 true 且 issues 为空才可 pass（`final_visual_review.py:57-58`）；不 pass 则整跑转 needs_visual_revision（`clean_chain.py:808-810`）。

### 1.14 交付
- 负责：链路结果 `clean_chain.py:864`（`chain_result.json`，含全部缓存绑定 sha）；终局记录 `run_outcome.write_run_outcome` `run_outcome.py:75-149`（汇总 audio_measurement、line_window_warnings、tts_automatic_resend、模型调用量）。
- 产出：`run_outcome.json`：outcome="delivered"、status="completed"、final_video=…/98e9427/final_subtitled.mp4、partial_reasons=[]、audio_measurement（§2.5）。`skill_finecut/result.json`：policy rough_to_story_finecut_v1、status="completed"、partial=false、final=delivery/story_finecut.mp4（与 candidate_v0 同 sha 7bde28d…）、时长 17.9 s。run_outcome 的 model_calls/花费/tts_automatic_resend 字段：未记录（证据 JSON 在 line_windows 中途截断）。
- 判据：`joint_quality_gate`=true（延迟的粗剪内容审核由声画审核兑现，`clean_chain.py:765-777` 的 deferred 分支）+ 最终画面审核 pass + 音量实测通过，`run_outcome.py:83-86` 才记 delivered。

## 2. 关键决定与数字（均取自证据原文）

### 2.1 节拍题目（questions.json，5 个节拍）
| 节拍 | 功能 | 参考证据区间 | 口型需求 |
|---|---|---|---|
| b1 | 建立角色登场与自我介绍的因果起点 | 0–3 s（"我叫黑虎阿福"特写） | 需要 |
| b2 | 喊名与出招绑定，动作+喜剧双推进 | 4–9 s（螳螂拳/蛇形步/莲花飘/蝎子掌） | 需要 |
| b3 | 招式名夸张程度升级，荒诞感即笑点 | 10–11 s（"龙卷风摧毁停车场"恶魔特写） | 需要 |
| b4 | 持续"喊名—出招"节奏 | 12–14 s（"飞天锤 飓风踢""乌鸦坐飞机"） | 需要 |
| b5 | 招式落空后自嘲收尾，幽默闭环 | 15–18 s（"我成了瘸腿鹅"瘫坐） | 不需要 |
角色全部为单一角色 A_boastful_villain；`dub_can_carry` 每节拍 2–3 类（自我介绍/逐招喊名/战吼/自嘲）。

### 2.2 选角
A_boastful_villain → 太郎（别名：大龙、大龍、残豹、殘豹、Tai Lung），films=[1]，rough_part="影片后段与主角对决的打斗段落"；`dropped_casting`=[]；`target_style`="3d"。锚点在精看阶段建立后全程锁定（太郎在 4 个可见 b1 候选中一致出现，`who`=["太郎"]）。

### 2.3 候选与窗口
- b1 可见候选 4 个（见 §1.4；b2–b5 候选未记录）。候选逐条给出 `tts_needed`（如"自我介绍/挑衅宣言（如'我叫某某'）"）与 `also_satisfies` 跨节拍复用（close_002 也满足 b3；close_003 也满足 b2/b3/b4）。
- 最终观察 13 个窗口（§1.5 列表）：12 个在 src_24675c65978d5360（《功夫熊猫2008》，候选可见部分均为此片），1 个在 src_5f9ca95e53664193（2236.0–2241.0 s）。
- 粗剪 8 段（seg1–seg8，0–17.9 s，selection.md 逐段写明用途：登场近景/冲撞连招/对手反应特写/怒吼定格/跃击蓄势/扑拳高潮/中招僵滞/倒地收尾）；精剪 8 段在父片输出轴上同构重切并统一裁掉黑边。

### 2.4 台词时长（script.md，11 句）
| id | 窗口 (s) | 类型/说话者/音色 | 文本 | 窗长 (s) |
|---|---|---|---|---|
| line_intro | 0.00–0.88 | dialogue·A·Ethan | 豹王来了！ | 0.88 |
| line_move1 | 1.00–2.12 | dialogue·A·Ethan | 猛虎撞山！ | 1.12 |
| line_move2 | 2.12–3.16 | dialogue·A·Ethan | 铁腿扫千军！ | 1.04 |
| line_react | 4.00–5.36 | narration·旁白·Moon | 对手吓得僵住了。 | 1.36 |
| line_move3 | 5.90–6.86 | dialogue·A·Ethan | 烈焰龙卷！ | 0.96 |
| line_move4 | 6.90–7.70 | dialogue·A·Ethan | 黑豹扑月！ | 0.80 |
| line_move5 | 7.70–8.82 | dialogue·A·Ethan | 乌鸦坐飞机！ | 1.12 |
| line_move6 | 8.82–9.94 | dialogue·A·Ethan | 碎木断石拳！ | 1.12 |
| line_move7 | 10.90–11.86 | dialogue·A·Ethan | 飞天锤！ | 0.96 |
| （未记录 id） | 12.90–14.26 | dialogue·A·Ethan | 啊——！ | 1.36 |
| （未记录 id） | 14.90–17.30 | narration·旁白·Moon | 喊得震天响，人先躺平了。 | 2.40 |
9 句对白 + 2 句旁白；A 一律 Ethan、旁白一律 Moon。最短窗 0.80 s（4 字"黑豹扑月！"，0.18×4=0.72 s < 0.80 s，高于硬下限）；末两句 id 与实测值未记录（line_windows 截断）。

### 2.5 审核结论与音量实测
- 声画审核：theme_alignment=matches；speech_clear=true；repair_at=none；issues/missing_core_meaning/limitations 均 []；audio_notes 逐句听出 8 个招式名与结尾自嘲；visual_notes 四条确认画面呼应（结尾"躺平"直接呼应台词）。
- 最终画面审核：status=pass，五项全 true（§1.13）。
- 音量实测（dBFS；阈值：静音峰值 −45、单句 RMS −50、零底 −120）：
  - 混音 mix：峰值 −1.547、RMS −20.568、非静音；最终封装 final_mux：峰值 −1.556、RMS −20.600、非静音。
  - 人声 voice：峰值 −6.992、RMS −26.504、非静音；音乐 music：峰值/RMS 均 −120（静音），`music_expected`=false（本片无音乐床，属预期）。
  - 单句窗（可见 9/11 句）：全部非静音；峰值最强 line_move5 −6.992 dBFS、最弱 line_move1 −14.358 dBFS；RMS 区间 −27.922（move1）～ −20.956（intro）dBFS。末 2 句实测未记录。
- 成片：17.9 s（参考 18.2 s）、final_subtitled.mp4 sha256 32596252a61b55ec03d13eb5c86a9c9daed76c959f32dd546470d75f665237d7；AV 审核候选 6,593,580 字节（base64 8,791,440 字节）。

## 3. 这条路线为什么能成功（逐阶段对应的规则/修复）

提交区间 `cf9a1aa…98e9427`（10 个），另有两个更早的底层提交（c296937、2c288ff）承担本路线依赖的机制。

| 阶段 | 靠什么过关 | 提交 |
|---|---|---|
| 参考理解 | creative 参考必须产出 strategy/speech_form/visual_form（reinterpret/mixed/action），后续 reinterpret 分支据此全程用静音遮罩素材且不买粗剪内容审核 | 早于区间（creative 路线既有规则，`pipeline.py:1482-1515`） |
| 出题 | 节拍由 visible_evidence 一比一稳定生成；选角被限制在已核验影片演员表内并做别名归一（太郎/Tai Lung/残豹…归并为同一人） | faa499b；骨架 cf9a1aa |
| 粗看 | 破 JSON 抢救 tile、内层 tile 不当整份回复；单张坏回复只丢该 sheet 不停跑 | c3beebb；并行与账本豁免 cf9a1aa |
| 精看 | 锚点建立后不许换角（太郎全程一致的直接保证）；硬性 3D-only + 已知 2D 片头区间排除（纯 3D 成片的直接保证）；已收未知 qfs 调用不再阻塞后续阶段 | faa499b、801d00e、a5d692d |
| 窗口观察 | 确定性黑边字幕遮罩让新影片也能稳定测量（否则 mask 校验可杀掉窗口）；usable_ranges 与事件的重叠/角色证据两条校验带一次性格式修复（本次 2 次修复均消化） | 1013f88（遮罩）；修复通道为既有 `_repair` 机制 |
| 规划 | cast 合同（重复实体被拒后修复）；台词窗硬下限 max(0.7, 0.18×字数) s 带 received_tts 例外，且反馈列出每句推导窗口（本次 1 次修复后通过或带警告继续）；仅剩台词窗违规的修复耗尽不再杀跑，交由实测 timing 兜底 | 98e9427；1013f88 |
| 粗剪渲染 | creative 分支只做技术渲染、审核延迟到配音后（省调用且避免对无声片误判内容） | 2c288ff 路线的既定策略（`pipeline.py:2257-2270`） |
| 配音时长 | 只允许改问题句与阻塞句的 text/时间、其余逐字段锁死；问题句带阻塞句与空闲区间的可操作反馈；修尽则丢句转 partial 而非停机 | 7da9891（区间外，机制来源）；98e9427（下限反馈） |
| 配音 | 零字节表头超时签名"每任务最多一次"自动补发——本次为该机制首次真实触发且成功；同文同声回执跨阶段复用省掉重复合成 | c296937（区间外） |
| 精剪 | 共享草稿：selection/finecut 已看过遮罩像素，精剪直接沿用粗稿台词，不再买 audio observe/plan（本次 creative_audio 零 GLM 调用）；审核裁决一致性合同防"partial 却 deliver" | 2c288ff 路线既有；裁决合同见 `story_finecut.py:24-30` |
| 声画审核 | 实测静音门禁（静音候选不送审）+ 审核结论与实测矛盾检查；music_expected=false 时不强求听到音乐 | 2c288ff（区间外）；`visual_audio.py:47-70` |
| 最终画面审核 | 只审全画布、禁空间裁剪；pass 门槛=五项全 true 且零 issues | 既有（`final_visual_review.py`） |
| 交付 | joint gate 要求"实测+声画+画面"三者齐绿才 delivered；run_outcome 汇总含 line_window_warnings 与补发账本 | 2c288ff（区间外） |

一句话：cf9a1aa…98e9427 把 question_first 检索链路的各类"格式/证据违规"从杀跑改成"可修复反馈 + 局部容错"，把不可能的台词窗口挡在规划期、把临界窗口交给实测修订，再用延迟审核与实测门禁保证交付物真的可听可看。

## 4. 对照检查清单（其他模板失败时逐步对比）

通用表：不依赖黑虎阿福的具体内容。按阶段顺序查，第一处"与正常不同"即为优先排查点。

| 阶段 | 正常应看到什么 | 在哪看 | 若不同，常见原因 | 通用修法方向 |
|---|---|---|---|---|
| 参考理解 | 1 次 reference 调用收到；reference_reading 含非空 visible_evidence、theme/intended_takeaway、strategy 三键、speech_form、visual_form.evidence | `reference_reading.json`；账本 calls 里 `reference` | 无 visible_evidence（参考无动作变化或 ASR 干扰）；strategy 缺键 | 修参考提示词/校验的对齐；无语音参考不应强配 speech_mode；参考本身无表达目标时如实记不确定 |
| 出题 | questions.json：节拍数=visible_evidence 条数且顺序一致；casting 全部落在影片演员表内；target_style 明确 | `question_first/questions.json`；`unknown_calls.json` | 模型自创节拍或漏证据（id 顺序校验拒）；选角名字不在演员表/别名未归一；全部节拍选不出角 | 对齐"一证据一节拍"的提示与校验；选角限演员表+别名归一；允许配角留空（空 casting 不杀跑） |
| 粗看 | 每影片每 sheet 一条 skim 记录；tile 命中带 beats/confidence/who；unknown_calls 为空或少 | `question_first/skim.json`、`question_first/unknown_calls.json` | 破 JSON/内层 tile 被当整份回复；tile 编号对不上 sheet；大片 sheet 无命中 | 保持 tile 抢救与"坏回复只丢单张"；检查 sheet 生成与 tile 编号映射；无命中时降低命中门槛或加 sheet 密度 |
| 精看 | 每节拍有 verdict∈{usable,partial} 候选；候选 style 与目标一致、film_in/out 在窗口内、who 可映射演员表；锚点节拍后固定 | `question_first/report.json`（beats[].candidates、cast_anchors）；`close_*.mp4/.jpg` | 候选被 2D/风格化区间过滤光；口型/动作不满足 must_see 全部拒；锚点跨节拍漂移 | 确认目标风格过滤与已知风格化区间表；must_see 过严时靠 equivalents 扩展；锚点一旦建立禁止换角；无候选节拍进 gaps 而非停机 |
| 窗口观察 | 13 个左右窗口全部 watched；每条 observation 有 roles/events/usable_ranges 且区间互相咬合；subtitle_bar 遮罩有效 | `watched_windows.json`；账本 `fine_*`（含 `_repair`） | usable_ranges 与事件不重叠/角色无事件证据（各有一次修复机会）；黑边测量失败（新影片遮罩测不出）；修复再失败窗口作废 | 让模型按"事件先于可用区间"组织观察；遮罩用确定性黑边测量；单窗口修复失败只丢该窗口，检查是否还有足够窗口支撑各节拍 |
| 规划 | plan 有 segments+audio_draft+cast；segments 全部挂在已看窗口的 usable 范围；台词窗≥max(0.7,0.18×字数) s（有实测回执例外）；audio_mode=silent（reinterpret） | `plan_0.json`；账本 `plan_0(_repair…)`；`line_window_exhaustion` 工件 | cast 实体重复/绑定到未确认角色；台词窗不足；引用未观察区间；修复耗尽 | 反馈已列每句推导窗口——缩文本或扩窗/换挂更长片段；cast 只绑 identity_confirmed 角色；仅剩台词窗违规可带警告继续（实测兜底），其他违规耗尽则停 |
| 粗剪渲染 | render_result：measured_duration 与计划一致、fps/分辨率符合、audio silent、逐段 provenance | `render_0/render_result.json`；`result.json`（status=rough_planned） | 段超时/裁剪参数非法；素材字节变化 | 渲染参数走合同校验；上游 sha 不符先查缓存污染；rough_planned 即正常（内容审核延迟） |
| 配音时长 | 先有 received_tts 实测，timing_problems 非空才出现 `rough_audio_timing_1`；修订后 script.md 每句窗长≥实测时长 | `rough_audio/story_context.json`（timing_problems）、`rough_audio/draft_timing_revision.json`、`rough_audio/script.md` | 修订改了不可编辑字段被拒；空闲区间为空仍硬塞长句；修尽仍超窗 | 只改问题句+阻塞句的文本/时间；无解时缩短阻塞句或丢句转 partial（不硬混）；确认 received_tts 缓存未被改动 |
| 配音 | 每句 tts state.json status=received 且 actual_duration_s 为正；（偶发）`tts_automatic_resend.json` 记录一次零字节补发 | `rough_audio/tts/<utt_*>/state.json`、`tts_automatic_resend.json`、run_outcome 的 tts_automatic_resend | 零字节表头超时（自动补发一次）；其他未知结果按策略停机；同文同声缓存 sha 不符 | 只对精确零字节签名补发且每任务一次；其他未知不得重发（先查账本再人工授权）；缓存失效时重新合成而非复用 |
| 精剪 | observed 自主分段；plan 在父片输出轴上重切且段数/时长达标；blind 有 facts；review 的 next_action=deliver、missing_core_meaning=[]；无 _revise 循环 | `skill_finecut/observed.json`、`plan_0.json`、`render_0/render_result.json`、`result.json`（review 字段） | 审核报 revise（有实际输出问题）→ 一轮修订；报 reselect_material（核心缺义）→ 回溯选料；裁决与状态矛盾被一致性合同拒 | revise 只许局部改版且需实际输出问题佐证；reselect_material 时回看选料/节拍 gaps，不在精剪里硬凑；无进展（指纹相同）即停不再渲染 |
| 声画审核 | 实测通过后才送审；review：theme_alignment=matches、speech_clear=true、repair_at=none、issues=[]；music_expected=false 时 music_audible 可为 null | `skill_finecut/creative_audio/audio_result.json`、`review_v0.json`、`av_review_v0/{request,response,state}.json` | 实测静音/单句低于阈值（不送审直接修）；审核与实测矛盾；repair_at=audio/selection（缺义或形式被替代） | 先修混音（音量/丢句）再送审；矛盾以实测为准复核审核；repair_at=selection 回选料、=audio 改稿重混 |
| 最终画面审核 | status=pass：五项全 true、issues=[]；reviewed_proxy 无裁剪；字幕清晰无重叠 | `final_visual_review/<sha16>/review.json` | 字幕重叠/截断（烧录参数或 srt 时间）；多层黑边/主体缩小（渲染裁剪参数）；混入片尾名单（素材 content_range 漏排除） | 字幕版式走固定合同；黑边问题回查 render 的 crop/fit；名单问题补 content_ranges 排除后再重跑相关窗口 |
| 交付 | run_outcome：outcome=delivered、status=completed、partial_reasons=[]；final_video 指向 final_subtitled.mp4；audio_measurement.passed | `run_outcome.json`、`chain_result.json`、`final_subtitled.mp4.receipt.json` | joint gate 未绿（三审任一不过）→ status 对应 needs_*；partial 交付带 partial_reasons | 按对应阶段回修；partial 不是失败但须读 partial_reasons 定位丢弃内容 |

## 5. 已知局限

- **素材集中度**：可见候选与 13 个观察窗口中 12 个都来自第一部（src_24675c65978d5360，《功夫熊猫2008》），第二源只贡献 1 个窗口。多库源、跨片混剪的行为本路线未验证；检索排序是否总能收敛到单片也未验证。
- **单角色**：全片只有 A_boastful_villain 一个选角（太郎），对白为独角喊名+旁白。需要双角色绑定、交替说话者音色、跨窗口双人同框核验的模板，cast 合同与 speaker 校验路径未被本次覆盖（4e8d2d9 只验证了"配角可留空"）。
- **模板类型**：本参考是 18.2 s 动作类、timing_driver=action、speech_mode=mixed。音乐驱动（music_audible/music_rhythm_status 必须实测）、纯对白问答、无语音参考的路线分支本次完全未走。
- **时长尺度**：成片 17.9 s、8 段、11 句。长参考（数分钟）下的窗口预算、轮次与字幕量行为未验证。
- **协议修复依赖**：本次仍消耗 4 次 `_repair`（fine×2、plan×2）+1 次时长修订才通过，说明模型格式合规仍是主要摩擦点；修复预算或反馈质量回退时，同样的素材可能停在修复耗尽。
- **审核独立性**：声画审核是 1 次自动模型听看（qwen omni），最终画面审核也是模型判断；两者都替代不了人工观众验证（结果文件自身也如此声明）。

## 附：证据未记录项清单（本文相应位置已就地标注）

1. 参考理解回复原文（reference_reading.json）与 ASR 文本：未含，仅能从下游引用还原部分字段。
2. `question_first/report.json` 截断：b2–b5 候选明细、`question_first_gaps` 是否为空、`unknown_calls.json` 内容：未记录。
3. 根计划 `plan_0.json` 原文与 glm_186 是"完全通过"还是"带台词窗警告继续"（line_window_warnings 清单）：未记录。
4. 粗剪/精剪渲染与字幕烧录的墙钟耗时：无时间戳（只能给出区间推断）。
5. TTS 调用次数、各句合成耗时、零字节补发落在哪一句及耗时（`tts_automatic_resend.json`）：未记录；"补发成功"这一事实引自 INC-20261028-02 的验收记录。
6. run_outcome.json 尾段截断：model_calls、cost、line_windows 末 2 句实测、tts_automatic_resend 字段：未记录。
7. 精剪 fine_blind / fine_review 回复原文（盲读 apparent_story、审核 next_action 原文）：未记录（deliver 由"无 revise 调用且完成交付"推出）。
8. 13:00:45–13:02:18 的 93 s 空窗（推断为导航拼图构建）与 13:15:15–13:15:33 的 18 s 空窗（推断为字幕烧录）：证据无直接记录。
