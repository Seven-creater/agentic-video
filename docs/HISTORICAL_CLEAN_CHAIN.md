# 从原聊天提取的粗剪到精剪流程

## 原始求证

原对话`01a0fcd3-ad4b-7602-a44e-b736938d0cd1`的JSONL记录已由工具逐项检索。不是依据后来的状态总结猜测。

| 原记录行 | 原动作或消息 |
| --- | --- |
| 2795 | 首次GLM交付约77秒粗剪，消息`msg_08a20f1694efbb43016ac1d8c346ec87d0b0acb3e49cc0d583` |
| 26802、26888 | 用户要求Codex充当老师示范，并将输入改为完整77秒 |
| 27293 | 用户要求接近参考时长即可，不一味追求短 |
| 27385 | 教师21.87秒视频及`visual-story-finecut` skill交付 |
| 27395 | 用户要求把skill迁移给GLM |
| 29168 | GLM77.37→21.9秒实际交付 |
| 29181、29247 | 用户质疑相似/抄袭，随后核验GLM实际请求与独立编辑表 |

原记录文件为Codex sessions/2026/10/03下的
`rollout-2026-10-03T22-26-38-01a0fcd3-ad4b-7602-a44e-b736938d0cd1_01a10228-ea09-7990-8921-a90094f3def2.jsonl`。
原workspace的`runs/history_clean_chain_20261010/CHAT_PROOF.json`保存筛选原文、时间、消息ID、行号和文件SHA。
独立核验251–274原始响应与计划：268/271的模型EDL与两实际计划一致，skill冻结副本确实出现在模型请求里。
未知265不重放，最终盲读partial与目标审核pass同时保留。历史约22秒由当时21.933333秒参考决定，不是固定项目长度。

## 现在的单一入口

```text
omni-server start
  -> server_cli._run
  -> clean_chain.execute
     -> pipeline.execute + 已求证的original_rough_v1通用提示/原渲染器
        -> 参考理解 / 已收到的参考缓存
        -> 素材导航与GLM自主检索、精看
        -> GLM粗剪EDL
        -> 实际粗剪渲染、盲读、目标审核和选片
     -> 审看实际选中的粗剪，保存rough_handoff.json
     -> story_finecut.execute_finecut
        -> 老师通用skill + decision-cards
        -> 完整粗剪视频观察
        -> GLM按缺项选局部视频与真实PTS帧
        -> GLM精剪EDL
        -> 实际精剪渲染、独立画面盲读、对照审核
        -> 有真实编辑与审核进展才修订
        -> 精剪视频 + 同速参考音轨 + 局限
```

粗剪和精剪共用一个模型台账；`result.json`保留粗剪结果，`chain_result.json`记录完整链。精剪输入绑定实际粗剪文件SHA和时长，不能只把粗剪文字计划当输入。GLM接收通用skill和观察证据，不能接收教师的电影剧情、选片或切点。

主流程只需要理解四个文件：`server_cli.py`启动、`clean_chain.py`衔接、`pipeline.py`生成粗剪、`story_finecut.py`精剪。旧恢复controller保留用于历史复查，不再是默认入口。`active_finecut`的渲染前计划精炼不参与这条线。

## 随机参考与停止条件

2026-10-10用户要求不设固定上限。新服务器入口不固定22/180秒、80次请求、16个精看窗口、32/24段或两轮渲染。历史资源原字节保留，薄运行适配只移除旧总量限制。

- 精剪目标由实际参考和实际粗剪决定。粗剪已经短于参考时，不靠等待、重复或整体慢放补长。
- 检索按缺项继续；请求已处理窗口不算新证据。重复真实EDL不再渲染。
- 没有新观察且实际审核不改善，停止粗剪迭代。
- 精剪无实际操作变化、审核变差或没有进一步改善，停止改版，保留有效候选和局限。
- 结果不明的请求不重放；每个格式错误的原始响应保持。真实源范围、类型、身份/事件证据和媒体绑定校验不取消。

工具按小窗口/图片网格分批，单次输入须适合官方接口；这些是处理粒度，不限制整条视频或任务总量。可播放与模型自评不构成独立质量通过。

## 两条参考的服务器测试

两个新测试各自位于原任务的`evaluations/history_clean_chain_20261010`。父台账及旧已完成恢复链的计数、字节SHA纳入输入锁；不重启原失败controller、不改旧回复、不清零旧用量。

```bash
omni-server --home /home/ubuntu/apps/agentic-video start \
  --reference /home/ubuntu/apps/agentic-video/shared/data/ref/video.mp4 \
  --library /home/ubuntu/apps/agentic-video/shared/data/videos \
  --output /home/ubuntu/apps/agentic-video/shared/runs/server_edit_test_20261009/evaluations/history_clean_chain_20261010 \
  --parent-task /home/ubuntu/apps/agentic-video/shared/runs/server_edit_test_20261009 \
  --reference-cache /home/ubuntu/apps/agentic-video/shared/reference_cache/original_reference_001.json \
  --asr --asr-model-dir /home/ubuntu/apps/agentic-video/shared/library_models/faster-whisper-small-536b066/from-local
```

第二任务使用198.461995秒参考`shared/data/ref/7692329355342679331/video.mp4`，相应父任务`server_reference_7692329355342679331_20261009`、接收缓存`reference2_001.json`。并行启动各自的脱离终端任务，不共用账本或输出目录；读取共用原素材。

原始过程、实际调用/失败、粗剪/精剪、部署与审计证据保存在`runs/history_clean_chain_20261010`。最终交付须提供可复制绝对路径；此文描述实现，不提前声称两模型测试或质量通过。
