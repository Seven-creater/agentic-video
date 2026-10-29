本目录逐份复制自只读证据：
`/Users/Admin/Desktop/agentic_video_all/.agent-team/out/e2e/evidence_01_不烧心_2.txt`。
证据文件 SHA-256：`1bca2559d0c0390e12ae3fc5e3f950b8fbd1510eb39b18581f0f56f5334630d8`。
JSON 只重新排版，字段和值没有修改。

- `glm_171_chain_e2e_v1_fine_revise.json`：真实修订回复，首段由 2.0 秒扩至 2.2 秒；
  `reason/limitations` 记录首句同文本、同音色 received TTS 实测 2.08 秒。
- `glm_173_chain_e2e_v1_fine_review_r.json`：真实目标审核回复，`partial/deliver`，
  明确说明声音修复尚未实际听审。它不构成终审跳切已修复的证据。
- `run_outcome.json`：真实退出记录；保留弃句原因和三条实际测量窗口。

`test_e2e_timing_recovery.py` 用这些字段重建最小粗剪上下文。证据中未提供完整粗剪
story_context 与 waveform 文件，因此测试在临时目录创建固定音频占位文件、模拟测量采样；
这些不是原始媒体，也不代表重新生成或听审了成片。第二组几何相同的用例更换标识及文本，
验证恢复判据不依赖模板、角色或台词。真实回复夹具始终保持原样。
