# 当前任务：历史粗剪到skill精剪的干净服务器流程

2026-10-10最新用户Goal：视频质量先不管，只验服务器完整线路正常跑完。
新任务从参考和素材库重新生成粗剪，再进入skill精剪、自审及交付；两参考并行、
同一冻结版本，不使用--rough-task。显式--functional-test仅越过粗剪负面质量
审核的早停，保留负面审核及false质量门槛，媒体/来源/时间校验不放宽。
短参考完整观察存在历史未知请求，因此复用已收到的参考知识，不重放未知。
当前新结果保存runs/server_full_contract_20261010；本段是新任务要求，下面终态
为上一Goal的历史结果，不能代替本轮完整重跑验收。

当前新任务：修复提交d2feddcd61d41ca29db403de842ba1ab01ab909f已推送/部署，
181本地相关测试、593服务器检查(1排除)及wheel/doctor/pipcheck通过，32保护文件未变。
GitHub run38047147486已双平台success，实际凭据runs/ci_checks/d2feddc_github_pass.json。
新两路于上海19:17:09从参考/素材库启动完整功能测试，均--functional-test，无
--rough-task，source/release冻结d2feddc。短job9398f358ee664f83972d51757716b8d9，
长a523777917ad47da9657b42a7862cfdc；父基线550／488，每个新output为下面已失败
1ed任务/evaluations/server_full_contract_20261010。短已于上海20:29:58 failed/exit1：
22received/20parsed、12watched，110.2/179秒两粗剪，选179秒；021/022交接审核
evidence误写object[]而合同要求string[]，唯一repair原样返回，尚未进入skill。
22次全部收到，无新增unknown；原件289文件已导出/SHA核验，累计550+22=572。
长上海20:47仍running：32登记/31received、7watched，156.8秒实际粗剪已交接skill，
032局部补看等待。长6个协议错误已各自唯一修复通过；无fine render，父ledger未变，
磁盘约8.13GB。不能因rough current_status=completed误报整链完成。
本地正在验证通用review字段类型诊断与runtime提示context，冻结模板/原验证规则不变；
80项诊断/MCP测试通过，另一次主线/合同/原模板/服务器接口兼容检查401passed、1skipped，
431.93秒；两批测试有重叠。凭据review_contract_fix_validation.json，尚未部署到活动worker。
启动/预检/官方MCP30文件基线见runs/server_full_contract_20261010，活动Goal未完成。
读取Temp/agentic-server-upload/watch_full_contract_pair.py，勿重启同job。
settled后export_full_contract_pair.py short/long导出，audit_full_contract_acceptance.py
只在两路succeeded/exit0后最终审核；该helper已按真实新父/失败保留策略适配，
不能运行旧audit把新任务输出混入1ed证据。源流程固定，不向GLM补人工切点。
本轮未通过，Goal仍active。勿重启短任务、勿覆盖原失败、勿对活动长worker热更。
修复验证后同步Git/CI，待长任务终态封存再部署；下一完整验证仍须两参考从头生成粗剪，
同一冻结版本、新evaluation绑定各自当前父台账（短572，长依实际终态），不以旧粗剪续跑替代。
最终两份真实fine、正常退出及来源核验、Git/服务器同步齐全才complete。

上一1ed完整功能测试失败终态（原件保持）：
本轮1ed133d完整功能测试已失败退出：短job d80ef8f49580436ba0a89cc0d905553e，
上海18:34:31退出1，63received／57parsed；生成80／139.633333秒两版粗剪，
选后者进入skill，062精剪128.266667秒超过目标，唯一063修复22.5秒仍超过原速
参考音轨上界，0fine。长job 34bae8304419424b823e5890541de2d8，上海18:42:21
退出1，46received／38parsed，20watched；生成72／197秒两版粗剪，第三版计划
及唯一修复角色证据校验失败，尚未选择交接或进入skill。两路0新增未知，父台账未变。
原失败已直连封存runs/server_fresh_functional_20261010/{short,long}/evidence，
短633文件、长360文件SHA核验；不要在旧任务内删除失败或重放原paid stage。
原源码1ed133d的580服务器检查及双平台CI曾通过，不能代替本轮功能失败事实。

正在修复通用执行合同：精剪同时展示目标与原速音轨交集、逐段取整总帧数预算；
粗剪反馈区分usable-range角色与实际时间重叠事件角色；后续计划唯一修复耗尽时，
停止新增修订并由GLM选择已实际渲染审阅候选，首次计划失败或未知请求仍停止。
归档历史模板/renderer/skill字节不改，不人工改切点，当前没有新的fine。
新冻结版本部署后需要重新从参考/素材库生成两路粗剪，不以--rough-task续跑替代。
新父用量须绑定本轮终态并累计550／488，原unknown保护及失败原件保持。
Goal仍active；最后两份fine、正常退出及全量来源验证、Git/服务器同步齐全才complete。

用户已授权清理40GB系统盘；26退役部署第三方依赖/bin/build、已结束临时库、
npm/APT缓存和11份本地SHA匹配的重复导出包已清理，释放后约12GB可用。
未删原任务/raw/台账/电影/参考/密钥；1ed133d及历史项目源码保留。
已退役旧venv不能直接运行，新部署从current创建独立环境；离线wheelhouse保留
releases/69fc197/.deploy/wheelhouse，凭据runs/server_storage_cleanup_20261010。

上一Goal终态（粗剪续跑，不能替代本轮完整验收）：
最新终态（2026-10-10）：两路已结束且停止模型调用，短108→20.466667秒、
长250.7→197.8秒。实际交付在runs/history_clean_chain_20261010/delivery，
reference1_glm_finecut.mp4 / reference2_glm_finecut.mp4；逐帧/PTS/音轨来源核验通过。
两路joint_quality_gate=false。短版有虚构击飞结果，长版主旨迁移失败；
不能把下面早期“运行中/尚未渲染”的历史快照当成现状，也不能自动重跑刷通过。
源码仍固定2af271e；最终报告见docs/CLEAN_CHAIN_SERVER_TRIAL_20261010.md。

2026-10-10用户授权Goal、提纯历史77→22流程、并行测试两参考，取消固定总上限。
原始聊天已检索，证明在原workspace runs/history_clean_chain_20261010/CHAT_PROOF.md。
旧修复/失败任务停止；本轮最初在codex/clean-historical-chain隔离worktree整理，
当前代码已在main；未完成的旧slice/CI等补丁保留备份，不混入本轮源码。

默认入口：omni-server -> clean_chain.execute -> 历史粗剪pipeline/renderer ->
实际选中粗剪审核/rough_handoff -> StoryFinecut(老师通用skill)。
不走92秒计划→88秒首次渲染的active_finecut分支，不注入旧77切点或老师EDL。
无固定总时长/段数/请求数/观察总数/轮次数；保留真实范围校验与工具分批。
重复EDL不再渲染；无新观察且审核不改善停止；精剪按实际编辑/审核进展继续。
未知付费请求不重放，父历史计数不重置。

服务器参考：
- /home/ubuntu/apps/agentic-video/shared/data/ref/video.mp4 (21.933333秒)。
- /home/ubuntu/apps/agentic-video/shared/data/ref/7692329355342679331/video.mp4 (198.461995秒)。
测试各自嵌套原task/evaluations/history_clean_chain_20261010并绑定父台账。
实况更新在runs/history_clean_chain_20261010；不预先宣称模型或质量通过。
代码需测试、wheel核验、实际CI、直连部署后启动。用户授权自主工程修复和
GitHub/服务器同步，禁止人工创作或无意义循环。
旧完整交接已存docs/archive/HANDOFF_before_clean_chain_20261010.md。

本轮实况：原并行短参考在16次登记后因ENOSPC停止，15received、016仍uncertain，
5个成功精看、0渲染；原长参考12received、0精看/渲染，旧参考缓存存在原画面矛盾。
两原任务和原生HTTP残行均已直连封存；不改状态或重放未知016。
退役环境第三方依赖按字节核验后硬链接去重，释放9,845,534,720字节，原路径保持。
6130cb4已通过实际双平台CI和服务器457检查并激活。新长参考job
37682886c0c34b92a3bb20b165a891f7在02:44:24上海时间启动，不用旧参考缓存，
完整ASR+GLM新参考理解已收到；实际03:45:01后以25received、0渲染停止，
plan_0及唯一格式修复失败：先写错window ID，修复后两段跨usable范围空隙；
原始诊断已核对，通用合同与全计划机械诊断已补充，不能宣称剪辑或质量通过。
新目录为原长evaluation/evaluations/fresh_reference_20261010，prior343。
短参考下一次新evaluation应继承397历史登记、只复用同请求/媒体/范围且校验通过的
父观察，保留016未知范围并检查磁盘；后续部署和启动实况见下。
本地恢复相关175项通过、1项Windows权限跳过；新增诊断及历史资源68项通过。
长参考下一次evaluation须继承368登记，复用新001原始理解而非已否定的旧缓存。
证据与后续结果见docs/CLEAN_CHAIN_SERVER_TRIAL_20261010.md。

0f4f722服务器新wheel检查517通过/1排除，未激活：GitHub Ubuntu测试暴露
旧IntervalResume模拟客户端绕过OpenCode初始化、缺parent_baseline字段。
仅修模拟初始化；039e194本地CI同命令1042通过/4跳过，服务器555通过/1排除，
Ubuntu CI实际通过；Windows在任务启动后也实际通过，双平台凭据为
runs/ci_checks/039e194_github_pass.json，run38015263038。
039e194已激活，旧runtime/20保护文件未变，10:09:58上海时间并行启动：
短job2831a4c38a8e4d259b4230bd1492f726，prior397，
原短evaluation/evaluations/known_observation_continuation_20261010；
长job112106d2320e431bab0ceaa34b03acec，prior368，
fresh_reference_20261010/evaluations/plan_contract_continuation_20261010。
长使用新001原回复缓存，不使用坏旧缓存；其提示上下文变化，观察缓存不保证命中。
上海13:09只读快照：短已于12:58:13失败停止；长已于12:21:08正常结束，父ledger未变。
短复用8项父导航观察，65received/54parsed、0pending/unknown，16个有效素材精看；99秒及108秒
两份粗剪SHA/完整解码通过，030自主选择108秒，031复核partial/partial/pass。
actual108 SHA2b4a61…已绑定rough_handoff和skill输入，032完整粗剪观察received，
随后模型自选局部视频和真实PTS帧；033本地/父时间域错误由034唯一修复通过，
042缺basis由043唯一修复通过。最终064多来源basis被单值合同拒绝，065唯一repair
字幕英文引号未转义造成非法JSON，仍无精剪EDL/实际渲染。602文件封存SHA核验通过。
长38received、0pending/unknown，13个有效精看；101.5、250.7、377秒三份粗剪
均实际下载/SHA/完整解码通过。037最终选择101.5秒，但其理由错把别版雪豹剧情
写进首版；038独立观看首版复核fail/fail/partial，流程按失败门槛不进入skill。
chain_result=rough_review_failed_candidate、joint_quality_gate=false；succeeded/exit0
只代表worker正常结束，不能称质量通过。rough_handoff确实存在但记录失败审核。
025盲读错误胜负与026目标审核冲突及037选择幻觉保留，未回传人工纠正。
代理全时轴核验正常，小画面窄带与MCP内部未知采样仍有限制；源码一直039e194。
原件、独立审计和实际媒体在short_continuation_audit与long_contract_audit。
短首次修复幼年角色身份的语义风险见short_continuation_audit，未回传模型。
短001 vision POST至HTTP200约387秒，等待未重发；两worker始终固定039e194。
最新已推送文档7f2d0b8双平台CI实际成功，凭据runs/ci_checks/7f2d0b8_github_pass.json。

本地前向修复：selection明确每候选实际SHA/时长和attached_media归属；事实basis支持
明确picture/text/inference组合，不改原描述。扩展严格匹配的skill观察缓存，原raw可
按当前合同重验证，派生凭据写新evaluation，不改旧失败/parsed、未知不重放。
显式--rough-task配合同一--parent-task只继续已settled实际粗剪后半链；不重检/重渲染。
短已允许handoff复用原上下文并校验SHA；长修正归属后由GLM重新选择实际候选并审核，
仍fail即停止，不人工代选或强行fine。本地相关检查287通过/1权限跳过、三项实际
媒体selection/续接检查通过；尚须服务器验证、Git同步、部署后真实运行。

2af271e已推送并部署：服务器573通过/1排除，13资源/4CLI/doctor/pip check通过，
24保护文件与旧runtime未变。GitHub Ubuntu实际成功，Windows待完成，run38027515262。
上海13:31:48并行后半链续接：短job13c92e6ede204aaeb14400e1b07eefed、prior462，
known_observation_continuation_20261010/evaluations/skill_protocol_continuation_20261010；
长jobd9934fa425c0494497ee21850f9ba228、prior406，
plan_contract_continuation_20261010/evaluations/selection_binding_continuation_20261010。
启动前短参考缓存及原skill context SHA一致；实际064原JSON6事实原范围验证通过，
旧失败记录不改。两个新进程存活，正在文件核验，尚无新fine。
只读watch用Temp/agentic-server-upload/watch_rough_continuation_pair.py，不能再把
旧watch_clean_continuations的两终态误当当前运行。启动凭据rough_continuation_pair_start.json。
上海13:36：短1received/1submitted，完整观察媒体重编码SHA变化故严格缓存没命中，
提示23,112字符及原spec完全相同；不忽略SHA、不宣称零付费复用。001局部范围超6秒，
002唯一repair等待。长001选择实际250.7秒原render1，002实际审核等待。两进程存活，
父ledger未变，0新fine。观察器明确reused_from_parent，复制manifest不是重新粗剪。
上海13:39两路均进入skill：长002复核partial/partial/pass，003完整250.7s观察submitted。
短002修复passed，模型新观察划3贡献/3局部，003局部视频和004PTS received，005PTS
submitted，0 fine渲染。新local queries20–26/74–80/97–103是GLM自己选择，不是人工答案。
所有新工作固定2af271e；不要为了缓存命中修改运行中worker、忽略媒体SHA或重放请求。
上海13:41 GitHub双平台实际成功，run38027515262，凭据runs/ci_checks/2af271e_github_pass.json。

上海14:12短路022原EDL通过、fine0已直连下载：20.466667秒/614帧，11段、4处慢放、
0.5秒冻结，SHA86c0d1e9c9fe0f8649ceb81cd22114dd5efde8d3d10ffd3b48e996b05a4ea854。
原plan/ledger/render input identity、服务器前后SHA、完整解码通过；仍是静音候选，
023盲读pass但有问题/局限，024目标审核submitted，不当质量通过。实际媒体在
rough_continuation_evidence/short/actual_media/render_0/final.mp4。
独立PTS审计发现误认被抓小角色为熊猫、金龙最佳帧出画、结尾双臂姿态解释不符，
没有回传GLM。长路8个自选局部已到第6个，尚无fine。仍固定2af，不热更/重启。
498e324文档提交双平台实际CI成功，run38028403966，凭据498e324_github_pass.json。

2026-10-10最终两路均正常结束：短14:25:59.584594、长14:41:03.741406（上海）。
固定2af271e源码，25/36新请求全部received，22/33parsed，0pending/unknown；
累计分别462+25=487、406+36=442，旧unknown265和016不改、不重放。
本轮各一份实际fine、没有后续实际改版；短108→20.466667s，长250.7→197.8s。
短11段/4处慢放/0.5s冻结；长14段全原速/两处尾帧停留。
GLM短盲读pass/目标partial，长partial/partial；两路joint_quality_gate=false。
短存在人物误认、虚构击飞因果、龙头出画冻结和尾句截断；长食品质量主旨迁移失败，
平行叙事/负面后果缺失，近参考时长合同带来部分冗余补段。人工审计未回传GLM。
短238文件、长302文件封存下载核验；最终614/5934帧及PTS与选中静音渲染相同，
音轨来自同时间参考，长参考含对白，不能称作纯BGM或声画质量通过。
本地便于找到的字节相同副本：runs/history_clean_chain_20261010/delivery/
reference1_glm_finecut.mp4 和 reference2_glm_finecut.mp4。
final SHA短544f567444ff964aef6b88be48397a9e1809fb85a80f9eb7a0680c524d44624a，
长7db6f0d196e258750170b2f98a92a74d3d657b3eec2e30182b32aa3d5462294f。
后续不得自动启动/续跑刷通过，旧失败保留。模型执行结束不等于质量成功。
