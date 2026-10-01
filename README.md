# Omni 自主参考视频到成片

唯一语义输入是参考视频。环境中的 API 凭据是基础设施，不是剧情指导。
不读取旧剧本、旧资产、人工评审答案或前一个项目的 Python 模块。

## 本版本范围

原声原字全片理解 → 三个主题及自主选题 → 整体故事与固定资产描述 →
逐段详细剧本／独立审核／有限修订 → 画面盲读 → 主旨对照 → 剧本包 →
固定资产主图／真实图片审核／有限改图 → 素材起始帧 → MiniMax H3 素材 →
Omni 观看真实素材 → 自主剪辑表 → FFmpeg 成片 → Omni 观看实际成片／最多一次重剪。

默认运行整条链；`--stage screenplay` 可以只停在文本规划。
没有人工选题、选图、选片或“接受草稿”入口。不保证随机再次产生攀岩故事。
复跑同一目录会校验剧本清单和源视频 SHA，再安全复用已完成阶段，不重新付费提交任务。

## 运行

要求 Python 3.10+、FFmpeg、FFprobe 和 curl。运行时代码只使用 Python 标准库。
凭据从 `DASHSCOPE_API_KEY` 读取；`DASHSCOPE_BASE_URL` 默认阿里云兼容接口。
默认模型沿用 `qwen3.8-omni-flash`，原声视频请求 2 fps；服务端实际采样未报告。
当前沿用已有的内嵌视频小于 10 MB 限制；不偷偷降清晰度或重做原视频。
实际素材与成片的模型观看副本由程序生成，原文件保持不变；请求 4 fps，服务端实际采样未报告。

用户已明确要求恢复原阿里云图片 API，不再依赖 Codex 图片服务的配置。
人物／道具主图使用 `qwen-image-3.0-pro`，场景主图使用 `wan2.7-image-pro`；
1–3 张参考图改图使用 `qwen-image-edit-plus-2025-12-15`，4–9 张融合使用 `wan2.7-image-pro`。
这是事先定义的输入能力分工，不是错误后悄悄换模型抽卡。每个请求只生成一张。
沿用 `DASHSCOPE_API_KEY` 和 `DASHSCOPE_BASE_URL` 对应地域，实际请求使用 DashScope 协议。
[千问 3.0](https://help.aliyun.com/zh/model-studio/qwen-image-generation-and-editing-api-reference)、
[千问改图](https://help.aliyun.com/zh/model-studio/qwen-image-edit-api)、
[万相 2.7](https://help.aliyun.com/zh/model-studio/wan-image-generation-and-editing-api-reference)。
不打印或提交 API Key；参考图片只在请求内存中编码，留档记录其 SHA。
图像文件保存在运行目录，另将预览镜像放到 `$CODEX_HOME/output/imagegen/`。
上次 helper 失败保持原样，阿里云任务写 `image_jobs_aliyun/`，旧尝试仍计入同一预算。
已返回的生产计划原字节复用，不重置创作或审核预算；后台实际 H3 任务只查询取回。

视频使用 `MINIMAX_API_KEY`，`MINIMAX_BASE_URL` 默认 `https://api.minimaxi.com`，
模型 `MiniMax-H3`、768P、4–15 秒完整动作素材。接口遵循
[MiniMax H3 V2 创建文档](https://platform.minimax.cn/docs/api-reference/video-generation-v2-create)。
使用起始帧的输入契约，不混用 first_frame 与 reference_image。

```powershell
cd C:\Users\29785\Desktop\omni-autonomous-screenplay
python -m omni_story --video C:\path\to\reference.mp4
```

可以设置 `--output` 保存位置，但它不是创作输入。默认输出为 `runs/video_<SHA前12位>`。
没有 `--parent`、故事种子、人工选题、放行或人工续写参数。
运行前不需要打开原项目，也不会连接服务器。

续跑只需相同命令与相同 `--output`。已完成的文本部分位于输出根目录，
生产部分写入独立 `production/` 子目录；旧 manifest、模型输出与失败不改写。
只允许续查已知任务／复用缓存。提交响应丢失且没有 task_id 时保留 `blocked`，
不能为追求“永不报错”再次扣费。不要删除 submission.json 或另开目录绕过预算。

请求体通过临时 JSON 文件上传，凭据仍只进入 curl 的标准输入；临时文件不含密钥，
请求结束即清理。这样多图改图／图审不会触发 curl 配置单行的大小上限。
只针对已明确的 JSON 参数 400 拒绝，或经 localhost 离线重现确认的旧配置解析失败，
留档后执行一次格式／上传纠正；响应丢失、网络失败和视频生成失败仍不能自动重新提交。
修图审核使用同一组固定资产主图，不把旧错图当作身份基准或连续视频帧。

## 后半段预算与时间

默认最多 36 次生产侧 Omni 请求（含格式修复）、12 次图像任务（含至多一次定向修图）、
6 个视频任务。视频独立素材最多三路并发，每条仅提交一次。最多一次实际成片重剪。
不是先生成几分钟完整电影，也不强迫所有剧本动作在参考片几秒内完成。
Omni 先设计覆盖关键事件的完整素材，实际视频生成后才指定素材入出点和成片位置。
素材覆盖不足只记录限制，不用字幕或旁白伪造结果，不自动补拍。

剪辑沿用参考片的粗组织和时长偏好，不要求固定镜头数／切点。只执行 Omni 提交的
真实素材区间、排列、速度、硬切／短淡变。音轨仅使用 Omni 实际听过并定位的
非参考叙事配音音乐区间，允许原速裁剪／循环／淡入淡出，不拉伸 BGM。
没有能确认的音乐区间就不复用原音轨。仅使用自有或获授权的参考音乐。

## 自主与门槛

通用创作要求提前冻结，不包含某条参考片的答案。作品主旨应相似，题材可以变化；
故事结构为软偏好，不限制固定三段或要求反转。身体差异允许、不要求、不排除。
新片按纯画面可读来规划；不依赖字幕／对白解释关键剧情。

每个写作阶段最多原稿及一次模型自主修订；每次无效 JSON/schema 最多一次格式修复。
全 run 最多 32 个实际请求，格式修复也计入。网络/HTTP 失败不自动付费重试。
重复输入＋同一冻结代码/提示词/配置拒绝另起目录重置预算。不能保证 API 永不出错；
失败会正常保存 `blocked` 和完整记录，绝不伪造通过或请求人工剧情答案。

审核只阻断核心矛盾、关键证据缺失、主旨不符。未知的生成风险或无影响的细节不阻断。
最终盲读和主旨对照失败时保留完整候选并结束，不临时追加补救调用。
这意味着自动化并不等于永远成功。文本通过和观看实际生成视频通过是不同阶段。

## 留档

`input_lineage.json` / `prompts.json` / `calls/` 保存代码、模型、输入与提示词 SHA，
实际请求、完整 API 响应、模型文本、格式检查和费用 token。
`outline.json` / `asset_bible.json` / `U*.json` / `screenplay.json` 是模型产物。
`blind_reading.json` 不读作者主旨、参考答案或观众效果字段。
`result.json` 区分候选通过、仍需复核与执行失败，`manifest.json` 逐文件记录 SHA。
模型自审不是人工真值，所有产物 `production_release_allowed=false`。

`production/production_plan.json` / `asset_inventory.json` / `start_frame_inventory.json`
保存 Omni 的生产决定、图审与文件 SHA；`image_jobs/` 和 `video_jobs/` 保存不可重发的
提交身份、原始响应、任务 ID、轮询和实际探测结果。
`source_observations.json` 只记录真实媒体；`edit_plan_*.json` 是模型选片决定。
`render_*/final.mp4` 是可播放成片，`final_review_*.json` 是 Omni 真实成片审核。
成功标为 `model_checked_final_video`，有缺口标为 `video_candidate_with_limitations`，
执行故障标为 `blocked`，不把任何一种情况冒充另一种。

## 复用与改动

`SOURCE_PROVENANCE.json` 记录原仓库 commit 和抽取文件 SHA。
复用完整参考理解提示词、untimed 剧本 schema 和逐段写作的核心流程；
不是声称原先多个人工入口只复制一下就变成了全自动。
新增的是自动衔接、自主选题、提前统一的纯画面条件和有界反馈。
旧仓库、运行记录和失败保持原样，新仓库没有复制参考片答案。

## 本地测试

```powershell
python -m pytest tests -q
```

测试使用合成响应验证自动衔接、前文状态传递、预算、失败、不泄露凭据及独立导入，
并运行真实 FFmpeg 验证执行与源区间边界、原速音乐策略及安全缓存；
测试响应不会进入实际运行请求。
