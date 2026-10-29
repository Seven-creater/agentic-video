# INC-20261029-05 新路线字幕遮挡出现白框:让 GLM 逐窗判断遮罩不可靠

| 项 | 内容 |
|---|---|
| 发现日期 | 2026-10-29 |
| 影响范围 | question_first 路线的字幕遮挡;错误成片(白框、漏遮、遮错范围) |
| 状态 | 已修复(1013f88) |
| 相关提交 | 1013f88 |
| 相关任务 | 报告 `wt/pfix/reports/f1_mask_line_window.md`;审查 `out/F2_review.*`、`out/F4_review.*`;黑边证据 `full_tpl_kuangbiao_fish_20261026_v1/watched_windows.json` |

## 1. 问题长什么样
13 个窗口的遮挡参数由 GLM 逐窗判断:返回的颜色有 #FFFFFF(白框)、有只遮部分的、也有不遮的(4 个窗口无遮挡)。烧录在源片内嵌字幕上的新字幕遮挡效果不可控。

## 2. 怎么查出来的
逐窗核对 GLM 返回的遮罩参数(颜色/几何/有无),与片源实际黑边位置对照;黑边几何证据取自 library 原生帧记录(watched_windows.json:native row 950/1080)。

## 3. 根因
代码问题(把确定性几何交给概率判断):源片底部有稳定的水平黑边,遮挡位置是可测量的物理量;交给 GLM 逐窗判断既引入幻觉(白色、漏遮)又浪费调用。

## 4. 解决方案(1013f88,f1 报告)
- question_first 窗口一律确定性整条底部黑边、纯黑(#000000)、全宽遮罩,不再请求模型生成遮罩;library 模式遮罩路径保持原行为。
- 三部 KFP 用已验证值(按完整 SHA 绑定,原生 1920×1080、picture bottom 950):遮罩 x=0、y=0.8796296296296297、w=1、h=0.12037037037037036。
- 新片逐列测量取中位数(方法 `ffmpeg_gray_column_median_v2`):内容范围内 9 个时间点、每点 12 张原生灰度帧;全宽每列找最后一个亮度>24 的像素下缘,先跨帧取该列最深下缘、再取所有列的中位数为 picture bottom;记录采样、列 p25/median/p75、IQR、阈值并按影片 SHA 缓存(旧 side-strips 记录不复用为 v2)。
- 抗角标:用列离散度 IQR=p75−p25,超过帧高 2% 抛 `question_first:subtitle_bar_column_spread_too_large`;黑边<5% 或无可识别画面抛 `question_first:subtitle_bar_below_five_percent_or_no_picture`;缓存几何同样查 5% 下限。合成测试含 200×100、真实 bottom 88、右下 20% 宽水印深入第 98 行的干扰用例(遮罩仍从 0.88 起)。
- 服务器脚本 `scripts/check_subtitle_bars.py`:`--shot-cache`、`--film`、`--measure`、`--write-cache`。
通用性:几何由测量决定,新片自动适配;与模板无关。

## 5. 为什么不用其他方案
- 继续让 GLM 逐窗判断:已被实际输出证伪(白框/部分遮/不遮并存)。
- 左右边缘条带取空间最大值(旧 side-strips 法):对角标/水印不稳健,角标会拉高结果;改为全宽列中位数+IQR 离群剔除。
- 已知局限如实声明:该方法假设稳定水平黑边、多数列可见真实画面边缘;≥25% 宽的角标是离群列不影响中位数,但大面积一致覆盖可能避开 IQR 检查,需人工核验——不当成任意水印/画内字幕定位器。

## 6. 怎么验收(f1 报告实际输出)
定向测试(含新文件 28 项)`336 passed, 2 skipped in 3.33s`;全量 `9 failed, 1539 passed, 104 skipped, 24 errors`(FAILED 与基线完全相同,新增/消失均为空);Replay 见 INC-20261029-06 第 6 节;Fault:E1=0、R1=0、M1_crash=0、M2_json=0、M2_crash=0、high_findings=0。本机无 ffmpeg,遮罩测量以合成帧数组验证;真实影片复核用服务器脚本。

## 7. 量化数据
修复前:13 个窗口中 4 个无遮挡、其余颜色/范围不一(含 #FFFFFF)。修复后:question_first 全部窗口确定性纯黑整条底部遮罩;三部 KFP picture bottom 一致为 950/1080。

## 8. 经验与后续
- 能测量的物理量(黑边位置)不交给模型;模型判断只留给语义。
- 测量方法要自带稳健性指标(IQR)与失效报错,而不是默默输出错值。
