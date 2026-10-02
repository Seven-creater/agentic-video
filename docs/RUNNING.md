# 安装、运行与安全续跑

## 1. 依赖与凭据

Python 3.10+；系统命令 `ffmpeg`、`ffprobe`、`curl`。Windows 使用 `curl.exe`。
运行时代码只有标准库依赖；测试依赖 pytest。

直接在仓库根目录执行 `python -m omni_story` 即可，不要求安装 Python 包。
需要在其他目录使用命令行入口时，可安装：

```powershell
python -m pip install -e .
omni-story --help
```

测试环境安装方式：

```powershell
python -m pip install -e ".[test]"
python -m pytest tests -q
```

安装可能下载 setuptools／pytest；这些不是模型权重。
`--help` 和测试不提交付费模型任务。实际 `--stage all` 会调用付费服务。

| 环境变量 | 用途 | 默认值 |
| --- | --- | --- |
| `DASHSCOPE_API_KEY` | Omni 与阿里云图片／编辑凭据 | 无，必须设置 |
| `MINIMAX_API_KEY` | H3 视频凭据 | 无，必须设置 |
| `DASHSCOPE_BASE_URL` | Omni 兼容接口地址；图片接口使用对应地域域名 | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| `MINIMAX_BASE_URL` | H3 API 地址 | `https://api.minimaxi.com` |

密钥需存在于启动进程的环境中。仓库不自动读取 `.env`，不要把密钥提交到 Git 或贴入日志。
H3 与阿里云账户的权限和余额相互独立。模型 ID 当前由后端代码固定，不提供临时自动换模型。

## 2. 三种入口

### 全链运行（默认）

```powershell
python -m omni_story --video "C:\path\to\reference.mp4" --output "runs\my_run" --unlimited-image-jobs
```

不指定 `--stage` 等同于 `--stage all`。输入必须是实际存在、小于 10 MB 的视频文件。
`--output` 只指定存储位置，不是创作输入。省略后使用参考 SHA 前 12 位构造默认目录。

`--unlimited-image-jobs` 是明确的图片支出授权，只移除图片数量上限。授权绑定参考片与剧本 SHA，
之后在同一目录续跑会复用；不修改旧请求、旧预算或旧失败，不解除单次视频提交规则。

### 只到剧本

```powershell
python -m omni_story --video "C:\path\to\reference.mp4" --output "runs\text_run" --stage screenplay
```

不生成图片和视频，但会调用付费 Omni 理解与写作。完成后用相同参考和相同输出目录运行
`--stage all` 可进入生产。没有人工故事、选题、接收草稿、选图或选片参数。

### 显式续剪现有素材

```powershell
python -m omni_story --video "C:\path\to\reference.mp4" --output "runs\existing_run" --stage reedit
```

需要原目录已完成剧本、生产规划及全部规划素材。
该命令记录新的有界续剪授权，重新联合理解参考并观看旧素材，不生成故事、图片或视频。
新增最多 12 次 Omni 请求，写入 `editing_continuations/joint_reference_v2/`。
重复执行复用同一目录和预算，不再次购买相同请求。
不能与 `--unlimited-image-jobs` 同时使用。

## 3. 预算与停止条件

| 项目 | 现有规则 |
| --- | --- |
| 理解与文本阶段 | 最多 32 个请求，含局部复看和格式修复 |
| 普通生产阶段 Omni | 最多 36 个请求，含图片审阅、真实素材观察、剪辑、成片审阅和格式修复 |
| 图片任务 | 默认 12；明确使用 `--unlimited-image-jobs` 时没有固定计数上限 |
| 定向改图 | 全生产阶段最多一次，不因取消图片数量上限变成无限循环 |
| H3 素材 | 最多 6 条、三路并发；每条仅提交一次 |
| 成片语义重剪 | 最多一次，不重新生成素材 |

网络失败、不明提交或终态失败不能自动重新 POST。已有 task_id 只查状态和取回输出。
确定的余额／认证错误需要处理账户；终态失败并不保证充值后原任务恢复。
代码里单独的余额重试能力不是默认全链自动重试，也不覆盖已获 task_id 后失败的任务。

## 4. 结果与退出码

文本结果在 `<run>/result.json`；整片结果在 `<run>/production/result.json`。
读取最终结果的 `final_video`、`final_video_sha256`、`media_probe`、`model_review` 和 `style_transfer`。
不要按 `render_0` 的名字推断它是被选中的最终版本。

| 状态 | 含义 |
| --- | --- |
| `model_checked_screenplay_candidate` | 文本规划经模型核查，不证明真实视频已成功 |
| `screenplay_needs_review` | 有实际可用剧本但审阅仍有问题，可按规则进入生产 |
| `model_checked_final_video` | 有模型核查的可播放成片候选，不是人工真值 |
| `video_candidate_with_limitations` | 有可播放成片，但质量、风格或核查仍有缺口 |
| `blocked` | 执行未完成；查看 `run_failure.json`、执行状态及原始响应 |

上述候选状态退出码为 0；返回 `blocked` 时为 2。前置文件错误也可能产生异常退出，保留 stderr。
模型决定关闭音乐时，成片可能没有音轨；这是模型决定和已知限制，不由程序偷偷加入音乐。

## 5. 续跑不是重跑

使用相同视频和输出目录，可复用已完成文本、资产、已有视频任务和成片缓存，均校验来源 SHA。
生产侧独立请求使用请求身份缓存；不确定的提交不会自动重发。
文本阶段尚未形成完整 `result.json` 时，当前代码没有逐调用自动续写能力；会拒绝覆盖已有目录。
不要据此删除目录或创建新目录来重置付费请求。

代码、提示词、输入或模型配置发生变化时，旧冻结来源可能拒绝继续。
已实现的执行兼容和明确的图片预算授权有独立记录；没有通用的“接受任意版本变化”机制。
不要编辑旧 `parsed.json`、`submission.json` 或 manifest 来伪造通过。

终态生成失败、余额不足、认证错误和没有任务 ID 的未知回复必须留档后处理，不能承诺一条
续跑命令就能修好。原女生参考片当前正是余额不足，尚不具备完整素材剪辑条件。

所有 `runs/`、缓存、构建产物和环境密钥均不属于需要分发的源码。
