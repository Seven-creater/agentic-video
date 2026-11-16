"""Unattended night supervisor: shell-side polling, GLM-5.3 (effort max) read-only diagnosis.

- Runs night_batch_20261115.py; deliveries and material-gap stops are handled by the driver.
- On a code failure: collect evidence, ask GLM for a root-cause report (no code edits, no deploy),
  then exit 3 so Claude is notified, fixes generically and restarts the supervisor.
"""
from pathlib import Path
import json,re,subprocess,sys,time
c=Path(__file__).resolve().parent
state_file=c/'night_batch_20261115.json';diag=c/'night_diag';diag.mkdir(exist_ok=True)
log_file=c/'night_supervisor_20261116.log'
GLM_ENV=['env','-i','HOME=/Users/Admin','USER=Admin','PATH=/Users/Admin/.npm-global/bin:/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin','TERM=dumb','LANG=en_US.UTF-8']
def log(msg):
    line=time.strftime('%m-%d %H:%M:%S')+' '+msg;print(line,flush=True)
    with log_file.open('a') as f:f.write(line+'\n')
def signature(error):
    return re.sub(r'[0-9a-f]{6,}|\d+','#',str(error))[:140]
def summary(reason):
    s=json.loads(state_file.read_text());lines=['# 夜跑汇总（自动生成）','','结束原因：'+reason,'','| 模板 | 状态 | 说明 |','|---|---|---|']
    for p,row in s['cases'].items():
        note=row.get('check') and ('%.1f 秒，%s 句，%s 个镜头'%(float(row['check']['duration']),row['check']['lines'],row['check']['shots'])) or str(row.get('error',''))[:160].replace('|','/')
        if row.get('glm_report'):note+='；GLM 诊断：'+row['glm_report']
        lines.append('| %s | %s | %s |'%(row.get('case_id',p),row['state'],note))
    (c/'夜跑汇总_20261116.md').write_text('\n'.join(lines)+'\n');log('summary written: '+reason)
def glm_diagnose(prefix,evidence,report):
    prompt=f'''你是夜间值守的诊断员。创作视频批次里模板 {prefix} 的任务停了，用户在睡觉，你只做只读分析，不改任何代码、不部署、不碰服务器。

现场证据：{evidence}（先完整读它）。
源码：{c}/fast_source/src/omni_story/library/ （生产在跑的版本）；夜跑驱动 {c}/night_batch_20261115.py；以往问题记录 {c}/docs/incidents/（先读 README.md 看有没有同类问题）。

要求：
1. 从报错往回追到具体 文件:行号，说明直接原因和根本原因；区分是代码问题（提示词与校验不一致、报错不可操作、流程分支、续跑编号/复用问题等），还是素材真的不够（合理停止）。
2. 给出通用修复方案：只能按输入类型/结构条件化，禁止针对模板名、角色名、演员ID、窗口ID或调特殊参数；写清改哪个函数、怎么改（可以给示意 diff），以及为什么对其他模板同样成立。
3. 说明你排除了哪些假设、还有什么不确定。
4. 把报告用中文写到这个文件（只允许写这一个文件）：{report}
   小节：现象 / 证据 / 根因（文件:行） / 是否素材不足 / 通用修复方案 / 备选方案与放弃理由 / 风险与不确定。
'''
    p=diag/(Path(report).stem+'_prompt.md');p.write_text(prompt)
    with p.open() as f:
        r=subprocess.run([*GLM_ENV,'claude','-p','--model','sonnet','--effort','max','--permission-mode','acceptEdits',
            '--allowedTools','Read','Write','Glob','Grep','--output-format','json'],stdin=f,capture_output=True,text=True,timeout=3600,cwd=str(c))
    (diag/(Path(report).stem+'_raw.json')).write_text(r.stdout[-200000:]+'\n'+r.stderr[-3000:])
    if not Path(report).exists():
        try:Path(report).write_text(json.loads(r.stdout.strip().splitlines()[-1]).get('result','(GLM 未写报告)'))
        except Exception:Path(report).write_text('(GLM 调用失败) '+r.stderr[-2000:])
last_sig=None;crashes=0
while True:
    log('driver start')
    r=subprocess.run([sys.executable,str(c/'night_batch_20261115.py')],capture_output=True,text=True)
    (c/'night_driver_last.out').write_text(r.stdout[-20000:]+'\n'+r.stderr[-5000:])
    if r.returncode==0:summary('全部模板处理完毕');break
    if r.returncode!=3:
        crashes+=1;log('driver crashed code=%d: %s'%(r.returncode,r.stderr[-300:]))
        if crashes>=3:summary('驱动连续异常退出 3 次，停下等待处理');sys.exit(5)
        time.sleep(300);continue
    crashes=0
    s=json.loads(state_file.read_text())
    prefix,row=next((p,row) for p,row in s['cases'].items() if row['state']=='needs_attention')
    stamp=time.strftime('%m%d_%H%M');evidence=diag/f'{prefix}_{stamp}.txt';report=diag/f'{prefix}_{stamp}_glm.md'
    subprocess.run([sys.executable,str(c/'night_diag_case.py'),prefix,str(evidence)],capture_output=True,text=True,timeout=300)
    log(prefix+' failed: '+str(row.get('error'))[:200]+' -> GLM diagnosing')
    try:glm_diagnose(prefix,evidence,report)
    except Exception as exc:log('GLM diagnose error: '+repr(exc)[:300])
    log(prefix+' GLM report: '+str(report))
    s=json.loads(state_file.read_text());s['cases'][prefix]['glm_report']=str(report.relative_to(c))
    state_file.write_text(json.dumps(s,ensure_ascii=False,indent=2)+'\n')
    # Hand over to Claude: it reviews the GLM report, applies a generic fix and restarts this supervisor.
    summary(prefix+' 出现代码问题，GLM 诊断已写好，等待 Claude 修复');sys.exit(3)
