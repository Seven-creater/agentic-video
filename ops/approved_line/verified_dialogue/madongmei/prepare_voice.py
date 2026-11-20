from pathlib import Path
import json, subprocess, sys, os, math
import numpy as np
from omni_story.library.character_voice import CharacterVoices
from omni_story.library.audio_render import _wav, SAMPLE_RATE
from omni_story.library.dialogue_mouth_bundle import voice_frames
from omni_story.library.media import sha256_file
from omni_story.library.state import write_json

creative,job=map(Path,sys.argv[1:3])
out=job/'bundle'
out.mkdir(parents=True,exist_ok=True)
if (out/'speech_ready.json').exists():
    raise SystemExit('The verified speech master already exists.')
read=lambda name: json.loads((creative/name).read_text())
locked=read('material_lock.json')
script=read('dialogue_script.json')
voices=read('autonomous_material/character_voices/cast_voices.json')
os.environ['DASHSCOPE_API_KEY']=json.loads(Path('/home/ubuntu/apps/agentic-video/credentials.json').read_text())['DASHSCOPE_API_KEY']
voice=CharacterVoices(job/'character_voices')
voice.reuse_folders=[str(creative/'evaluations/fast_preview/character_voices'),str(creative/'autonomous_material/character_voices')]
references={r['line_id']:r for scene in locked['scenes'] for r in scene['reference_lines']}
prepared=[]
for raw in script['lines']:
    line=dict(raw)
    y=voice.render_line(line,voices[line['role']])
    refs=[references[i] for i in line['reference_line_ids']]
    span=(len(y)+1599)//1600
    audio_path=line['tts_audio_path']
    prepared.append((line,y,span,round(refs[0]['start_s']*30),math.ceil(refs[-1]['end_s']*30),audio_path))
    print(line['line_id'],line['role'],span,flush=True)
total=math.ceil(locked['reference_duration_s']*30)
mix=np.zeros((total*1600,2),dtype=np.float32)
clock=[]
sources=[]
previous_end=0
for i,(line,y,span,a,b,file) in enumerate(prepared):
    # Keep the template's turn unless the actual delivery needs some of its neighbouring quiet.
    preferred=min(a,b-span)
    at=max(previous_end+4,preferred)
    end=at+span
    if end>total:
        mix=np.pad(mix,((0,(end-total+28)*1600),(0,0)))
        total=end+28
    mix[at*1600:at*1600+len(y)]=y
    actor=locked['cast_bindings'][line['role']]['actor']
    clock.append(dict(id=line['line_id'],text=line['text'],actor=actor,role=line['role'],at=at,end=end,
        on_screen=True,ref_start=a,ref_end=b,voice_frames=voice_frames(mix.mean(axis=1)*32767,at,end)))
    sources.append(dict(line_id=line['line_id'],file_sha256=sha256_file(file),
        used_saved_audio=not Path(file).is_relative_to(job),trimmed_samples=len(y)))
    previous_end=end
wav=out/'dub.wav'
_wav(wav,mix)
subprocess.run(['ffmpeg','-v','error','-y','-i',wav,'-c:a','aac','-b:a','192k',out/'dub.m4a'],check=True)
def stamp(frame):
    cs=round(100*frame/30)
    return f'{cs//360000}:{cs//6000%60:02d}:{cs//100%60:02d}.{cs%100:02d}'
header='''[Script Info]
ScriptType: v4.00+
PlayResX: 1280
PlayResY: 720
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Noto Sans CJK SC,47,&H00FFFFFF,&H00FFFFFF,&H00181818,&H70000000,-1,0,0,0,100,100,0,0,1,2.5,0,2,40,40,33,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
'''
events=[f'Dialogue: 0,{stamp(l["at"])},{stamp(l["end"])},Default,,0,0,0,,{l["text"]}\n' for l in clock]
(out/'subs.ass').write_text(header+''.join(events),encoding='utf-8-sig')
write_json(out/'speech_ready.json',dict(total=total,clock=clock,sources=sources,
    script_sha256=sha256_file(creative/'dialogue_script.json'),dub_sha256=sha256_file(out/'dub.m4a')))
print('ready',total,[(l['id'],l['at'],l['end'],len(l['voice_frames'])) for l in clock],flush=True)
