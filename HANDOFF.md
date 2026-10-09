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
