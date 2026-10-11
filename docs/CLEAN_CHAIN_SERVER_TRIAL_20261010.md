# 实验20261010：从参考生成实际粗剪再使用教师skill精剪

**上海2026-10-11：冻结版本706da57的双参考服务器完整功能测试通过，均succeeded/exit0，实际粗剪与精剪已直连下载并完成媒体、来源和时间轴核验。** 两份`joint_quality_gate=false`保持；本轮功能目标不要求视频质量通过，功能结论依据完整最终审计。

实际服务器环境为`/home/ubuntu/apps/agentic-video/releases/706da57`，完整源码SHA为`706da57ceb2cd7b0b6f0c6d2f1ccf6be7be229bd`。两路上海10月10日22:54:39启动，使用`--functional-test --parent-task`，无`--rough-task`，均从参考与电影库重新生成实际粗剪。原失败、父台账及历史unknown保留；短参考仅复用已收到的参考知识，避免重放历史未知完整参考请求。新证据根目录为`runs/server_review_contract_20261010`，启动凭据为`server_pair_start.json`。

### 当前706完整链终态

终态依据只读快照`snapshots/1791686996144447200.json`，所有owned子进程组已退出：

| 核验项 | 短参考 | 长参考 |
| --- | --- | --- |
| job ID | `4d756dbe43d644038b2a3a7c7c394c4f` | `2b49ba4d678645f2a1ebb0f63658952f` |
| 原参考时长 | 21.933333秒 | 198.461995秒 |
| 新粗剪候选 | 56.0秒一版 | 45／227／91秒三版 |
| GLM选中实际粗剪 | render0，56.0秒 | render2，91.0秒 |
| skill实际精剪 | 21.2秒初版→21.9秒第一次修订→21.9秒第二次修订并交付 | 79.2秒一版并交付 |
| 终态与结束时间（上海10月11日） | succeeded/exit0，00:48:49.295 | succeeded/exit0，02:27:02.243 |
| 原视觉台账 | 48登记＝48received，44parsed | 92登记＝92received，86parsed |
| 新pending／unknown | 0／0 | 0／0 |
| 分支累计 | 572＋48＝620 | 571＋92＝663 |
| 最终GLM盲读／目标审核 | pass／pass；原粗剪交接三个partial保持 | partial／pass；原粗剪负评保持 |
| 联合质量门槛 | false | false |
| 最终交付SHA | `c7be5acd4e153a93e13680899c10345be7c0c57bdb4dfbe14192685e627b5159` | `082e7cc47313e5cf2879a90508d7d3d57f0b9a02603947bba6e0436b2d7cb10c` |

共140次新视觉请求全部收到；44／86 parsed说明原失败并未删除或假装成功。两个累计分支共享更早历史，620与663不能相加当成账号用量；收到回复不用于推断套餐计费。长路实际使用92次请求，未受旧80次固定总限；观察、候选和修订均依据当前任务进展，未规定以后随机参考的固定总时长或总次数。

短路002及唯一003事件与可用区间重叠校验失败保持，候选丢弃；021 JSON语法错误、027空JSON分别经唯一022／028修复通过。长路008页面身份、018事件重叠、022角色证据、027事件越界、042非有限时间、089空JSON分别经唯一009／019／023／028／043／090修复通过。没有新增重试层或重放未知请求，也未手改模型JSON、切点或创作提示。

短路10段实际EDL使用省略、一段0.5倍慢放和一段1.1倍提速；长路9段均原速。短最终盲读虽写pass，仍记录因果断裂，主旨仍只能从具体身体限制与生活技能向动画冲突作抽象映射；目标审核的正面描述不能代替看懂画面的证据。两路自审由GLM执行，负面/冲突结果保留。联合质量要求也包括前序粗剪审核，因此最终短pass/pass不能覆盖原粗剪交接三个partial；长partial/pass也不构成联合通过。完整结果不声称模型已学会所有剪法或音乐卡点，也不新增人工创作来补成质量成功。

### 当前验证与交付状态

短路605份、长路819份原件均已直连下载并逐项SHA核验，两路实际粗剪/精剪副本均存在；凭据为`short/download_verified.json`和`long/download_verified.json`。archive SHA分别为`613c4d19b27be338321ed31d239bad11566f161536b6a9c5eeef0d0ce4c01c25`和`5d47aba7986c2688e914bf1c521c6f907a3a72177c733dc5f3a17faea42ab4d2`。

严格最终验收首次发现release内必要的`.deploy/`安装回执未被Git忽略，原失败保存在`FINAL_FUNCTIONAL_AUDIT_INITIAL_FAILURE.json`。只在服务器该release的`.git/info/exclude`添加`/.deploy/`规则，两份回执及169份源码SHA不变、Git状态归零；凭据`DEPLOY_METADATA_EXCLUDE_FIX.json`。没有删除回执、放宽验收或改变跟踪源码；这属于部署元数据问题，不是新的模型流程失败。

外部auditor另外两次失败均已原样保留：一是用台账紧凑JSON计算原帧网格的request SHA，但帧writer/verify_grid实际都使用`ensure_ascii=False, sort_keys=True, indent=2`的JSON字节；二是改用原函数后，系统Python无法导入3.13编译的`av`。前者按原帧序列化协议修正，后者仅从SHA已验证的冻结源码提取并执行原`_json_bytes`函数AST，保留全部其他严格校验。凭据为`FINAL_FUNCTIONAL_AUDIT_FRAME_HASH_FAILURE.json`和`FINAL_FUNCTIONAL_AUDIT_INTERPRETER_FAILURE.json`；生产代码、模型请求和视频未改，新增模型请求0。独立`FRAME_REQUEST_HASH_AUDIT.json`确认51组网格全部匹配原服务器sidecar、源SHA、真实PTS及PNG SHA：短20组/120帧，长31组/164帧；没有重新解码原电影来证明这些局部静帧RGB一致。

原严格最终审计实际exit0，`FINAL_FUNCTIONAL_AUDIT.json`于上海11:26:56记录`functional_acceptance=true`：三部电影分别完整文件streaming SHA重算一次并核对流/时长和前后size/mtime；两路粗剪/精剪源范围、原EDL、交接SHA、完整解码及PTS全部通过。短粗剪1680帧、精剪657帧，长粗剪2730帧、精剪2376帧，四视频全量FFmpeg解码exit0且PTS严格递增；最终精剪画面与PTS同选中静音渲染一致，参考音轨来源核验通过。169份冻结源码、46份历史保护文件、官方MCP30文件及全局历史manifest SHA保持；原未知/失败、累计用量和负评保留，无重放、无旧粗剪复用、无固定总量上限，实际并行时间重叠获证实。

模型任务已停止，不为质量强行通过重新运行。核验与导出新增模型请求0；最终四文档发布、实际GitHub CI和服务器文档同步的凭据见部署目录/CI记录，文档同步保持运行源码706da57，不把待发布记录提前当已发布。

两路实际视频副本已下载并核验SHA，完整路径如下：

```text
C:\Users\29785\Desktop\omni-autonomous-screenplay\runs\server_review_contract_20261010\delivery\short_glm_rough.mp4
C:\Users\29785\Desktop\omni-autonomous-screenplay\runs\server_review_contract_20261010\delivery\short_glm_finecut.mp4
C:\Users\29785\Desktop\omni-autonomous-screenplay\runs\server_review_contract_20261010\delivery\long_glm_rough.mp4
C:\Users\29785\Desktop\omni-autonomous-screenplay\runs\server_review_contract_20261010\delivery\long_glm_finecut.mp4
```

较早`boundary_progress/1791646677415841600.json`保留短012首次通过旧交接失败边界的证据：`evidence`5项、`limitations`3项、`revision_requests`2项均为非空string[]，原严格验证首次通过无repair；模板前缀SHA未变、通用schema位于runtime context、回复SHA与ledger一致。该次交接绑定当前完整56秒粗剪，主题/剪法/连续性partial保持。它证明通用反馈修复在本次样本实际生效，不保证以后随机输出总能正确。

当前helper为`watch_review_contract_pair.py`；原`prepare_review_contract_pair.py`与`start_review_contract_pair.py`已成功，不可重复启动。下面保留之前冻结测试的原失败与局限，旧视频不能替代当前完整验收。

**前次完整测试：d2feddc短参考失败，长参考完成实际精剪，整对未通过。** 该冻结版本181项本地相关测试、593项服务器检查和wheel/doctor验证通过，32份历史保护文件SHA保持。GitHub [run38047147486](https://github.com/Seven-creater/agentic-video/actions/runs/38047147486)已双平台实际成功，凭据`runs/ci_checks/d2feddc_github_pass.json`。上海19:17:09并行从参考和素材库新建任务：短`9398f358ee664f83972d51757716b8d9`、长`a523777917ad47da9657b42a7862cfdc`，各自继承550/488累计基线；旧证据根目录`runs/server_full_contract_20261010`，不使用旧粗剪或`--rough-task`。代码检查成功没有消除下面的真实模型字段错误，不能宣称整体验收通过。

短参考于上海20:29:58以failed/exit1结束：22received/20parsed，12个有效素材观察，生成110.2秒和179秒两份实际粗剪并选中后者；交接审核021及唯一022修复均因`review/evidence:text_required`拒绝，未进入skill。两份输出合法JSON但`evidence`是4个时间戳对象，现合同及冻结模板要求非空字符串列表；022逐字重复021，repair反馈没有指出元素索引与预期类型。原件289文件已直连导出并SHA核验，累计550+22=572，无新增unknown，父ledger未变；详见`short/fatal_selected_review_evidence.json`及`short/evidence`。不得在旧任务中删除失败、静默转换证据类型或重放请求。

长参考上海22:48:33以succeeded/exit0结束：83received/69parsed、7个有效素材观察，14个协议失败均经各自唯一修复通过，无新增unknown，父ledger未变，累计488+83=571。156.8秒实际粗剪审核后进入skill，先渲染140.766667秒精剪候选，GLM自行局部修订后选择142.266667秒最终精剪；`joint_quality_gate=false`保持。最终SHA为`35cb9c22e2508e6faeb2b422f42488076fa441c2f69957f77aa39db313dcb553`。原件800文件已直连下载并逐项SHA核验，archive SHA `d39d4079133479c6721e5ef8ccd9f094e55756382f063d6767f810a24f2f05de`；凭据`runs/server_full_contract_20261010/long/download_verified.json`。本地实际视频为`runs/server_full_contract_20261010/delivery/long_glm_rough.mp4`及`long_glm_finecut.mp4`，导出未新增模型请求。旧短没有精剪，故这份长视频不构成两路完整功能验收。

旧d2长视频的离线技术核验`runs/server_full_contract_20261010/long/DELIVERY_TECHNICAL_AUDIT.json`通过：粗剪4704帧、精剪4268帧完整解码无错误，PTS严格单调，实际输入、来源和交付SHA连通，最终封装保持选中精剪的画面包及PTS，800份归档原件未变。此审核不重哈希三部13.8GB原电影、不评价叙事或剪法，也不代替当前706两路验收。

075及079原HTTP200的最终content为空、finish_reason=length，分别由唯一076/080修复通过；075的32768输出token中32438为reasoning，原始message没有遗漏的输出字段，接收层提取与原content一致。安全证据为`plan_metadata/long_075_original_http_1791641841297713000.json`，没有输出推理原文或重发请求。这些原回复、协议失败与负面审核继续保留。

本地前向修复仅澄清review输出类型：运行时context补充既有string[]合同；失败诊断列出`$.evidence[0]`、expected/actual及通用占位格式，并由现有MCP加入原有唯一repair。原验证规则、冻结模板、画面事实、负评与旧失败不改；80项诊断/MCP测试通过，另一次主线/合同/历史模板/服务器接口兼容检查401passed、1skipped，431.93秒，两批有重叠。修复`706da57`已推送并通过[GitHub实际双平台CI](https://github.com/Seven-creater/agentic-video/actions/runs/38053733983)，服务器独立新环境649passed/1skipped/1deselected、wheel13资源/4CLI/doctor/pipcheck通过。凭据为`runs/server_full_contract_20261010/review_contract_fix_validation.json`、`runs/history_clean_chain_20261010/deployments/706da57/deployment_prepare.json`和`runs/ci_checks/706da57_github_pass.json`。旧d2两路均终态后激活新环境，34份保护文件SHA未变，旧worker未热更；激活凭据`deployments/706da57/deployment_activate.json`，官方MCP0.1.5的30文件基线字节未变。当前012已实际首次通过原失败边界；完整线路及后续输出仍待验收，不由离线检查推断。

**前次完整测试：1ed133d两路失败退出。** 短参考上海18:34:31退出1，63received/57parsed，80秒及139.633333秒两版粗剪中选择后者，进入skill后062计划128.266667秒超过目标，唯一063修复22.5秒仍超过参考原速音轨上界；无精剪。长参考上海18:42:21退出1，46received/38parsed、20个有效素材精看、72秒及197秒两版粗剪，第三版计划及唯一修复的角色/时间证据不成立；未进入skill。两路无新增未知请求，父台账未变。终态原件已下载633/360文件并SHA核验，见`runs/server_fresh_functional_20261010/{short,long}/evidence`。通用执行反馈和无效修订的停止处理已在d2feddc修复并验证，完整真实线路仍待本轮终态验收。

本记录保留历次真实终态。`039e194`两路失败已封存；接口修复后`2af271e`部署并通过GitHub双平台实际CI。两路于上海13:31显式复用已生成的实际粗剪，在同一主线并行完成技能精剪：短108秒→20.466667秒，长250.7秒→197.8秒，分别于14:25:59、14:41:03正常退出。两份最终视频已直连下载并完成逐帧、PTS和音轨来源核验。两路`joint_quality_gate=false`：本轮完成了真实流程测试和输出交付，未证明内容与剪法质量通过。

用户随后明确新增Goal：**视频质量先不管，服务器完整线路正常跑完即可**。新功能试验重新执行两参考的素材检索、粗剪生成、实际自审、skill精剪、再审及输出；不以旧粗剪续跑代替。显式`--functional-test`保留负面粗剪审核并继续下游，默认质量门槛不变，媒体校验不放宽。短参考仍使用已收到的历史参考理解以排除未知完整参考请求；已付费观察只按严格匹配规则复用。预检凭据为`runs/server_fresh_functional_20261010/preflight.json`：两父任务已退出，新目录不存在，父用量为487/442，磁盘可用4,307,525,632字节。本段仅记录启动前要求和证据，尚无本轮新视频。

新源码`1ed133d7744cb685868b8f2eaf0cf5a38e3928ff`已推送及部署：117项本地相关测试、580项服务器检查通过；wheel资源和CLI验证通过，依赖无冲突，28个历史保护文件未变。上海15:56:57并行启动短`d80ef8f49580436ba0a89cc0d905553e`与长`34bae8304419424b823e5890541de2d8`。启动参数含`--functional-test --parent-task`而无`--rough-task --reference-cache`；实际冻结环境路径为`/home/ubuntu/apps/agentic-video/releases/1ed133d`。启动凭据为`runs/server_fresh_functional_20261010/server_pair_start.json`，后续只读快照保存在同目录`snapshots`；启动不能代替终态验收。

该源码[GitHub实际双平台CI](https://github.com/Seven-creater/agentic-video/actions/runs/38035823509)成功，凭据`runs/ci_checks/1ed133d_github_pass.json`。用户随后授权清理服务器磁盘：26个非活动版本的第三方依赖/bin/build已退役，保留历史安装源码和原Git/source；旧部署环境不能直接运行。先释放3,638,128,640字节，12,349个受保护源码/当前依赖/历史台账文件校验未变。另一阶段删除已退出进程留下的386个临时库、3个旧测试目录、npm/APT下载缓存和11个与本地SHA/字节匹配的重复导出包；逐项计入4,529,958,912分配字节，阶段实际可用空间升至11,999,891,456字节。当前部署、官方MCP及FFmpeg字节不变；原任务、raw、台账、电影、参考及密钥未删。证据为`runs/server_storage_cleanup_20261010/inactive_dependencies_cleanup.json`与`transient_cleanup.json`。清理并行进行，两模型任务未被停掉或热替换。

## 1. 任务要求

### 1.1 总目标

按原始聊天求证并提纯用户指定的一条连续流程：GLM从参考和电影库生成粗剪，先渲染与审看实际粗剪，再把这份实际视频交给教师蒸馏的通用`visual-story-finecut` skill，由GLM观察、选择局部证据、精剪、审看及交付。

不向GLM提供历史77秒EDL、教师影片切点或教师成片作为答案。本轮验证的是方法顺序和实际新输出，不要求新任务复刻77秒或22秒。

### 1.2 实验对象与基线

| 对象 | 输入或版本 | 用途及证据边界 |
| --- | --- | --- |
| 历史首轮粗剪 | 77.366667秒，`render_0/final.mp4` | 原聊天第2795行实际交付；GLM原选择结果 |
| 教师示范与skill | 教师77.37→21.87秒，通用skill及决策卡 | 原聊天第26802、26888、27385行；示范EDL不提供给GLM |
| 历史GLM技能试验 | 原77.37→21.9秒，657帧 | 原聊天第29168行；251–274调用可追溯，盲读partial与目标pass同时保留 |
| 首次服务器发布 | `333d25bc052ab5d70c43da14c3e458798d03217a` | 默认`omni-server`→`clean_chain`→历史粗剪→实际交接→`StoryFinecut` |
| 后续服务器发布 | `6130cb4592cbacb9c2b0884fba13571cc862b8e3` | 457检查通过，实际双平台CI成功；新长参考运行版本 |
| 原并行参考1 | 21.933333秒，`shared/data/ref/video.mp4` | 02:26:05失败退出；16次登记含1uncertain，无渲染 |
| 原并行参考2 | 198.461995秒，`shared/data/ref/7692329355342679331/video.mp4` | 02:15:30失败退出；12received，无精看或渲染 |
| 新长参考任务 | 同一198.461995秒原参考，不用旧interpretation缓存 | 02:44:24启动，启用完整参考ASR；新结果待核验 |

历史数字与原文来自[CHAT_PROOF.md](../runs/history_clean_chain_20261010/CHAT_PROOF.md)和[CHAT_PROOF.json](../runs/history_clean_chain_20261010/CHAT_PROOF.json)。参考与新任务绑定来自[并行启动凭据](../runs/history_clean_chain_20261010/server_pair_start.json)及[流程说明](HISTORICAL_CLEAN_CHAIN.md)。历史21.9秒交付不是本轮新结果。

### 1.3 约束与验收要求

- 用户最新要求取消固定请求数、观察总数、段数、总时长和渲染轮数上限；以真实编辑或审核进展决定继续与停止。
- 单次局部观察≤6秒、≤36请求帧，工具按小窗口和网格分批。真实源范围、类型、身份与媒体SHA校验保持；接口实际输入边界仍适用。
- 粗剪与精剪共用本轮台账；父任务、旧controller、原回复及未知请求保持，旧用量不清零、不退款、不重放。
- 精剪目标由实际参考和实际粗剪决定。粗剪已经短于参考时不靠重复、等待或整体慢放补长。
- 验收必须包含实际粗剪、SHA绑定的交接、精剪原始EDL、可播放输出、盲读与目标审核。可播放、HTTP成功和模型自评都不能单独确立整体质量通过。

## 2. 实际做法与进展顺序

### 第一步：从原始聊天确认77秒粗剪到skill精剪

流式筛选原始JSONL，保存15条命中消息的原文、消息ID、行号、UTC及上海时间、原行SHA；没有复制工具命令或凭据。关键顺序如下。

| 原聊天行号 | 用户指令或实际产物 |
| --- | --- |
| 2795 | 原77秒粗剪交付 |
| 26802、26888 | 要求Codex当教师示范，并把输入改为完整77秒 |
| 27293 | 接近参考时长即可，不一味追求更短 |
| 27385 | 教师21.87秒交付及通用skill入口 |
| 27395 | 明确要求迁移给GLM试验 |
| 29168 | GLM77.37→21.9秒实际交付 |
| 29181、29247 | 质疑相似／抄袭，随后核对实际请求及独立EDL |

直接核对251–274原调用：24条记录中23条received、22份parsed；22份parsed与原response JSON相同。268／271原模型EDL与`plan_0`／`plan_1`一致，原77秒输入、精剪渲染与交付SHA可连通。265保持uncertain，273格式失败及其唯一274修复保留。老师skill冻结副本确实完整出现在实际请求中。

证据：[CHAT_PROOF.json](../runs/history_clean_chain_20261010/CHAT_PROOF.json)。本步骤证明历史发生顺序与输入继承，没有新增电影质量评测。

### 第二步：提纯默认业务主线

发布`333d25b`的默认顺序为：

```text
server_cli._run
  → clean_chain.execute
    → 原始粗剪通用提示 / 归档renderer
    → 素材导航、GLM自主检索和连续精看
    → GLM粗剪EDL、实际粗剪渲染、盲读、目标审核及选择
    → SHA绑定实际选中粗剪，保存rough_handoff.json
    → StoryFinecut读取老师通用skill和decision-cards
    → 完整粗剪正常速静音代理观察、模型选择局部视频与真实PTS帧
    → GLM精剪EDL、实际渲染、独立画面盲读及对照审核
    → 有实际编辑与审核进展才局部修订
    → 精剪交付与同速参考音轨，保留局限
```

`result.json`保留粗剪结果，`chain_result.json`记录完整链，默认`active_finecut=False`。教师skill和决策卡的打包资源与历史冻结副本字节相同；归档原renderer保持原字节。实现细节与停止条件见[HISTORICAL_CLEAN_CHAIN.md](HISTORICAL_CLEAN_CHAIN.md)。

与历史实验相比，本轮取消固定总量上限，改为无进展停止；迁移到Ubuntu／OpenCode，并可复用已收到的原参考观察；增加实际粗剪交接、fail门槛和更严格的事实、未知项、时间区间与贡献保留检查。核心顺序恢复，但不是逐调用复刻原77→22实验。

### 第三步：完成离线检查、wheel与双平台CI

| 核验 | 已记录结果 | 原始凭据 |
| --- | --- | --- |
| Ubuntu服务器检查 | 451 passed，1 deselected；158.76秒 | `deployment_prepare.json` |
| wheel安装核验 | 13资源、4项CLI帮助检查；依赖检查无损坏 | `deployment_prepare.json` |
| doctor | Python 3.13.16；OpenCode、Node、FFmpeg、FFprobe均可用；official MCP 0.1.5；该核验新增模型请求0 | `deployment_prepare.json` |
| 实际GitHub Ubuntu job | completed／success，job `113946949977` | `333d25b_github_pass.json` |
| 实际GitHub Windows job | completed／success，job `113946950303` | `333d25b_github_pass.json` |
| release激活 | `90bfedc`→`333d25b`；14份保护文件核验通过；该操作新增模型请求0 | `deployment_activate.json` |

来源：[准备与检查凭据](../runs/history_clean_chain_20261010/deployment_prepare.json)、[激活凭据](../runs/history_clean_chain_20261010/deployment_activate.json)、[实际CI结果凭据](../runs/ci_checks/333d25b_github_pass.json)。CI运行`37967948388`的两个平台分别成功，不能把本地检查推断成CI成功；这里引用的是已保存的实际GitHub结果。

wheel SHA256为`e9fe07f7737616b36659f028585106f8fddf405db6276620ecc3c7c0ab107064`。上述检查证明部署与纯工程合同通过，不证明新电影任务的语义或剪辑质量通过。

### 第四步：并行启动两个新参考任务

两个任务于2026-10-10 01:45:21上海时间启动，使用同一release及共用只读原素材，各自拥有输出目录和模型台账，`automatic_restart=false`。两者均启用原始可选ASRsmall CPU证据，其语言结果仍未核验。

| 项目 | 参考1 | 参考2 |
| --- | --- | --- |
| job ID | `be64f3bff1164ea7bcd2f28f53caafa1` | `c5866e9786ec46939b401654df3189a7` |
| 父任务 | `server_edit_test_20261009` | `server_reference_7692329355342679331_20261009` |
| 新输出 | 父目录下`evaluations/history_clean_chain_20261010` | 父目录下`evaluations/history_clean_chain_20261010` |
| 已绑定父台账请求 | 36+71=107；更早累计274保留 | 57；更早累计274保留 |
| 本轮前累计基线 | 381 | 331 |
| 已结算结果 | failed／exit1；16登记、15received、1uncertain、5watched、0render | failed／exit1；12received、0fine、0render |

启动证据：[server_pair_start.json](../runs/history_clean_chain_20261010/server_pair_start.json)。原参考缓存分别为`original_reference_001.json`、`reference2_001.json`，仅复用已收到的模型原观察及其局限。

截至[02:03:17运行快照](../runs/history_clean_chain_20261010/snapshots/1791568997972559500.json)，两个job均为running；各登记7条新请求，末条为submitted；两个父台账均标记未改变，实际粗剪和精剪渲染列表均为空。这是运行中快照，不能当作最终请求数、成功或失败结论。

### 第五步：长参考在空素材条件下进入plan并终止

长参考job `c5866e9786ec46939b401654df3189a7`于2026-10-10 02:15:30.139上海时间退出1。实际12次请求全部received，9份parsed、3份协议失败、0unknown；没有`fine_*`连续精看调用，也没有粗剪或精剪渲染，未进入实际视频交接和教师skill阶段。两个owned进程已退出，父台账SHA保持不变，未自动重启。

GLM010搜索返回`windows=[]`，其原理由认为：已观察的联系表缺少参考“诚信／黑心摊贩对比、掺假、中毒、客户介入、危机担当”的对应人物与事件，没有新的指向性线索。其“素材库不存在”的措辞属于GLM在有限粗抽样基础上的判断，不是本实验对整库内容不存在的证明。实际为7张各18帧联系表、126个静态位置；三源overview和四个局部zoom没有形成任何可用于EDL的watched窗口。覆盖范围不等于连续逐秒观看。

pipeline仍提交011 `plan_0`。GLM首先返回空bindings、slots和segments并记录没有可核验usable_ranges；校验失败于`plan/focus_role_bindings:list_required`。唯一012修复新增不存在于watched数据的`w_sheet_f00_f17`绑定，仍无片段；校验失败于`plan:focus_binding_unknown_window`，最终`model_protocol_repair_exhausted:plan_0`。另外008 zoom协议失败为`coarse_does_not_match_requested_page`，009唯一修复通过；这些原始失败均保留。

原证据包直接下载并核验189个文件，archive SHA256为`b473a6eb8c2d5b1a75cba4e081fae8e2901771d2fb19ea41931170f0b682ef40`。来源：[download_verified.json](../runs/history_clean_chain_20261010/reference2/download_verified.json)、[终态摘要](../runs/history_clean_chain_20261010/reference2/evidence/settled_job_summary.json)、[search_0原搜索](../runs/history_clean_chain_20261010/reference2/evidence/search_0.json)、[011协议失败](../runs/history_clean_chain_20261010/reference2/evidence/calls/glm_011_plan_0/protocol_failure.json)、[012唯一修复失败](../runs/history_clean_chain_20261010/reference2/evidence/calls/glm_012_plan_0_repair/protocol_failure.json)、[chain_failure](../runs/history_clean_chain_20261010/reference2/evidence/chain_failure.json)。证据下载核验新增模型调用0；这不表示失败任务本身没有12次模型调用。

### 第六步：短参考在第016视觉调用后未知终止

原短参考job `be64f3bff1164ea7bcd2f28f53caafa1`于上海02:26:05.395退出1。台账16次登记中15received、1uncertain、14parsed；已有5个watched窗口、0粗剪／精剪渲染。016为`fine_68774485f86e6654`，没有捕获原始vision回复，保持uncertain；不能把它当已知未提交、已成功或免费请求，也不能重放。

原证据包直接下载校验206个文件，archive SHA256为`2f5be3bdf383c191ad71d46b57f160d17e6fd26bdf77beb9d4ea83a8dd7bdabc`；owned进程退出，父台账保持不变。来源：[短参考下载核验](../runs/history_clean_chain_20261010/reference1/download_verified.json)、[终态摘要](../runs/history_clean_chain_20261010/reference1/evidence/settled_job_summary.json)、[台账](../runs/history_clean_chain_20261010/reference1/evidence/library_state.json)、[5个watched窗口](../runs/history_clean_chain_20261010/reference1/evidence/watched_windows.json)、[失败记录](../runs/history_clean_chain_20261010/reference1/evidence/chain_failure.json)。本任务没有plan、实际粗剪或skill质量结果。

后续封存的[实际MCP日志](../runs/history_clean_chain_20261010/reference1/native_call16/calls/glm_016_fine_68774485f86e6654/agent/mcp_server.log)先记录视觉API请求尝试，随后在上海02:25:51报`ENOSPC: no space left on device, write`；[agent事件](../runs/history_clean_chain_20261010/reference1/native_call16/calls/glm_016_fine_68774485f86e6654/agent/events.jsonl)也记录同一tool错误。10份原生文件SHA核验通过，原HTTP为50,495,488字节，含16个完整request、15个完整response和1个不完整尾行；未捕获016原回复。空间耗尽已获原生日志证实，但不能将请求尝试改判为“确认未POST”，agent总结中的未执行说法也不覆盖原始HTTP和MCP证据。来源：[native_call16_verified.json](../runs/history_clean_chain_20261010/reference1/native_call16_verified.json)；原uncertain状态及原不完整HTTP均保持，封存新增模型请求0。

### 第七步：回收重复依赖并部署6130cb4

磁盘回收针对退役release中经字节相同核验的第三方依赖，采用hardlink去重，释放9,845,534,720字节；76,634个路径字节相同。电影、台账、项目源码、活动333d25b和新6130cb4文件未改，操作新增模型请求0。它是重复依赖空间回收，不是删除电影、原证据或模型用量。证据：[dependency_dedup_20261010.json](../runs/history_clean_chain_20261010/dependency_dedup_20261010.json)。

`6130cb4`服务器457项检查通过、1项排除，耗时177.39秒；wheel资源／CLI／doctor／依赖检查通过。[实际CI凭据](../runs/ci_checks/6130cb4_github_pass.json)记录run `37972995688`的Ubuntu job `113964107587`与Windows job `113964108132`均success。release已由333d25b激活为6130cb4，18份保护文件核验通过，旧运行代码SHA保持，新增模型请求0。来源：[部署准备](../runs/history_clean_chain_20261010/deployments/6130cb4/deployment_prepare.json)、[实际激活](../runs/history_clean_chain_20261010/deployments/6130cb4/deployment_activate.json)。工程检查与CI仍不能替代模型或成片验收。

### 第八步：不用错误缓存重新开始完整长参考理解

独立原片核对确认长参考缓存存在局部语义错误，见3.6。新的长参考job `37682886c0c34b92a3bb20b165a891f7`在6130cb4上于上海02:44:24.163启动，命令启用ASRsmall完整参考语言证据，没有`--reference-cache`，不能复用旧错误interpretation作为完整理解。它是新evaluation，旧12次和原累计331保留，启动前累计基线343。

新输出为`/home/ubuntu/apps/agentic-video/shared/runs/server_reference_7692329355342679331_20261009/evaluations/history_clean_chain_20261010/evaluations/fresh_reference_20261010`。实际任务已在03:45:01后failed/exit1：25次全部received，9个watched窗口，零渲染。024把真实window前缀缩写为w；025修复ID后，seg_4跨过未支持范围，仍被严格拒绝。进一步全段诊断发现seg_5也跨过可用区间空隙。来源：[启动凭据](../runs/history_clean_chain_20261010/fresh_reference_start.json)、[原始失败与只读快照](../runs/history_clean_chain_20261010/reference2/fresh_long_failure_audit/remote_readonly_snapshot.json)、[全段机械诊断](../runs/history_clean_chain_20261010/reference2/fresh_long_failure_audit/INDEPENDENT_PLAN_DIAGNOSTICS.json)。ASR仍是需画面和原声核对的语言证据，不是自动真值。

### 第九步：实际粗剪交接与长参考的内容失败

短任务027提交第二份EDL，实际渲染为108秒／3240帧，18,135,675字节，SHA `2b4a61d01d11e749c604c672519f8246745d0d330a2844ed121966e183163993`，完整解码exit0。030选择这份视频，031独立目标审核为theme partial／editing partial／continuity pass；允许带局限进入skill，不代表联合质量通过。`rough_handoff.json`、skill输入及完整正常速度代理绑定同一实际视频，通用SKILL／decision-cards字节与打包资源相同，没有输入旧77秒切点或教师EDL。证据见`short_continuation_audit/ACTUAL_ROUGH_TO_SKILL_BINDING.json`。

032已观察完整108秒粗剪；033局部视频把本地0–6秒误写成粗剪时间，被时间域校验拒绝，034唯一修复通过。035真实PTS帧将2D→3D变化夹在5.9667–6.1667秒之间，036进一步承认摔倒过程不可见；“脸朝下”与“仰躺”姿态描述仍有冲突。真实图及SHA绑定已独立核验，未把人工纠正回传GLM。042缺失合法fact basis，由043唯一修复通过；截至12:26，044继续新的PTS页观察，尚未生成精剪EDL或视频。这些是定位证据增加，不是循环重剪。

长任务034实际渲染377秒／11,310帧，45,437,312字节，SHA `9cfb3fbbeb305bf02d31886e378c5e2f4d40d9cc113488a0d0b04b2ba30eecbd`，完整解码exit0。实际PTS284／288／289.1秒显示宫殿外景、台阶及熊猫到达，增加了旧版缺少的空间衔接。036审核为theme partial／editing partial／continuity pass。随后037选择首版101.5秒，其理由却将别版的“雪豹强夺卷轴”写入首版；首版实际EDL没有雪豹或打斗。038重新观看首版后判theme fail／editing fail／continuity partial，明确指出食品伦理主旨未成立、计划“切菜备料”与实际道歉画面不同。

长job于上海12:21:08结束，succeeded／exit0仅代表worker正常完成。38次全部received，无pending／unknown，父台账保持，`chain_result.status=rough_review_failed_candidate`、`joint_quality_gate=false`。`rough_handoff.json`确实保存了失败审核及首版SHA，但没有执行skill。这是模型语义选择和内容表达失败，审核门槛有效阻止将候选冒充精剪成功。原037／038、三份实际视频及独立审阅见`long_contract_audit/`；旧任务没有重启。

### 第十步：封存终态，修复两处接口并复用实际粗剪

短job在上海12:58:13以failed／exit1停止：65received、54parsed，无pending／unknown。064原JSON完整，但`basis=picture+text`及`text+inference`被只支持单值的来源枚举拒绝；065唯一修复将引用字幕中的英文双引号未转义，导致非法JSON。未产生精剪EDL或渲染。602个文件完整直连封存核验，archive SHA `d7a37ca5f60c64a4ee2e7ec99c2682274fabe041d87084c2f9683693201f539b`；长终态343文件封存SHA `952a6484406e7714a91fdd5baae5176cabb7ba4f896a1b72bdd0e1d6f489b59e`。两路父台账未变，旧进程退出。

前向工程修复保留原回复及原失败：接受由picture／text／inference组成的明确多来源标注，保持推断与未知类别边界；相同提示、媒体SHA、原范围和provider下，已收到原响应可用当前合同重新验证，派生凭据写入新evaluation，旧parsed／protocol_failure不改。这样064可被检验复用，065非法JSON不被人工修复或重放。skill观察缓存只扩展到完整observe、局部inspect和真实PTS detail，不包括EDL与质量审核。

另一处确定的接口缺口是选片附带视频没有版本标记：037附带render2，却将其剧情写入render0理由。是否直接造成模型幻觉仍属推断。前向修复在运行时加入每候选实际SHA／时长，以及attached_media的round／SHA／范围，禁止跨版本挪用画面；历史资源字节、选择自由和失败门槛保持。

显式`omni-server start --rough-task <已结束父任务> --parent-task <同一父任务>`在原task/evaluations下继续同一后半链，继承全部历史用量及未知排除，不重检电影库或重渲染粗剪。已允许交接的粗剪保留原实际审核与上下文SHA，复用完整／局部观察；失败选片则让GLM在已有真实候选上用已修正归属的接口重新选择，并实际复核，仍fail就保留失败候选。该选项不自动重启旧任务，也不根据时间、评分或人工剧情代选版本。部署及实际新运行结果必须另补，工程测试不能当新精剪交付。

本地相关合同、缓存、真实范围、server入口、资源字节和渲染检查287通过／1 Windows权限跳过；真实两版媒体测试确认附带视频属于实际最后版，模型可自由选择另一版，选中结果仍绑定自身SHA。两项实际粗剪续接测试通过，验证原任务文件逐字不变、无重新检索／粗剪渲染、允许交接的上下文完全相同，以及修复归属后仍走实际选中视频审核。这些测试使用合成媒体和队列回复，没有新增真实模型请求，不能证明新成片质量。

实际064原回复也已在本地按原99.4–100.60000000000001秒稀疏范围重验证：6条事实全部通过，JSON value SHA保持`8deaa71e33727a158f01b82e4bf258f5d624ec345e393bf0da09a0b94a7f0b2f`，没有人工更改描述、时间或来源；这仅证明合同适用，不证明事实语义正确。凭据为`short_skill_failure/ORIGINAL_064_FORWARD_VALIDATION.json`。

`2af271e`已推送。服务器573通过／1排除，13资源／4CLI／doctor／pip check均通过；24份历史保护文件及旧runtime SHA保持，激活时没有活动worker。上海13:31:48在原任务下并行启动显式后半链：短job `13c92e6ede204aaeb14400e1b07eefed`，新目录`skill_protocol_continuation_20261010`，累计前置462；长job `d9934fa425c0494497ee21850f9ba228`，新目录`selection_binding_continuation_20261010`，累计前置406。短启动前已验证参考缓存与原skill context SHA完全相同。凭据为`rough_continuation_pair_start.json`及`deployments/2af271e/`。最新GitHub Ubuntu检查实际通过、Windows尚在执行（run38027515262），不能提前声明双平台成功；两路新运行也尚无精剪交付。

上海13:36，长001在明确附带视频归属后选择原250.7秒render1，002正在实际复核。短完整observe没有命中缓存：新旧提示完全相同（23,112字符），原范围／ROI／30fps／静音spec完全相同，但重新编码的代理SHA从`739ac20f…`变为`bbc809ed…`；严格缓存没有忽略文件字节差异。因此短新增完整观察请求，累计计数保留；001提出超出单次6秒工具粒度的局部范围，002正进行唯一协议修复。不能宣称已经零调用复用了全部观察，也不能将重建代理说成重新生成粗剪。当前两路原进程存活、父台账保持，尚无新fine。

上海13:39，两路均已进入skill：长002实际选中复核为partial／partial／pass，003完整250.7秒粗剪观察已提交；短002修复通过，GLM自行选择3个局部（20–26、74–80、97–103秒）用于轻视反应、金龙形态及权威认可定位，003短片与004真实PTS回复已收到，005下一页等待。原32观察曾划6贡献／5局部，新观察划3贡献／3局部；这是模型新组织方式，未提供人工slot或切点。skill来源同一通用资源，参考与actual rough SHA保持。图间未显示的动作、字幕与可见画面分歧，以及静音代理不能验证声音的限制仍保留。

上海13:41再次读取实际GitHub job，两平台均completed／success，run38027515262精确绑定`2af271e53be1c486c8c14060502b4ef9acd027d9`，凭据`runs/ci_checks/2af271e_github_pass.json`。此前“Windows待执行”的文字是启动时状态；现在两平台工程检查均通过，成片质量仍需实际产物。

### 第十一步：实际局部证据、模型精剪表和首份精剪候选

短路GLM自选三组局部后提交022精剪EDL。原生HTTP记录显示14:02:43.444发出、14:09:49.233收到HTTP200，约426秒；原请求未取消或重发。完成token为22,197，其中reasoning token 19,823；只据原usage解释模型等待时间，不据此推断计费。此前21条观察／修复均received。015的`picture and text`仍被当前来源合同拒绝，016唯一repair通过；英文组合缺口及修复后的来源变化保留，不能说来源协议已完全解决。

原始022与实际render input逐字段绑定：11段、4处慢放（0.5／0.75倍）、一处0.5秒冻结，108秒父片产生20.466667秒／614帧／6,229,984字节候选，SHA `86c0d1e9c9fe0f8649ceb81cd22114dd5efde8d3d10ffd3b48e996b05a4ea854`。直连SFTP前后服务器SHA／mtime一致，原model plan、ledger请求／响应SHA、renderer input identity、完整FFmpeg解码均通过。这只是静音render0，不是已配音乐的最终交付。证据：[实际候选核验](../runs/history_clean_chain_20261010/rough_continuation_evidence/short/actual_media/render_0/verification.json)。

独立三组真实PTS审计确认抓举动作、金龙可辨形态和认可字幕存在；同时确认模型把灰脸棕衣的小角色错认成黑白熊猫、将龙头已部分出画的79.966秒叫作最佳完整形态，以及把结尾单臂或未展示姿态说成双臂完全展开。这些错误进入了022部分解释，尚须核对实际输出；未给GLM人工纠正或教师切点。审计在`rough_continuation_evidence/short/audit_key_moments/`，只证明实际提供的PTS帧，不声称正常播放或精确峰值已确认。

023独立静音盲读已received，状态写pass但同时记录两个问题及三项局限；其“犀牛被金光击飞”等事实也须与实际20.47秒候选核对。024目标审核仍submitted，不能先写联合通过。长路自选8局部，已到第6个；程序确实没有4局部总限。两worker仍固定`2af271e`，未热更或重启。文档提交`498e324`的实际双平台CI也已成功，run38028403966，凭据`runs/ci_checks/498e324_github_pass.json`。

### 第十二步：两路精剪终态、最终音轨与质量分歧

短任务025唯一目标审核修复通过，最终`partial/deliver`；长任务032原计划182.2秒因偏离参考目标被拒绝，033唯一修复得到197.8秒、14段原速EDL和两处尾帧停留。033补入3秒`clip_6b`并延长部分保留区间，原回复自己承认该补段没有新增故事信息。这是按时长合同填充的负面结果，不能算精炼改善。长035审核错误使用父片时间，036唯一修复改为实际成片时间后仍为`partial/deliver`。两路各产生一份实际精剪，没有后续实际改版。

| 终态核验 | 短参考 | 长参考 |
| --- | --- | --- |
| 实际粗剪输入 | 108秒，原render1，SHA `2b4a61d01d11e749c604c672519f8246745d0d330a2844ed121966e183163993` | 250.7秒，原render1，SHA `e9c4892a567d3971024f27adf43c43fd7f97e3845d4dc8e68616767abab1bddc` |
| 本轮终态 | succeeded/exit0，上海14:25:59.584594 | succeeded/exit0，上海14:41:03.741406 |
| 本次模型调用 | 25登记、25received、22parsed，0pending/unknown | 36登记、36received、33parsed，0pending/unknown |
| 累计台账 | prior462＋25＝487；更早未知请求保留 | prior406＋36＝442；不清零 |
| 实际操作 | 11段；4处0.5/0.75倍慢放；0.5秒冻结 | 14段；均原速；两处尾帧停留 |
| 最终输出 | 20.466667秒，614帧，SHA `544f567444ff964aef6b88be48397a9e1809fb85a80f9eb7a0680c524d44624a` | 197.8秒，5934帧，SHA `7db6f0d196e258750170b2f98a92a74d3d657b3eec2e30182b32aa3d5462294f` |
| 原始盲读／目标审核 | pass／partial，联合false | partial／partial，联合false |
| 证据导出 | 238文件，archive SHA `405802a08392fd0c1349ac99a951ee367aeae2b9a355811384549c19701ffda8` | 302文件，archive SHA `c5f29d5e07d4731984680e4cfcbca8352c4f15547b172741eae647190da67d70` |

终态依据各自`download_verified.json`、原ledger、skill原始计划/审核和`chain_result.json`。全量解码核验确认最终614/5934帧RGB及PTS分别与选中静音渲染完全相同；最终音轨来自原参考的同时间位置，解码样本相关性分别0.999046/0.999202。AAC有损样本无需逐点相同，核验只证明封装及来源，不证明音乐卡点或声画艺术通过。短审核称“父片原声”，与实际mux来源不符；长参考含对白，直接映射其音轨到动画成片会带入参考对白，不能把它称为纯BGM。

短版独立PTS核对发现角色误认、金龙冻结帧龙头出画及结尾字幕半句截断。相邻实际帧424（14.133333秒）还是金龙冻结，425（14.166667秒）已切到桃树灵界，429（14.3秒）仍是桃树与熊猫/乌龟；直接反证GLM023/025声称此处角兽被击飞的关键因果。长版原目标审核明确指出食品质量底线被替换为自我接纳、平行叙事变为顺序对照、负面后果缺失。其“电影库完全没有这些素材”仍是有限观察下的推断，本实验只确认本轮没有找到并选出所需证据。独立审阅留在`rough_continuation_evidence`；没有回传人工剧情、切点或纠正来改写本次GLM输出。

可复制的原始本地交付路径如下（媒体与私有原始记录不进Git）：

```text
C:\Users\29785\Desktop\omni-autonomous-screenplay\runs\history_clean_chain_20261010\reference1_skill\evidence\skill_finecut\delivery\story_finecut.mp4
C:\Users\29785\Desktop\omni-autonomous-screenplay\runs\history_clean_chain_20261010\reference2_selection\evidence\skill_finecut\delivery\story_finecut.mp4
```

为了便于定位，另保存字节相同的交付副本`runs/history_clean_chain_20261010/delivery/reference1_glm_finecut.mp4`和`reference2_glm_finecut.mp4`；`local_delivery_copies.json`记录原路径与SHA，没有重新剪辑或渲染。

最终只读保护核验`FINAL_PAIR_COMPLETION_AUDIT.json`确认：当前仍为`releases/2af271e`；143份安装源码/打包资源与该Git版本相同；24份保护文件、10份旧runtime源文件、7份父台账、8份原交接/结果文件及全局历史清单字节未变。61份本次请求/响应与台账SHA匹配，无pending/unknown；与历史265及016比较请求SHA、媒体SHA及同源时间范围，未发现重放或重叠。两路owned PID/进程组均已退出、`automatic_restart=false`。这是指定范围的保护及终态验证，不宣称模型语义准确；两份联合质量false保持。

## 3. 出现的问题与解决过程

### 3.1 把92→88计划预精炼误当作实际粗剪到skill精剪

**现象：** 第二参考旧分支先把92秒`draft_plan_0`改成88秒EDL，之后只渲染88秒`render_0`；不存在先渲染92秒粗剪、观看该视频后再精剪的阶段。

**根因：** 旧任务运行`active_finecut`渲染前精炼架构；该流程的数据流与用户要求的实际粗剪视频交接不同。EDL可计算时长不能代替可播放视频。

**解决：** 新默认入口明确关闭`active_finecut`，先实际渲染、审看并选出粗剪，再将SHA绑定的真实视频传入`StoryFinecut`。旧任务和唯一88秒候选保持，不改写为粗剪精剪闭环成功。

**验证：** [旧分支原实验记录3.4节](../runs/server_reference_7692329355342679331_20261009/SCHEMA_CONTINUATION_RECORD.md)明确区分计划与实际视频；新代码及离线合同检查支持正确顺序。原并行两任务都在零渲染状态失败，没有验证实际交接；新长参考尚无结果。

**后续实际验证：** 2af两路最终分别绑定108秒与250.7秒已生成粗剪，随后完整观察、局部取证、产生新EDL和真实精剪输出。各自原始交接、渲染input identity和最终媒体核验相连，后半链续接没有重新生成粗剪。

### 3.2 从一次示范迁移方法时混淆通用skill与具体影片答案

**现象：** 历史教师版与GLM版使用相同77秒素材、音乐和约22秒目标，观感接近，引发用户对复制教师答案的疑问。

**根因：** 输入和目标共享会约束创作空间；相似观感本身不能证明直接复制，也不能证明陌生素材迁移成功。

**解决：** 原始请求核验区分通用skill／决策卡与教师视频、教师EDL和示范复盘；本轮继续只传方法及模型观察，GLM拥有新检索、选片和切点。

**验证：** 原GLM268／271回复与实际EDL一致；教师18段含局部变速，GLM9段全部原速，精确相同源入／出区间为0段。定向检查未发现教师文件路径标记；此结果不等于无限范围无泄漏证明。原并行两任务未进入skill，没有新的技巧迁移结果；新长参考仍待实际输出。

**后续实际验证：** 本轮短GLM自选3个局部、长GLM自选8个局部，新的11段／14段EDL及实际输出各自保存；没有输入教师影片计划。最终只读重验证原聊天快照232,243,634字节、15条消息原行SHA和85份历史主文件全部匹配，凭据`HISTORY_FINAL_REVALIDATION.json`；历史21.9秒媒体没有被换成当前20.47秒。

### 3.3 历史固定总量上限与随机参考、持续有效进展不兼容

**现象：** 历史默认合同含180秒editing总长、粗剪32段／精剪24段、观察和改版总量上限；新用户要求按实际证据继续，且第二参考为198.461995秒。

**根因：** 单次试验的有限授权数量曾混入通用实现上限。局部工具输入粒度与整任务总量属于不同约束，不能互相替代。

**解决：** 新workflow显式取消总量上限，保留默认历史行为和归档资源。重复执行EDL不再渲染；没有新观察且审核不改善时停止粗剪；精剪无实际操作变化、审核变差或无进一步改善时停止改版。单次媒体边界和真实范围校验保持。

**验证：** 无模型合同与渲染检查覆盖超过180秒带hold、超过24段显式opt-in、默认拒绝、缓存身份不变及局部输入范围。333d25b的451项检查和实际双平台CI已通过；旧长参考仍暴露零watched进入plan的问题，见3.5。6130cb4的457项检查与双平台CI随后通过，不能将其写成新的电影链路已验证。

**后续实际验证与限制：** 本轮长精剪197.8秒超过历史180秒总限，长局部8组超过旧4组授权总量，当前新请求25／36均无固定总数上限。另一个按参考目标±2秒的计划校验仍将182.2秒拒绝，033修复补段至197.8秒；补段无新增信息说明接近目标时长也可能妨碍精炼，此负面结果保持。

### 3.4 目标自评与独立画面理解不能合并成成功结论

**现象：** 历史技能试验最终盲读partial而目标审核pass，后者还包含缺乏实际支持的clip_8修复和切点偏移说法。

**根因：** 目标审核已获计划、参考和文字上下文，可能将预期补进实际画面。声明或计划变化不等同已执行切点变化。

**解决：** 保留盲读与目标审核两份原结果，核对真实EDL、provenance和文件SHA；新主线增加实际粗剪交接及严格事实／未知项合同，联合质量不由单份pass决定。

**验证：** 历史真实执行参数只改clip_5入点37.3→38.0、clip_6入点42.5→41.8，其余7段不变；原21.9秒交付消息已明确整体质量未通过。原并行两任务都没有实际成片，无法评测上述质量；新长参考仍待输出审阅。

**后续实际验证：** 2af271e续接短版盲读pass仍虚构14秒角兽被击飞，目标审核进一步把它作为完整因果闭环；实际425帧起已切到桃树灵界。同期长版盲读和目标均partial，目标明确承认主旨失败。结果证明多份模型审核仍可能共享错误；严格范围/SHA/JSON只能约束执行，不能证明叙事语义准确。该历史独立审计保持原负面结果，不把联合false改为pass；当前706两版也保留联合false。

### 3.5 没有watched素材仍强制生成合法非空plan

**现象：** 010搜索已返回空windows，前面也没有`fine_*`成功观察，`watched_windows`为空。流程仍调用011 plan及012唯一格式修复，两个回复都没有segments，最后因虚构watched绑定名被严格校验拒绝；零渲染退出。

**根因：** 新progress workflow允许搜索返回空列表，但pipeline没有在零watched／零usable_ranges时形成“证据不足、无法规划”的终态分支，继续进入要求非空人物绑定和EDL的plan合同。空素材前提与合法非空计划要求矛盾；格式修复不能补出已看片证据。011如实承认证据不足，012为满足非空字段加入联系表假窗口，进一步触发未知窗口拒绝。

**解决与当前状态：** 原真实失败与回复完整保存，原job停止且不自动重启；未以手填窗口、放宽身份校验或解包方式强行接受原计划。后续改进需要单独处理“没有可规划素材”的停止状态，并区分继续寻找有指向性的局部证据与改换素材。6130cb4已完成工程部署，但新长参考尚不能提供实际模型验证；GLM的整库不存在判断仍不作为事实。

**验证：** `search_0.json`确有`windows=[]`；台账只有001–012、全部received，无fine调用、9parsed、3协议失败。011／012保存的原模型内容和校验错误明确绑定上述前提矛盾；终态摘要确认exit1、owned进程退出和父台账不变，189文件下载校验通过。此结果是确定的工程终止失败，未证明整个电影库不存在可迁移素材，也不构成剪辑质量结论。

### 3.6 相同参考SHA不保证缓存理解正确

**现象：** 长参考缓存把167–178秒写为工人集体中毒，把客户是否成交写为未展示，并添加主角／女助手搀扶收束。独立核对同一SHA原片后，这些指定局部与实际画面、字幕冲突。

**根因：** SHA证明媒体内容相同，不能证明模型interpretation准确。复用已收到缓存时保留了历史错误；同次未核验ASR已给出订单谈判的相反线索，但未完成冲突复核。这不能证明缓存错误单独导致整轮搜索与协议失败。

**解决：** 保留错误缓存与原失败；单独保存54个真实PTS帧、6张联系表及2份manifest进行局部核对。新任务不用旧缓存，从完整长参考与ASR重新理解，不预编替代剧情。

**验证：** 154秒字幕“明天300份”，163.5／165.5秒为每天100份及质量保证；168.6／169.4秒可见“一百份”“一千八”，170.8秒可见“明天送到再给就行”，支持订单数量与付款约定已展示。189秒以后所检车间帧有工人捂腹；所检结尾没有缓存描述的摊主／女助手帮扶。这是指定静帧、字幕和实际PTS证实的局部缓存失真；没有宣称订单已履约、医学因果成立或整段逐帧absence。原参考SHA重算匹配，详细证据见[独立失败审阅](../runs/history_clean_chain_20261010/reference2/INDEPENDENT_FAILURE_REVIEW.md)。

### 3.7 目标时长与原速音轨的交集未清楚反馈

**现象：** 新短任务062编译128.266667秒，被目标时长校验拒绝；唯一063修复675帧/22.5秒，满足21.933333±2秒，却超出原速参考音轨上界。

**根因：** 提示分别提供目标、容差和音轨上界；第一次错误只报告目标偏差，没有展示所有执行约束的交集和真实逐段取整总帧数。原始计划和唯一修复均已收到，不是传输未知或JSON解析失败。

**解决：** 提供参考与实际粗剪动态决定的有效时长交集及30fps帧数预算；修复反馈附编译总帧数和每段帧数。保留原校验、唯一修复和模型切点选择，不自动裁短、放宽音轨或重放旧请求。

**验证：** 独立回放原计划确认128.266667/22.5秒；新增测试覆盖随机参考时长、逐帧边界、短粗剪不补长及原数据不变。新冻结版本真实端到端仍待验证。

### 3.8 无效后续计划阻断了已有实际粗剪的选择

**现象：** 新长任务已有两份实际粗剪和自审，045第三版计划引用未在该时间出现的人物，046修复换成人物又不受同条usable_range支持，导致整链退出而没有选择已有候选。

**根因：** usable_range角色集合和时间重叠events角色集合是独立约束，现有完整反馈表只展开前者；后续计划耗尽唯一格式修复还被作为全任务失败，即使已有可审阅实际候选。

**解决：** 反馈两种角色证据及缺项；进展驱动路线中，后续计划唯一修复仍无效即停止该修订，由GLM选择现有实际候选。首版计划失败、未知请求及其他工程异常仍停止，失败计划不渲染，负面审核不改写。

**验证：** 新增真实FFmpeg流程测试覆盖已有粗剪保留、首版失败不吞及其他错误仍传播；真实服务器新任务待验证。

### 3.9 审核内容合同的时间戳对象与字符串列表混用

**现象：** d2短路已实际生成并选择179秒粗剪，但交接021把evidence写成四个时间戳对象；022唯一repair逐字返回相同内容，因原合同要求非空string[]而失败，尚未进入skill。

**根因：** 本次原JSON和原schema直接确认元素类型不匹配。旧反馈只给出text_required，没有元素路径和预期类型；对象形式也用于其他审核合同，模型输出混淆在本样本真实出现，但不能据此确认全部模型类型错误的来源。

**解决：** 706在runtime context明确已有review输出类型，失败诊断附JSON路径、预期类型及实际元素形状，交给原有唯一repair。验证规则、冻结模板字节、旧失败和具体画面事实保持，不自动把对象强转字符串，也不增加请求重试。

**验证：** 80项诊断/MCP检查及401项相关主线检查通过；两批有重叠。当前短012第一次就返回合法的5项evidence、3项limitations、2项revision_requests，原严格校验通过且无repair，绑定完整56秒实际粗剪；最终短、长都进入skill并正常结束。这个真实样本支持本次边界修复有效，不保证随机参考以后没有协议错误，质量负评仍保持。

## 4. 简洁实验报告

### 4.1 主要结果

| 当前706完整测试 | 短参考 | 长参考 |
| --- | --- | --- |
| 输入参考 | 21.933333秒 | 198.461995秒 |
| 实际新粗剪与GLM选择 | 新56秒，选择该版 | 新45／227／91秒，选择91秒 |
| skill精剪交付 | 21.2→21.9→21.9秒，三份实际渲染 | 79.2秒，一份实际渲染 |
| 执行终态 | succeeded/exit0；owned进程退出 | succeeded/exit0；owned进程退出 |
| 视觉台账 | 48received／44parsed，0新unknown | 92received／86parsed，0新unknown |
| 联合质量 | false，原负面审核保留 | false，原负面审核保留 |
| 导出与技术验收 | 605原件及粗剪/精剪实际副本SHA核验；1680/657帧完整解码与PTS通过，全链功能验收true | 819原件及粗剪/精剪实际副本SHA核验；2730/2376帧完整解码与PTS通过，全链功能验收true |

运行源码`706da57`已通过401项主线相关检查（1跳过）及另外80项诊断/MCP检查，两批有重叠；服务器649passed／1skipped／1deselected、wheel13资源／4CLI／doctor／pipcheck通过，[实际双平台CI](https://github.com/Seven-creater/agentic-video/actions/runs/38053733983)成功。激活前34份保护文件及官方MCP30文件基线字节保持。上述工程检查与最终真实视频验收分开记录。

| 历史对照 | 已有证据的结果 |
| --- | --- |
| 原聊天与调用求证 | 15条聊天命中、251–274原调用和EDL核验；77.37→通用skill→21.9秒真实发生 |
| 2af271e实际粗剪续接 | 短108→20.466667秒，长250.7→197.8秒；均正常退出、联合false，但只验证后半链 |
| 1ed133d完整新粗剪测试 | 短63received后精剪目标/音轨边界失败；长46received后角色证据失败；均无fine |
| d2feddc完整新粗剪测试 | 短22received后review字段类型错误且唯一修复失败；长83received、156.8→142.266667秒完成；整对未通过 |
| 当前706完整新粗剪测试 | 两路从参考/素材库重新生成粗剪后进入skill并自审，140新请求全部收到，旧失败/unknown不改；最终完整功能验收true |

完整来源证据位于`runs/server_review_contract_20261010`：`server_pair_start.json`、终态快照、两份`download_verified.json`及最终`FINAL_FUNCTIONAL_AUDIT.json`。最终审计真实通过，覆盖三电影完整SHA、169份冻结源码、46份历史保护文件、官方MCP30文件、原付费证据绑定、来源范围及完整媒体解码/PTS；51组局部网格另有严格原协议核验。发布/文档同步凭据以部署目录和CI实际记录为准。

### 4.2 结论

原始聊天和原调用证明77.37秒实际粗剪→通用教师skill→21.9秒GLM精剪确实发生。当前默认单链恢复这一方法顺序，GLM自行检索、观察、选择和剪辑，程序校验并执行。历史模板、renderer和skill字节不改，旧固定总量上限由薄适配层移除，已知失败及未知请求保持。

当前同一冻结706版本确有两路完整实际新输出：短56→21.9秒、长91→79.2秒，均经过GLM自身盲读和参考目标审核。长路92次请求也证明旧80次总限不再阻断有效进展。技术接口及完整调用链正常结束仍不能证明语义与剪法质量通过：两份联合门槛均为false；本次模型调用已停止，不为了pass无意义重剪。

两路严格功能验收已通过，三电影实际SHA、粗剪/精剪解码与时间轴、选中渲染画面一致性及历史保护均有直接证据；此次新视频不是旧77→22或旧88秒产物。官方MCP内部采样和模型语义判断不属于这些技术证据的保证范围。

### 4.3 尚未完成

- 本轮严格功能验收和视频交付完成；最终四文档发布、GitHub双平台检查及服务器文档同步依据实际部署目录/CI凭据，不以文档中的计划代替。文档同步不更换冻结运行源码。
- 无字幕、无预设答案的陌生观众理解评测、参考主旨与剪法质量、音乐卡点/声画质量未通过验证，本轮不要求解决这些质量问题。
- 静音代理、抽帧盲读和SHA链只能约束执行证据，不能证明云端内部采样策略、连续观看覆盖或模型每项语义判断正确。较长参考音轨含对白，映射到动画的声画对应限制保留。

本记录依据实际请求、原响应、渲染媒体及直连核验；技术审计未给模型回传人工创作答案，记录不含密钥、密码或访问令牌。
