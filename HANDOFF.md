# 当前任务：历史粗剪到skill精剪的干净服务器流程

2026-10-10用户授权Goal、提纯历史77→22流程、并行测试两参考，取消固定总上限。
原始聊天已检索，证明在原workspace runs/history_clean_chain_20261010/CHAT_PROOF.md。
旧修复/失败任务停止；本轮在codex/clean-historical-chain隔离worktree整理，
不混入原workspace未完成的slice/CI等补丁。

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
最新只读快照确认两worker running、父ledger未变，磁盘可用约8.0GB。
短复用8项父导航观察，新search选10个窗口并进入精看；新2登记/1received。
长新5登记/4received，overview JSON唯一修复已过，进入coarse_zoom_search。
短001 vision POST至HTTP200实际约387秒，是等待云端回复，未重发。尚无新render。
