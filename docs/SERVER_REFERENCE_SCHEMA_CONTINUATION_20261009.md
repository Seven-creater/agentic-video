# 第二参考：补全观察协议后继续原剪辑任务

用户在39次请求全部结算、零渲染后再次明确说“继续”。这次只纠正已知的第四段观察协议失败，随后使用原任务尚未执行的阶段。不是重新检索、另开任务预算或复活已结算的第一参考链。

## 已有证据

任务目录为 `shared/runs/server_reference_7692329355342679331_20261009`，参考时长198.461995秒。原038及唯一039修复均有HTTP200/stop完整响应：038首先因直接事实填写非空basis被拒绝；039修复该问题后仍缺必需的`uncertainties`，最终`semantic/uncertainties:list_required`。两份原始响应、失败记录和已退出的控制进程保持不变。已有92/88秒只是EDL计划，不是实际视频。

旧提示省略字段类型说明，strict validator又只报告首错，因此一次修复没有同时看到遗漏字段。接受规则本身无需放宽，代码也不能替模型填`uncertainties=[]`。

## 最小工程修正

- 保留原strict validator及旧提示字节。新增纯函数聚合字段诊断，前向包装器保留原首错、异常类型和区间诊断，只附加`field_errors`。
- 新续跑使用已有的完整`explicit_slice_observation_prompt`，包括所有绑定字段、人物与证据表、直接事实空basis、推断引用和`uncertainties:string[]`。只规定协议，不提示影片剧情、人物选择或切点。
- 复用`server_interval_resume`模块，增加固定的`--schema-correction`选项。新证明`server_schema_resume`绑定39条received前缀、输入锁、父29证明、旧失败及控制文件、HTTP前缀和本次运行代码。旧证明的runtime仍绑定旧release原文件。
- 仅第四段`semantic_slice_0_f8a4f183a2d3df76`映射到`semantic_schema_resume_v1`及原生唯一格式修复；已完成的第一段继续映射原030以命中缓存。其它阶段保持原名。
- controller为`controllers/schema_resume_v1`，worker输出仍指向原任务。最多2候选、2渲染、16有效精看窗口不变；39次用量不退款或清零。结果不明请求继续禁止重放。终止失败不自动再注册纠正或循环启动。

本轮保留第二参考原有`active_finecut`架构：草案预精炼、事实核验，再渲染/审核。它仍未恢复成历史的“实际粗剪视频→技能精剪”架构；不能把本次协议修正说成该架构已复现。

## 验证与实际结果

本地Python续跑及区间回归70项通过；原038/039只读回归、服务器wheel回归、GitHub双平台结果、登记与真实运行状态分别保存在`runs`下的独立证据。实现和合成测试不构成模型剪辑质量通过。后续以实际请求、可播放渲染及审阅报告结算，不以EDL时长替代成片。
