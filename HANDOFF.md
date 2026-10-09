# 新窗口交接：参考驱动电影素材库的粗剪 → 精剪

**最新实际状态（12:16停止）：** 服务器已按GLM原始方案渲染147秒粗剪，
但36号第一次粗剪盲读在视觉POST之前被本地守卫阻止：MCP启动追加共享日志，
触发历史文件SHA保护。累计36＝35received＋1failed_known，没有36号HTTP事件，
没有精剪。旧日志仅追加6782字节，2726312字节原前缀仍匹配原SHA。
日志现已前向改成每次调用独立保存，并保留嵌套原错误；尚未重新调用。
原日志、36条记录、失败与控制文件不改写。后续不能直接start或重放36，
需要证明这次已知无POST失败及原剩余阶段的一次机械carryover；不要新增候选/渲染额度或Goal。
实际粗剪本地路径：`runs/server_edit_test_20261009/delivery/server_rough_147s.mp4`。

**最新授权：** 用户明确要求直接在服务器端到端测试、按实际错误修复，并继续。
前述“只回顾、不运行”的暂停已被该新指令取代。使用同一任务的
`one_chain_e2e_v1`：复用已完成检索及GLM147秒原始粗剪方案，先渲染、审核实际粗剪，
再用通用skill完整观察与局部PTS补看，精剪到参考约22秒并审核实际成片。
允许1次粗剪渲染、最多2次精剪渲染/一次实际证据修订；保留35条旧回复，
不登记旧slice_wrapper、不新开候选搜索、Goal或未知重放。实现不等于真实结果，
实际启动、日志及视频证据追加至docs/SERVER_EDIT_TEST_20261009.md。

**2026-10-09 当前用户澄清与回顾优先：** 只有一条自动流水线：GLM从固定参考和电影库
制作可播放粗剪，审核内容/前后呼应，然后紧接完整粗剪观察、局部补看与技能精剪，最后实际审核交付。
77秒是粗剪样例，21.9秒是随后技能精剪结果；不是两个业务路线。
先读[历史回顾与当前统一约定](docs/WORKFLOW_RECAP_20261009.md)。服务器迁移漏了真实粗剪交接、
完整粗剪观察/局部PTS补看、相同技能材料和近参考时长约束，不能继续把JSON草稿精炼当作已通过粗剪。
实际服务器job73d38b19在35 received/0render停止；152秒只是方案，未出新片。
本轮新slice_wrapper及其他恢复修改保持未提交、未部署、未登记/启动；用户要求先回顾、收敛主流程。
不要自动续跑恢复分支、重发请求或新开Goal。下面274以及更早记录为保留的历史证据。

**2026-10-08 最新交付274：** 通用skill已完成真实GLM测试，77.37秒粗剪产出两版21.9秒候选，
选中唯一局部改版`render_1`。累计274；本试验251–274共24条，265仍uncertain未重发，
273已知JSON失败、274唯一修复已收到解析，零pending。CLI/MCP均exit0，
`mcp_stop=visual_story_trial_settled`，未恢复Goal（当前会话没有活动Goal），不自动新轮或追加渲染。
先读[真实成片、执行变化与审阅矛盾](docs/GLM_VISUAL_STORY_SKILL_TRIAL_20261008.md)。

```text
C:\Users\29785\Desktop\omni-autonomous-screenplay\runs\library_reference_20261004\artifacts\visual_story_skill_trial_v1\delivery\glm_skill_finecut.mp4
```

视频SHA为`63056eeaeaa5543f37ca4aa345fdeda5a64300e500e71628319059ceb8090768`，
1280×720/30fps/657帧。9段均1×，末段停留0.4秒，没有慢放或提速。
唯一改版将对峙入点37.3→38.0、冲击波入点42.5→41.8，重新分配0.7秒；
其余7段包括clip_8两版像素相同。最终无字盲读272为partial，目标审核274为pass，
不能合并成质量通过。模型称clip_8错渲染已修复、盲读能复述文字主线、clip_2裁点偏移，
均缺实际支撑；CPU核验与独立帧审阅另存，原错误回复不改。
基本事件顺序可读，学习过程、对手结果和纯画面因果仍有缺口。
已按原速封装参考原音轨，独立CPU核验确认交付657画面帧与选中版完全一致，
audio结束21.896009秒、同时间参考音频相关系数0.998910129，约4ms尾差，无长段截断。
这只是媒体执行技术核验，没有音乐卡点或声画艺术验收。
附加`delivery_evidence_limits_20261008.json`标记joint quality未建立，不改原result/评分；
云端内部抽帧策略仍unknown，本地代理30fps不证明云端逐帧观看。
下方265、251及250状态保留为历史，不覆盖本次交付。

**2026-10-08 继续授权已登记、启动前中断：** 用户再次要求继续，已追加`vss_unknown_265_resume_v1`
冻结265前缀，允许跳过lost帧页并复用264及其他已收到观察，原未用render数不增加。
flow已支持skip与实际编辑指纹无进展停止，45项flow/JS联合检查通过；Python恢复68项、
JS/bridge114项检查也通过。但真实MCP预检出现授权字段生产者/消费者不一致：
Python actualgrant为authorization_sha256/new_renders=0，JS期待original_authorization_sha256/
reuse_original_unused_renders；不得改已固化artifact，需窄修JS消费者并真实配置交叉验证。
用户主动中断后已停，零新POST、累计仍265、零pending、无新视频，265原uncertain保持。
先读[追加实际记录](docs/GLM_VISUAL_STORY_SKILL_TRIAL_20261008.md)，不要把测试通过当接通成功。
JS修复请求此前用send_message发给已完成agent，没有触发新turn；继续时要followup_task或root直接修。

**2026-10-08 当前结算265、无新成片：** 通用skill真实迁移已执行，251–264共14次收到并解析；
265图片请求首次POST发生ECONNRESET，无捕获回复，结果不明，随后自动重试被守卫拦截。
这不是已证实的套餐/80额度问题。GLM自主选了四处精看，训练描述有修正，
但尚未汇总EDL或渲染，不称新skill质量通过。MCP与CLI已经退出、Goal仍暂停、零266。
旧250和新全部回复/失败保留；旧004/131/166与新265不明都不重放。
本轮另收到主动中断，检查时网络停止已发生；停止控制记录不解释为网络原因。
见[实际步骤、范围和停止证据](docs/GLM_VISUAL_STORY_SKILL_TRIAL_20261008.md)。
后续不能直接重启原入口绕过unknown265，应在新的明确继续授权下先保全和处理范围。
下面251提交状态是此次结算之前的历史。

**2026-10-08 新授权已进入GLM测试：** 用户要求迁移通用skill并让GLM试，
同任务250基线追加`visual_story_skill_trial_v1`，官方MCP已连接、251完整77秒观察已登记提交。
流程为自主段落/精看问题→真实局部视频和PTS帧→自主EDL→实际无字盲读/目标比较，最多一次局部改版。
只给通用skill/cards，不给教师电影切点。整参考131不明谱系不重发；使用收到的060模型子对象及其局限。
详见[新流程与实际运行记录](docs/GLM_VISUAL_STORY_SKILL_TRIAL_20261008.md)。
Goal仍暂停、旧004/131/166不明不改；当前未有新GLM成片，等待原251而非重发。
后续结算快照将追加，下面“未请求GLM执行”的教师记录是此前历史。

**2026-10-08 当前任务已交付教师示范：** 用户要求Codex充当老师亲自剪辑，并改用完整77秒，
最新又明确只需接近约22秒参考，不一味追求最短。实际教师v3为21.866667秒/656帧，
1280×720，关键收指0.5×、必要过程局部提速、短尾帧停留，使用原参考音轨。
视频：`runs/library_reference_20261004/teacher_demo_20261007/delivery/teacher_finecut_v3.mp4`；
先读[完整观察/取舍/执行/盲读/局限](docs/CODEX_TEACHER_FINECUT_20261008.md)，
后续给GLM使用[通用skill](skills/visual-story-finecut/SKILL.md)及其决策卡，不预给电影EDL。
教师路由仅本次人工创作授权例外；不是GLM新运行或质量通过。视觉盲读读出受制→主动→群体接纳，
未验证具体学习过程、对手最终结局或音乐卡点。GLM仍250、Goal暂停、MCP停止；
原账本SHA `a0607cd818375be6a58bcc01bcd5c32cb44d59a6e9ae4c6e5d17857344604a6a` 不变。
用户尚未要求把这个skill接入另一次GLM执行，不能以教师视频替换旧模型成片/评审。

**2026-10-07 最新250主动停止：** 先读[局部边界运行记录](docs/GLM_BOUNDARY_NAVIGATION_20261007.md)。
局部补看和238 inactive-range解析恢复已实际完成，239独立邻帧确认第一事件出点2.1333333，
有效receipt与原231阻塞并存。随后第三事件共享义务的范围被混合：250提名出点9.1333333，
与后续原提名7.2–9.0333333重叠，且existing_shot_boundary字段与“没有切镜/主动裁切”解释矛盾。
Root在已知回复后主动停掉MCP；250累计、零pending、零251/confirm5_1调用，零新计划/渲染。
`result_boundary_metadata_recovered.json` stopped/finalnull/77localframes/qualityfalse；connection_stopped
是controller stop的产物，真实原因另存`mc2_controller_scope_stop_250`，不是network/auth failure。
Goal保持暂停；004/131/166未知和全部失败不重放。下一步应处理跨事件贡献/冲突协调，
不要只重复单个边界补看。Forward只读原事件route/shared-ID提示与明确enum规则已经实现，
44合同/合成闭环、17metadata和15bridge/launch检查通过；当前实际进程使用旧加载提示，
不声称新提示效果已实测，不自动重开一轮。下面231与更早记录保留为历史。

**2026-10-07 当前结算231：** v2完成多区域补看、五事件提名和第一入点确认；第二边界
231有效blocked，邻帧证伪229“1.833秒硬切前最后一帧”的推断。模型请求后续新帧或一般性
镜头内出点澄清，当前协议缺同事件缺项→扩展观察→重选边界回路，因此有限试验停止。
没有新视频、计划或render，不能称GLM完全不会剪，也不能称完整slot通过。
结果为`artifacts/microclip_slot_finecut_v2/result_dispatch_recovered.json`。226–231received；
228格式失败/229唯一修复、224HTTP500、225无POST本地拦截均保存。230/231原HTTP200已从
日志恢复，原队列unknown是V8整文件字符串溢出包装错误，保持原字节未改；桥接改流读。
CLI/MCP停止、零pending、Goal暂停、004/131/166仍不明；运行只返回缓存，不自动另一轮。
详细证据及下一版边界回路建议见[完整记录](docs/GLM_SLOT_MICROCLIP_V2_20261007.md)。
下面授权与等待记录均为此次结算之前的历史。

**2026-10-07 已知失败显式续跑：** 用户在224 HTTP500停止后再次明确“继续”。已在原目录登记
`mc2_known_http500_resume`，仅允许原训练区域请求的一次传输重试，绑定原提示/图片/范围和全部224历史。
原224仍failed_known，两份旧停止结果和004/131/166不明状态保留；新结果使用
`result_network_recovered.json`。沿原同slot试验唯一未用渲染继续，不恢复Goal或自动新轮。
官方MCP已连接，实际后续结果将在运行结束后追加。下面224停止是此次续跑之前的历史。

**续跑更正与实际派发：** 225在原生body去重守卫处被拦，所有HTTP日志均无225事件，
不是又一次服务端500。保留225failed_known及result_network_recovered，追加
`mc2_known_http500_dispatch_resume`完成原未用的一次服务端重试。原生body仅允许与224捕获500完全相同，
同job再次发送/重启均拦截。全局日志流读及v2文件stat绑定JSON树缓存修复启动耗时，哈希规则不变。
226 `mc2_region_1_retry_dispatch`已真实POST（seq151），正在等待原回复，不能重发或结束为已知失败。
累计226；新结果单独使用result_dispatch_recovered.json。后续实际结论在结束后追加。

**2026-10-07 最新停止：** 用户在因果审计后明确“继续”，完整 slot 精剪 v2 已修正并通过检查，
在同 run 的 218 基线上独立登记一次试验。读[新版流程与运行](docs/GLM_SLOT_MICROCLIP_V2_20261007.md)。
全段独立观察→原目标核验/义务映射→多个区域与事件→含锚点邻域→实际切片独立事实→
一次渲染→连续与密帧盲读→实际含义/精炼审核。Goal仍暂停，不恢复整片重规划。
累计224：219–223已收到，220 known格式失败、221唯一修复成功；224实际HTTP500/code1234，
无模型内容，failed_known；SDK内部重试被拦，没有第二POST。没有新成片或渲染，MCP/CLI停止。
初始CPU帧身份误判另记恢复，原result.json保持，result_recovered.json记录HTTP500停止。
75新版检查及80原回归通过；已确认模型选择三个必要区域，但尚未完成动作/出口补看。
旧004/131/166仍unknown不重放，Goal暂停。再次执行仅返回停止缓存，不能擅自开启新轮。
旧 v1 完成状态和下面停机记录是历史。新执行只用 `microclip_v2` 和 v2 守卫，不能用旧已耗尽适配器。

**2026-10-07 追加只读因果审计：** 先读[端到端精剪问题](docs/GLM_MICROCLIP_E2E_AUDIT_20261007.md)。
原 slot 表达要求没有成为稳定约束，单分支缩小与单组三锚点将全部合法边界锁在约 0.8 秒内；
补看排除锚点本身、未解决缺项仍 ready，实际输出盲读未可靠识别动态。修正建议尚未实施。
本次零新请求、零渲染；累计仍 218、Goal 暂停、MCP 停止，旧结果及失败记录保持。

**2026-10-07 最新状态：** 局部精细抽帧 skill 与一次实测已完成，累计 218，208–218 全部 received。
先读[局部 skill 实测](docs/GLM_MICROCLIP_SKILL_RUN_20261007.md)，再读下方旧结果。
GLM 对原 77 秒版首个既有 15 秒 slot 做三轮自主缩小、三个边界邻域与一次微剪，输出 1.3 秒。
31 个实际显示帧 ID，三段不连续源切片，0.5× 慢放和 0.5 秒停留。模型总体 partial、可读性 fail；
完整段落未保留。只确认单组动作锚点使可用边界局限于动作；上下文、落地与反应被删。
输出盲读报告只有首帧且认错角色，不能作为动态理解证明，云端采样实际策略未知。
原 WinError 5 CPU 发布停止与临时帧图保持，恢复另记 `mc_infrastructure_resume`，
结果为 `artifacts/microclip_finecut_v1/result_recovered.json`。未追加渲染授权或自动新轮。
Goal 暂停、旧完整链路冻结、CLI/MCP 停止；004/131/166 不明且不重放。
下一版需要同时确认表达所需的上下文和结果边界，并对实际输出也提供密帧证据；尚未实测这些改进。

```text
C:\Users\29785\Desktop\omni-autonomous-screenplay\runs\library_reference_20261004\delivery\microclip_action_1_3s.mp4
C:\Users\29785\Desktop\omni-autonomous-screenplay\runs\library_reference_20261004\delivery\microclip_original_slot_15s.mp4
```

**2026-10-06 当前交付状态（优先于下方历史快照）：** 用户明确“下一步”后，已实际并行完成
父成片直接逐段精剪：34→31秒，77.366667→76.2秒，输出各一次；不是电影库重新规划。
累计207，195–207全部received，无在途，原004/131/166仍uncertain，不重放。
CLI与官方MCP已结束、mcp_stop存在；本轮没有启动或恢复Goal，不自动再剪。
31秒模型partial；76.2秒模型pass只属模型判断，净缩短约1.17秒、前两段延长，
不能宣称短小精悍验收通过。带字幕审核及31秒训练动作描述均有局限。
先读[真实双父成片精剪记录](docs/PARENT_TIMELINE_FINECUT_20261006.md)。
交付完整路径为：

```text
C:\Users\29785\Desktop\omni-autonomous-screenplay\runs\library_reference_20261004\delivery\finecut_from_34s_31s.mp4
C:\Users\29785\Desktop\omni-autonomous-screenplay\runs\library_reference_20261004\delivery\finecut_from_77s_76_2s.mp4
```

31秒SHA为`e2424779e3f0f56316ca93e131d7318d440304d5e19e69d2f82cb01998e544a6`，
76.2秒SHA为`46664216d08bfb1e596e3a30f8eaea673e734a965407b0a49876c912072a0222`。
复制件与原渲染SHA一致。以下“未来分别测试”“没有新渲染”等均为先前记录。

**2026-10-05 当前优先任务：** Goal已按用户要求暂停。用户进一步确认77.37秒render_0为粗剪、
34秒render_3为已有精剪候选；先写逐slot精剪代码，未来分别精剪两版再比较。
读 [双父视频精剪实现](docs/SLOT_FINECUT_IMPLEMENTATION_20261005.md)。代码与合成闭环测试已完成，
`--prepare-slot-finecut`已在原目录完成CPU准备；没有新GLM调用或真实影片渲染。
两部原视频SHA、历史ledger字节及mcp_stop保持不变，累计131，未知004/131不重放。
未来执行要记录新的明确恢复指令，不能把代码／prepare存在或旧Goal授权当作本轮执行许可。
交付任何视频同时给播放器、可复制完整绝对文件路径及文件夹路径。以下均为历史快照。

**此前传输停止边界（累计131）：** 第10轮131草稿真实POST在600秒时AbortError、未捕获HTTP原回复。
131保留uncertain，004也仍uncertain；没有有效第10轮草稿/事实/精剪/审核/电影。
追加goal_research_network_10已绑定请求与传输事件；执行与MCP停止，mcp_stop存在，Goal仍active未完成。
现有guard阻止越过新unknown付费执行。不能重发131或换编码/目录/timeout重交同媒体与scope。
新audience/dense代码及合成队列渲染恢复已通过并推GitHub，不等于真实GLM质量验证。
后续先核查原回复可恢复性，或另行记录明确独立输入策略，不能直接--goal-next绕过unknown。

**更新快照（累计130）：** 第9轮127/128草稿为39秒，129实际精剪27秒，但129/130原回复与
唯一修复均失败：130改写同slot原义务，未渲染。第10轮前向方案在实施/测试中，尚未提交。
新草稿区分观众含义与可替换动作，原义务validator不放宽；新精切用官方同GLM的analyze_image
接触表取证，正常proxy与底层源scope仍保留。不要用image kind绕过004或耗尽范围。
09:59UTC官方MCP已重新连接，但没有新的创作请求。旧126/第9轮在途描述均为历史快照。

**2026-10-05最新研究续跑快照（累计126）：** 用户已在停止后明确恢复“先调研，再Goal完成”。
先读 [精炼与可读性研究](docs/EDITING_READABILITY_RESEARCH_20261005.md)。第8轮局部裁切实际
形成32秒、10段全1倍方案，但独立事实/主张核对发现缺项，125/126唯一修复耗尽，未渲染。
下一步使用前向第9轮evidence-first接口，在写草稿前提供已绑定typed事实，保留所有16个窗口，
旧创作正文只保留归档SHA及反证，旧slots不成为新草稿义务。第9轮尚未提交时才能激活。
原004仍不明，已耗尽的069/070、102/103和125/126输入不能第三次观察/比较。
模型独立观察也可错，提交代理的只读抽帧审计单独保存，不能将人工动作答案交给GLM。
最新可播放影片仍是历史render_3；当前Goal未完成。下方较早状态均为历史快照。

**2026-10-05最新实际执行：** 用户已明确不再考虑额度、授权继续GLM技巧精剪。
先读 [实际主动精剪续跑](docs/ACTIVE_FINE_CUT_RUN_20261005.md)。同一任务已记录no-cap v2授权，
用户进一步授权Goal完成任务。`--continue-finecut`第4轮已保存已知渲染前失败；旧79条基线和
原80配置仅历史，累计93次调用，080–093均已收到，原004仍不明。真实草案与精剪为61秒、
6段、普通速度和3秒尾帧；完成4/6段独立核查，第5段旧原回复及唯一修复仍未通过。
尚未渲染本轮成片，不得称为时间压缩成功。Goal后续从独立事实反馈重新组织段落、精剪和实际审核。
MCP恢复官方输出默认131072，解决低输出上限截断；跨轮精切事实/claims缓存通过追加绑定证明复用，
不重放旧请求，盲读前覆盖全部最终切片。下方未授权/未新增调用为较早历史快照。
缓存复验曾误改旧069的协议失败元数据，已按原保护SHA恢复完全相同字节；误改记录和恢复收据
另存。现在跨阶段旧parsed/failure只读，旧唯一修复复用原请求，无第三次输入重试。

**最新用户指令：** Max 套餐；取消程序自己设置的80次数上限，通过超时、同阶段失败和无进展停止。
先读 [Max 额度与原生视频接入](docs/GLM_MAX_QUOTA_AND_NATIVE_VIDEO_20261004.md)。新公开文档为模型/MCP
共享积分，账户月400次视觉限额未核验；GLM-5.3-Flash 原生视频能力不要求MCP，但自建Python标准API
不是套餐可随意调用的独立服务。用户最新要求先查不经过MCP的方案，本轮未切换或新增实际调用。
原任务仍79条历史calls/四版历史媒体，mcp_stop存在；没有真实增量授权artifact。
工作区新增未激活的 extension_budget/finecut_continuation 代码与合成测试；旧80与44提案仅历史，
新的进度策略不设总次数cap。下方“追加执行器未实现”对应较早快照，不能据此覆盖最新工作区。

**最新主动精剪工程：** 先读 [实现与下一轮提案](docs/ACTIVE_FINE_CUT_IMPLEMENTATION_20261004.md)。
前向 `--active-finecut` 自动包含 semantic-audit/editing-v2，增加模型自主精剪和无剧情答案的独立冗余审核。
最终切片重新观察；原草案表达要求以不可遗漏的 claim 加入实际成片审核，候选须联合通过。
合成媒体端到端执行及预算／恢复已测试；没有新增真实 GLM 请求或电影视频。
当前固定任务仍为 79/80 次请求、4 个实际渲染、16 个唯一窗口。CPU preflight 预留最多 44 次后续请求、
8 个最终精切及一次新渲染，但未授权／激活；前向开关不能用于重跑该已有任务。
同目录预算扩展适配尚须另行实现并获得明确授权；不能直接改 base max_requests=80。
旧 reference-craft 只允许其四次请求与原渲染目录，旧 continuation/revision 校验也绑定基线，
扩展时需保存旧阶段快照和校验，不能删除这些限制。旧媒体、结果、420 个保护文件和79条调用已核验未改。

**最新参考知识阶段：** 先读 [知识与自主复看实施记录](docs/REFERENCE_CRAFT_IMPLEMENTATION_20261004.md)。
`--reference-craft` 将通用手册和八张知识卡实际送入 GLM，自主选择复看窗口并获取连续原时间代理。
真实两阶段工作共新增 3 次请求（含一次协议修复），累计 79/80；无新库内窗口或第五次渲染。
GLM 本次只选择结尾两个区间，比赛段细粒度剪法、准确倍速和声画节奏仍未完成核验。
新增结果在 `artifacts/reference_craft_result_001.json`，不是新成片或质量通过证据。
官方连接已停止；原 76 个调用、420 个受保护文件、旧结果及 render_3 SHA 已核验未改。
新入口和旧 `--continue-semantic` 恢复均为零新增请求。不要用旧76次成片记录冒充新阶段总用量。

**前一轮授权执行：** 先读 [完整参考与精切事实续跑](docs/REFERENCE_LIBRARY_SEMANTIC_RUN_20261004.md)。
用户要求自主分析全片，不给 GLM 旧解释或指定 5–17 秒。`--continue-semantic` 在原目录运行／恢复
这一次追加授权，render_3 是唯一新增版本，80 次总预算和 16 个唯一窗口不变。
059/060 参考协议失败以有局限的原始模型解释恢复导航；061/062 计划证据失败未改写，
一次新语义重规划由 GLM 自主重选，持久化最多五条精切和后续完整审核预算。
不得用旧 result_revision_2 冒充这次结果，也不得重放不明的 004。
本轮实际已生成 34 秒 render_3，结果在 result_semantic_revision_3.json；76/80 次请求，
review=None、review_status=incomplete_protocol_failure、semantic_gate_passed=false。
盲读文字依赖 essential，源观察与部分计划/盲读冲突，不能伪造终审通过或把所有模型反证当真值。
官方 MCP 已停止，同目录恢复不增加请求；58 个原调用、305 个历史文件和全部调用文件保持不变。
本段以下调用数和渲染数保留各历史版本的快照；实际最新状态以原目录 current_status 和新报告为准。

**2026-10-04 当前任务补充：** 先读 [电影素材库任务约定](docs/REFERENCE_LIBRARY_SPEC.md) 和
[端到端调研](docs/REFERENCE_LIBRARY_RESEARCH_20261004.md)。用户最新确认的是参考先确定目标，
再检索大素材库；旧 MiniMax 生成线与抖音发现线保持冻结。用户随后已授权实施及真实运行，
新入口、当前固定输入和恢复规则见下方追加记录及 [本地运行指南](docs/REFERENCE_LIBRARY_LOCAL_RUN.md)。
下文是 2026-10-02 的历史交接，不代表当前素材库路线、Git remote 或运行状态。

**同日最新工程补充：** [剪法实施记录](docs/EDITING_IMPLEMENTATION_20261004.md) 说明新增逐项剪法绑定、
候选的可见剪辑条件、真实切镜候选、定格与中文字幕、实际输出核验，以及 `--editing-v2`／`--audit-editing`。
首轮 43 次调用、两版成片和 partial 评价保留。用户随后明确同意在原任务追加一次渲染；
当前累计 58/80 次请求、16/16 个唯一窗口、3/3 次授权渲染。GLM 自主选中新版 34 秒的
`render_2/final.mp4`，修订结果为 `result_revision_2.json`；不要把旧 `result.json` 当作新版选片。
模型给出三项 pass，但其画面描述存在前后矛盾，22–34 秒实测静音；
没有完成可靠的剪法质量和声画节奏验收。完整记录见 [修订运行报告](docs/REFERENCE_LIBRARY_REVISION_20261004.md)。
官方 MCP 已正常停止，缓存恢复验证无新增请求；旧 43 次调用和 216 个受保护文件 SHA 均未变。
不可新增第四个渲染、重放未知 004、换目录或删状态重置预算。375 项测试通过、2 个依赖模块跳过；
工程测试不证明实际剪辑质量。

**同日后续用户澄清与审计：** 用户确认参考只有 BGM，核心是静音观察能理解准确内容。
此前依据未核验 ASR 推断参考有解说的事实表述已撤回，原记录保留。新内容标准已记入任务约定，
尚未接入运行提示。实际端到端审计判定画面叙事与剪法迁移部分成立，主要断点为
宽事件概括与精切错位、遗漏行动结果、计划影响审核、盲读矛盾未处理、最终选择继承错误。
详见 [端到端审计](docs/REFERENCE_LIBRARY_E2E_AUDIT_20261004.md)。本轮仅审计，仍为 58 次调用与三版视频。

## 2026-10-04 追加：本地素材库路线已开始实施

**后续实施补充：** [时间压缩与事实审核](docs/REFERENCE_TIME_COMPRESSION_20261004.md) 记录
参考比赛段的实际跳切、关键踢击强调、倒地与微笑，以及多精切 slot 的研究设计。
新增 `--semantic-audit` 包含 editing-v2：计划前启用，独立看每个精切，再核对计划要求，
成片无声盲读分动作／结果／文字／推断，未解决矛盾和局部不支持的事实阻止通过。
每段两次独立调用与协议修复预留计入原预算，搜索／计划预算持久化，恢复不改付费请求。
这是新协议工程与合成媒体测试；没有新 GLM 创作质量评测，当前真实 run 仍为 58/80、三次渲染。
不要把旧任务复制到新目录应用该模式以绕过预算；旧计划和评分不追溯改写。

本段优先于下方旧交接中的“仅整理、无新运行授权”等历史说明；用户本轮已授权新路线实施和实际测试。
该授权不启动旧生成／发现线，不改旧任务的付费提交身份。

- 真实参考固定在 `C:/Users/29785/Desktop/omni-autonomous-screenplay/data/ref/video.mp4`：
  SHA256 `2f95e24edd2cf4b79cc1f40f7e202174c53a6abcf084e728bb49e3ca92938a17`，21.933333 秒，720×1280。
- 真实电影库在 `data/videos/`，三部《功夫熊猫》共 16,648.224 秒，即 277.4704 分钟。
  各原件、SHA、实测时长、原时间基与音轨存于当前 run 的 `catalog/inventory.json`。
  三部片均记录国语全局音轨 index 2；参考音轨全局 index 0，不按默认英语轨剪辑。
- 当前模型为 `glm-5.3-flash` 官方视觉 MCP，连接在 Codex 会话内；本地 Python 向 MCP 队列提交工作，
  不直接构造 Coding Plan 模型 HTTP API。CPU Whisper small/int8 提供语言证据，FFmpeg 执行实际剪辑。
  没有租服务器、加载服务器模型或重新生成 MiniMax 素材。本轮粗看是抽帧联系图，未运行 FlashVID。
- 可选依赖安装在独立 `.venv-library`；新入口为 `omni-library`，无需变更旧 `.venv`。
  `mcp_launch` 读取 `Z_AI_API_KEY` 或隐藏输入，不把密钥放进仓库、文档和命令参数。
- 固定 run：`runs/library_reference_20261004/`。全部恢复均沿用它及 `runs/.library_runs.json` 注册记录，
  不新开目录、不删 `library_state.json`、不重置 80 次总请求预算。协议修复也计入。
- 已完成的真实阶段：原件库存与 SHA 核验、参考 ASR 缓存、GLM 实际参考分析。
  首张电影粗看已返回 HTTP 200，输出上限 8,192 token 耗尽且无可用 content。
  原始 MCP 错误、已捕获的 HTTP 回复、用量与恢复解释均保存；没有重放原 POST。
  同一预算内的一次协议修复仍在进行。电影检索、成片和成片审核尚未完成。
- 渲染模块的 9 项真实 FFmpeg 合成测试通过；这验证时间、音轨、混合、缓存等执行行为，
  不证明三部真实电影的片段选择、故事表达与剪法迁移已经验收。

接手先读 `current_status.json`、`library_state.json`、`failure.json` 及对应 `calls/`，
再核对 `mcp_http.jsonl`／队列响应；历史 failure 文件的存在不能覆盖后来追加的明确 HTTP 结果。
同样，没有 `result.json` 及其实际媒体时不能宣布成片成功。

### 同日追加：修复结果、未知请求与 adaptive_v2

上方“一次修复进行中”是实施初期快照；`glm_003_coarse_978d5360_00_repair` 已实际成功，
其原错误、HTTP 200 回复与修复用量全部保留。后续 `glm_004_coarse_978d5360_01`
约 306 秒后发生 `fetch failed`，没有捕获服务回复，继续永久保留 `uncertain` 原记录；
该请求仍占原 80 次预算中的一次，消耗 token／实际账单未知，不能填零或猜作退款。

规则是禁止未知请求重放，不是禁止所有新工作。当前 run 追加
`continuation_policy = independent_media_no_unknown_replay_v1`，允许其他新独立输入；
相同请求摘要、相同媒体 SHA 或同 `kind + sourceSHA + 原区间` 的 lineage 都不能再次提交，
改编码、文件名、提示或输出目录也不能绕过。尚在 `submitted` 的请求仍须先等结果；
未启用这一追加政策的基础 state 保留原保守阻断，不删除任何历史调用。

`strategy_transition` 记录从 `fixed_30_page_grid` 到 `adaptive_coarse_v2`：
每部电影 18 帧全局导航，由 GLM 在全库选择最多 4 个、不超过 600 秒的区域展开；
随后每轮最多 8 个、不超过 90 秒的连续精看窗口。原 16 个窗口、2 个成片、80 次请求硬上限不变。
005、006 全局概览已有明确回复，007 在途；尚无实际素材库成片。

外部官方 MCP 包根目录新增 `undici@7.16.0`，本地 dispatcher 的 headers/body timeout 为 600 秒，
官方 MCP 配置输出上限 16,384／timeout 600 秒；内部重试仍由 guard 阻断，官方模型请求 body 未改写。
约五分钟的 Node header timeout 是 004 的原因候选，原 cause 未捕获，不能当作确定根因。

更新日期：2026-10-02，Asia/Shanghai。

本文由当前源码、真实运行产物和本地保存的服务器执行记录核对而来。
本次只写交接文档，没有 SSH 连接、模型加载、API 调用或新增付费素材。
**服务器信息是历史留档，不代表此刻的 GPU、进程、文件或服务已经复查。**

## 1. 新窗口首先确认的事实

- 当前唯一主工作仓库：`C:/Users/29785/Desktop/omni-autonomous-screenplay`。
- 分支：`main`。写本文之前的 HEAD：`03fd06688db1d285044fa6d06477f3d46d37db78`。
- 当前仓库没有配置 Git remote。不要把旧仓库的 GitHub 同步状态当成这个仓库的状态。
- 用户最近明确要求：以整理代码为主，不需要多生成；随后要求复测，再要求本文。
  这不是新的付费运行或服务器启动授权。新窗口先阅读，不要自动启动模型或后台定时任务。
- 此仓库是独立 Python 包，不运行时导入旧 `douyin`／`douyin-final-pipeline` 的代码。
- 当前默认后端：阿里云 Qwen3.8 Omni + 阿里云生图／改图 + MiniMax H3 API。
  **服务器 Qwen3-Omni／本地 H3 尚未接入这个独立仓库的主 CLI。**
- 本地复测：69 passed，19.39 秒；12 个运行模块导入及 Python 3.10 语法检查通过。
  实际测试 Python 为 3.13.12，不把语法检查冒充 Python 3.10 上的完整运行。
- 离线 wheel 构建、临时目录安装、脱离源码目录的模块／console 入口及错误参数检查通过。
  这些验证不证明真实模型生成质量已经全部合格。
- 旧请求、raw response、失败、任务 ID、资产、素材和成片全部保留。

本文新增后，`git status` 可能显示本文未提交；不要因此清理或回滚文件。

## 2. 用户目标和不可丢失的要求

用户的唯一创作输入是参考视频。由 Omni 自主完成内容与剪辑理解、主题、故事、逐段剧本、
人物／道具／场景描述、资产审阅与选择、真实素材观察、切片及成片审阅。
Codex 负责通用程序、工具执行和证据核对，不在中途手写剧情、指定题材、选图或选片。

尤其不要恢复已经否定的旧要求：

1. 身体差异允许，但不是必选，也不是拒绝理由。
2. 新故事保留相似的主旨和表达机制；段落结构尽量相似，不锁死原片秒点、镜头数或段数。
3. 剧情要便于画面理解和后续生成，资产只要求粗身份、主要服装、场景和重要道具稳定。
4. **故事时间、素材生成时长、最终成片时长是三个概念。**
   先写完整事件，生成完整动作素材，Omni 看过实际素材以后才决定哪些瞬间剪入成片。
5. 内容审核不理想时，在有限预算内修订或由 Omni 选现有最佳候选继续，并保留缺点。
   不把拒绝记录改成通过，不因微小动作细节无限修稿。
6. 缺失文件、无效媒体区间、认证／余额失败和提交状态不明是执行故障，不能伪造结果。
7. 每条视频素材只提交一次；已有任务只查／下载。不能删 `submission.json` 或开新目录重置预算。
8. BGM 可复用、保持原速；故事不必复用原配音。当前实现是纯画面创作，没有新增字幕或配音模块。
9. 剪辑必须基于真实素材和贯穿下游的同一份参考记录，不是把所有视频按生成顺序串起来。

先读 [AGENTS.md](AGENTS.md)，其中的执行边界继续有效。

## 3. 按这个顺序阅读代码

下表路径均相对于 `C:/Users/29785/Desktop/omni-autonomous-screenplay`。
建议按顺序读，不必先翻旧工程里的大量历史 attempt。

| 顺序 | 文件 | 重点看什么 |
| --- | --- | --- |
| 1 | [README.md](README.md) | 当前流程、默认后端、真实限制，不把“已有代码”理解为所有实际运行均成功 |
| 2 | [docs/RUNNING.md](docs/RUNNING.md) | CLI、凭据、预算、付费边界、续跑与重复扣费保护 |
| 3 | [docs/CODE_MAP.md](docs/CODE_MAP.md) | 实际函数调用图、决策归属、各产物怎样传给下一层 |
| 4 | [omni_story/__main__.py](omni_story/__main__.py) | `main`；`all / screenplay / reedit` 分支；默认是付费全链 |
| 5 | [omni_story/pipeline.py](omni_story/pipeline.py) | `execute`、`analyze_reference`、`Loop.call`、`Loop.stage`、`blind_view`；整体规划后逐段写作、前文状态和有限反馈 |
| 6 | [omni_story/prompts.py](omni_story/prompts.py) | REFERENCE、REFERENCE_LOCAL、ROUTES、OUTLINE、SEGMENT、REVIEW、REVISION、BLIND、ALIGNMENT、SELECT_AVAILABLE |
| 7 | [omni_story/donor_prompts.py](omni_story/donor_prompts.py) | 从旧工程复制并标注来源的基础协议；本地文件，不是运行时导入旧工程 |
| 8 | [omni_story/contract.py](omni_story/contract.py) | `validate_outline`、`validate_segment`、固定资产引用、状态及字段检查；结构校验不等于语义真值 |
| 9 | [omni_story/reference.py](omni_story/reference.py) | `build_transfer`、`validate_editing`、`validate_coverage`、`validate_mapping`、`validate_style_review`；内容与剪辑共同来源 |
| 10 | [omni_story/production.py](omni_story/production.py) | 先读 `full_run`，再读 `execute_production`、`Calls.call`、`edit_sources`；最后读续跑、续剪和音乐分支 |
| 11 | [omni_story/production_prompts.py](omni_story/production_prompts.py) | PLAN、IMAGE_REVIEW、WATCH、EDIT、FINAL_REVIEW、SELECT_IMAGE、SELECT_RENDER、MUSIC_REPAIR；不能提前杜撰实际入出点 |
| 12 | [omni_story/media_backends.py](omni_story/media_backends.py) | `claim`、`AliyunImages`、`MiniMaxH3`；真实接口、原始任务及一次提交保护。`ImageHelper` 不是当前默认路由 |
| 13 | [omni_story/api.py](omni_story/api.py) | `QwenAPI`、`curl_json`；媒体／JSON 传输、凭据脱敏、配置与采样声明 |
| 14 | [omni_story/editing.py](omni_story/editing.py) | `compile_plan`、`edit_metrics`、`render`、`mux_music`、`review_copy`、`audio_measurements`；实测素材区间与 FFmpeg 执行 |
| 15 | [tests/test_pipeline.py](tests/test_pipeline.py)、[tests/test_reference_chain.py](tests/test_reference_chain.py) | 无人工答案、有限修订、共同参考传递、局部复看、预算及历史不覆盖 |
| 16 | [tests/test_production.py](tests/test_production.py)、[tests/test_aliyun_backend.py](tests/test_aliyun_backend.py)、[tests/test_api.py](tests/test_api.py) | 模拟后端＋真实 FFmpeg、图审、不可重发、缓存、接口及密钥保护 |
| 17 | [REAL_RUN_STATUS.md](REAL_RUN_STATUS.md)、[REFERENCE_EDITING_CHAIN.md](REFERENCE_EDITING_CHAIN.md)、[SOURCE_PROVENANCE.json](SOURCE_PROVENANCE.json) | 从历史状态看到最新追加段落；保留失败和效果不足，不只看通过记录 |

主调用链：

```text
__main__.main
  → production.full_run
    → pipeline.execute
      → analyze_reference：原音画字联合理解＋至多两次局部复看
      → reference.build_transfer：同一份内容／剪辑信息与来源
      → ROUTES：三主题，Omni 自主选择
      → Loop.stage(OUTLINE)：完整框架、固定人物／道具／场景
      → 各 unit 的 Loop.stage(SEGMENT)：逐段、前文状态、独立审核
      → BLIND / ALIGNMENT → screenplay.json
    → execute_production
      → PLAN → 固定资产主图 → 素材起始图 → 图审／有限改图／候选选择
      → H3 完整动作素材（最多三路并发）
      → edit_sources
        → WATCH 真实素材 → EDIT 实际入出点及顺序
        → compile_plan / render → FINAL_REVIEW 看实际成片
        → 最多一次重剪 → 现有成片候选选择
    → continue_music_coverage：必要时只调整音乐，画面锁定
```

普通文本预算 32 次、生产侧 36 次、显式续剪 12 次；格式修复也计入。
默认图片上限 12，明确使用 `--unlimited-image-jobs` 才移除该数量上限；
全生产阶段改图上限仍为 1，视频素材上限仍为 6。不是无限抽卡。

## 4. 先读哪些真实产物，当前到底完成到哪里

### 4.1 原女生跆拳道参考片

输入：`C:/Users/29785/Desktop/douyin/data/videos/7682719919410072847/video.mp4`。
SHA256：`2f95e24edd2cf4b79cc1f40f7e202174c53a6abcf084e728bb49e3ca92938a17`。
实测约 21.933333 秒。

运行目录：[runs/reference_7682719919410072847_001](runs/reference_7682719919410072847_001)。

按顺序看：

1. `input_lineage.json`、`reference_reading.json`、`reference_transfer.json`：读懂了什么、根据什么。
2. `routes.json`、`outline.json`、`screenplay.json`、`asset_bible.json`：自主选择的故事。
3. `blind_reading.json`、`alignment_review.json`、`result.json`：15 次文本请求，文本候选通过。
4. `production/production_plan.json`、`asset_inventory.json`、`start_frame_inventory.json`：素材及资产。
5. `production/calls/`、`production/video_jobs/*/submission.json`、`production/run_failure.json`：真实执行结果。

这次自主选中 **The Blind Chef's Precision（盲人厨师）**，四段剧本。
没有偷读旧单臂攀岩剧本或手工指定厨师。完成 17 张图片：10 主图、6 起始画面、1 改图。
生产侧 20 次 Omni 请求。部分图片仍有批评，由 Omni 选现有候选，不假称全部图审通过。

H3 六条素材各尝试一次，只有 M02 成功，实测 6.583333 秒。
M01／M03／M04 得到任务 ID 后 `failed / 1008 insufficient balance`；
M05／M06 创建返回 `402 insufficient_balance_error`，没有任务 ID。
因此该 run **尚无整片，也未做整批素材观看、剪辑及成片审核**。
不要把账户充值理解为失败任务可自动重发，不要继续这个 run 的付费任务而不先核对状态及授权。

### 4.2 第二条参考片

输入：`C:/Users/29785/Desktop/douyin/data/videos/7682750673951198510/video.mp4`。
SHA256：`706acfe6705b0afc487672c93f7ed71dc367ab9ce71f0ffab1351d1b35403bb4`。
约 37.533333 秒。

运行目录：[runs/reference_7682750673951198510_001](runs/reference_7682750673951198510_001)。
Omni 自主写《夜光园丁的黄昏觉醒》，16 次文本请求、五段剧本，已有五段视频素材。

- 旧成片：`production/render_music_1/final.mp4`，45.666667 秒，有音乐。
  SHA256：`9c38470da2e6103abf6d4aecc01306444905dfd98a4518cfba9b0c23a30fe36c`。
  最后只调整混音，没有再次模型看片，仍是有限制候选。
- 新版续剪：`editing_continuations/joint_reference_v2/production/render_0/final.mp4`，
  44.875 秒、1024×576、24 fps，无音轨。
  SHA256：`675f4b352ac18b2f55871d4c42fae1b0a9af124748780799c44571ad8ecfad56`。
  新增 12 次 Omni 请求，没有再次生成图片／视频。
- 模型看了实际成片并接受主线，但风格迁移 `partial_or_unknown`；
  12 行编辑表实际是 6 段连续素材、5 个连接，不是 12 个真正切镜。
  仍未显著压缩，且模型错误关闭了可独立复用的参考音乐。程序没有人工覆盖这个决定。

查看最终 `production/result.json` 与续剪自己的 `production/result.json`。
**旧 `run_failure.json` 可能仍存在，不能仅因该文件存在就断言后续没有成功；也不能仅看 accept 忽略限制。**

## 5. 当前云端代码怎么用

这里只给命令，不在交接时执行。模型 ID／地址来自当前代码，不是新查询得到的服务可用性承诺。

| 工作 | 当前模型 | 地址／接口 |
| --- | --- | --- |
| 理解、创作、审阅、剪辑决定 | `qwen3.8-omni-flash` | `https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions` |
| 人物／道具主图 | `qwen-image-3.0-pro` | 阿里云 `api/v1/services/aigc/multimodal-generation/generation` |
| 场景主图／4–9 图融合 | `wan2.7-image-pro` | 阿里云 `api/v1/services/aigc/image-generation/generation` |
| 1–3 图编辑 | `qwen-image-edit-plus-2025-12-15` | 阿里云 multimodal-generation 接口 |
| 动作视频 | `MiniMax-H3` | `https://api.minimaxi.com/v2/video_generation` 及 V2 查询接口 |

凭据环境变量：`DASHSCOPE_API_KEY`、`MINIMAX_API_KEY`。
可配置地址：`DASHSCOPE_BASE_URL`、`MINIMAX_BASE_URL`。不在文档或仓库中存密钥；代码不自动读取 `.env`。

先做免费本地检查：

```powershell
Set-Location "C:\Users\29785\Desktop\omni-autonomous-screenplay"
git status --short
python -m omni_story --help
python -m pytest tests -q
```

当用户另行明确要求付费运行时，全链入口示例：

```powershell
python -m omni_story --video "C:\path\to\reference.mp4" --output "runs\new_explicit_run" --unlimited-image-jobs
```

输入需小于 10 MB。上述命令是实际付费运行，**不是 dry-run**。
`new_explicit_run` 仅表示用户批准的新任务，不是绕过既有失败任务的办法。
续跑同一任务必须保留原输出目录和所有提交身份。

`--stage screenplay` 只到剧本；`--stage reedit` 是已有完整素材的有界续剪，仍有 Omni 费用。
文本阶段未形成完整 `result.json` 时没有通用逐调用续跑能力。
当前代码没有 `--backend server` 或 `--model-path` 的全链选项。

## 6. 服务器相关模型库存：仅列本链相关的历史记录

服务器：`10.1.4.86`，历史账号／路径为 `wangqihao`。用户确认过直接 `ssh 10.1.4.86` 可连接。
本次没有重新连接；下面路径、环境和 GPU 配方必须在真正启动之前再次只读核对。

| 模型 | 用途 | 已留档路径／ID | 环境及资源 |
| --- | --- | --- | --- |
| Qwen3-Omni-30B-A3B-Instruct | 原生音视频／图像理解、文本创作，thinker-only 推理 | `/data02/pretrained_model/cvr_learn/cvr_model/03_audio_vlm2vec_backbone/qwen3-omni-30b-a3b-instruct` | `/data02/usr/wangqihao/miniconda3/envs/omni_src/bin/python`；历史用 GPU0/1、BF16、device_map=auto |
| Qwen/Qwen-Image | 文生图 | `/home/wangqihao/.cache/huggingface/hub/models--Qwen--Qwen-Image/snapshots/75e0b4be04f60ec59a75f475837eced720f823b6` | `h3` 环境；QwenImagePipeline；历史 GPU4、CPU offload |
| Qwen/Qwen-Image-Edit-2511 | 保身份改图 | `/home/wangqihao/.cache/huggingface/hub/models--Qwen--Qwen-Image-Edit-2511/snapshots/6f3ccc0b56e431dc6a0c2b2039706d7d26f22cb9` | `h3` 环境；QwenImageEditPlusPipeline；历史 GPU5、CPU offload |
| MiniMax-H3 diffusers 权重 | 文字／首帧／参考条件视频生成 | `/data02/usr/wangqihao/Demo/checkpoints/MiniMax-H3-diffusers` | `/data02/usr/wangqihao/miniconda3/envs/h3/bin/python`；历史双卡 int8＋CPU offload，5–15 秒、24 fps、768 短边 |

图像缓存路径取自旧脚本常量，未实时确认 snapshot 仍存在；不存在时先定位本机缓存，不要自动下载新权重。
历史 h3 环境记录为 diffusers 0.40、torch 2.9 cu128；目前版本未复查。
本地 Qwen-Image/Edit 与当前云端 Qwen3.0／万相／edit-plus 不是同一权重。
服务器有其他 Qwen 文本／理解模型的历史记录，但不属于这条链需要启动的后端；本文不是实时全盘模型清单。

### 6.1 三个旧目录各自是什么

- `/data02/usr/wangqihao/Demo/research`：原研究工程；图像 worker 等旧模块位于这里。
- `/data02/usr/wangqihao/Demo/douyin_final_pipeline_20261001`：旧成功路线提炼仓库的完整服务器 checkout。
- `/data02/usr/wangqihao/Demo/final_result_route_20261001`：旧 transport 部署目录，含 Omni worker、perception 依赖与 H3 service；不是当前独立仓库。

本地对应历史文件：

- `C:/Users/29785/Desktop/douyin-final-pipeline/final_route/omni_worker.py`
- `C:/Users/29785/Desktop/douyin-final-pipeline/final_route/backends.py`
- `C:/Users/29785/Desktop/douyin-final-pipeline/src/generation/minimax_h3_ref2va_serve.py`
- `C:/Users/29785/Desktop/douyin/src/agentic_video/asset_studio/gen_worker.py`
- `C:/Users/29785/Desktop/douyin/src/agentic_video/asset_studio/backends.py`

不要把 `C:/Users/29785/Desktop/douyin-final-pipeline` 中的旧故事框架、攀岩资产或用户选择自动作为新输入。
不要恢复已停掉的指挥家／雕刻师 run、旧 heartbeat 或 `run_server_omni_local_pipeline.py` 简化路线。
当前独立仓库未确认有服务器部署，不能声称它已同步 GitHub／服务器。

## 7. 真正启动服务器模型之前：只读预检

下面示例在用户明确允许服务器工作后使用。SSH 进入后按 Linux Bash 执行，不是 PowerShell。

```powershell
ssh 10.1.4.86
```

```bash
nvidia-smi
nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv
ps -eo pid,args | rg 'omni_worker|minimax_h3_ref2va_serve|asset_studio.gen_worker'
test -x /data02/usr/wangqihao/miniconda3/envs/omni_src/bin/python
test -x /data02/usr/wangqihao/miniconda3/envs/h3/bin/python
test -d /data02/pretrained_model/cvr_learn/cvr_model/03_audio_vlm2vec_backbone/qwen3-omni-30b-a3b-instruct
test -d /data02/usr/wangqihao/Demo/checkpoints/MiniMax-H3-diffusers
```

同时核对两个图像 snapshot 和要用的 worker 源文件是否存在；`test` 退出码非零即先停下查原因。
GPU编号只是历史配方，不代表空闲。不要杀其他用户进程或盲目再次启动已有服务。
若本地还要使用 Qwen 图像 GPU4／5，H3 不得同时占 GPU4／5；可在确认空闲后用 GPU2／3 或6／7。
当前 API 路线不用 GPU，也不需要启动任何这些本地服务。

## 8. 服务器 Omni：启动方式与协议

旧部署的实际命令形式：

```bash
CUDA_VISIBLE_DEVICES=0,1 HF_HUB_OFFLINE=1 \
/data02/usr/wangqihao/miniconda3/envs/omni_src/bin/python -u \
  /data02/usr/wangqihao/Demo/final_result_route_20261001/final_route/omni_worker.py \
  --model /data02/pretrained_model/cvr_learn/cvr_model/03_audio_vlm2vec_backbone/qwen3-omni-30b-a3b-instruct \
  --cache /data02/usr/wangqihao/Demo/final_result_route_20261001/media
```

这是 **stdin/stdout JSON-lines worker，不是 HTTP OpenAI-compatible 服务**，没有 base URL 或端口。
旧 `RemoteOmniRunner` 用 SSH 保持管道、上传媒体、传请求、读取响应。
worker 收到首个非 `ping` 请求后才加载模型；只启动进程或 ping 返回 ready 不代表权重已加载／推理已通过。

协议入口：`ping`、`ask`、`watch`、`chat`。`watch` 的媒体路径须是服务器已存在的文件。
worker 输出 Answer 的 text／token／采样记录，不能直接冒充当前 `QwenAPI.request` 的 HTTP 响应。
`chat` 历史实现只处理一个 user 消息及 text／inline image，不能把 video_url 直接塞给它当视频已支持。

不要用 `nohup ... < /dev/null` 启动该 worker：stdin EOF 会让它退出。
应由保持 stdin/stdout 的受控客户端启动，并记录 stderr 日志和精确 PID；不要手工塞故事答案。

## 9. 服务器 H3：启动方式与接口

实际入口：`src.generation.minimax_h3_ref2va_serve`，旧服务使用 diffusers，
不是聊天记录里早期因 CUDA／驱动问题未采用的 SGLang 四卡启动方案。

源码默认端口 30011，但旧 transport 配置使用 **30111**；必须与客户端的 endpoint 一致。
下面示例使用30111、GPU2／3、loopback。**只在这两张卡空闲且获得启动授权后执行。**

原服务 `REPO` 与 `OUTDIR` 是模块常量，CLI 仅有 host／port，没有 `--output`。
不能虚构输出参数；下面用运行时设置 `OUTDIR` 的包装方式隔离新日志／媒体，不编辑旧源码：

```bash
cd /data02/usr/wangqihao/Demo/final_result_route_20261001
H3_JOB_ROOT="/data02/usr/wangqihao/Demo/h3_service_runs/$(date +%Y%m%d_%H%M%S)"
mkdir -p "$H3_JOB_ROOT"
CUDA_VISIBLE_DEVICES=2,3 HF_HUB_OFFLINE=1 \
/data02/usr/wangqihao/miniconda3/envs/h3/bin/python -u -c \
'import sys; from src.generation import minimax_h3_ref2va_serve as s; s.OUTDIR=sys.argv[1]; s.uvicorn.run(s.app,host="127.0.0.1",port=int(sys.argv[2]),log_level="warning")' \
  "$H3_JOB_ROOT/media" 30111
```

这是由当前源码导出的启动示例，本次未在服务器执行。默认前台运行，另开 SSH 会话只读检查：

```bash
curl -sS http://127.0.0.1:30111/health
curl -sS http://127.0.0.1:30111/v1/models
```

`health.status` 应从 loading 变为 ready；failed 是加载失败。
CPU offload 下 GPU 闲时显存低，不用显存占用单独判断服务就绪。

接口：

- `POST /v1/videos`：请求含 `model=MiniMaxAI/MiniMax-H3`、prompt、seconds、task、conditions、target，返回 id。
- `GET /v1/videos/<id>`：查询 queued／in_progress／completed／failed。
- `GET /v1/videos/<id>/content`：下载完成的真实视频。
- 首帧条件：`role=keyframe`、`frame_index=0`，选择 fl2va；普通参考图为 reference，选择 ref2va。
- 服务内部 `cuda:0`／`cuda:1` 对应 `CUDA_VISIBLE_DEVICES` 中的两张物理卡，不是固定GPU0／1。
- 一个服务实例队列串行；多服务并发要先确认独立卡对、端口和输出目录，不盲目启动三份。

注意：旧服务的 JOBS 索引在内存里；重启后不能假定原 task id 仍可查询。
不能因404或断线重发生成，应先找原进程、日志和已落盘媒体。

云端 `MiniMaxH3` 走 V2，服务器走 V1，两种协议不能只换 `MINIMAX_BASE_URL` 就认为接通。
旧 `final_route.backends.LocalH3Bridge` 做了协议／首帧转换、任务落档、查询和下载；它目前不在独立仓库内。
该旧桥还有针对旧竖屏编辑器的格式转换，不能未经核对直接套到当前支持横／竖画布的新主线。

## 10. 服务器 Qwen 生图／改图：启动方式

这两个 worker 属于原研究工程，不在旧 transport 部署目录或当前独立包中。
只有要改为本地图像后端时才需要启动；当前用户恢复并使用的是阿里云 API。

在确认路径及 GPU4／5 空闲后，分别在保持 stdin 的终端／受控客户端中启动：

```bash
cd /data02/usr/wangqihao/Demo/research
HF_HUB_OFFLINE=1 /data02/usr/wangqihao/miniconda3/envs/h3/bin/python -u \
  -m src.agentic_video.asset_studio.gen_worker t2i 4 \
  /home/wangqihao/.cache/huggingface/hub/models--Qwen--Qwen-Image/snapshots/75e0b4be04f60ec59a75f475837eced720f823b6
```

```bash
cd /data02/usr/wangqihao/Demo/research
HF_HUB_OFFLINE=1 /data02/usr/wangqihao/miniconda3/envs/h3/bin/python -u \
  -m src.agentic_video.asset_studio.gen_worker edit 5 \
  /home/wangqihao/.cache/huggingface/hub/models--Qwen--Qwen-Image-Edit-2511/snapshots/6f3ccc0b56e431dc6a0c2b2039706d7d26f22cb9
```

参数形式是 `<role> <gpu> [model_path]`，worker 自行设置 CUDA_VISIBLE_DEVICES。
输出 loading／ready 事件；stdin 每行 JSON 任务，stdout 每行 JSON 结果。
通用任务字段：id、prompt、width、height、seed、out_path；改图额外 `reference_path`。
关闭协议：`{"cmd":"shutdown"}`。不能关闭其他任务所属 worker。

代码实际调用：QwenImagePipeline／QwenImageEditPlusPipeline，BF16、enable_model_cpu_offload，
默认文生图50步、改图40步、true_cfg_scale=4.0；参考图经 PIL 加载后传 `image=[PIL.Image]`。
旧 worker **只接一张 reference_path**；当前云端主线最多多张参考图，不能声称旧worker已经等价支持多图。
旧 `asset_studio.backends` 提供 `_GenWorkerClient`、AssetImageT2IBackend、AssetImageEditBackend，
管理加载超时、请求超时及 stderr。不要直接nohup丢弃stdin，也不要手工编资产prompt作为自动链的运行输入。

## 11. 如果新窗口要将当前主线换成服务器模型

这项工作未完成，本次也未实施。先向用户确认要继续 API 路线还是切换本地；
仅有本交接不代表获得了模型运行或后端重构授权。

切换时先读上述旧 transport／worker／H3 bridge，但**不要运行旧整体创作流程**。
当前公开 CLI 没有本地后端开关，`QwenAPI`、`AliyunImages`、`MiniMaxH3` 仍是默认对象。
已有依赖注入接口可测试适配，但不能把它说成已有可执行的服务器全链。

必须保留当前创作／审核提示词、reference_transfer、逐段状态、真实素材观察、Omni剪辑决定，
仅在工具层对齐返回格式、任务身份、实际媒体和后端配置 SHA。
若真的改变后端，另记明确的来源变更，不能把新模型输出嫁接到旧缓存或重置失败视频预算。
服务器没有实时验证、GPU未知、原女生run素材未齐这三件事都要如实说明。

## 12. 给新窗口的首条消息（可以原样复制）

```text
请先完整阅读 C:\Users\29785\Desktop\omni-autonomous-screenplay\HANDOFF.md，
再按第3节顺序阅读当前代码和真实产物。当前主线是这个独立仓库，不是旧douyin研究workflow。
我的最终目标是只输入参考视频，由Omni自主完成故事、资产、素材和剪辑，不要中途人工写故事或选片。
最近只要求整理代码、离线测试及交接；请先确认现状，不自动付费生成、不启动服务器模型或定时任务。
服务器权重和历史启动协议在交接第6–10节；当前独立CLI还没接本地模型，不能只换baseurl冒充接入完成。
先告诉我你核对到的主线状态和下一步，等待我明确选择运行范围。
```

## 13. 交接完成核对

- 本文的源码和本地引用文件已核对；服务器绝对路径保留为历史证据，未声明实时存在。
- 运行代码基线为03fd066；本文只新增文档，不改Python、prompt、旧run或现有模型结果。
- 本次没有读取／写入API密钥，没有SSH、GPU占用、模型加载、生成或任务重发。
- 没有新建goal、自动化或后台监控；新窗口不应从聊天历史自动恢复这些任务。

## 2026-10-04 再追加：实际精看中的 ASR 工程修复

本节追加当前素材库实施事实，不修改上方旧交接、模型原回复或旧缓存。
首个实际 fine 调用 `glm_014` 已收到；第二个原片 1,260–1,350 秒窗口的 Whisper
包含两个零时长词（局部 `0→0`、`89.97→89.97`），首次过严 word 校验导致阻断。
新的 `asr-time-validation-v2` 先保存每次原始推理 `raw_asr_NNN.json` 与 SHA，
零时长词保留 text/raw/alignment_issue，但不给可用 source 时间；有效 segment 仍保留。
严重越界、非有限时间等不作可用证据；已有参考、首 fine 的 ASR 缓存字节未变。
该真实本地窗口复验有 46 个有效段。

可选 ASR 的纯对齐失败单独记录为无可用语音证据，随后继续视觉精看；
源文件／缓存完整性失败仍 fatal，不能自动伪造时间或绕过 SHA 检查。
入口另支持参考达到／超过 8 MB 时生成目标 7.5 MB 的全时长分析 proxy，
完整参考 MP4 和原 reference SHA 均保留，代理另存 lineage，不静默改换创作输入。

修复后媒体与 execute 相关检查 38 项通过；新环境全量 227 通过、2 跳过，
因为 `.venv-library` 未安装冻结发现线的 browser_use/requests；原 `.venv` 中两组
discovery 另有 28 项通过，补齐该依赖范围。不要把重叠测试数量相加为独立总数。
当前实际 4 个 fine 窗口已完成、第 5 个在途，仍没有素材库成片或最终审核结果。

## 2026-10-04 追加：首轮素材库闭环完成

上面的在途描述是实施历史。当前同一 `runs/library_reference_20261004/` 已完成 16 个连续窗口、两版渲染与模型选择。
选择 round 0，`render_0/final.mp4` 约 77 秒，SHA `e6d10d908d8ae508a3df9a36f8630e0b5161e838e8302414d4019bd9255b4111`。
首次评分保留不变；选中版本按最终明确的主旨/剪法规则另审，`selected_review_v2_0.json` 为 partial/partial/pass。
`result.json` 状态是 `library_candidate_with_limitations`。旧主体身份混淆的 fail 与选片理由不是最终质量通过，不能直接比较不同协议评分。

43/80 请求（42 received、004 uncertain），没有重放不明输入或重置预算。第二轮补看没有进入最终 EDL，两版都用了首轮相同的三个窗口，不能声称补看带来提升。
MCP 桥已在所有在途调用结束后关闭；停止连接后的实际缓存重跑没有新增请求，最终 SHA 未变。新未缓存工作在 stop 标记存在时会直接阻断；显式重连只清 stop 标记，不清记录。
本轮有工程和提示协议修复，自动创作由 GLM 完成，但不是冻结线零人工干预验收；未调用 Omni/Qwen、MiniMax、生图或 FlashVID。
最新全量 241 passed、2 skipped，停止连接保护另有 2 项定向通过；原环境发现相关 28 项此前通过，不相加夸大测试总量。
完整事实、费用边界、媒体与限制见 [首轮运行报告](docs/REFERENCE_LIBRARY_FIRST_RUN_20261004.md)。下一轮不得删除这次记录或换目录重置预算。
# 2026-10-06 最新停止状态

双父视频逐slot精剪的实际总数166，没有新精剪成片。最新166的34秒父版素材反馈
有原始HTTP请求，无捕获回复，原CLI/MCP进程已结束；已记录原submitted→uncertain
和`sf_unknown_166_observation_v1`，不得重放或伪造结果。004/131也仍uncertain。
77秒父版最后提案的2.37s/3s曝光短缺已允许在首次组装中由GLM修正，但组装尚未提交。
Goal仍暂停，mcp_stop存在；当前不是后台继续运行。读AGENTS末尾及
docs/SLOT_FINECUT_IMPLEMENTATION_20261005.md的166追加记录。性能修复18项定向测试通过。
此前旧阶段保留为历史，不能当作最新成功状态。

# 2026-10-06 独立恢复（后于166停止记录）

已记录sf_independent_slot_recovery_v1，004/131/166继续冻结。真实167/168并行，
168父77秒组装首次通过，16个切片预计67.558秒，等待独立源片与实际输出审核。
167和唯一169局部修正失败，父34秒result_independent_resume.json为stopped_protocol_failure；
无新渲染，不再调用这个已耗尽阶段。Goal仍暂停。
77首源片请求在本地ledger提交os.replace WinError5时失败，实际未发送/未登记170。
sf_pre_submission_write_recovery_v1保存并归档未发送request；Windows原子替换已加有界
短重试，7项故障测试通过。全部在途结算后重连，继续77秒版原进度。没有费用重试或预算重置。
后续实际调用、成片与全字节审计须继续追加到SLOT_FINECUT_IMPLEMENTATION_20261005.md。

# 2026-10-06 最新191：实际双路测试停止

77版16源片facts已全部完成；前三段合法claims比较5 supported/4 partial/1 unsupported，
支持均为角色出场。第四段190缺limitations、唯一191缺visual_outcome-typed证据而失败，
虽引用了直接visual_action画面事实；不得误称只靠文字/推断。两路result_independent_resume.json
均stopped_protocol_failure，无任何新render/final.mp4，没有成片盲读/精炼/目标审核。
当前总191（167–191新增25），004/131/166仍uncertain，无在途。CLI/官方MCP已停止，Goal仍暂停。
阅读docs/SLOT_FINECUT_EVIDENCE_DIAGNOSTICS_20261006.md区分代码/合同误拒绝与未证实的叙事缺口。
1,833整文件+原166日志前缀全字节SHA核验通过，收据eb5f39...17602.json。
不自动开始新规划、补旧parsed、重放未知或对169/191发第三次修复。

# 2026-10-06 最新194：用户质疑无效循环后全部停止

事实驱动新协议代码及合成闭环通过，但真实192/193都因重复focus window绑定失败，
34父版唯一194修正又因角色重叠事件证据失败；没有新影片。
用户质疑反复循环后设置mcp_stop：193已返回，不再给它发修复。
新路径fact_grounded_v1下父0 stopped_execution、父3 stopped_protocol_failure。
当前194均已结算，无在途，004/131/166仍不明；CLI和官方MCP均结束，Goal仍暂停。
不继续全链重剪、加救援政策或启动小实验，除非用户新指令。
读docs/FACT_GROUNDED_FINECUT_20261006.md；不要把合成测试当作真实剪辑完成。

# 2026-10-06 最新207：直接精剪实际完成并收尾

sf_parent_timeline_finecut_v1在194基线注册后，两路真实父片slot裁片并行观察；
9次逐段决策、各一次渲染和两次实际输出审核，共13次新请求，没有格式修复或额外重规划。
render_3输出31秒、7段，1.5x局部加速与1秒尾帧停留，没有慢放；
render_0输出76.2秒、19段，有0.5/0.8x慢放、1.2/1.5/2x加速与合计6秒停留。
两路result及comparison已完成，旧194停止状态保持为历史。没有进一步质量修改或新模型调用。

31秒的s2模型partial，但关于9–14秒“激烈快切”的具体动作描述与实际11秒托举、15秒站立
抽帧不一致。身份／授予候选父24–27只选24–26，输出22–24共2秒；未证明解决看不清问题。
76.2秒的s1/s2加长到20/16秒，整体仅缩短约1.17秒；模型pass不等于短小精悍达标。
父片字幕与静音尾部保留，音乐节拍未核验。保留独立抽帧反证，不回填人工剪辑答案。
