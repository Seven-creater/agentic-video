# 从现有成片逐段精剪

版本 `slot_finecut_v1`。这是项目的通用知识卡，既不是官方 GLM skill，也不证明模型已经掌握专业剪辑。没有参考剧情、指定时间或预设电影镜头。

## 先保持段落关系，再改段内实现

自主从父视频划分连续 slots，写明每段观众必须理解的信息、进入状态、离开状态和前后呼应。父视频可以是较长粗剪，也可以是已有精剪候选；不因视频长度给出语义通过判定。段落数由观察决定。若相邻人物、目标、空间或因果关系不清，保留问题，不能用预想剧情补足。

实际父输出的中性事实使用 slot 局部输出秒数。动作、形态、状态、结果、身份和反应均可成为候选高光，但必须区分画面、可见文字、推断。字幕不能证明画面中不存在的动作。父输出事实只说明当前视频呈现了什么，不能证明延伸的原电影片段。

## 按信息价值选择技巧

- **省略与跳时蒙太奇**：只留下必要的准备、关键变化和可见结果，跳过可自然补全的重复过程；每次跳时后重新检查人物状态与行动逻辑。一个 slot 可由多个不连续源片区间组成。
- **动作高光**：选择有辨识度的姿势、动作变化或接触结果。更晚进入、更早退出要有依据，不能只留下动作启动而删掉结果，也不能把单帧形态当完整因果动作。
- **反应与结果镜头**：表情、旁观者反应和行动后的状态可以补足意义，但只有与前一行动关系明确时才形成因果表达；相似表情不自动代表相同情绪或结果。
- **匹配与呼应**：相似构图、动作方向、形态或前后对照可帮助衔接。检查相似形式是否真的服务当前内容，不能把两个无关动作拼接后声称连续发生。
- **加速与慢放**：重复过程可加速，需要辨认的动作或形态可放慢，也可保持原速。当前执行器仅支持每段恒速 0.5–2；多个恒速区间不等于平滑速度坡道。普通帧率慢放可能重复帧，不能冒称生成了真实高帧率动作。
- **停留与定格**：辨认人物、指向关系、结果或身份变化可能需要延长，而不是继续压缩。尾帧保持仅适用于必要信息在末帧仍存在；定格不会补出缺失动作，早先已经消失的信息不能获益。

## 不设统一秒数或统一缩短目标

每个必要信息区间单独估计观看时间：谁在画面里、画面是否复杂、动作是否清楚、是否需要理解多人关系。检查变速后的区间曝光，而不只检查整个镜头或整段总时长。身份/指认等信息可以比动作高光停留更久；短不自动等于精悍，长不自动等于冗余。`min_readable_s` 是模型提案估计，不能当成人类可读性实验的结果。

源片选择使用原电影秒数，严格绑定 SHA 与同一已看 window；每个连续区间必须落入单个 usable range，人物 role 集合也来自同一行，不能跨范围拼凑。可以提出在同窗口内扩展原片范围，但渲染前仍须完成独立 exact-source 事实与主张核验。父片高光证据不替代该核验。

实际成片先隐藏意图静音盲读，再检查每段新增信息、必要停留、前后关系和参考主旨。分别记录画面理解、文字辅助与不确定推断；局部候选自报 `preserved` 只是待验证声明，不是质量通过。局部最优还需全片相邻衔接检查。

## 三个独立阶段的 JSON 根对象

下面是**虚构输入的协议示例**，不是本项目参考或电影的剪点。实际绑定值、段落数、候选数、信息和时间均由当前输入与模型观察产生。每个阶段只返回其自己的完整根对象，不套入额外的 `result` 或 `plan`，不复制示例剧情。

划分父输出：

```json
{
  "baseline_id": "synthetic_parent",
  "parent_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "slots": [
    {
      "slot_id": "s1",
      "start_s": 0,
      "end_s": 10,
      "intended_takeaway": "人物状态发生变化且留下可见结果",
      "entry_state": "进入时的实际状态",
      "exit_state": "离开时新增的实际状态",
      "link_to_previous": "开篇",
      "link_to_next": "收束"
    }
  ],
  "limitations": [],
  "uncertainties": []
}
```

中性观察实际父输出片段；所有证据时间从提供片段的 0 秒开始。`kind` 仅取 `action`、`state`、`identity`、`result`、`reaction`、`shape`；`basis` 仅取 `visual`、`visible_text`、`inference`。此阶段不得放入期待主旨、原电影时间或原电影 SHA：

```json
{
  "baseline_id": "synthetic_parent",
  "parent_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "slot_id": "s1",
  "slot_start_s": 0,
  "slot_end_s": 10,
  "time_domain": "slot_local_output",
  "evidence": [
    {
      "evidence_id": "e1",
      "start_s": 1,
      "end_s": 3,
      "observed_fact": "画面中的一个人物抬起手臂",
      "kind": "action",
      "basis": "visual"
    }
  ],
  "limitations": [],
  "uncertainties": []
}
```

局部精剪提案；原片秒数与父输出证据的局部秒数不能混用。候选最多 3 个，每个候选可有多个操作。`parent_segment_index` 是父 `provenance` 列表的零基序号，含其原片 SHA、window 和不可跨行合并的人物 role 集合。`meaning_status` 仅取 `preserved` 或 `unresolved`；后者必须报告局限：

```json
{
  "baseline_id": "synthetic_parent",
  "parent_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "slot_id": "s1",
  "candidates": [
    {
      "candidate_id": "c1",
      "operations": [
        {
          "parent_segment_index": 0,
          "source_in_s": 102,
          "source_out_s": 106,
          "speed": 1,
          "freeze_tail_s": 0,
          "evidence_ids": ["e1"],
          "reason": "保留可以辨认的状态变化",
          "essential_intervals": [
            {
              "source_start_s": 102,
              "source_end_s": 106,
              "min_readable_s": 2,
              "information": "需要看清的手臂状态变化",
              "evidence_ids": ["e1"],
              "continues_in_tail_frame": false
            }
          ]
        }
      ],
      "meaning_status": "preserved",
      "rationale": "局部候选理由；仍需原片与成片核验",
      "limitations": []
    }
  ]
}
```

## 研究依据及适用边界

- [Write-A-Video, ACM TOG 2019](https://cg.cs.tsinghua.edu.cn/papers/TOG-2019-Write-a-Video.pdf)：联合考虑镜头、切点与段落成本；本卡借用信息需求下的局部选择思路，没有复现其优化器，也不把论文示例时长当通用最低观看时间。
- [DIRECT, 2026 预印本](https://arxiv.org/abs/2604.04875)：全局意图与局部检索、动态裁切和反馈；本路线以已有成片 slots 为局部精剪对象，并非原方法的质量复现。
- [Magliano & Zacks, 2011](https://doi.org/10.1111/j.1551-6709.2011.01202.x)：连续性剪辑与事件理解；用于提醒省略后动作和事件关系仍需可理解，不能直接给出一律适用的自动切点。
- [Cutting & Armstrong, 2016](https://link.springer.com/article/10.3758/s13414-015-1003-5)：镜头尺寸、画面复杂性与观看时间相关；不提供本任务每个身份镜头的可靠固定阈值。
- [Nonlinear Video Editing Transfer, 2021](https://arxiv.org/abs/2105.06988)：参考编辑与速度迁移需要有依据的操作描述；当前恒速片段方案不能宣称完整复现速度曲线。
