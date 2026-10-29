来源：`/Users/Admin/Desktop/agentic_video_all/.agent-team/out/e2e/evidence_01_不烧心_3.txt`。

- `glm_171_fine.json`：完整 response.json 中 text 的原始 JSON，未修改字段。
- 三份 plan 夹具：对应 protocol_failure.json 的 model_text。证据中每节只保留约 4000 字符，外层 JSON 以及 audio_draft 已截断。这里只解码并复制所有完整的顶层字段；不补齐缺失台词，不声称是完整模型回复。每份夹具的 provenance 记录具体来源和截断边界。
- 测试中的另两个窗口为明确标注的构造上下文，仅用到拒绝链能确认的身份状态；不代表恢复了缺失的视觉观察。第二来源完整 SHA 未出现在证据中，测试使用相同可见前缀和合成后缀，观测本地实体 ID 按现有入库规则赋值。

测试验证原拒绝仍成立、同一轮能看到独立违规、合法跨来源表达有明确提示，以及现有请求次数和已付费记录保持不变。没有把截断计划改造成可交付视频。
