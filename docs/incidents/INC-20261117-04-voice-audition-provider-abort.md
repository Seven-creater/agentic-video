# INC-20261117-04 Qwen 原声试听返回"生成中止"400，整个任务停机（02 雪山救狐狸 A 线）

| 项 | 内容 |
|---|---|
| 发现日期 | 2026-11-17 |
| 影响范围 | 选角阶段的原声找样本、原声核听（`source_voice_review.py`，A、B 两条线共用）；致命停机 |
| 状态 | 已修复：A 线 885e392（release d82361fc0bee），B 线 dc46262（release 42d283e25e56） |
| 相关提交 | 885e392、dc46262（仅本地分支，未推送） |
| 相关任务 | `trials/fast_template_20261117_015248_02_雪山救狐狸_01_e5a516763632` |

## 1. 问题长什么样
`error: source_voice_sample_transport_failed:400`，报错位置 `source_voice_review.py:91`。

## 2. 怎么查出来的
读失败那次试听的 `response.json`（`character_voices/33ced98504_3/audition_v2/dc677aea04e2516a`）：`http_status: 400`，返回原文：

> `<400> InternalError.Algo.InvalidParameter: Model output became abnormal while generating a JSON response for response_format. The generation was aborted ... Please retry the request`

`state.json` 的状态是 `received`，说明结果是明确的：请求失败了，没有收到任何回答。不属于"结果不确定"的情况。同一阶段的其余 8 次试听都是 200。

## 3. 根因
外部服务偶发的问题：Qwen 在生成 JSON 时自己中止了。代码层面：`find_and_audition` 和 `checked_review` 把所有非 200 的返回都当作致命错误，没有区分"接口明确中止、可以重问"和其他错误。属于容错缺失。

## 4. 解决方案
- `audio_review._result`：非 200 时把接口的错误信息放进 `provider_error` 字段。
- `source_voice_review.aborted_generation`：HTTP 400 且错误信息含 `InternalError.Algo`，判定为接口中止。
- 两处试听循环遇到接口中止时，走现有的第 2 次尝试。第 2 次的提示词不同，所以是一个新请求，绝不重发已收到的那一个。两次都失败时，按现有规则以 `SourceVoiceProtocolError` 结束。
- 其他非 200 的错误照旧停机。
- 这个判断只看接口的错误类型，和模板、角色都无关。

## 5. 为什么不用其他方案
- 所有 400 都重试：参数错误、时长超限这类问题重试也没用，还会浪费调用。
- 把接口中止当成"这个角色没有原声"：没有证据说明没有原声，A 线会因此误换角色。

## 6. 怎么验收
- 新增 `test_voice_provider_abort.py`，共 4 项：
  - 用真实返回原文识别接口中止，别的 400 和 200 都不算；
  - 中止后用新请求重问一次就能拿到结果；
  - 其他 400 照旧停机；
  - 连续两次中止，以协议错误结束。
- 服务器全量测试：A 线 228 项、B 线 232 项全部通过。
- 真实续跑：02 A 线从停机处续跑（030021），结果待补。

## 7. 量化数据
这次停机前，同阶段 9 次试听里有 1 次被接口中止。这类中止整体有多频繁，未统计。

## 8. 经验与后续
外部接口的错误要分三类处理：明确失败可以重问、结果不确定绝不重发、参数错误要修代码。
