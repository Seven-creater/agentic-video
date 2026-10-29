# INC-20261029-02 接入创作后调用账本强制串行:136 页粗看要一个多小时

| 项 | 内容 |
|---|---|
| 发现日期 | 2026-10-29 |
| 影响范围 | question_first 生产接入(--material-search question_first)的 skim/close GLM 调用;性能回退(真实服务器 8 并发 5 分钟 vs 串行 1 小时+) |
| 状态 | 已修复(cf9a1aa:仅 qfs_* 免费 GLM 调用开 max_parallel=8 窄口例外) |
| 相关提交 | cf9a1aa |
| 相关任务 | 任务书 `tasks/I3_qfs_parallel.prompt.md`;报告 `out/I3_parallel.md`;审查 `out/I4_review.md` |

## 1. 问题长什么样
qfs 接入创作流水线后,调用账本"同时只允许一个未完成提交"的规则把 136 张宫格的粗看变成串行。真实服务器上同样的量 8 路并发 5 分钟完成;串行要一个多小时,用户明确不接受。

## 2. 怎么查出来的
I1 集成报告披露串行现象;I3 任务书对照服务器实测(136 sheets、8 并发、5 分钟)定量给出差距。

## 3. 根因
代码问题(规则适用范围过宽):单一未完成提交规则是为"付费调用结果未知必须阻塞"设计的正确保守策略,但 qfs 的 GLM 套餐调用免费、只计次数,串行等待没有收益。

## 4. 解决方案
- 状态锁内短临界区:预约调用(id、name、request sha)先写 `submitted` 再发送(保持先写账本后发送);HTTP 在锁外并发;`received`/`failed`/unknown 回锁内落盘;请求上限与预算检查在预约时(锁内)做。
- 窄口例外仅限:调用名前缀 `qfs_*`、question_first 阶段、GLM direct 传输(`state.py` 的 `_qfs_direct` 校验名称前缀/stage/provider/endpoint/model 且禁 repair);其余一切调用(全部 Qwen/TTS/付费调用、非 qfs 的 GLM 阶段)单一未完成规则原样保留。
- 未知的 qfs 结果依旧永不重发:上报后 runner 用其余结果继续;崩溃恢复把 qfs submitted 归为 uncertain,重跑只发未发过的名字。
- I3 同时修 GLM 审查的 5 项低危:clips/锚点静帧原子写(临时文件+rename,复用时校验非空可解码)、`load_shot_cache` 显式键校验、tile 用 `tile['position']`(校验 0..len-1 唯一)、resume `first_round>0` 从钉住的 qfs artifact 恢复 `story_slots`、`parser.error` 拒绝 `--reuse-plan` 与 `--material-search question_first` 组合。
通用性:按"调用是否免费"划分账本策略,与题目、影片无关。

## 5. 为什么不用其他方案
- 保持串行:1 小时+ 的粗看不可接受(用户明确)。
- 全面放开并发(所有调用都可多个未完成):付费调用结果未知时必须阻塞,这是防重复计费的核心安全设计,不能动(用户决定:GLM plan 免费只计次数,故仅 qfs 开例外)。
- 未知 qfs 结果自动重发:违反不重发规则;选择"上报、继续用其余结果"。

## 6. 怎么验收(I3_parallel/I4_review 实际输出)
- QFS 测试 168 passed(0152f74 隔离源码对照 168 failed)。
- 并发模拟(16 请求、单次 sleep 0.25 秒):skim 0.539 秒、close 0.578 秒,峰值并发 8(串行约 4 秒)。
- 崩溃恢复:子进程 `os._exit` 模拟中途崩溃,重试不重发、二次重跑 0 POST。
- 全量:`9 failed, 1508 passed, 104 skipped, 24 errors`,FAILED 为基线子集;Replay 149/0/22/32;Fault 5 项+high 全 0。
- I4 审查逐项复核通过;其"策略性提醒"——任一 qfs 未知会阻断后续全部付费调用——由 INC-20261029-08 处理。

## 7. 量化数据
16 请求模拟:0.539 秒(并发)vs 约 4 秒(串行推算),峰值并发恰为 8;真实服务器 136 页粗看 8 并发约 5 分钟(v1 实测 368.6 秒)vs 串行 1 小时+。锁内重复哈希 pending request.json 的性能小注(I4):136 张表时锁内开销明显,只影响速度不影响正确性。

## 8. 经验与后续
- 保守规则要以"风险类别"为粒度开例外,例外条件要在代码里逐项校验(前缀/stage/传输/端点/模型),不能靠约定。
- 先写账本后发送在并发下依然成立:预约在锁内、发送在锁外。
