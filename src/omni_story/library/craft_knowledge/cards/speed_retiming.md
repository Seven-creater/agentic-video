# 慢放、加速与速度变化

**术语**：恒定倍速与随时间变化的速度曲线不同。[Adobe](https://helpx.adobe.com/ae_en/premiere/desktop/edit-projects/change-clip-speed/change-clip-speed-and-duration-using-time-remapping.html) 剪辑识别和作用解释也需分开。[VEU-Bench](https://arxiv.org/html/2504.17828v1)

**项目观察**：局部复看连续运动，考虑自然缓慢动作、动画、低帧率采样和代理播放变化等替代解释。参考准确倍速未知时保持未知。
**作用**：强调可辨瞬间或缩短重复过程；不是每段都要变速。
**素材条件**：动作阶段与结果清楚，慢放不会暴露明显掉帧，加速后必要信息仍可辨。
**实际操作**：每区间自主设置 `speed` 0.5–2。不同恒速区间可相接，但必须标明“分段恒速”；平滑坡道和光流补帧 unavailable，不静默替代。
**检查**：核验实际源／输出时间关系与主观可读性；参数符合不等于参考速度已被复原。需要更精确执行时记录能力缺项。
