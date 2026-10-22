# reliable_v1 工程执行模式

`omni-reliable batch --home HOME --manifest MANIFEST --root OUTPUT` 启动正式执行链。
`omni-reliable status` 使用相同参数读取结果。服务器安装 `deploy/omni-reliable@.service` 后，
由 systemd 托管并在协调进程意外退出或服务器重启后读取原检查点。
每个批次的 `service.env` 仅配置 `PYTHONPATH=不可变release/src`，凭证由既有受限配置读取。

输入清单包含 `experiment_id`、`execution_mode=reliable_v1`、`library`、`output_spec`、
`cases`、`max_cost_cny=100`、`prior_budget_file` 和 `prior_budget_sha256`。
case 的 `input_kind` 为 `reference`（实际文件、SHA、可选主题标题）或 `live_hotspot`。
正式确认批次声明 `acceptance_profile=frozen20_live2`，固定20参考及2实时热点，全部保留分母。
新清单继承原预算文件的费用上界和未决预占，不改写旧实验，也不重新获得100元。
新版本可显式绑定 case.received_sources 台账及SHA，精确匹配原prompt、媒体与时间范围后复用received响应；
仍重新检索、编译、渲染和验收，不能复制旧成片计为新版通过。previous_captures仅排除已尝试热点，
不将其当作新捕获；原拒绝和失败继续保留在旧批次分母。

链路：核对输入 → 理解参考 → 检索已验证片段 → 选择ID并编译计划 → 画面 → 声音 → 技术验收 → 原子交付。
模型不生成任意源时间区间；所有片段来自可核查的已收到素材观察，来源和历史未决范围仍受保护。
一次协议修复后可从同一相关候选池生成基础剪辑。补齐最低时长、配音回退等均写入降级原因。
明确的暂时性HTTP失败最多两次恢复；未知请求不重发，也不能换输出目录规避。
每个配音payload有共享注册记录；未知旧配音也不能通过修改line_id重复调用。

`engineering_status` 为 completed/completed_degraded/blocked/failed。
`quality_status` 独立，当前可靠模式默认 unassessed；旧质量门槛不会被改写为通过。
纯BGM、参考完整原声、原片音轨分别标记。新配音需验证WAV、voice stem、混音收据和成片音轨绑定。
技术完成要求完整声画解码、1280×720、30fps、10–180秒、时间线与来源一致、实际有声且非全黑。
没有可用相关素材或声音时如实阻塞。

每阶段写检查点。已完成且哈希一致的阶段复用；未完成本地渲染可重建，付费收据不能删除。
交付后可清理已列明的本地中间渲染/混音缓存，保留最终视频、配音、关键声音证据和来源记录。
并发上限5，每路1024MB/768MiB，磁盘保留2GiB，FFmpeg单槽2线程。
资源排队最长30分钟，单个worker最长45分钟；资源或本地渲染故障有界恢复，整批期限24小时。
终态失败退出码2，systemd不会反复重启已失败的完整批次。

`report.json` 分别报告参考与热点的出片/技术通过、正常/降级数量、自动恢复、人工介入、耗时和费用。
只有20/20及2/2真实视频通过才达到本次工程验收。历史回放和故障注入不是独立真实出片成绩。
