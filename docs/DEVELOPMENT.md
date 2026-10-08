# 本地开发

Python 包源码在 `src/omni_story/`。从仓库根目录安装后运行 CLI 或 `python -m omni_story...`；包名和三个 CLI 名称没有改变。当前验证环境为 Windows 与 Python 3.13，项目声明支持 Python 3.10+；抖音发现的可选依赖需要 Python 3.11+。

## 安装

```powershell
py -3.13 -m venv .venv-library
.\.venv-library\Scripts\python.exe -m pip install -e ".[library,test]"
```

电影素材库开发保留根目录 `skills/` 与 `craft_knowledge/`。运行时使用的知识文本另有字节相同的包内副本，纳入 wheel；修改知识时需同步副本，布局测试会检查一致性。媒体执行需要 PATH 中的 `ffmpeg` 和 `ffprobe`。官方视觉 MCP 连接需要 Node.js/npm 和有效的 Codex 会话，配置步骤见 [本地运行说明](REFERENCE_LIBRARY_LOCAL_RUN.md)。

## 无模型 smoke 检查

以下命令只检查已安装包的导入及 CLI 参数帮助，不启动模型、浏览器、素材生成或剪辑任务：

```powershell
.\.venv-library\Scripts\python.exe -c "import omni_story, omni_story.library, omni_story.discovery; print(omni_story.__file__)"
.\.venv-library\Scripts\omni-library.exe --help
.\.venv-library\Scripts\omni-story.exe --help
.\.venv-library\Scripts\omni-discover.exe --help
```

首条命令应显示仓库 `src/omni_story/__init__.py` 的位置。三个帮助命令也可写为 `python -m omni_story.library --help`、`python -m omni_story --help` 和 `python -m omni_story.discovery --help`。

`omni-discover smoke` 会启动付费 Qwen 浏览器试验，属于另一路线的真实执行命令。上述本地检查仅使用 `--help`。

也可用 `python -I scripts/check_installation.py` 一次核查安装资源、源码指纹、三个模块帮助及三个 CLI 帮助；安装 wheel 后加 `--expect-wheel`，确认没有使用 editable 安装。

## 测试

```powershell
.\.venv-library\Scripts\python.exe -m pytest
```

测试覆盖协议约束、恢复、模拟队列和媒体执行；实际 FFmpeg 检查需要相应工具。测试通过不能替代真实模型的剪辑质量审阅。

## 构建 wheel

```powershell
.\.venv-library\Scripts\python.exe -m pip install build
.\.venv-library\Scripts\python.exe -m build --wheel
```

wheel 输出在 `dist/`。在新的虚拟环境中安装生成的 wheel，再执行三个 CLI 的 `--help`，可检查安装产物的入口。源码仓库中的 `data/`、`runs/` 是本地媒体和历史运行记录，不随代码交付。

## 持续集成

[GitHub Actions 配置](../.github/workflows/ci.yml) 在 Windows / Linux 上检查布局、离线协议、wheel 构建与安装资源。持续集成仅运行指定的快速检查，不是完整媒体测试或模型质量评测；不会连接 GLM、Qwen 或启动付费生成。

开发时保留既有输入锁、预算、原始响应与未知请求记录。启动或恢复真实任务须遵循 [任务规范](REFERENCE_LIBRARY_SPEC.md)、对应实现文档及该任务的已登记授权。
