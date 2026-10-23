# 模板参考上传与评测

更新：2026-10-24。采集模块、参考文件和评测代码均部署在 Linux 服务器。已收到 24 个模板目录、240 条视频；全量媒体盘点与小规模开发评测的原始结果单独保存。这里的模板理解评测不等于新模板自动成片验收。

## 上传位置

```text
/home/ubuntu/apps/agentic-video/shared/template_hunter/inbox/
  01_模板名/
    01.mp4
    02.mp4
    metadata.json
  02_另一个模板/
    ...
```

保留原目录分组，`metadata.json` 有则一并上传。缺少元数据可以盘点，但来源或审核状态不会自动补造。单个模板只有一条视频时仍可入库，暂不满足示例与未见实例分开的评测条件。目录名称不提供给理解模型。

另一个电脑开发的模块可以继续独立更新；服务器使用锁定的部署副本。密钥来自本地 Claude 设置，只把需要的接入字段写入服务器权限为 `0600` 的运行配置，不把整个 Claude 设置复制进仓库。

## 本轮部署检查

- 模型：`glm-5.3-flash`，端点：`https://api.z.ai/api/anthropic`。
- 服务器 FFmpeg／FFprobe、浏览器启动及 CDP 连接检查。
- 一次真实模型请求识别合成蓝色图片；这是接入验证，不是参考视频采集或模板质量评测。
- 收到的回复持久保存；重复检查复用原回复。网络未决不重发，同一未决视觉输入不能通过修改提示词再次提交。
- 旧热点参考与未引用的渲染缓存已按用户要求清理；原电影、素材库、台账和已交付产物保留。旧参考的历史评测仅供归档，不能再把已删除媒体当作存在。

部署使用已有 Linux headless shell；页面数据钩子通过 CDP 在文档加载前注入，保留扩展的防重复安装逻辑。没有在本轮自动登录或声称完成一次新的抖音采集。

## 入口

```bash
/home/ubuntu/apps/agentic-video/bin/hunter-server status
/home/ubuntu/apps/agentic-video/bin/hunter-server doctor --browser
/home/ubuntu/apps/agentic-video/bin/template-eval --help
```

`doctor --live` 是显式模型验证。已有完全相同请求时使用回执缓存；它不作为每次业务启动的前置付费探测。

上传完成后的无付费准备：

```bash
template-eval inventory \
  --root /home/ubuntu/apps/agentic-video/shared/template_hunter/inbox \
  --output /home/ubuntu/apps/agentic-video/shared/template_hunter/inventory.json

template-eval prepare \
  --inventory /home/ubuntu/apps/agentic-video/shared/template_hunter/inventory.json \
  --experiment-id templates_dev_v1 \
  --support-count 4 --query-count 2 \
  --output /home/ubuntu/apps/agentic-video/shared/template_hunter/draft.json
```

盘点会完整解码媒体，保存 SHA、时长与音轨事实，报告坏文件和完全重复文件。`prepare` 只生成草稿，不会自动把文件夹认作干净评测集。之后补充近重复分组、族别、核心要素与必要顺序，再 `freeze --draft ... --output ...`。这是数据集准备；运行中为某条结果改提示词或代码属于另一类人工介入。

冻结后可用：

```text
check       核对冻结身份与文件哈希，不调用模型
understand  指定 template-id 与 single_reference / multi_reference，输出 prediction.json
judge       读取 prediction.json，由独立声画模型实际观看未见实例并评分
report      从 record.json 汇总 JSON 和 Markdown；无记录的计划项也保留
```

`understand` 与 `judge` 都要求服务器运行配置和同一预算台账；完整参数见各命令 `--help`。这轮提供逐任务入口，也提供可恢复的 `run` 批次入口；先做 3 个模板的小规模开发试点，不直接运行全部 24 模板。

## 服务器批次执行

冻结清单放在 `shared/template_hunter/experiments/<实验ID>/manifest.json`，输出写入 `shared/jobs/<实验ID>/`。安装仓库的 `deploy/omni-template-eval@.service` 后，以实例名启动：

```bash
sudo systemctl start omni-template-eval@templates_dev_20261024_v1.service
sudo systemctl status omni-template-eval@templates_dev_20261024_v1.service
```

服务默认最多两路，沿用已有资源准入、每路 1024 MiB 硬限、全局 FFmpeg 锁及累计 100 元费用账本；总内存另限制为 2800 MiB。资源不足排队十分钟后如实记阻塞。一个任务出错后记录状态，其他独立任务继续。版本、输入、输出位置固定，重启不能借新目录重发未决请求。只有本地进程中断允许恢复一次，模型失败不触发盲目重调。

结果位置：

```text
batch_state.json                 执行检查点、进程身份与完成状态
jobs/<模板ID>/<方法>/record.json  分数或失败原因
jobs/.../resources_*.json         每路内存限制、峰值与 OOM 计数
execution.json                   耗时、恢复次数、调用数、累计费用预占
reports/report.json              保留全部计划项的指标
reports/report.md                便于阅读的指标表
```

实验锁定代码内容；中途修改代码需新实验版本，原失败不覆盖。停机时 systemd 清理整个进程组；恢复时复用原始回执。`runtime_manual_interventions=0` 只描述冻结后运行，不包含提前制定评测要素、筛选开发样本或检查近重复。首轮要素由本会话依据抽帧制定，属于模型辅助的开发标准，不是独立人工金标准。

## 当前评测范围与打分

第一版比较单参考与多参考的**模板视觉理解**。理解器只读取 support 的稀疏画面，输出机制、事件和最多 20 条可核验主张；不读取模板名称、全量分析、query 或标准答案，也不能声称听到了音频。

独立评审使用原有实际声画评审通道，观看完整 query 实例，并读取冻结要素表和候选主张。它不知道候选方法名称；与 GLM 理解请求分别保存回执。无音轨素材在评测传输包中补的静音有明确标识，不能当成原视频的真实声音。

| 指标 | 计算 |
|---|---|
| 模板保持分 | 每项 0／1／2 分，必需项权重 2，可选项权重 1；`100 × Σ(权重×分数)/(2×Σ权重)` |
| 必要顺序正确率 | 正确体现的冻结顺序关系数／全部冻结顺序关系数；事件遗漏不能算顺序正确 |
| 无证据主张率 | 核定 unsupported／已核定主张；同时报告核验覆盖率，unknown 不冒充 supported |
| 评分覆盖率 | 实际完成有效评分的任务数／全部计划任务数 |
| 检索指标工具 | 提供 Recall@K 与 MRR 计算；尚未在上传数据上建立检索题或实测 |

非零要素分必须有实际 query 视频 ID、原视频内时间段与观察证据；越界、遗漏要素、遗漏顺序或伪造主张 ID 均拒绝。报告复核原始评审响应和台账哈希，不能只编辑 `record.json` 改分。

传输时评审观看的是拼接视频。新版要求回复明确声明 `time_base=packet_seconds` 并直接引用拼接时间，代码依照该次请求中已保存的映射换算成原片时间；模型不负责做减法。原始响应、换算规则和标准化结果分别留存，汇总时重新计算换算结果并核对。缺少时间基准、引用其他片段或只引用编码补齐段仍然拒绝，不猜测、不改成片外时间。

第一轮开发试点的旧版结果为 4/6 有效评分，另 2 项因原片／拼接时间混用失败，原结果保留。修复后的单项线上验证为 1/1，独立报告，不与旧版成功项拼成“新版 6/6”。旧版还把 support 的具体人物、道具描述拿到 query 核验，出现误判。新版增加了“这种事实应记 unknown”的指令，但本次验证仍出现把 support 中的猫与 query 中的真人差异误报成幻觉的问题。因此无证据主张率尚未校准，不作效果结论；后续需要明确区分示例事实与可迁移断言，并分别用 support/query 核验。77.78 分仅为该次评审给出的诊断值，不是质量验收成绩。

无输出项的模板分为 0，未评项保留 pending／blocked／failed 等状态及分母。只有一组或少量开发模板时不宣称统计显著性。单参考与多参考的比较同时改变参考数量，不能把效果全归因于结构化表示。

声音理解、后续预测题、事件检索与实际成片评分仍需要各自的输入和标准；当前模板理解分不能替代这些任务，更不能当作工程出片率。纯 BGM 本身合法，不因无配音扣分。

## 预算与恢复

沿用原累计 100 元授权、历史费用及未决预占。新请求预算链接到此前最终预算，不重获一笔 100 元。Coding Plan 的原始 usage 单独保存，未拿到可靠结算依据前保留整笔操作预留，不套用旧北京接口的 token 价格，不把预留金额当发票费用。

模型支持和订阅用量说明见 [Z.ai 模型文档](https://docs.z.ai/guides/vlm/glm-5.3-flash) 与 [Coding Plan 用量说明](https://docs.z.ai/devpack/overview)。本轮真正是否可用以服务器接收到的业务响应为准。

代码与正式模板资产分开。重复提交复用已收到响应；修改输出目录不能重发未决评审。同一 query 媒体还有未决评审时，相关任务阻塞，其他模板可继续准备。平台登录失效需要恢复认证，不能把安装与 API 验证通过解释为以后永远不需要维护。
