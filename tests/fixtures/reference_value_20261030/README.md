# 夜跑最小视觉要求与 plan 协议夹具

来源为 `.agent-team/out/e2e/` 下 `evidence_01_不烧心_3.txt`、`evidence_02_雪山救狐狸_4.txt`、`evidence_18_粉色娇嫩_14.txt`、`evidence_19_马冬梅_16.txt`、`evidence_19_马冬梅_17.txt`，以及 `evidence_01_不烧心_4.txt`。

- `bushaoxin_questions.json`：最后一份证据中 questions 的前 3 个完整 beat；原文件在第 4 个 beat 内截断，未补造余下部分或 casting。
- `bushaoxin_av_review.json`：同份证据的完整 AV 负评，保留“缺双人对话”形式误判及“画音割裂”问题；不把原判决改成通过。
- `protocol_failures.json`：五份耗尽记录的 16 条协议失败（其中 15 条 plan、1 条 fine）。`model_text_excerpt` 保留原始模型文本片段；`reply_excerpt` 仅解码其中完整的顶层字段，未补造被截断的 audio_draft。原 evidence 的长度截断也可能发生在 JSON 包装层内。

测试对缺失的 request/watched_windows 明确使用受控观察，不声称恢复了缺失像素。马冬梅事件间隙和不烧心跨来源绑定同时复用已有 `e2e_plan_role_events_20261030`、`e2e_plan_bindings` 的真实 fine / plan 夹具。没有重新发送模型请求。
