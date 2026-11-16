# INC-20261116-03 同一窗口重看后原声核对换了问题，被"禁止重放"保护拦停（狂飙吃鱼）

| 项 | 内容 |
|---|---|
| 发现日期 | 2026-11-16 |
| 影响范围 | scene_v1 素材发现的原声说话人核对（`scene_casting.verify_source_speakers`），凡是会重看同一窗口的任务都可能触发；致命停机 |
| 状态 | 已修复（release scene_v1_schedule_63bb273df233），17_ 已断点续跑，正在验证 |
| 相关提交 | 未提交（夜跑本地源码 fast_source） |
| 相关任务 | 17_狂飙吃鱼_01_79c33eac598f：原任务 fast_template_20261115_193148_…，续跑任务 fast_template_20261115_220913_… |

## 1. 问题长什么样
`AssertionError:creative_stopped_before_usable_draft:audio_review:request_binding_changed_no_replay`。抛出位置是 `audio_review.py:190`，最后一次调用是 `glm_106_material_scene_discovery_observe_6`。停机前一共做了 106 次调用、13 次修复。

## 2. 怎么查出来的
- 值守脚本取证（`night_diag/17__1116_0602.txt`），GLM 写了诊断（`night_diag/17__1116_0602_glm.md`）。
- Claude 到服务器核实（只读）：
  - observe_6 发出的视频是 `browser/blind_media/703e75289b66db0b529f/window.mp4`，和 observe_2 是同一个窗口；
  - `source_speakers/<窗口>/review/` 下已有一次 `status=received` 的核对记录，提示词末尾的 `people` 表是第一次看片时确认的人物；
  - 第二次看片经过身份拆分（identitypair_16 判定为不同人物）后，确认的人物集合变了。
- 定位：`scene_casting.py` 的 `verify_source_speakers` 把核对证据固定写在 `<窗口>/review` 目录下。人物表一变，提示词就变，请求指纹也跟着变，`review_audio_video` 于是判定为"同一个目录、请求被改动"并停机。

## 3. 根因
付费证据目录只按窗口编号命名，没有按请求内容命名。"重看窗口后对同一段媒体提一个新问题"本来是合法流程（INC-20261115-09、INC-20261115-10 修通之后才第一次走到这里），却被当成了篡改已付费证据。属于代码问题：复用键设计错误。

## 4. 解决方案
在 `scene_casting.py` 新增 `source_review_dir(base, prompt)`，按问题内容选目录：
- `review` 目录还没有记录，或里面记录的就是同一个问题：照旧用 `review`，已付费的回复直接复用，旧任务和续跑的路径不变；
- 记录的是另一个问题：改用 `review_<问题哈希前16位>`，作为新调用。旧证据不动，同一个问题再问时也会稳定落到这个目录；
- 记录损坏（state 文件在但 request 读不出来）：仍然用 `review`，让原有的禁止重放保护照常停机，不绕开这道检查。

本次运行和从上一次运行（父账本）复用这两种情况，都改为用这个函数找目录。目录键只取决于问题内容，不含模板名、角色名或窗口特判，对任何模板都一样成立。这与仓库里已有的 `source_voice_review.py:71` 按内容编址是同一做法。

## 5. 为什么不用其他方案
- GLM 首选方案：把按内容编址下沉到 `review_audio_video`，覆盖所有调用方。改动面太大：audio_segments、hot_capture、reference_pair 等所有调用方的路径都会变，而夜里只有这一处被触发过。记为后续项。
- 删除旧记录后在原目录重发：破坏"已付费证据不可变"的规则，还可能重复计费。
- 把人物表冻结成第一次的版本：等于拿过时的人物表去核对新的看片结果。
- 把这道保护降级为警告：会失去防篡改检查。
- 换候选时不再重看已看过的窗口：会改变看片行为，INC-09 已经否决过这个方案。

## 6. 怎么验收
- 新增测试 `test_source_review_dir.py`，共 4 项：
  - 第一次提问使用 `review`；
  - 同一个问题复用 `review`；
  - 新问题进入稳定的新目录，且旧记录不变；
  - 记录损坏时仍落在受保护的目录。
- 服务器全量测试 173 项全部通过。
- 17_ 已在 63bb273df233 上断点续跑，结果待补。

## 7. 量化数据
- 修复前：任务停在第 106 次调用，之前 13 次修复白费，没有成片。
- 修复后：需要 1 次新的原声核对调用（Qwen 付费），其余已收到的调用按账本复用。实际次数待续跑结束后补记。

## 8. 经验与后续
- 凡是会重做的付费阶段，证据目录都应该按请求内容编址，不能按实体编址。
- 后续项：`action_dialogue.py` 的 `source_review_{attempt}`、`visual_audio.py` 的 `av_review_v{version}` 等按序号或版本编址的位置，有同类风险（GLM 指出，尚未触发）。等夜跑结束后统一评估，看是否把按内容编址下沉到 `review_audio_video`。
