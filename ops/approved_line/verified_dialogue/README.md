# A 线对白剪辑 · 2026-11-20

## 当前通用入口

A 线的新片与改剪从同一个发布回执取得剪辑器。正常启动或续跑仍使用 `launch_reviewed_template.py`，已有剧本和配音的改剪使用 `launch_reviewed_recut.py`。两个入口都先核对同一版本的剪辑器指纹。每个模板只提供参考、锁好的场景和演员、台词与音轨。

选材使用 11 月 20 日的文字分页、带人物外观的候选和留用区间复核。锁演员核对白中的说者、听者、换场前后与首次露面的焦点。剪辑器对旧任务读取素材锁单里的实拍范围，对多场新任务读取与所选秒段完全相符的复看回执。对白按原配音排片，在已锁素材内检查当前演员和同框其他人的嘴；镜头动作或说话人有问题时生成局部候选，实片逐帧核对后再由 GLM 看对照决定是否换。原片里的单片补镜清单和构图留作历史核验。实际交付区分“已生成可播放文件”和“复看通过”。

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
