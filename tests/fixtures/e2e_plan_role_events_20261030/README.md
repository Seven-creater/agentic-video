来源：`/Users/Admin/Desktop/agentic_video_all/.agent-team/out/e2e/evidence_19_马冬梅_16.txt`。

- `glm_165_fine.json`：完整 response.json 的 text JSON，逐字段复制。
- 三份 `*_excerpt.json`：从对应 protocol_failure.json 的 model_text 解码并复制完整的 reference_sha256、focus_role_id、focus_role_bindings、slots、segments、cast 字段。原文每节约 4000 字符后截断，未补齐缺失的回复或台词。
- 证据不含首个失败窗口的完整观察，不能证明其精确事件边界。测试的前两个窗口为明确构造的上下文；最后一个窗口使用真实 fine 回复。真实 fine 中的事件空隙也能独立复现同一条裸错误。测试中的边界选择和 role_ids=[] 修改是合法性探针，不是模型原始回复。

测试只回放本地数据，不调用模型、不重发历史请求，不宣称已重新完成端到端成片。
