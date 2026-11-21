# INC-20261121-02 服务器只剩 6.1 GiB 贴近开跑准入线；功夫熊猫素材库里藏着每次启动必校验的文件

| 项 | 内容 |
|---|---|
| 发现日期 | 2026-11-21 |
| 影响范围 | 服务器运维；运维阻塞（剩余空间 < 5.6 GiB 时 `launch_reviewed_template.py` 拒绝开跑）。若整目录删除功夫熊猫素材库，所有新任务会在 `load_history` 处停机 |
| 状态 | 已缓解：按用户授权删除功夫熊猫素材库和过期缓存，剩余空间 6.05 → 8.69 GiB；启动依赖文件保留在原位 |
| 相关提交 | 无（服务器数据清理，未改代码） |
| 相关任务 | 收据 `baselines/approved_chiikawa_dialogue_20261114_v1/disk_cleanup_authorized_20261121_194146.json` |

## 1. 问题长什么样
- `df`：`/` 已用 91%，剩余 6.05 GiB。11-15 清理后是 20.8 GiB。
- 准入线：`launch_reviewed_template.py` 远端代码 `assert shutil.disk_usage(h).free>5.6*2**30`；运行底线 `OMNI_MIN_FREE_BYTES=5 GiB`。

## 2. 怎么查出来的
1. `du` 分目录：`trials` 23 GB、`shared/data` 13 GB、`shared/jobs` 4.5 GB、`shared/template_hunter` 2.8 GB、`releases` 2.0 GB（大多是硬链接）、`~/.cache` 1.5 GB。
2. 确认功夫熊猫相关目录：`visual_library/three_films_v1`（24 MB）、`three_films_v2`（1.7 GB，`cast.json` 为阿宝、师父等）、`shared/data/shot_cache_kfp_v1`（86 MB）、`shared/data/material_index_v1`（106 MB，`manifest.json` 的 `library_root` 指向 three_films_v2）。
3. 查引用：现行素材档案 `chiikawa_v2/.../production_profile.json` 只绑定 chiikawa 一个片源；`material_profile.apply_cli` 用档案覆盖 `library`/`visual_library`/`material_index`；chiikawa 索引里没有功夫熊猫路径（命中的只是 schema 名 `material_index_v1`）；11-14 以后 18 个 `fast_template` 任务的锁定配置都不引用功夫熊猫库。
4. **发现隐藏依赖**：`server.json` 的 `history_file` 指向 `visual_library/three_films_v2/provenance/runtime-history.json` 并带 `history_sha256`；`server_cli.py:92 load_history()` 在 `start` 和 `_run_task` 都会调用，文件缺失或哈希变化即停机。
5. `~/.cache/omni-opencode`（1.3 GB、891 个目录）：`opencode_provider.py:340`、`pipeline.py:1656` 只把它设为每个任务的 `XDG_DATA_HOME`（opencode SQLite，避开锁冲突），代码从不回读。

## 3. 根因
运维问题：多次续跑和实验累积占用；11-15 后没有再清理。隐藏依赖是配置问题：服务器历史记录文件被放在一个素材库的 `provenance` 目录里，素材库名义上可删，实际上是启动前提。

## 4. 解决方案
- 删除 `three_films_v1`、`shot_cache_kfp_v1`、`material_index_v1`，以及 `three_films_v2` 下除 `provenance/`（41 MB）以外的全部内容，共 1.80 GiB。
- 删除 2 天内无改动的 `~/.cache/omni-opencode` 目录 542 个，共 0.81 GiB。
- 删除前检查无 `omni-*` 任务运行、历史文件哈希一致；删除后再次核对哈希；每个删除路径、字节数和关键文件 SHA 写入收据。

## 5. 为什么不用其他方案
- 整目录删 `three_films_v2`：会删掉 `runtime-history.json`，所有新任务启动即停。
- 迁走历史文件并改 `server.json`：多改一处持久配置，收益只有 41 MB。
- 清 `trials`、`shared/jobs`：里面有祖先任务媒体和历史交付，续跑会校验祖先媒体 SHA，未经逐项确认不动。

## 6. 怎么验收
- 清理后 `three_films_v2` 只剩 `provenance`，`runtime-history.json` 哈希与 `server.json` 一致。
- 新发布 `980bf32d3381` 的《不烧心》从零任务通过准入并正常启动（worker 加载 chiikawa_v2 档案）。

## 7. 量化数据
- 剩余空间：6.05 GiB → 8.69 GiB（+2.64 GiB）。
- 删除：功夫熊猫 1.80 GiB；opencode 缓存 542 个目录 0.81 GiB。

## 8. 经验与后续
- 每次从零跑约增加数百 MB 到 1 GB 以上（推断，《立刻有》任务链 12 次续跑共 1.6 GB），8.69 GiB 大约只够 3～5 轮。下一步可选：清今天 8 次失败的《华强买瓜》试验目录（约 1–2 GB，需确认），或在云控制台扩容。
- 建议把 `history_file` 迁到 `shared/` 下的专用目录，避免以后清素材库时误删。
