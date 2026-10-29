本目录保留证据中的完整 JSON，未改写内容：

- `unknown_calls.json`、`run_outcome.json`：`evidence_18_粉色娇嫩_13.txt` 的同名段。
- `fine_response.json`：`evidence_18_粉色娇嫩_12.txt` 的
  `glm_185_fine_2d82f39a6adccc09 response.json` 段。

原证据不含失败请求正文、MCP HTTP 日志或逐次 failure.json。
测试用 DirectTransport 生成合约相同的本地合成请求，复现真实的未知 skim 状态；
原调用编号不作为业务条件。接下来的 MCP 使用本地假传输验证发送准入，
并用真实 fine 回复验证观察合同、记录收到的回执。
没有提供真实视频，也没有访问模型服务。fine 的测试窗口只绑定回复中明确的
window_id/source_id 及 0–8 秒局部范围，不推断原片入出点。
