# INC-20261101-06 一个精看修复被限流(429)拒绝后，发送护栏连带拒发后续全部调用

| 项 | 内容 |
|---|---|
| 发现日期 | 2026-11-01 |
| 影响范围 | 创作路线 opencode 发送护栏；致命停机（不烧心 fix1，200 次调用零产出） |
| 状态 | 已修复（9f21764）；不烧心 fix2 用该版本完整交付 |
| 相关提交 | 9f21764 |
| 相关任务 | sb_01_不烧心_11010232/evaluations/fix1 |

## 1. 问题长什么样
`opencode_vision_job_failed_no_retry:glm_200_plan_0`。账本 185 次 received、15 次 failed_known：`glm_186_fine_370f212b32324320_repair` 报 `library_mcp_retry_or_budget_blocked`，之后 glm_187…glm_199 共 13 个精看和 glm_200 plan_0 都报 `library_mcp_opencode_current_job_or_previous_outcome_invalid`。

## 2. 怎么查出来的
读账本非 received 行 → 读 mcp_http.jsonl：glm_185 返回 200 但本地校验失败（`fine:usable_range_event_does_not_overlap`）；其修复 glm_186 只 POST 了一次（seq 7），收到 **HTTP 429** `{"code":"1302","message":"Rate limit reached for requests"}`，客户端在同一 job 内再试被 src/omni_story/library/mcp_guard.mjs:379 拦下；之后的 14 个 job 在 HTTP 之前被 src/omni_story/library/mcp_opencode_guard.mjs 的“所有先前调用必须 received”规则拒绝，一个都没发出。

## 3. 根因
直接原因：当时两条试跑任务并行，智谱接口限流。根本原因：Python 主流程把失败的独立精看窗口视为可跳过（继续提交后续窗口和规划），而 JS 护栏要求之前每一行都是 received，只对预检失败、丢失的 qfs_ 调用等少数情况放行。两边规则不一致，一个“已知且未被处理”的拒绝毒化整条任务。与 INC-20261030-03 同类（代码问题：流程分支不一致）。

## 4. 解决方案
mcp_opencode_guard.mjs 新增放行条件：仅 `reference_rough_skill_v1` 工作流；行名为独立精看 `^fine_[0-9a-f]+(_repair2?)?$`；状态 `failed_known` 且 failure.json `uncertain:false`；mcp_http.jsonl 证明该 job 没有发出请求，或每次请求都得到明确的非 2xx 响应。任何 2xx、未知结果、未响应请求、非观察阶段仍然拦截。429 不新增自动重发。Python 侧确认失败窗口不进入 watched_windows。

## 5. 为什么不用其他方案
- 429 自动等待重发：违反用户“不重发”规则。
- 放行所有 failed_known：可能放过已产生输出或计费的调用。
- 只靠串行运行避免限流：治标，单任务内并发也可能 429。

## 6. 怎么验收
新增测试：证据形态（一次 429）放行；有 200 响应、uncertain、失败行为 plan_0、旧工作流均仍拦截；一个失败窗口被跳过后规划照常。全量测试失败集合等于基线，回放 0 退化，fault_check 关键项为 0。服务器：不烧心 fix2（9f21764）delivered。

## 7. 量化数据
fix1：200 次调用、14 个调用被连带拒发、0 产出。fix2：219 次调用，交付 33.0 s 成片。

## 8. 经验与后续
试跑改为一次只跑一条任务。qfs 直连的 HTTPError（多为 429）目前记为 uncertain 并跳过，影响召回（雪山 fix2 丢 4 张缩略图），以后可区分“明确拒绝”与“未知结果”。
