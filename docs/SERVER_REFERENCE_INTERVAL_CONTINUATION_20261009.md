# 第二条参考视频的同任务续跑

用户在区间反馈修复后明确要求“那接着后续剪辑”。本次仅续用
`shared/runs/server_reference_7692329355342679331_20261009` 的原任务、输入锁和台账。
初始停止点为29条已收到回复、0次渲染；已有GLM草案92秒、预精炼方案88秒。
参考为 `7692329355342679331/video.mp4`，实际时长198.461995秒。

`server_interval_resume` 只将已知失败的首切片审核映射到一个有明确授权的新别名。
输入仍是原片2020–2025秒的同一个代理，追加通用的零长度诊断，最多一次纠正及其唯一格式修复。
旧028/029的请求、回复、拒绝记录和已耗尽修复保持原样；不填造观察时长或人工切点。
其余阶段继续复用原收到回复，后续素材与剪辑判断仍由GLM产生。

登记记录保存原29条台账副本、调用文件、失败控制、计划、历史HTTP前缀及部署代码SHA。
Python与原生请求守卫都核对这些绑定，新增请求仍使用官方视觉MCP及国内Coding Plan。
原有效16窗口、最多2个候选和2次渲染范围保持不变；没有新增候选轮次或重置调用计数。
第二候选仅在原流程审阅需要时使用原未用范围。终止错误不自动再次启动。

控制目录为原任务的 `controllers/interval_resume_v1`，仅保存进程控制信息。
工作进程的 `--output` 仍为原任务；控制目录不创建新的 `LibraryState` 或预算。
原 `.omni-server` 的失败控制不覆盖；续跑失败另存 `failure_interval_resume_v1.json`。

入口为：

```bash
python -m omni_story.library.server_interval_resume start \
  --home /home/ubuntu/apps/agentic-video \
  --output /home/ubuntu/apps/agentic-video/shared/runs/server_reference_7692329355342679331_20261009
```

同一模块的 `status` 和 `stop` 操作新控制目录，不会重新提交模型请求。
上线前须完成离线登记/队列边界测试、原生请求守卫测试、安装包验证，
并对真实登记记录执行Python及JavaScript只读交叉核验。
实现和合成测试不能证明视频质量；实际调用、渲染、盲读及目标审核另存本轮记录。
