# 认可线路（scene_v1 快速线）运维脚本

本目录对应服务器 release `scene_v1_schedule_206c0d1710a6`（2026-11-15）。`src/` 是该 release 的完整源码（不含 vendor）。

- `launch_reviewed_template.py <模板前缀> [--resume]`：按模板前缀启动或断点续跑一条创作任务（串行，单 worker，5 GiB 磁盘底线）。
- `fast_template_controller.py`：服务器端控制器，负责创作 → 快排渲染的自动衔接。
- `quick_preview_runner.py`：按真实配音时长排片和渲染；自动终审默认关闭（`auto_review_off`）。
- `deploy_schedule_fix.py`：从本地源码生成按内容哈希命名的 release，并在服务器上跑 `tests/`。
- `fetch_verified_output.py`：取回成片，做完整解码、SHA 核对，并生成逐句截图。
- `tests/`：部署时在服务器上运行的测试（152 项）。
- `manifests/`：release 的逐文件指纹。

问题记录见 `docs/incidents/`，线路说明见 `docs/approved_line/`。
