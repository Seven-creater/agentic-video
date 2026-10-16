# 服务器运行

## 环境

后台作业和资源限流使用Linux进程与文件锁。建议Python 3.13、Node.js 22、FFmpeg/FFprobe、中文字体。安装Python的`.[library]`依赖，以及OpenCode、官方`@z_ai/mcp-server`和`undici`运行依赖。部署时固定自己已验证的版本，官方MCP包不作源码修改。

运行根目录与Git代码目录分开：

```bash
export OMNI_SERVER_HOME=/srv/agentic-video
export PATH=/srv/runtime/node/bin:/srv/agentic-video/.venv/bin:$PATH
```

`$OMNI_SERVER_HOME/server.json`示例：

```json
{
  "project_root": "/srv/agentic-video/code",
  "mcp_package_root": "/srv/runtime/glm-mcp",
  "opencode_executable": "/srv/runtime/opencode/node_modules/opencode-linux-x64/bin/opencode",
  "asr_model_dir": "/srv/models/faster-whisper-small"
}
```

ASR目录需要实际`config.json`和`model.bin`，不能只指向外层下载目录。热点入口不会自动下载缺失的模型。

`omni-server configure`通过隐藏输入配置GLM密钥。TTS及声画审核还需要`DASHSCOPE_API_KEY`，热点捕获需要`WELLBYTE_API_KEY`，可通过环境变量或私有`credentials.json`提供。私有文件权限设为`0600`，不要放到Git代码目录或命令参数中。

```bash
omni-server configure
omni-server doctor
```

`doctor`不请求模型。配置、接口额度和媒体都准备好后再启动实际任务。

## 默认路径

`$OMNI_SERVER_HOME/hot-video.json`示例：

```json
{
  "library": "/srv/media/movies",
  "visual_library": "/srv/agentic-video/shared/visual_library",
  "output_root": "/srv/agentic-video/shared/runs",
  "audio_music_bed": "/srv/media/music.wav"
}
```

音乐床可省略。已有任务需继承其记录时可配置`parent_task`，必须是已终结任务；新子任务会放在其`evaluations`下。普通新安装不需要私人历史清单或旧任务。

素材目录不可有未完成的`.part`。媒体、视觉库、模型、凭据、任务目录都不随Git发布。

## 并发与空间管理

```bash
omni-hot-video batch --count 10 --concurrency 2 --dry-run
omni-hot-video batch --count 10 --concurrency 2
```

`--count`控制总任务数；`--concurrency`控制期望同时编辑数，默认2，范围1–32。启动时的内存预算和运行中的实际余量都会进一步限制接入。

| 参数 | 默认值 | 用途 |
| --- | --- | --- |
| `--min-free-gb` | 2 | 始终为磁盘保留的GiB水位 |
| `--worker-disk-mb` | 512 | 每条编辑任务输出预留MiB |
| `--worker-memory-mb` | 1024 | 每条编辑进程的内存估算MiB |
| `--render-workers` | 1 | 同时运行FFmpeg的数量，范围1–10 |
| `--max-video-mb` | 256 | 每个新下载参考文件的字节上限MiB |

每次接入会扣除在途任务尚未使用的磁盘预算，并为三条参考下载及比较代理预留空间。下载同时核对声明长度、实际字节和剩余空间；已验证的缓存可以直接读取。FFmpeg通过进程锁限流，默认使用2线程，按剩余预算限制单文件写入。GLM、TTS和声画请求在记为“已提交”之前再检查空间，空间不足不会假记成网络未知。

默认每条1GiB是估算，不是操作系统内存配额；大分辨率、长视频或额外服务可能需要调高。输出预算与预检也不是文件系统硬配额。其他程序写盘、机器故障、供应商限流仍可能导致运行失败，因此保留磁盘水位并先看`--dry-run`结果。不要只升级磁盘便假定32路内存也足够。

容量不足且无作业在途时停止并记录原因，不无限占着后台等空间。不自动删除电影、视觉库、成片或请求证据；下载失败只清理本次拥有的临时文件。需要扩容或归档后，再创建新的明确任务，不重启旧请求。

## 查询、停止和结果

```bash
omni-hot-video status --output "$OUTPUT"
omni-hot-video logs --output "$OUTPUT" --lines 100
omni-hot-video stop --output "$OUTPUT"
```

单条任务在`hot_result.json`提供视频路径、SHA、参考来源和内容状态；原链结果在`edit/chain_result.json`。批量任务在`batch_progress.json`与`batch_result.json`列出子作业，视频位于各条`lines/编号/edit`下。

`stop`写带作业身份的停止标记，由原supervisor停止其进程组；批量停止同时请求已登记子作业停止。停止正在调用模型的任务可能留下未知结果，不能随后自动重发。输出目录仅允许启动一次，断开SSH不影响后台作业，重启机器不自动恢复。

批次捕获或子任务出现未知结果时停止新增付费工作。子任务未知还会写私有`shared/hot_batch_unknown.json`，阻止下一批自动绕开；需先核对原记录并明确决定后续动作，不能通过删除账本重置。

## 复用与兼容记录

素材观察、热点响应和音频按实际SHA与请求绑定复用。已有方案因本地技术错误停止且请求全部已知时，可使用明确的父任务与`--reuse-plan`继续；已有真实粗剪可使用`--rough-task`继续精剪。两者都不授权重放未知请求。

旧库如果带特殊`received_protocol_compatibility`标记，需要显式设置`OMNI_RECEIVED_NOTE_SEED_FILE`指向仓库外的原兼容JSON。程序仍核对原来源、ledger、请求和回复SHA，通用源码不内嵌某次实验记录。普通新库无需此项。

当前并发改动已完成离线边界验证，真实多路模型端到端运行留待服务器扩容后验证。
