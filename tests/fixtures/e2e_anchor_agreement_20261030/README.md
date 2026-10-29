数据来源：`.agent-team/out/e2e/evidence_18_粉色娇嫩_14.txt`。

- `glm_173_fine_response.json`：第 68–81 行完整 response.json，内层模型文本保留原样。
- `close_004_candidate.json`：第 466–500 行完整候选对象，来自末尾截断的 report.json；没有补造缺失的 report 字段。
- `glm_174_plan_cast.json`：第 86 行截断 model_text 中仍完整的 cast 数组；没有补全缺失的规划尾部。

测试的请求窗口和锚点板是最小重建：实体 id 来自 fine，close 的 null 来自候选，原片窗口按现有规则从候选 [472,476] 扩展到 [470,478]；锚点文件名为明确标记的占位值，不读取或伪造图片证据。测试中显式修改的 false/重复绑定是反例，true 是黄金路径既有规则的对照，均不冒充真实回复。
