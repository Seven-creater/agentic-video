# INC-20261029-09 parent_baseline 读父任务未知 qfs 调用时 KeyError 'arguments',子任务起不来

| 项 | 内容 |
|---|---|
| 发现日期 | 2026-10-29 |
| 影响范围 | 嵌套评测(`--parent-task`)的基线计算;致命停机——子任务启动前崩溃 |
| 状态 | 已修复(a5d692d) |
| 相关提交 | a5d692d |
| 相关任务 | 真实停机:父任务含未知 `qfs_close_25` 调用(glm_168_qfs_close_25,status uncertain,请求无 arguments 键) |

## 1. 问题长什么样
父任务的调用账本里有一条未知的 qfs 调用;`parent_baseline` 逐条读取父任务历史请求计算基线时,直接取 `request['arguments']`,而 qfs 直连请求没有该键——`KeyError: 'arguments'`,子任务在启动前崩溃。

## 2. 怎么查出来的
崩溃栈定位到 server_cli.py 的 parent_baseline;对照 qfs 请求结构(provider `qfs_direct_https_v1`,payload 直连)确认无 arguments 字段。

## 3. 根因
代码问题(容错缺失):基线计算假设所有调用都是"绑定媒体的工具调用",没有为免绑定的 qfs 直连请求留分支;与 INC-20261029-08 是同一类"qfs 调用与账本假设不匹配"的问题。

## 4. 解决方案(a5d692d)
`parent_baseline` 用 `LibraryState._qfs_direct` 识别免费 qfs_* 直连调用(名称前缀+请求结构):这类调用没有要排除的绑定媒体、也永不重发,直接跳过;call 名称取值加安全处理。其余调用照旧逐条校验(request sha、媒体 lineage)。
通用性:按调用类型分支,与任务内容无关。

## 5. 为什么不用其他方案
- 给 qfs 请求补一个空的 arguments 键迁就读取方:污染请求记录语义。其余备选未讨论。

## 6. 怎么验收
新增 `test_parent_baseline_skips_unknown_free_qfs_calls`(test_server_cli.py:354-369):构造含未知 qfs_close_25 的父账本,断言基线正常产出且 `unknown_inputs` 为空。专项验收输出未单独归档。

## 7. 量化数据
未测量(一次性崩溃;修复后该路径 0 复现)。

## 8. 经验与后续
- 引入新调用类型(qfs 直连)时,所有遍历调用历史的代码(parent_baseline、ledger、replay)都要过一遍兼容性;INC-20261029-08/09 是同一盲区的两处。
