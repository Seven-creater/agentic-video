# INC-20261029-03 question_first 预检把清单写进任务输出目录,start 拒绝启动

| 项 | 内容 |
|---|---|
| 发现日期 | 2026-10-29 |
| 影响范围 | `--material-search question_first` 的启动预检;运维阻塞——任务在模型调用前即被拒 |
| 状态 | 已修复(5ddde00) |
| 相关提交 | 5ddde00 |
| 相关任务 | 未记录(首次触发该报错的具体任务目录未归档) |

## 1. 问题长什么样
启动即报 `output must be a new or empty directory`:预检阶段(prepare)把素材清单 catalog 写到了 `--output` 目录下,而 start 要求输出目录为新建或空目录,自检产物把自己挡在门外。

## 2. 怎么查出来的
报错信息指向输出目录非空;核对预检写入路径(`server_cli.py` 的 configure_question_first)确认 catalog 落在 `Path(args.output)/'catalog'`。

## 3. 根因
代码问题(流程分支):预检产物写错了位置——写入目标与"输出目录必须为空"的启动约束冲突。

## 4. 解决方案
预检改写临时目录:`server_cli.py:571-572` 用 `tempfile.TemporaryDirectory(prefix='qfs_preflight_')` 作 scratch,catalog 写到 scratch 下;真正运行时的写入路径(395 行)不变。改动 4 行。
通用性:与任务内容无关,纯路径修正。

## 5. 为什么不用其他方案
- 允许输出目录里存在 catalog(放宽 start 约束):会削弱"新任务从干净目录开始"的既有保证。其余备选未讨论。

## 6. 怎么验收
提交内改动(server_cli.py +4/-1);专项验收输出未单独归档(修复后该报错在后续 e2e 运行中未再出现,为间接验证)。

## 7. 量化数据
未测量(一次性启动失败;修复后 0 复现)。

## 8. 经验与后续
- 自检/预检类产物一律写临时目录,任何"写进输出目录"的辅助文件都会与空目录约束打架。
