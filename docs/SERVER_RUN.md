# 独立服务器运行

服务器入口 `omni-server` 与本地 `omni-library` 分开。OpenCode 是智谱官方支持的 Coding Plan 客户端，使用国内 Coding 端点；其 MCP 工具将一个不可修改的作业转交官方视觉服务器。图像、视频与提示由 Python 管线准备，GLM 决定检索、段落、切点与编辑表，FFmpeg 执行。

官方配置依据：[智谱 OpenCode 文档](https://docs.bigmodel.cn/cn/coding-plan/tool/opencode)、[OpenCode 非交互 CLI](https://opencode.ai/docs/cli/)、[MCP 配置](https://opencode.ai/docs/mcp-servers/)。本项目没有新增普通付费模型 API provider。

## 环境与配置

需要 Linux、Python 3.13、Node 22、FFmpeg/FFprobe、中文字体，Python 安装 `.[library,test]`。固定外部依赖为 OpenCode `1.18.35`、`@z_ai/mcp-server@0.1.5` 与 `undici@7.16.0`。无 GPU 也可执行；GLM 推理在云端，视频处理在服务器 CPU 上。

`OMNI_SERVER_HOME` 指向服务器私有配置根目录，例 `/home/ubuntu/apps/agentic-video`。该目录的 `server.json` 保存以下非密钥配置：

```json
{
  "project_root": "/home/ubuntu/apps/agentic-video/current",
  "mcp_package_root": "/home/ubuntu/apps/agentic-video/runtime/glm-mcp",
  "opencode_executable": "/home/ubuntu/apps/agentic-video/runtime/opencode/node_modules/opencode-linux-x64/bin/opencode"
}
```

安装 `opencode-ai` 时若关闭 npm postinstall，必须使用已核验的实际平台二进制路径，不能假设 wrapper 名称或软链接已经生成。部署本机上的 `env.sh` 把虚拟环境、Node 和 `OMNI_SERVER_HOME` 加入运行环境。

配置一次密钥：

```bash
source /home/ubuntu/apps/agentic-video/env.sh
omni-server configure
omni-server doctor
```

密钥输入不回显，保存到配置根目录的 `credentials.json`，权限 `0600`，不进入代码目录。worker 将其注入 OpenCode/官方 MCP 的环境，不放入命令参数。`doctor` 只检查配置、可执行文件、凭据权限与历史清单，不请求模型。

## 启动与控制

先将参考和素材放到 `shared/data`；不要复制本地虚拟环境或直接改写 Windows 历史运行的路径。上传未完成时保留 `.part`，校验大小和 SHA 后原子改名。不要对存在 `.part` 的素材库启动编辑。

```bash
omni-server start \
  --reference /home/ubuntu/apps/agentic-video/shared/data/ref/video.mp4 \
  --library /home/ubuntu/apps/agentic-video/shared/data/videos \
  --output /home/ubuntu/apps/agentic-video/shared/runs/my_edit
```

启动后可以断开 SSH 或关闭 Codex。supervisor 与 worker 在服务器后台运行；服务器关机或重启不会自动续跑。默认不运行 ASR，`--asr` 可启用 CPU small/int8；首次需要下载权重，ASR 仍是不确定的语言证据。

```bash
omni-server status --output /home/ubuntu/apps/agentic-video/shared/runs/my_edit
omni-server logs --output /home/ubuntu/apps/agentic-video/shared/runs/my_edit --lines 100
omni-server stop --output /home/ubuntu/apps/agentic-video/shared/runs/my_edit
```

管理记录在输出目录 `.omni-server/job.json`，合并运行日志在 `.omni-server/job.log`。剪辑结果位置由 `result.json` 的 `final_video` 给出；失败原因在 `failure.json` 或 job log。可播放候选、模型审核通过与独立质量验收分别描述，不能因程序退出码为零就宣称剪辑质量通过。

`stop` 使用任务 token 写停止标记，由原 supervisor 清理自己持有的进程组；控制命令不向保存的 PID 直接发信号。后台状态丢失记为 `unknown`，不会自动重启。输出目录只可提交一次；已有任务用 status/logs 查看，不删除 ledger 后重新启动。

## 阶段、失败与历史

没有程序设置的 80 次视觉调用总上限。流程仍有明确边界：最多 16 个精看窗口、两轮候选、32 个最终片段；每阶段一个原请求和至多一次格式修复。OpenCode 每个作业最多两步工具执行，绑定的官方视觉 POST 只允许一次；agent 的文字总结不进入模型观察结果。

新任务锁定 `vision_generation`：官方输出上限 32,768 tokens、模型 HTTP 超时 1,200 秒、MCP 工具等待 1,260 秒，OpenCode 总等待 1,320 秒。配置只使用官方支持的原生环境变量；不改官方包或请求的思考开关。历史无 profile 任务保持原 16,384 / 600 秒配置。已知 HTTP 失败、无回复的未知请求和格式修复失败都会停下；不通过重编码、换文件名、换提示或新输出目录重放未知请求。

### 已知输出容量失败的单次恢复

2026-10-09 的同一服务器任务提供一个专用工程恢复模块。它只接受原 `plan_0` 及唯一修复都实际 HTTP200、`finish_reason=length`、空内容且生成 16,384 tokens 的情况，并要求原后台任务 exit1、进程组退出、全部调用 received、零渲染。必须先有绑定该任务的用户工程修复授权；普通失败不能用此入口重启。

恢复保留原 input lock、全部历史调用、原 `failure.json` 与 `.omni-server` 控制文件，追加不可变基线、授权和 `plan_0_capacity_v2` 别名；别名仍只有一个原请求与一次格式修复。不会增加候选轮次或精看上限。新控制目录固定为 `.omni-server/recoveries/output_capacity_v1`，恢复失败另存 `failure_capacity_recovery_v1.json`。

```bash
python -m omni_story.library.server_capacity_recovery \
  --home /home/ubuntu/apps/agentic-video \
  --output /home/ubuntu/apps/agentic-video/shared/runs/server_edit_test_20261009 \
  status
```

同一模块的 `logs`、`stop` 控制恢复进程；原 `omni-server status` 仍显示原失败任务。`start` 需要登记授权文件及其 bytes SHA，只能启动一次，不能通过删除控制目录重新执行。

`library_state.json` 和 `mcp_http.jsonl` 记录视觉作业及原始 HTTP。每个 call 的 `agent/events.jsonl` 保存 OpenCode 聊天步骤、tokens 与平台报告的 cost；两者是不同用量范围，视觉作业数量不等于所有 GLM 网络请求数量。

从本地迁移时，`server.json` 可添加 `history_file` 与 `history_sha256`。迁移清单保留原 calls 摘要、未知媒体/时间范围，以及已接收参考子对象的原请求与回复。启动时核验 SHA 和原模型内容；Windows 路径是历史证据，不尝试在 Linux 解析或改写。

本次迁移基线为 274 次视觉调用，未知 004/131/166/265 保持原样。固定参考只复用 060 的已接收 reference/editing_reference 子对象，保留“全协议未通过、覆盖报告不连续、模型估计未独立验证”的限制；不会重新发完整参考。规划载体改用已观察的库内联系表，避免借规划再次发送未知参考输入。旧历史和新服务器追加作业分别保存，累计明确包含原基线。

部署验证包括：真实图片与视频的独立 GLM 调用、关闭发起 SSH 后继续完成、Linux supervisor 停止和进程组清理、现有管线回归。服务器真实电影任务的进度和局限见 [测试记录](SERVER_EDIT_TEST_20261009.md)。云端视频内部采样未知，不能将连接测试视为剪辑质量结果。
