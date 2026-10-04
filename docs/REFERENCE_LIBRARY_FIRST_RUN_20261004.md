# 本地参考驱动电影素材库首轮实测（2026-10-04）

首轮完成真实本地闭环，最终交付为**有局限的可播放候选**，不是全部质量目标通过。GLM 自主决定搜索窗口、人物对应、段落、入出点、顺序、音轨和两版选择；没有人工剧情或人工素材选择。本轮有工程修复与审核定义澄清，**不能记为冻结版本的零人工干预验收**。

## 固定输入与运行边界

- 参考：`data/ref/video.mp4`，21.933333 秒，SHA `2f95e24edd2cf4b79cc1f40f7e202174c53a6abcf084e728bb49e3ca92938a17`。
- 素材：`data/videos/` 的三部《功夫熊猫》，合计 277.4704 分钟。三部均使用全局音轨索引 2（国语），参考音轨索引为 0。
- 固定目录：`runs/library_reference_20261004/`。输入与原 80 次请求、16 个精看窗口、2 次渲染的硬预算没有重置。
- 当前 Codex 会话连接官方 `@z_ai/mcp-server@0.1.5`；实际 HTTP 模型为 `glm-5.3-flash`。本机运行 FFmpeg、CPU Whisper small/int8 与缓存。没有运行本地 Qwen、FlashVID、Omni、MiniMax 或生图 API。
- 本轮不用租服务器。Coding Plan 的官方 MCP 会话使用方式不等于独立 Python/服务器可直接获得同样订阅权益；没有测得人民币账单，不能从 token 数推算实际套餐扣量。

| 素材 | 时长/秒 | 原件 SHA-256 |
| --- | ---: | --- |
| 功夫熊猫 | 5529.536 | `24675c65978d5360586f02edcab4735708640027b2f6dff654bda1e5a5041060` |
| 功夫熊猫2 | 5423.968 | `5f9ca95e536641933924044c8711c18eea22ed81d312969bf2c57351dda66ad0` |
| 功夫熊猫3 | 5694.720 | `cf2fec1024243517c1811dce0bba3c1790af887823ab93ec543627606323b64e` |

## 实际结果

GLM 选择 round 0；选片理由存于 [render_selection.json](C:/Users/29785/Desktop/omni-autonomous-screenplay/runs/library_reference_20261004/render_selection.json)。第一次对照审核使用了过严的参考人物身份要求，且剪法评价仅举叙事顺序，因此保存其原始 fail/pass，不把选片理由改写为质量通过。选中的实际视频另按最终明确规则重新审看：[selected_review_v2_0.json](C:/Users/29785/Desktop/omni-autonomous-screenplay/runs/library_reference_20261004/selected_review_v2_0.json)。

最终判定：**主旨 partial、剪法 partial、人物连续性 pass**。成片能表达受到否定、坚持训练、对抗取得结果、获得认可，但缺少参考中独立生活能力这一维度；宏观对比的间隔较长，没有迁移紧邻强反转，也没有实现结尾的图文蒙太奇。GLM 没有审听音频，不能声称音乐卡点或完整声画节奏已实现。

| 版本 | FFprobe 容器时长 | 段落/片段 | 最终选用 |
| --- | ---: | --- | --- |
| round 0 | 77.366667 秒 | 4/6 | 是 |
| round 1 | 64.000000 秒 | 3/3 | 否 |

- [最终视频](C:/Users/29785/Desktop/omni-autonomous-screenplay/runs/library_reference_20261004/render_0/final.mp4)：720×1280、30 fps、H.264/AAC，约 11.86 MB；SHA `e6d10d908d8ae508a3df9a36f8630e0b5161e838e8302414d4019bd9255b4111`。
- [第二版](C:/Users/29785/Desktop/omni-autonomous-screenplay/runs/library_reference_20261004/render_1/final.mp4)：64 秒；SHA `0b9c0bbf9018932a0bd8ffa5810ea2f16e108063d334a8394449dd5581bd0417`。
- [最终结果记录](C:/Users/29785/Desktop/omni-autonomous-screenplay/runs/library_reference_20261004/result.json)、[原片至输出时间映射](C:/Users/29785/Desktop/omni-autonomous-screenplay/runs/library_reference_20261004/render_0/render_result.json)。
- 两版均经 FFmpeg 完整解码，无解码错误。最终版联系表已作目视检查。模型选了 fit，竖屏画布有较大黑边；未人工改成裁切，也未新增字幕。
- 容器时长与按帧编译的画面时长可因 AAC 尾部略有差异；来源入出点仍以原电影秒数绑定。

## 观察量、请求与用量

7 张有效稀疏联系表，共 126 个抽样图块，用于导航，不能称为看完三部电影。16 个连续窗口合计 24 分钟，占素材总时长 8.6496%；未将稀疏图块间的空白算作观察证据。

共 43/80 次请求：42 次收到回复，1 次结果不明。未知 `glm_004_coarse_978d5360_01` 没有重放，原输入范围、记录与预算占用保留。修复只针对已知回复的协议错误，每个原请求最多一次。

| 已捕获回复中的 token | 数量 |
| --- | ---: |
| 输入 | 2,060,953 |
| 输出 | 219,561 |
| 合计 | 2,280,514 |
| 输入缓存（包含于输入） | 399,104 |
| 推理（包含于输出） | 180,901 |

这些数不包含未知请求可能产生的消耗；未知费用不能当作零。队列记录的工具等待合计约 71.92 分钟，包括失败等待；它不是完整运行时间，也不等于纯模型计算时间。工程调试、暂停和本地 ASR/渲染尚无统一单独计时。

第一版规划输入为 178,988 token，修复请求为 180,835 token。后续改为精确字段打包，16 窗口的序列化上下文从 626,242 字符降至 135,842 字符，所有 fine 观察、来源 SHA、时间映射、ASR 句子及段时间保留；原始 metadata 和逐词 ASR 留在文件中。字符下降不能直接等同 token、费用或质量下降。

## 工程问题与追加政策

| 记录 | 问题与处理 |
| --- | --- |
| PyAV | v19 与 faster-whisper 的 metadata_errors 参数不兼容；固定 av>=16,<17，实装 16.1.0。 |
| glm_002→003 | 8,192 输出预算被推理耗尽、最终内容空；保留 HTTP200 原件，单次格式修复成功。官方 MCP 输出上限改为 16,384。 |
| glm_004 | 约 306 秒后 fetch failed，无 HTTP 回复；不重放。后续 Undici 延长 header/body deadline；不能据持续成功断定首次根因。 |
| ASR | 第二连续窗口两零时长词导致旧严格校验阻断；保留原词和 raw，标为 timing_usable=false，不给可用源时间。46 个有效段保留，旧已完成缓存不改。 |
| 身份 | 各 fine 的角色字母发生碰撞；新计划使用模型给出的 focus_role_bindings 与窗口局部角色，程序仅校验绑定/事件/时间，不宣称语义身份真值。 |
| 审核 | 明确允许异源人物与具体事件变化；区分主旨与传记事实，区分剪法与叙事顺序。旧评分保留，最终实际成片另审。 |
| 恢复 | 同目录复用已收模型回复和真实媒体；停止官方连接后整条入口再运行，仍为 43 请求且最终 SHA 不变。新未缓存工作会在已停止连接时阻断，重连只清 stop 标记，不清任务记录/预算。 |

协议错误留档：
- `glm_002_coarse_978d5360_00`：`Expecting value: line 1 column 1 (char 0)`。
- `glm_009_zoom_bb950ab47e586a00`：`coarse_does_not_match_requested_page`。
- `glm_018_fine_367e017be92c91ab`：`fine:usable_range_event_does_not_overlap`。
- `glm_020_fine_acc6457168967f9b`：`Expecting ':' delimiter: line 1 column 605 (char 604)`。
- `glm_024_plan_0`：`plan:range_not_supported_by_fine_observation`。
- `glm_032_fine_28789f9176a0b1c2`：`fine:usable_range_event_does_not_overlap`。
- `glm_036_fine_db69377e99bb2182`：`fine:usable_range_event_does_not_overlap`。

相关追加政策与代码 SHA 存于运行目录的 `artifacts/`；原始提示、模型响应、首次评分、未知请求与补审前 result 均保留。旧生成路线和发现路线未重新启动。

## 尚未证明的部分

第二轮追加了 8 个窗口，但两版最终都仅使用首轮相同的 3 个窗口；第二版没有利用补看结果。因此本轮**不能证明需求驱动补看提升了质量**，暴露了观察与重规划之间的脱节。

完整角色事件图、局部复制检测、FlashVID 推理、音频节拍测量、运动/切点精度评测、自动跟随构图、静态定格及新字幕尚未实现。本轮不是跨参考批量评测，也没有人类盲评；不同阶段审核策略发生澄清，历史评分不能直接用于版本提升的实验结论。

最新全量本地测试为 **241 passed、2 skipped**（独立环境缺少冻结发现线的可选依赖）；停止连接保护另有 2 项定向检查通过。原环境的相关发现测试此前 28 项通过，不与重叠测试相加。真实合成媒体测试验证新增补审只提交一次、旧请求和评分字节不变、再次运行不增加请求；它不证明创作质量。

下一阶段优先验证：补看结果是否进入可解释的候选比较、主旨是否需要战斗以外的事件证据、具体剪法是否有渲染能力与实际时间证据。先固定这轮明确后的协议，再设计新的独立任务与评测，不能删除本轮记录或新建目录重置预算。
