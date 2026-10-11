# 交接：参考 → 实际粗剪 → 通用skill精剪

**2026-10-11最新终态：两参考在冻结版本706da57上通过严格完整功能验收，均succeeded/exit0，粗剪与精剪已下载核验。** 视频质量不属于本轮功能验收，两份`joint_quality_gate=false`保持，不能写成质量通过。

## 1. 本轮要求与实际结果

用户要求在服务器并行从参考和素材库重新执行检索、粗剪、实际自审、通用skill精剪、实际渲染、再审及输出。两路均使用`--functional-test --parent-task`，无`--rough-task`，不复用旧粗剪。短参考完整观察存在历史未知请求，因此复用已收到的参考知识，不重放未知；素材观察缓存只允许严格匹配的已收到证据。

当前服务器`current`指向`/home/ubuntu/apps/agentic-video/releases/706da57`，代码完整SHA为`706da57ceb2cd7b0b6f0c6d2f1ccf6be7be229bd`。两路上海10月10日22:54:39启动，以下数据来自`runs/server_review_contract_20261010/snapshots/1791686996144447200.json`：

| 核验项 | 短参考 | 长参考 |
| --- | --- | --- |
| job | `4d756dbe43d644038b2a3a7c7c394c4f` | `2b49ba4d678645f2a1ebb0f63658952f` |
| 实际新粗剪 | 56.0秒，render0 | 45／227／91秒三候选；GLM选择render2的91秒 |
| 实际skill精剪 | 21.2秒初版，两次自主修订均21.9秒，选择最后版 | 79.2秒一版 |
| 终态 | succeeded/exit0，上海10月11日00:48:49 | succeeded/exit0，上海10月11日02:27:02 |
| 台账 | 48received／44parsed，0pending/unknown | 92received／86parsed，0pending/unknown |
| 累计分支用量 | prior572＋48＝620 | prior571＋92＝663 |
| 联合质量 | false | false |
| 最终交付SHA | `c7be5acd4e153a93e13680899c10345be7c0c57bdb4dfbe14192685e627b5159` | `082e7cc47313e5cf2879a90508d7d3d57f0b9a02603947bba6e0436b2d7cb10c` |

两路owned子进程组已退出，共140次新视觉请求均收到；分支共享更早历史，620与663不能相加作为账号用量。旧失败、原回复、父台账和历史unknown保持，不通过新目录清零。

GLM自主选择角色、故事段落、素材、局部观看范围、切点和编辑操作，未提供旧77秒EDL、教师影片切点或人工创作答案。短版10段中真实使用一段0.5倍慢放、一段1.1倍提速；长版9段均原速。最终短盲读/目标审核为pass/pass，长为partial/pass，但原粗剪审核负评保持，两路联合false。短盲读仍记录因果缺口，具体参考事实与动画间仍有差距；质量结论依据原审核保留，不靠循环刷通过。

## 2. 当前验证与发布凭据

706发布前已通过401项主线/合同/历史资源检查、1项跳过；80项诊断/MCP检查与前者有重叠，不能相加。服务器独立环境649passed／1skipped／1deselected，wheel13资源／4CLI／doctor／pipcheck通过；[GitHub run38053733983](https://github.com/Seven-creater/agentic-video/actions/runs/38053733983)实际双平台成功。激活时34份保护文件SHA未变，官方MCP0.1.5的30文件基线保持；旧worker未热更。

运行中历史模板、renderer、skill字节不改。706只澄清已有review字符串列表输出合同并增加逐项格式诊断，保留原验证规则和每错误唯一repair，没有增加循环重试。

当前：

- 短路605份原件已直连下载并逐项SHA核验，粗剪/精剪副本实际存在；凭据`short/download_verified.json`。
- 长路819份原件已直连下载并逐项SHA核验，粗剪/精剪副本实际存在；凭据`long/download_verified.json`。
- 最终验收工具首次因部署所需的未跟踪`.deploy/`回执报Git不干净；原失败保留。仅在服务器release的`.git/info/exclude`增加本地`/.deploy/`规则，两份回执和169份源码SHA不变，Git状态归零；凭据`DEPLOY_METADATA_EXCLUDE_FIX.json`。这不是模型剪辑失败，也未改跟踪文件。
- 外部auditor误用台账紧凑JSON计算帧网格SHA，后续直接导入原帧模块又遇到系统Python无法加载3.13编译的av；分别按帧writer原缩进JSON协议修正，并仅执行SHA验证源码内原`_json_bytes`函数AST。失败凭据分别为`FINAL_FUNCTIONAL_AUDIT_FRAME_HASH_FAILURE.json`与`FINAL_FUNCTIONAL_AUDIT_INTERPRETER_FAILURE.json`；生产源码、模型请求及视频未改。
- `FRAME_REQUEST_HASH_AUDIT.json`独立核验全部51组网格：短20组/120帧、长31组/164帧；原服务器sidecar、源SHA、真实PTS及PNG SHA全部匹配，不宣称重新解码原电影来比对这些局部帧RGB。
- 原严格最终审计已实际exit0：`FINAL_FUNCTIONAL_AUDIT.json`于上海11:26:56记录`functional_acceptance=true`。三部电影完整文件SHA、两路来源/交接/原EDL、实际粗剪和精剪全量解码、帧数与PTS、全部收到的paid证据、46份保护文件、169份运行源码与官方MCP30文件核验通过；未重放未知、未复用旧粗剪、无固定总限且实际并行时间重叠。
- 交付657/2376帧及PTS与GLM实际选中渲染一致，音轨来自各自原参考；最终四文档发布及服务器文档同步凭据见部署目录和CI记录，文档同步保持运行代码706da57。

这些只读核验、外部auditor修正与导出新增模型请求0。不要重启已结束job、重跑预检/启动脚本、热换worker或改旧失败；本次功能通过依据完整最终审计，正常退出、媒体可播放和模型pass都不能单独证明全部验收。

## 3. 可复制交付路径

两路粗剪、精剪实际副本均已下载并核验SHA：

```text
C:\Users\29785\Desktop\omni-autonomous-screenplay\runs\server_review_contract_20261010\delivery\short_glm_rough.mp4
C:\Users\29785\Desktop\omni-autonomous-screenplay\runs\server_review_contract_20261010\delivery\short_glm_finecut.mp4
C:\Users\29785\Desktop\omni-autonomous-screenplay\runs\server_review_contract_20261010\delivery\long_glm_rough.mp4
C:\Users\29785\Desktop\omni-autonomous-screenplay\runs\server_review_contract_20261010\delivery\long_glm_finecut.mp4
```

对应服务器原路径见各`download_verified.json`及`server_pair_start.json`，媒体与私有凭据不进入Git。详细历史与最新验收以[双参考服务器记录](docs/CLEAN_CHAIN_SERVER_TRIAL_20261010.md)和实际JSON为准。

## 4. 方法、历史与后续边界

默认入口：`omni-server → clean_chain.execute → pipeline`历史粗剪与renderer → 实际粗剪审核/选片/SHA交接 → `story_finecut`。Fine读取打包`visual-story-finecut`及decision-cards，先观察完整实际粗剪，再让GLM选择局部视频和真实PTS帧，输出EDL、渲染、独立画面盲读和目标审核；有新证据或实际修改/审核进展才继续。

用户取消固定总时长、段数、请求数、观察数和轮数上限；单次工具粒度、源时间与身份/媒体SHA校验保持。长路92次真实请求已超过旧80总限。无进展、未知请求、唯一修复失败等仍按显式规则停止，不为强行pass循环。显式functional-test仅继续负面粗剪审核，下游仍保留负评；默认质量门槛不变。

历史求证见[HISTORICAL_CLEAN_CHAIN.md](docs/HISTORICAL_CLEAN_CHAIN.md)及原workspace`runs/history_clean_chain_20261010/CHAT_PROOF.md`。77.37→21.9确由同一粗剪到通用skill流程产生，新任务恢复方法顺序而非复制切点。原HTTP、EDL、失败、unknown和交付保留。

| 历史冻结测试 | 已保存终态，不能替代当前706完整验收 |
| --- | --- |
| 039e194 | 短108秒粗剪进入skill后协议失败，无fine；长选择101.5秒粗剪复核fail，无skill |
| 2af271e显式粗剪续接 | 短108→20.466667秒、长250.7→197.8秒；均正常退出、联合false；只验证后半链 |
| 1ed133d完整重跑 | 短63received，目标/参考音轨交集失败，无fine；长46received，后续角色证据失败，无fine |
| d2feddc完整重跑 | 短22received，交接review误用object[]且唯一repair失败，无fine；长83received，156.8→142.266667秒完成，联合false |

服务器参考为`shared/data/ref/video.mp4`（21.933333秒）和`shared/data/ref/7692329355342679331/video.mp4`（198.461995秒）；三电影库为`shared/data/videos`。Domestic Coding Plan/OpenCode＋未修改官方视觉MCP；无FlashVID，MiniMax与抖音发现冻结。脱离SSH/Codex后job可独立执行。

40GB盘的已授权清理只回收非活动第三方依赖、缓存、临时测试与本地SHA一致的重复导出包；电影、参考、原任务/raw/台账、源码和密钥保留。退役venv不能直接运行，当前独立环境及`releases/69fc197/.deploy/wheelhouse`保留；凭据`runs/server_storage_cleanup_20261010`。

更早完整交接保存在`docs/archive/HANDOFF_before_clean_chain_20261010.md`；本文件保持当前可执行状态，历史细节见唯一实验记录。
