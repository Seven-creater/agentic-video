这些 JSON 逐项复制自脱敏证据：
`/Users/Admin/Desktop/agentic_video_all/.agent-team/out/e2e/evidence_01_不烧心_1.txt`。

- `run_outcome.json`、`chain_failure.json`：真实停止结果与失败记录。
- `glm_161_qfs_close_22.json`、`glm_162_qfs_close_23.json`、`glm_163_qfs_close_18.json`：三条完整的真实 HTTP 回复，保留原始 content、reasoning_content 和 usage。

测试直接重放这三条 close 回复。异常类型的测试只在内存中修改 `tts_needed`，不改原始 fixture。
fine 测试使用真实停止记录的错误码与失败分类，绑定到合成的两个独立窗口及实际 LibraryState 台账；重入分支执行真实 `_submit` 的禁止重发检查。
证据未包含失败 fine 作业的 request、events、HTTP 明细或 failure.json，因此不伪造其供应商错误回复，也不推断其 HTTP 状态。
