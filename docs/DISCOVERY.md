# 本地 Qwen 抖音发现与 Omni 剧本入口

`omni-discover` 将本地浏览器发现接到现有剧本链：专用 Chrome 登录抖音 → Qwen 自主搜索、浏览与初筛 →
下载完整候选视频 → Omni 完整审看与比较 → 选择参考片 → 自主生成创新剧本。
当前范围只到剧本，调用此入口不会生成资产图片、MiniMax 视频或成片。

参考价值由清楚的故事关系与行动、剧本改编和创新空间、拍摄与资产生成难度，以及剪辑卡点的学习价值决定。
优先少量人物和场景、容易呈现的动作与道具，并用实际声画证据说明剪切与音乐节拍、动作节奏的关系。
不按热度、点赞量或发布日期评分，不限定动漫、真人或最近一周；首轮至少覆盖两种不同搜索方向，
每条搜索词都包含“剪辑”，同时表达内容或叙事意图。卡点服务内容，纯炫技合集不因转场数量多而优先。
Qwen 页面预览只用于初筛，完整理解和最终选择属于 Omni。后续创作只接收已验证的本地视频，
不会把 Qwen 的页面故事猜测作为创作答案。语义批评、未解决限制和失败候选都保留。

## 安装与凭据

推荐使用当前项目的 Python 3.13；发现入口最低要求 Python 3.11，原生成链仍支持 Python 3.10。
需要本地 Chrome，以及 PATH 中的 `ffmpeg`、`ffprobe`、`curl.exe`。不下载本地模型权重、不需要 GPU 或服务器。

在仓库根目录的 PowerShell 中执行：

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[discovery,test]"
.\.venv\Scripts\omni-discover.exe --help
```

`discovery` 可选依赖固定 Browser Use `0.13.10`，另安装 requests；`test` 安装 pytest。
如果默认镜像不可用，可给安装命令追加 `--index-url https://pypi.org/simple`。
在已创建的项目虚拟环境中无需重新创建 `.venv`。也可用
`.\.venv\Scripts\python.exe -m omni_story.discovery` 代替 `omni-discover.exe`。

浏览默认复用 `DASHSCOPE_API_KEY` 和 `DASHSCOPE_BASE_URL`；Omni 也使用现有百炼凭据。
密钥应在启动进程的环境变量中提供，仓库不会自动读取 `.env`。
此阶段不需要 MiniMax 凭据，登录本身也不需要模型密钥。
Qwen 浏览请求使用 curl，并显式指定 `--noproxy '*'` 直连；不会继承 `HTTP_PROXY`、`HTTPS_PROXY` 或
`ALL_PROXY`，也不会修改这些变量或系统代理。此修复仅作用于 Qwen 浏览适配器，原生成链及 Omni 的 API 路由保持原设置。

| 环境变量 | 用途与默认值 |
| --- | --- |
| `DASHSCOPE_API_KEY` | 现有百炼密钥；Omni 必需，浏览密钥的默认来源 |
| `DASHSCOPE_BASE_URL` | 默认 `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| `QWEN_BROWSER_API_KEY` | 可单独覆盖浏览模型密钥 |
| `QWEN_BROWSER_BASE_URL` | 可单独覆盖浏览接口；要求不含凭据的 HTTPS 地址 |
| `QWEN_BROWSER_MODEL` | 默认 `qwen3.8-27b` |
| `QWEN_BROWSER_EXECUTABLE` | 可指定本地 Chrome 可执行文件；自动查找 Chrome，未找到时尝试 Edge |
| `QWEN_BROWSER_PROFILE_DIR` | 专用持久化配置目录，Windows 默认 `%LOCALAPPDATA%/OmniStory/DouyinProfile` |
| `QWEN_BROWSER_DIRECT` | 默认 `1`：专用 Chrome 与视频 CDN 下载直连；`0`、`false` 或 `no` 使用系统／环境代理 |

Windows 默认配置目录按用户的 `AppData/Local` 构造，以保持多次启动时路径稳定。
登录配置、密钥和完整运行记录都不应提交到 Git；项目已经忽略 `.venv/`、`.env*` 和 `runs/`。
若将自定义配置或输出放到仓库其他位置，需自行将该目录排除。

## 登录、代理与运行

先创建并登录专用环境：

```powershell
.\.venv\Scripts\omni-discover.exe login
```

程序打开可见的专用 Chrome，请在该窗口扫码并处理验证码。检测到登录后关闭专用浏览器，保留配置供下次复用。
该环境与日常 Chrome 配置分开。不要同时启动两个使用同一专用配置的进程。
运行中登录失效或出现验证码时，程序暂停等待人工处理，完成后继续原会话；人工等待不计入主动运行时长。

若日常代理的海外出口导致抖音无法访问，默认直连只作用于此专用 Chrome 与视频下载，其他应用的代理设置不变。
需要让专用环境使用系统代理时，在启动前设置：

```powershell
$env:QWEN_BROWSER_DIRECT = "0"
```

若代理启用了 TUN 等系统级流量接管，Chrome 的直连参数可能仍受其路由控制；需在代理应用中为抖音和实际视频 CDN 配置直连规则。
此变量不改变百炼模型 API 的提供商或地址。
`QWEN_BROWSER_DIRECT` 只控制专用 Chrome 和视频 CDN 下载；Qwen 浏览 API 始终按上述 curl 直连规则发送。

完成登录后，默认运行到剧本：

```powershell
.\.venv\Scripts\omni-discover.exe run --output "runs\douyin_reference"
```

只验证下载、审看与选片时使用：

```powershell
.\.venv\Scripts\omni-discover.exe run --output "runs\douyin_reference" --stage reference
```

这两条 `run` 命令都会调用付费 Qwen 和 Omni。使用同一输出目录恢复同一任务；
已完成参考阶段后可在原目录使用默认 `--stage screenplay`，复用原下载、审看和选择。
已有生成链仍用 `omni-story`，详细说明见 [原生成链运行说明](RUNNING.md)。

## 有界浏览与视频校验

| 限制 | 首版上限 |
| --- | --- |
| 去重候选 | 20 个作品 ID，失败候选也保留并占名额 |
| 完整 Omni 审看 | 3 条成功验证的视频 |
| 最终参考 | 1 条，由 Omni 自主选择 |
| 浏览动作 | 80 步，每步最多一个已通过本地校验的动作 |
| Qwen 浏览请求 | 100 次，协议修复包含在内 |
| 主动发现时间 | 30 分钟；不包括人工登录／验证码等待与剧本阶段 |
| 发现阶段 Omni | 8 次：3 条审看与 1 次比较，各最多一次协议修复 |
| 剧本阶段 | 原有 32 次请求预算，单独记录 |

浏览模型使用页面截图、JSON Object、动作示例和本地类型校验。关闭思考模式和传输自动重试；
模型动作格式错误最多修复一次，修复仍计费并计入预算。浏览工具限制在搜索、查看与播放相关操作。
接近时间截止或 Qwen 请求上限时提前停止浏览，为已审看候选的 Omni 比较留出预算。
若已准入的下载或审看耗时超过剩余窗口，最终比较仍可能被时间预算阻断；保留已确认的候选和审看，记录 `blocked`。

下载只能来自当前独立作品页面的播放器，并同时匹配作品 ID 与真实媒体网络证据。
无法可靠绑定当前作品、拿不到完整直链或下载失败时记录原因并寻找其他候选，不使用录屏代替。
当前技术入口接受正时长且少于 10 分钟的视频；这不是按时长评价故事价值。
旧项目仅迁入下载器、数据结构、下载台账和对应的 10 个下载测试，来源见
[下载复用记录](../DOWNLOAD_PROVENANCE.json)。没有迁入热榜、排序、创作或服务器同步功能。

下载器保留流式写入、`.part` 临时文件、原子改名与失败历史。
随后用 FFprobe 检查媒体流、时长，并以 FFmpeg 实际解码完整文件；媒体错误、播放器时长不匹配等情况会跳过。
保存原件及 SHA。原件达到 10 MB 时，为现有 Omni 接口生成目标约 9 MB 的全时长 H.264/AAC 分析副本，
长边不超过 720，保留原时间轴和已有音轨；再验证时长、音轨数量、文件大小和 SHA。
原件不覆盖，分析副本的实际路径与来源关系写入 `media_lineage.json`。

Omni 审看包含完整可用视频和原有音轨，以时间戳支持故事迁移、创新空间、拍摄与资产可行性，
以及镜头切换、信息揭示、音乐和动作卡点的学习判断。
有可用参考时可交付实际候选中的最佳选择及其局限；不能把语义拒绝改成通过，也不能虚构缺失视频。

## 产物、退出码与恢复边界

以下路径均位于指定的输出目录：

| 文件／目录 | 内容 |
| --- | --- |
| `discovery_state.json` | 会话 ID、冻结配置、预算累计、搜索方向、候选、审看与付费请求状态 |
| `calls/<调用ID>/` | 发出前保存的请求、实际响应、用量、协议验证或传输失败记录 |
| `screenshots/` | Qwen 请求对应的浏览截图；请求留档用相对产物路径替代 Base64 |
| `browser_history_<累计步数>.json` | 各次真实浏览的 Browser Use 历史，按累计步数保留；本地 smoke 使用 `browser_history.json` |
| `videos/<作品ID>/video.mp4` | 下载原件 |
| `videos/<作品ID>/analysis.mp4` | 需要压缩时生成的完整分析副本 |
| `videos/<作品ID>/media_lineage.json` | 原件／分析副本 SHA、媒体信息和实际路径 |
| `videos/manifest.json` | 下载结果与历史失败 |
| `selected_reference.json` | 来源页面、参考视频、审看、选择理由与保留的限制 |
| `screenplay/` | 现有剧本链的请求、响应、剧本、审核与结果 |
| `source_snapshot.json` | 首次运行时的发现代码 SHA 快照 |
| `source_snapshots/<快照SHA>.json` | 运行中使用过的各版发现代码 SHA 快照，旧版不覆盖 |
| `result.json` | 当前结果、候选与审看计数、发现调用用量及 `image_video_generation: false` |
| `failure.json`、`failures.jsonl` | 当前失败及追加的失败历史 |

`result.json` 的 `video` 是实际选中的分析输入；`selected_reference.json` 同时保留原件路径。
完成剧本时查看 `screenplay/result.json` 和 `screenplay/screenplay.json`。
`reference_selected`、`model_checked_screenplay_candidate`、`screenplay_needs_review` 表示有对应实际结果，退出码 0；
`blocked`、`interrupted` 或 `no_candidate` 表示未完成该任务，退出码 2。
模型核查与程序校验不能证明人工观众一定认可故事或剪辑质量。

同一输出目录有操作系统锁，防止并行进程丢失预算或重复请求。
恢复会核对配置、预算与媒体 SHA，复用已验证的下载和已完成的审看、选择与剧本；
已确认的 Omni 响应也可用于恢复中断在保存结果前的阶段。
已有完整选择结果时直接交付本地产物，无需重新打开抖音或请求浏览模型。
未完成剧本遵守原文本阶段边界，不自动重写或覆盖已有目录。

付费 POST 发出前先写入 `submitted` 状态；收不到确定响应时，结果保持不明，后续启动拒绝重放。
传输失败保存脱敏、截断的错误消息及可提取的 curl 退出码；错误记录不会自动触发重试。
确定的 HTTP 拒绝、认证／余额故障和协议修复耗尽也需要查看原记录处理，不能保证一条续跑命令恢复。
不要删除记录、修改旧状态或新建输出目录来重置同一任务的预算。

如果本地 smoke 留有不明 Qwen 请求，而用户明确要求改做真实抖音下载测试，可以在**原 smoke 目录**
显式使用 `--continue-from-smoke <不明调用ID>`。参数必须列出该目录所有不明的 smoke Qwen 调用 ID；
只允许一次 smoke → run 转换，记录在 `discovery_state.json` 的 `continuations` 中。旧请求、旧 `submitted`
状态和全部累计预算保持原样，程序拒绝再次发送相同请求。转换仅允许新增抖音页面操作，不能用来重跑 smoke、
放行后续新出现的不明请求或提高预算。转换后的普通恢复使用原目录的 `run` 命令，不重复传该参数。

## 检查命令与真实验证状态

免费本地测试：

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
```

测试包含合成响应、真实 FFmpeg 媒体校验，以及实际本地 Chrome 的页面搜索、点击、滚动和截图；
不会调用付费模型。未安装可选浏览依赖时，相应浏览检查会跳过。

以下是**付费** Qwen 动作测试，使用独立本地测试页面和测试配置，不访问抖音，也不调用 Omni：

```powershell
.\.venv\Scripts\omni-discover.exe smoke --output "runs\qwen_browser_smoke"
```

它要求 Qwen 搜索、打开测试内容、滚动到页底并识别截图中的颜色和形状，最多 12 个浏览步骤；
协议修复也消耗持久化 Qwen 请求预算。测试通过才会得到 `browser_smoke_passed` 和 `fixture_checks.json`。
重复执行必须使用原输出目录；有不明付费请求时不要另开目录强行再测。

2026-10-03 完整回归 **136 个测试通过**；wheel 打包和新增命令行入口也已验证。
专用环境已修复 Browser Use `0.13.10` 默认将所给 Chrome 配置克隆到临时目录的问题：
创建浏览器后指定固定的专用配置目录，并将已登录记录恢复到 `DouyinProfile`。
已实际重启确认抖音页面正常、登录复用成功；检查结果为 `authenticated: true`、`gated: false`、`direct: true`。
实际付费 Qwen 测试的 SDK 连接请求失败，未获得服务回复，记录中仍有 `qwen_002_browser` 的
`submitted` 状态；该不明请求没有重放，保留在 `runs/douyin_fixture_20261003/`。
适配器随后切换到项目已有 curl 传输，并为 Qwen 显式禁用代理。经用户明确要求，在原目录记录续跑授权，
保留旧不明请求与预算后，已完成实际 Qwen 自主搜索、浏览和选取下载候选。没有重复发送旧 smoke 请求。
已从当前作品的实际浏览器媒体请求下载作品 `7689719658726837530`：原件 **8,544,988 字节**，
完整时长 **53.801995 秒**，保留音轨，视频分辨率 2160×3840、25 fps。原件低于 10 MB，无需分析副本。
FFprobe 检查和 FFmpeg 全文件解码通过；原件 SHA 为
`f5be6124c00ed1d7bcb7de31866ac9b542310ee18ca36965211ded9c2c6ae2e5`。
下载器原来误拒绝有效的 28 字节 `ftyp` 盒；修复已单独追加到下载来源记录，旧迁入历史与下载失败记录不覆盖。
Omni 已通过两次得到确定响应的请求完成完整审看和最终选择，结果为 `reference_selected`。
实际审看候选为 1 条，未虚构另外两条比较对象；语境理解、光线／环境音和结尾表意等局限如实保留在选择记录中。
截至该结果，会话累计 79 步、78 次 Qwen 请求、2 次 Omni 请求；同目录 `run --stage reference` 恢复已验证，
直接返回原参考片，计数保持不变。真实“搜索 → 下载 → Omni 审看与选择”验收已完成；剧本阶段未运行。
