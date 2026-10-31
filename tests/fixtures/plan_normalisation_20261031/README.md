# 2026-10-31 规划规范化回归证据

- `glm_182_plan_0.json`、`glm_183_plan_0_repair.json`、`glm_184_plan_0_repair2.json`：从 `.agent-team/qfs_fixture/plan_r1_bushaoxin/*_response.json` 的 `result.content[].text` 解码所得，未改写模型字段。
- `observations.json`：同目录 `watched_windows.json` 中的真实观测、窗口边界、qfs 身份和内容范围；参考、catalog、参考时长取 `glm_182_plan_0_request.json`。去掉媒体代理元数据与服务器路径；身份/事件/可用区间保持原值。
- `xueshan_fine_plan_0.json`：逐字复制 `.agent-team/qfs_fixture/xueshan_r1/fine_plan_0.json`。

182 的 `seg_8.source_id` 可直接从已选窗口回填；本次补齐后整份真实回复通过生产规划校验。183 未提供 cast，所有选段使用不存在的 `ref_movie`，其焦点无任何所用窗口的确认匹配；保留原非空绑定错误及 `focus_role_id`，反馈列出合法窗口和带姓名的确认角色行。184 的 `seg_3/seg_6` 重复同一来源 219–223 秒，保留拒绝，反馈列出冲突与空闲替代范围；其余对白校验通过。真实回复保持原值，不替模型重选素材。

- `legacy_focus_plan.json`：历史回放 `full_tpl_xiaobiesan_20261026_v1/evaluations/regress_ab014be/calls/glm_006_plan_0_repair/parsed.json` 原计划。
- `legacy_focus_observations.json`：该调用 request 中的参考、catalog、参考时长及实际使用的 4 个窗口（省去源路径）。验证不同局部实体的原有效焦点声明仍被保留，程序不凭局部 ID 差异否定原声明，也不向新窗口外推未知身份。

雪山表只引用 `chain_parent`，其所选父片区间互不重叠，单独这份文件不能证明原电影镜头重复。测试中明确标注的 synthetic parent mapping 来自任务说明的两组重复位置（父片 6–7/19–20 秒、8–10/21–22 秒），其中原片绝对时间是测试坐标，不是真实服务器溯源回执。它验证真实精剪表结合原片映射后能检出 `clip_3/clip_6`；真实父片回执未提供，不能宣称已完成该运行的完整溯源验收。
