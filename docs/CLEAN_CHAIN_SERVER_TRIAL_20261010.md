# 实验20261010：从历史粗剪到教师skill精剪的干净服务器流程

本记录保留原并行两任务与`6130cb4`长参考任务的真实失败终态。`039e194`后续两路仍在运行；截至上海11:03，长参考已实际渲染101.5秒粗剪，目标审核三个partial后继续检索缺项；短参考完成8个候选窗口精看。尚无本轮精剪交付，整体任务未完成。后续质量结果以原始回复和实际视频为准。

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

## 3. 出现的问题与解决过程

### 3.1 把92→88计划预精炼误当作实际粗剪到skill精剪

**现象：** 第二参考旧分支先把92秒`draft_plan_0`改成88秒EDL，之后只渲染88秒`render_0`；不存在先渲染92秒粗剪、观看该视频后再精剪的阶段。

**根因：** 旧任务运行`active_finecut`渲染前精炼架构；该流程的数据流与用户要求的实际粗剪视频交接不同。EDL可计算时长不能代替可播放视频。

**解决：** 新默认入口明确关闭`active_finecut`，先实际渲染、审看并选出粗剪，再将SHA绑定的真实视频传入`StoryFinecut`。旧任务和唯一88秒候选保持，不改写为粗剪精剪闭环成功。

**验证：** [旧分支原实验记录3.4节](../runs/server_reference_7692329355342679331_20261009/SCHEMA_CONTINUATION_RECORD.md)明确区分计划与实际视频；新代码及离线合同检查支持正确顺序。原并行两任务都在零渲染状态失败，没有验证实际交接；新长参考尚无结果。

### 3.2 从一次示范迁移方法时混淆通用skill与具体影片答案

**现象：** 历史教师版与GLM版使用相同77秒素材、音乐和约22秒目标，观感接近，引发用户对复制教师答案的疑问。

**根因：** 输入和目标共享会约束创作空间；相似观感本身不能证明直接复制，也不能证明陌生素材迁移成功。

**解决：** 原始请求核验区分通用skill／决策卡与教师视频、教师EDL和示范复盘；本轮继续只传方法及模型观察，GLM拥有新检索、选片和切点。

**验证：** 原GLM268／271回复与实际EDL一致；教师18段含局部变速，GLM9段全部原速，精确相同源入／出区间为0段。定向检查未发现教师文件路径标记；此结果不等于无限范围无泄漏证明。原并行两任务未进入skill，没有新的技巧迁移结果；新长参考仍待实际输出。

### 3.3 历史固定总量上限与随机参考、持续有效进展不兼容

**现象：** 历史默认合同含180秒editing总长、粗剪32段／精剪24段、观察和改版总量上限；新用户要求按实际证据继续，且第二参考为198.461995秒。

**根因：** 单次试验的有限授权数量曾混入通用实现上限。局部工具输入粒度与整任务总量属于不同约束，不能互相替代。

**解决：** 新workflow显式取消总量上限，保留默认历史行为和归档资源。重复执行EDL不再渲染；没有新观察且审核不改善时停止粗剪；精剪无实际操作变化、审核变差或无进一步改善时停止改版。单次媒体边界和真实范围校验保持。

**验证：** 无模型合同与渲染检查覆盖超过180秒带hold、超过24段显式opt-in、默认拒绝、缓存身份不变及局部输入范围。333d25b的451项检查和实际双平台CI已通过；旧长参考仍暴露零watched进入plan的问题，见3.5。6130cb4的457项检查与双平台CI随后通过，不能将其写成新的电影链路已验证。

### 3.4 目标自评与独立画面理解不能合并成成功结论

**现象：** 历史技能试验最终盲读partial而目标审核pass，后者还包含缺乏实际支持的clip_8修复和切点偏移说法。

**根因：** 目标审核已获计划、参考和文字上下文，可能将预期补进实际画面。声明或计划变化不等同已执行切点变化。

**解决：** 保留盲读与目标审核两份原结果，核对真实EDL、provenance和文件SHA；新主线增加实际粗剪交接及严格事实／未知项合同，联合质量不由单份pass决定。

**验证：** 历史真实执行参数只改clip_5入点37.3→38.0、clip_6入点42.5→41.8，其余7段不变；原21.9秒交付消息已明确整体质量未通过。原并行两任务都没有实际成片，无法评测上述质量；新长参考仍待输出审阅。

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

## 4. 简洁实验报告

后续工程修复仅补通用执行合同和诊断：ID逐字复制，每段必须由同一条usable_range及其角色支持，不能跨空隙；GLM自行拆段、缩短或重选，程序不代写EDL。父观察缓存、未知范围排除与Python/Node磁盘检查共175项通过、1项Windows符号链接权限跳过；运行适配与历史资源再验证38项通过。尚未部署的检查结果不算真实模型纠错成功。

原024先返回`w_…`缩写而非实际`window_…`；025修复ID后，seg_4选799–826但支持区间为799–810／815–826，seg_5选2016–2033但支持区间为2016–2019／2026–2033。完整机械诊断现在一次反馈所有段落及窗口范围，保持严格校验及原回复不变。新增诊断30项通过，包含真实pipeline、FFmpeg合成素材和模拟MCP验证；与历史资源适配合跑68项通过。独立原始证据在[新长参考计划失败审阅](../runs/history_clean_chain_20261010/reference2/fresh_long_failure_audit/REVIEW.md)。这些检查不代表实际模型已纠错或新成片质量通过。

发布前审阅另补`1e309`／负溢出／NaN的诊断安全写盘：只在诊断副本标无效数，原模型内容不变；诊断文件33项通过。0f4f722的服务器wheel检查517通过／1排除，但GitHub Ubuntu旧IntervalResume测试绕过OpenCode初始化，缺少新增`parent_baseline`字段，1失败／949通过／2跳过；该版本未激活。只补模拟客户端初始化，相关94项通过／1权限跳过，随后重新检查并按平台分别保存实际凭据。此阶段没有新模型请求。

039e194随后本地执行CI同命令：948通过／4跳过及94通过，合计1042通过／4跳过；服务器555通过／1排除，wheel13资源／4CLI／doctor／pip check通过。Ubuntu GitHub CI实际成功；Windows仍执行中，上一成功版同组17分钟，因此Linux试验与Windows检查并行，不写成全平台成功。039e194在20个保护文件和旧runtime SHA不变的条件下激活，上海10:09:58同时启动两个普通服务器job，未重启旧worker。新凭据为`short_continuation_start.json`、`long_contract_continuation_start.json`及`deployments/039e194/`，均在本地`runs/history_clean_chain_20261010/`。

| 后续测试 | job与累计基线 | 当前已核验状态 |
| --- | --- | --- |
| 短参考恢复 | `2831a4c38a8e4d259b4230bd1492f726`；prior397 | 上海11:03：running，8条父导航观察复用；search选10窗，13登记／12received，8个有效精看窗口；未渲染 |
| 长参考合同修复后 | `112106d2320e431bab0ceaa34b03acec`；prior368 | 上海11:03：running，18登记／17received，3个有效精看窗口；实际粗剪101.5秒，016盲读和017目标审核已收到，018检索缺项等待中 |

长015原response与`plan_0.json`逐字段一致，独立严格校验通过：5个slots、8段、全1倍速，全部ID存在且每段完整落在一条usable_range。名义101.5秒／3045帧与真实render时长相符。GLM主动选择source音轨，并承认原参考对白与动画有冲突；后续skill交付仍固定映射参考音轨，需独立复核。计划主动舍弃双摊对比及负面后果，改成面馆经营、切菜、不加秘方／相信自己与父子同行，尚未证明能表达原参考的伦理主旨。原件及机械审计见`runs/history_clean_chain_20261010/long_contract_audit/`，没有将人工判断回传GLM。

实际粗剪已直连下载，本地`runs/history_clean_chain_20261010/long_contract_audit/media/render_0/final.mp4`：10,908,909字节，SHA `05c7049ccd043cd8aae611b6ecced9a71c80d62c84736f543df92e17427d63b5`，3045帧，完整FFmpeg解码exit0。原plan→render input→真实媒体绑定均通过。016盲读独立理解为“自我信念”，017目标审核的theme／editing／continuity均partial，明确指出伦理冲突和对比线缺失。GLM自行要求新观察并提交018，不是重启或已经进入skill。24张实际PTS静帧显示大量字幕与日间厨房→夜间对话切换；完整正常速度和音频质量尚未独立验证，保留原画面上下大黑边的可读性风险。

短回复的三次协议错误分别是事件与usable时间索引错位、source ID拼写多了两位、零时长事件。唯一修复均已通过；有效窗口按去重window_id计数，包含合法repair。首次修复还新增幼年角色认定，独立真实PTS抽帧不能支持该身份及部分胜利描述；这个语义风险保留到实际EDL和成片审阅，不把格式通过当画面事实通过。原件及审阅见`short_continuation_audit/`。两路运行源码保持039e194，没有热更新或重启。

最新文档提交9cdedc4的双平台CI也实际成功，run38016716367，凭据`runs/ci_checks/9cdedc4_github_pass.json`。只读资源检查显示oom_kill=0、可用交换约1.19GB，内存压力低；当前没有系统终止进程的证据。

启动后Windows CI也实际成功，run38015263038、两job completed/success，凭据`runs/ci_checks/039e194_github_pass.json`绑定原提交。短001原生事件确认POST于02:12:51.649 UTC发出，02:19:19.085收到HTTP200，约387秒；此等待未取消或重发。最新两路父ledger SHA仍一致，磁盘可用约8.0GB。运行和缓存推进不代表剪辑质量通过。

长参考seed的来源说明会改变导航提示，严格父观察缓存可能不命中；不为命中改写旧提示或忽略问题字段。两路创作仍由GLM执行，不注入原77秒EDL、教师剧情或人工切点。已启动不代表粗剪／精剪成功，后续须补真实响应、渲染、交接及独立质量证据。

### 4.1 主要结果

| 已完成项目 | 有证据的结果 |
| --- | --- |
| 历史求证 | 原聊天15条命中；251–274原调用和EDL直接核验；77秒输入到21.9秒历史交付绑定成立 |
| 主线提纯 | 默认实际粗剪→审阅与SHA交接→老师通用skill→实际精剪；旧92→88预渲染分支不参与 |
| 工程与发布验证 | 333d25b：451通过／1排除；6130cb4：457通过／1排除；各自wheel／doctor及GitHub双平台实际job成功 |
| 并行启动 | 两个独立新evaluation已启动，父历史基线381／331保留 |
| 长参考真实结果 | 02:15:30 failed／exit1；12received／9parsed／3协议失败／0unknown；0fine／0render，未到skill |
| 短参考真实结果 | 02:26:05 failed／exit1；原生MCP报ENOSPC，无016原回复；16登记=15received+1uncertain，14parsed；5watched／0render |
| 磁盘处理 | 退役第三方依赖字节相同hardlink去重，释放9,845,534,720字节；未改媒体、台账或项目源码 |
| 新长参考与整体结果 | 6130cb4上02:44:24启动、03:45:01后失败；不用旧参考缓存，原累计343保留；25received、9watched、0render，整体未完成 |

以下记录原并行两任务的终态。不得将旧77／21.9／88秒产物填为本轮新任务结果。

| 核验项 | 原并行参考1 | 原并行参考2 | 应引用的原始证据 |
| --- | --- | --- | --- |
| 终态、exit code、结束时间 | failed／exit1；上海02:26:05.395；owned进程退出 | failed／exit1；上海02:15:30.139；owned进程退出 | token绑定job状态、进程退出记录 |
| 最终请求数及received／unknown／协议失败 | 16登记，15received，1uncertain，14parsed | 12received，9parsed，3协议失败，0unknown | `library_state.json`、原responses、HTTP记录 |
| 实际连续精看 | 5watched窗口；016未知仍保留 | 0fine／0watched | `watched_windows.json`、原调用 |
| 粗剪候选、实际时长／帧数／SHA | 0render，无粗剪视频／时长 | 0render，无粗剪视频／时长 | `render_N/render_result.json`、实际媒体解码 |
| 选中粗剪审核与实际交接 | 未执行；没有可播放粗剪 | 未执行；没有可播放粗剪 | selected review、`rough_handoff.json` |
| 精剪原始EDL、修订次数及实际变化 | 未到plan／skill | 未进入skill；011／012仅失败粗剪plan | skill原responses、`plan_N.json`、provenance |
| 精剪实际时长／帧数／SHA与可播放性 | 无精剪视频／时长 | 无精剪视频／时长 | `skill_finecut/render_N`、完整解码与交付绑定 |
| 盲读、目标审核、冲突与联合质量 | 未执行；无质量结论 | 未执行；无质量结论 | 两份原review及独立实际画面检查 |
| 正常速度／无字幕因果／音画与音乐 | 无成片，无法评测 | 无成片，无法评测 | 单独评测证据，不能以静音抽帧替代 |
| 父台账及历史保护文件最终核验 | 父台账未变；206份证据文件下载SHA核验通过 | 父台账未变；189份证据文件下载SHA核验通过 | 启动前后SHA比较 |
| 可复制交付绝对路径 | 无交付；仅终态失败证据目录 | 无交付；仅终态失败证据目录 | 终态delivery与`chain_result.json` |

| 新长参考后续核验项 | 当前状态 | 后续必须补充的证据 |
| --- | --- | --- |
| 启动、版本、累计基线 | 已启动；6130cb4；job `37682886c0c34b92a3bb20b165a891f7`；prior343 | `fresh_reference_start.json`、终态job |
| 完整参考理解与语言／画面冲突 | 001 received/parsed；纠正订单错时，仍混淆两位顾客及推断转单 | [原始5文件绑定](../runs/history_clean_chain_20261010/reference2/fresh_reference_evidence/received_reference_snapshot.json)、[独立核对](../runs/history_clean_chain_20261010/reference2/fresh_reference_audit.md) |
| 请求结算、粗剪／交接／skill及实际渲染 | 25received、0unknown、9watched；plan_0和唯一修复失败，0render | 新ledger、原responses与protocol_failure；没有handoff/skill/实际视频 |
| 盲读、目标审阅、正常速度／音画质量 | 尚无新结果 | 两份原review与独立实际审阅 |
| 交付、历史保护及总任务状态 | 无新交付；整体未完成 | delivery／chain_result、父ledger SHA比较 |

### 4.2 结论

现有证据支持“按原聊天证实的方法顺序恢复默认单链，并按用户最新要求通用化”。原粗剪renderer与教师通用skill资源保持；333d25b与6130cb4工程发布和实际双平台CI均已通过。

原长参考在空watched素材下进入plan并失败，12次received、零精看／零渲染；原短参考在016视觉请求尝试后发生ENOSPC，未捕获原回复，保持uncertain，5watched、零渲染。二者均未测试实际粗剪到skill的后半链。长参考同SHA缓存存在实际画面可核对的局部语义错误；GLM在126静帧下判断全库没有素材仍越过证据边界。

新长参考不用旧缓存，001完整参考观察于02:49:27收到并解析，5个原始文件SHA和原响应一致。独立74帧核对确认订单数量与质量底线的错时得到修正，仍存在顾客身份、未展示订单转移及ASR词误识别。该核对没有回传GLM。任务在25received、9watched、0render时因计划ID和跨可用范围问题停止。参考源含对白，现有精剪交付固定映射原参考音轨的策略仍须另行检查，不能直接称为BGM。整体任务不能标完成，也没有新成片时长、技巧迁移或质量通过结论。旧92→88预渲染分支仍不能充当本轮实际粗剪到skill成功基线。

### 4.3 尚未完成

- 原并行两任务保持已失败终态，016uncertain不重放；完成后续原生日志／HTTP封存核验也不能改写请求状态。
- 新长参考25次终态保持；完整理解已有独立局部核对，保留画面、ASR与模型解释冲突，不给预编影片答案。下一次有明确工程变化的evaluation绑定累计368，不重置历史。
- 如产生粗剪，核对可播放性、选中审阅和SHA交接，再核对skill原EDL与实际精剪执行；目前没有这些产物。
- 后续独立检查画面因果、人物／事件顺序、结果可辨认性、正常速度与同速音乐覆盖；保留与目标审核的分歧。
- 填写两路后续结果、父保护文件比较与可复制交付路径；失败、无进展停止或无skill执行同样如实记录。长参考101.5秒粗剪审核为partial，正检索缺项，尚未交接／skill；短参考仍在精看，不能写成完整恢复成功。

本骨架仅依据本地已保存证据创建；未新增模型／服务器调用，未包含密钥、密码、访问令牌或连接凭据。
