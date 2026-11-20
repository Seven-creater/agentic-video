# A 线对白剪辑 · 2026-11-20

## 给另一条片子剪对白

在项目根目录运行下面的命令。RUN 指向已经完成选材、定稿台词和角色配音配置的任务目录，REFERENCE 指向该任务的参考视频。使用配好 GLM、语音服务和 FFmpeg 的 A 线环境，并安装项目的 library 依赖。

```sh
PYTHONPATH=src python -m omni_story.library.reviewed_sequence_edit "$RUN" "$REFERENCE" --speaker-mouth
```

多场选材和人物衔接见 [multiscene_pick.py](../../../src/omni_story/library/multiscene_pick.py)。对白剪辑从 [reviewed_sequence_edit.py](../../../src/omni_story/library/reviewed_sequence_edit.py) 进入，按配音定位有声帧，检查画面里可见的说话者，再渲染和复看。口部运动判定见 [dialogue_mouth_tracks.py](../../../src/omni_story/library/dialogue_mouth_tracks.py)，剪辑规则见 [edit-selected-footage](../../../src/omni_story/library/resources/edit-selected-footage/SKILL.md)。

## 回放《立刻有》的定稿

[likeyou](likeyou) 保存了该次实跑的选段、补镜头、裁切、渲染和复看脚本。片中角色、可用构图、素材编号等来自这一条片的实测。下面的回放命令依赖服务器原任务的中间文件，路径均使用绝对路径。

- BUNDLE 是锁定的剪辑包，含 bundle.json、配音、字幕和素材。
- PREVIOUS 是含 draft_render.json 的上一次剪辑目录。RABBIT 是含 final_recheck.json 和候选片段的补镜头目录。
- TASK 是全片试跑目录，含 original_motions.json、两轮素材扫描及对应视频、finished_pair、full_refined、full_locked 和 opening_options.json。要重建全片候选，依次使用 edit_full_single_pass.py、edit_full_refined.py、edit_full_locked.py 和 plan_opening.py。

```sh
export PYTHONPATH="$PWD/src"
python ops/approved_line/verified_dialogue/likeyou/final_reconcile.py \
  "$BUNDLE" "$PREVIOUS" "$TASK/finished_pair" "$RABBIT" "$TASK"
```

成片在 TASK/full_verified/single_full.mp4。同目录的 decision.json 记录 GLM 最终选了什么，outcome.json 记录实际渲染和逐句复核结果。

## 回放《马冬梅》的定稿

[madongmei](madongmei) 保存了原选段对照和最终出片步骤。CREATIVE 指向原任务目录，里面有锁定的素材、台词、角色音色和 watched_windows.json。JOB 是该次剪辑的工作目录。第一次准备配音时，prepare_voice.py 会查找服务器上已有的角色语音缓存和语音服务配置。已有音轨和时间表时，直接复用。

```sh
export PYTHONPATH="$PWD/src"
stages=ops/approved_line/verified_dialogue/madongmei
if [ ! -f "$JOB/bundle/speech_ready.json" ]; then
  python "$stages/prepare_voice.py" "$CREATIVE" "$JOB" || exit 1
fi
python "$stages/prep_alternate.py" "$CREATIVE" "$JOB" &&
  python "$stages/prep_alternate_extra.py" "$CREATIVE" "$JOB" &&
  python "$stages/alternate_verified.py" "$CREATIVE" "$JOB"
```

最终版用同一素材源的 s451、s452 两场。代码先渲染四条备选时间线，按成片逐句核对，再交给 GLM 选择。输出为 JOB/alternate_verified_film/madongmei.mp4，旁边的 decision.json 和 outcome.json 可追溯决定和逐句结果。原选段的对照版可依次运行 extract_original.py、scan_original.py 和 locked_solve.py，参数同为 CREATIVE 和 JOB。
