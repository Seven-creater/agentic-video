这些夹具来自只读证据文件：
`/Users/Admin/Desktop/agentic_video_all/.agent-team/out/e2e/evidence_01_不烧心_0.txt`。

`glm_165_qfs_close_26_20261029.json`、`glm_166_qfs_close_28_20261029.json`、
`glm_167_qfs_close_29_20261029.json`、`glm_168_qfs_close_27_20261029.json`
分别复制四段完整的 `response.json`，只重新格式化外层 JSON，未改动回复内容。

证据中的 `questions.json` 和 `report.json` 已被截断。
`material_gap_report_excerpt_20261029.json` 只保留能完整解析的 b1，以及 b2 的元数据和首个完整候选
`close_006`；不是完整运行报告。测试用的身份锚点由该候选的角色、影片、静帧和视频字段构造，
原始锚点及完整主节拍 b5 不在证据里，未虚构为原始请求。
四条 `no` 回复在测试中使用已收 b1 重建“有锚点”校验分支；无匹配的判断不依赖主节拍文字。

`null` 的正向候选用原始 b2 候选检查有锚点时的保守降级。
非法身份测试只把真实 `close_26` 回复的身份字段改为字符串，检查可修复错误及收据复用。
流水线测试使用真实 b1 空候选、b2 合格候选，媒体准备、连续观察和模型调用均以离线桩替代。
这些测试不证明最终成片的叙事质量，也不证明另外 11 条空断言由同一字段引起。
