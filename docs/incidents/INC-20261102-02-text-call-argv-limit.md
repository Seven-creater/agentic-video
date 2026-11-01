# INC-20261102-02 188 KB 静音审查提示词整段作单个 argv 元素传给 opencode，超 Linux MAX_ARG_STRLEN(131,072) 在 spawn 即 OSError 却记 uncertain：三次验证运行全部停在 text_check_outcome_unknown_no_replay

| 项 | 内容 |
|---|---|
| 发现日期 | 2026-11-02 |
| 影响范围 | picture_aware 默认风格（INC-20261102-01）的静音审查文本调用（text_completion 传输层与失败分类）；S25 上线后全部 default 验证运行致命停机（证据任务 9 次调用零产出） |
| 状态 | 已修复（5e697f6 传输＋spawn 分类＋精简提示词＋advisory 降级；d5f88ae 服务器冒烟并修配置文件指令措辞） |
| 相关提交 | 5e697f6、d5f88ae；引入面 93edb02（S25 上线该审查调用） |
| 相关任务 | sb_01_不烧心_11010232/…/theme1/evaluations/default1（release 93edb02，9 次调用后停机；证据 calls/glm_009_silent_review_0）；任务 .agent-team/tasks/S26_text_call_argv_limit.prompt.md；报告 .agent-team/out/s26/S26_fix.md、S26_review.json（审查 verdict pass） |

## 1. 问题长什么样
- default1 运行 9 次调用后停：`text_check_outcome_unknown_no_replay:glm_009_silent_review_0`。
- 该调用目录三份文件：status `uncertain`；failure.json `{"error":"OSError","uncertain":true}`；agent_events.jsonl **0 字节**（子进程从未启动，什么都没发送）；request.json 187,969 字节（审查提示词约 188 KB）。
- 三次 default 验证运行全部以同形状停在 `text_check_outcome_unknown_no_replay`（用户侧汇总；存档证据为 default1，另两次任务号未记录）。

## 2. 怎么查出来的
- 对比同一调用的文件：request.json 187,969 字节 vs agent_events.jsonl 0 字节→子进程从未运行，问题在启动前而非模型侧。
- 定位 `src/omni_story/library/pipeline.py` 的 `_submit_text`（93edb02 时约 1196–1260 行，任务说明记载）：把 `request['arguments']['prompt']` 作为**单个 argv 元素**传给 `opencode run`（约 :1230）。
- 核对系统限制：Linux 对单个 argv 字符串上限 MAX_ARG_STRLEN＝131,072 字节→188 KB 必然 `OSError: [Errno 7] Argument list too long`，发生在 Popen 构造期。
- 判定链闭合：OSError 出现在 begin_call 之后、子进程启动之前→按当时逻辑记 uncertain→uncertain 全局不重放→停机。（推断）no-replay 拦停本身是既有付费调用保护、按设计工作，错的是把"从未发送"记成"结果未知"。

## 3. 根因
- 直接原因：提示词 187,969 字节＞131,072 字节单 argv 上限，subprocess 创建即失败。
- 根本原因（代码问题，两层）：
  1. 传输层无长度防护：文本调用把整个 prompt 放 argv；仓库内视觉路径早有"prompt 不进命令行"的先例（OPENCODE_CONFIG_CONTENT），文本路径未跟进。
  2. 失败分类缺失：spawn 前 OSError（E2BIG/ENOENT/EACCES，什么都没发送）被记 uncertain，而 uncertain 触发全局 no-replay 停机；"从未发送"应是 failed_known。
- 诱因（非缺陷）：S25 的 silent_review 证据负载天然大（逐句时间轴＋逐段精看观察＋盲看全文＋主旨）→188 KB。
- 流程缺口：离线测试无法演练真实 opencode CLI——本机无该二进制，测试 monkeypatch 的是子进程运行期行为，spawn 期失败形状从未被覆盖，上线前也未做真实冒烟。

## 4. 解决方案
5e697f6：
- **传输**（`src/omni_story/library/pipeline.py:30` `TEXT_ARGV_MAX_BYTES = 100_000`；:1388–1400）：＞100 KB 的 prompt 追加进 config 写入 `work/opencode.json`，经 `OPENCODE_CONFIG`（文件路径）交给 opencode，argv 只留一句短指令；`OPENCODE_CONFIG_CONTENT` 在追加 prompt 前就用基础 config（约 1 KB）赋值——argv 与任何环境字符串都不含 prompt；≤100 KB 仍走 argv（100,000＜131,072 留余量）；request.json 照记完整 prompt，回放按其哈希，录制行为不变；无安全传输时在 begin_call 前报错（兜底原则）。
- **spawn 失败＝已知**（pipeline.py:1389 一带）：内层 try 只包 subprocess.run，用 traceback 帧检查 process 是否已绑定（Popen 构造期未绑定）——构造期 OSError→`failed_known`（未发送），走正常修复/停机逻辑、不触发 no-replay；子进程启动后的错误维持 uncertain。
- **精简审查提示词**：只留检查所需——每行（id/text/picture_relation/mode/speaker/起止/segment_ids）、每段与其源区间重叠的精看观察事实（events、usable range notes、roles）、盲看全文、主题一句；确定性生成；字节数记 `prompt_bytes` 入 silent_review.json 与 run 结果。大型合成案例 13,280 字节（实际任务约 13–21 KB），不再逼近任何上限。
- **advisory 闭环**（pipeline.py:234 一带 `_picture_aware_review`）：blind_*/silent_review_* 调用 failed_known/uncertain/修复耗尽→记 `review_unavailable`（reason、call_id）后继续 TTS，不修订、绝不重发 uncertain；仅对 13 个可用性/协议类前缀降级，完整性错误（如 silent_review_record_changed）仍停跑；picture_first 严门行为不变；legacy 检测（无 prompt_bytes 且无 review_unavailable 的旧记录）保旧录制回放字节不变。

d5f88ae（冒烟修复）：
- 服务器真实冒烟：小 prompt（argv 通道）正常；151 KB 配置文件 prompt——含糊英文指令 1/1 只回摘要、不回要求的 JSON；把配置文件指令改为**明确的中文指令**后 2/2 返回要求的 JSON。

## 5. 为什么不用其他方案
- 经 stdin 传 prompt：opencode run 无此接口先例（推断：需改 CLI 调用形状，回放录制面更大），配置文件是仓库已有先例的扩展。
- 调大系统 argv 上限：MAX_ARG_STRLEN 是内核对单个字符串的硬上限，不可配置绕过，且服务器环境不受我们控制。
- 只精简提示词、不设防护：任何未来证据负载变大都会再撞墙；精简与上限防护两层都要。
- 审查失败即跳过审查：审查是 S25 用户裁定的 advisory 信号，正确语义是"记录 review_unavailable 后继续"，不是静默丢。
- 不做冒烟、靠回放兜底：本事故正说明离线测试覆盖不到真实 CLI；真实冒烟成为新传输上线门（见第 8 节）。

## 6. 怎么验收
- 新增 `tests/test_text_call_transport.py`（110 行）：超限 prompt 走安全传输且命令行保持小；模拟 E2BIG/ENOENT/EACCES spawn→failed_known 无 uncertain（communicate 期 EIO 仍 uncertain、timeout/exit 亦覆盖）；picture_aware 审查失败→review_unavailable 后继续 TTS；真实证据形状的合成大型窗口集 prompt＜上限；picture_first 不变。
- 三门（作者实测，S26_fix.md）：定向 116 通过；离线 pytest 2488 通过、8 失败 ⊆ baseline_2879e5c（24 个 socket 测试沙箱排除）；replay ok/fixed 降级 0；fault E1/R1/M1_crash/M2_json/M2_crash 全 0；无 uv.lock。变更 pipeline.py 183、prompts.py 24、测试 +272（含新增文件），4 文件 +457/−22。
- S26 审查 verdict pass、无 findings（审查沙箱拒跑 pytest，逐 hunk 核对代码与作者日志：无 argv/环境变量泄漏路径、spawn 分类双向、advisory 降级白名单、legacy 回放字节不变）。
- 服务器冒烟（d5f88ae）：小 prompt 正常；151 KB 配置文件 prompt＋中文明确指令 2/2 返回要求的 JSON（含糊英文指令 1/1 摘要）。
- 后续真实运行：default 系列继续暴露 S27 分类缺陷（INC-20261102-03），其修复后 default4 交付。

## 7. 量化数据
- 修复前：审查请求 187,969 字节（＞131,072 上限）；default1 9 次调用零产出停机；三次验证运行全部停 `text_check_outcome_unknown_no_replay`。
- 修复后：审查提示词大型合成案例 13,280 字节（实际任务约 13–21 KB），较 188 KB 降约一个数量级；＞100 KB 走配置文件，argv 恒小。
- 冒烟对照：151 KB 配置文件——含糊英文指令 1/1 摘要（不合格）→中文明确指令 2/2 要求的 JSON。
- 回放/故障：ok/fixed 降级 0；fault 五项 0。
- 修复开发自身花费：未记录（离线开发任务）。

## 8. 经验与后续
- **流程教训（已按此执行）**：离线测试无法演练真实 CLI 的传输与失败形状（本机无 opencode、monkeypatch 的是运行期行为）——新的模型调用传输上线前必须先在服务器做一次真实冒烟；d5f88ae 即补做冒烟，并立刻发现测试永远发现不了的问题（含糊英文指令只回摘要，配置文件通道对指令措辞敏感）。
- argv/环境变量永远不放大 payload：视觉路径的"prompt 进配置"先例应作为所有新调用通道的默认设计，而不是事后补。
- "从未发送"与"发送后未知"必须分类：spawn 期错误是已知失败（failed_known），只有子进程启动后才可能 uncertain——该分类原则随后在 INC-20261102-03 被推广到"已应答但正文无效"的形状。
- S26 审查当时遗留的关注：审查调用若以 uncertain 结束，begin_call 的全局 unknown 阻塞仍会拦住后续需要模型的阶段——该关注在 INC-20261102-03 成真并修复。
- 需要用户决定：无（冒烟闭环；遗留已转入 -03 记录）。
