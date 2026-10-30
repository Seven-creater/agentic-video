# INC-20261030-06 照搬参考调度为硬条件导致漏检，审核又把形式差异误判为主旨缺失

| 项 | 内容 |
|---|---|
| 发现日期 | 2026-10-30 |
| 影响范围 | 所有参考模板的 question-first 检索、精剪目标审核、创作 AV 审核；漏检、浪费调用、错误重选 |
| 状态 | 已修复（工作树，按用户要求不提交；未进行新的付费成片重跑） |
| 相关提交 | 基础 HEAD `93fd5d4`；本批无提交 |
| 相关任务 | `.agent-team/out/e2e/evidence_01_不烧心_*.txt`；测试夹具 `tests/fixtures/reference_value_20261030/` |

## 1. 问题长什么样

`不烧心`的问题把“站在柜台后”“摆手或制止式的手势”“柜台或单据”“店员姿态显出叹服”写进 must_see。用户提供的夜跑记录中 b3、b4 无候选。AV 审核把“店员与老板的双人对话形式未实现”列为 `missing_core_meaning`，同时指出画音割裂；后者仍是有效质量问题。

## 2. 怎么查出来的

逐块读取上述 evidence 的 questions / report / review JSON；日志在部分 beat 中间截断，夹具只保留 3 个完整 beat，未补造剩余问题。检查 `src/omni_story/library/question_first.py:26` 的旧 schema、CLOSE 的 equivalents 要求，再对照 `src/omni_story/library/story_finecut.py:625` 和 `src/omni_story/library/visual_audio.py:245` 的目标审核规则。使用 pytest 离线捕获实际调用方提示词，未访问模型服务。

## 3. 根因

提示词与用户“剪辑和配音重赋意义”的原则不一致：旧字段显式要求 object、result、shot_size，并把具体替代画面当满足条件；审核又要求 visual_form / speech_form 的形式对应。由此把可以由配音承载的价值、情绪、结果和情境变成画面硬门槛。画音矛盾与形式偏离也未独立表达。

## 4. 解决方案

`src/omni_story/library/reference_requirements.py:6` 提供同一最小视觉合同，供 questions、close、校验和意义迁移审核共享。must_see 仅允许 who_relation、action_class、shot_need、mouth_open_needed。前三项分别上限为 48、16、24 个 Unicode 字符：对应两个角色标签及关系、一个短动词类、一个镜头指令，避免塞入小剧本。长度门槛只管结构，不能替代视觉语义判断。

`normalise_must_see`（同文件第 53 行）对未知键、类型错误、超长值提供含路径及 hint 的 violation；questions 无修复轮，故完整移入 nice_to_have 并记录 normalised_must_see，不截字或推断新硬条件。旧 schema 也按未知键处理，不把旧调度重新变成硬条件。

`question_first.py:477` 按逐项 minimal_checks 处理可用性，并继续执行嘴动、身份和画风限制；nice_to_have_score 仅参与排序，评分格式坏时记账并归零，不丢候选；旧 equivalent_id 填错也只留未匹配记录，不否决最小事实已成立的镜头。partial 仍能继续精看，但不能消除最小要求的素材缺口。

精剪及 AV 调用方共用 meaning_transfer_contract，判断“画面 + 配音”能否传达 takeaway；形式差异不能单独成为核心缺项。picture_audio_coherence 独立输出状态并列明实际冲突；明确矛盾或不可核验时禁止交付。历史回复没有新字段时保留旧协议，绝不重写原判决。原 next_action / missing_core_meaning 一致性合同继续执行。

## 5. 为什么不用其他方案

- 不为“面馆”“平先生”添加模板特例：其他参考同样会过拟合调度。
- 不截断超长字符串：会制造残句并保留任意一段细节为硬条件。
- 不把历史 AV 负评自动改为通过：真实割裂仍需要重配音或重选。
- 不删除 identity / mouth / 3D / 正文规则：它们保护配音无法修补的可见事实。

## 6. 怎么验收

完整命令、原始输出及失败基线比较见 `reports/reference_value_understanding_r1_zh.md`。新增回归测试在原 HEAD 临时源码副本复现失败；修复后定向套件通过。真实 `不烧心` AV 负评仍不能交付；错误嘴动、身份、画风及明确画音矛盾均有负向保护测试。`audio_review.py` 请求及传输未改。

## 7. 量化数据

独立新增文件共 33 个测试场景：原 HEAD 29 个失败、4 个已有保护对照通过；修复后 33 个通过。相关定向套件共 181 个通过。全量测试 9 个 FAILED 与用户基线完全相同，另有 24 个 TLS 测试 setup 因当前沙箱禁止端口监听报错，在原 HEAD 同样复现。未测量新的在线候选召回率或成片质量。

## 8. 经验与后续

结构门槛应服务于最小可见事实；真实意义迁移仍需 GLM 看实际素材、AV 看实际成片。下次在线任务应比较候选数、素材缺口和画音割裂率；本次没有宣称已完成付费夜跑复验。计划协议根因另见 [INC-20261030-07](INC-20261030-07-plan-valid-choice-tables.md)。
