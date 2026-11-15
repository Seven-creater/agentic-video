"""Portable HTML and Markdown reports with two original time bases."""
from html import escape
import json
from pathlib import Path

from .prompts import REFERENCE_DIMENSIONS, QUALITY_DIMENSIONS, STATUS_LABELS


def span_text(span):
    return f'{span[0]:.2f}–{span[1]:.2f} 秒' if span else '无对应片段'


def render_reports(result, folder):
    folder = Path(folder)
    c = result['comparison']
    as_text = lambda value: value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    corrections = [as_text(value) for value in c['observation_corrections']]
    requirements = {r['id']: r for r in result['observations']['reference']['requirements']}
    labels = dict(pass_='正常', partial='部分保留/局部问题', fail='明显问题', uncertain='待复核',
                  not_applicable='不适用', audible='听到音乐', absent='未听到音乐', essential='核心', supporting='辅助', none='无',
                  visual='画面', audio='声音', joint='声画共同支持', major='重要问题', minor='局部问题')
    labels['pass'] = labels.pop('pass_')
    translated = lambda value: labels.get(value, value)
    e = lambda v: escape(str(v), quote=True)
    md = lambda v: str(v).replace('|', '\\|').replace('\n', ' ')
    def play_button(kind, span):
        if not span:
            return ''
        return f'<button onclick="playSpan(\'{kind}\',{span[0]},{span[1]})">▶ {span_text(span)}</button>'
    scores, score_md = [], []
    for field, names, title in [('adaptation_scores', REFERENCE_DIMENSIONS, '最终成片的四项表现'),
                                 ('quality_scores', QUALITY_DIMENSIONS, '新片自身的观看质量')]:
        if not names:
            continue
        cards = []
        score_md += ['## ' + title, '', '| 维度 | 等级 | 依据 |', '| --- | --- | --- |']
        for s in c[field]:
            grade = str(s['score']) + ' / 5' if s['score'] is not None else ('不适用' if s.get('applicability') == 'not_applicable' else '无法判断')
            if s['dimension'] in result.get('score_review_dimensions', []):
                grade = '待复核（原始 '+grade+'）'
            cards.append(f'<article><span>{e(names[s["dimension"]])}</span><b>{grade}</b><p>{e(s["reason"])}</p></article>')
            score_md.append(f'| {names[s["dimension"]]} | {grade} | {md(s["reason"])} |')
        scores.append('<section><h2>' + title + '</h2><div class="cards">' + ''.join(cards) + '</div></section>')
        score_md.append('')
    rows, row_md = [], ['## 逐项参考对照', '', '| 要求与重要性 | 参考依据 | 新片依据 | 判定与差距 |', '| --- | --- | --- | --- |']
    for a in c['alignment']:
        r = requirements[a['requirement_id']]
        importance = '关键' if r['importance'] == 'critical' else '辅助'
        buttons = ''.join(play_button('candidate', span) for span in a['candidate_spans'])
        rows.append(f'''<tr><td><small>{e(r['id'])} · {importance} · {e(REFERENCE_DIMENSIONS[r['dimension']])}</small>
<h3>{e(r['requirement'])}</h3><details><summary>允许的改编</summary>{e(r['permitted_changes'])}</details></td>
<td>{play_button('reference', r['reference_span'])}<p>{e(a['reference_evidence'])}</p></td>
<td>{buttons or '<small>无对应片段</small>'}<p>{e(a['candidate_evidence'])}</p></td>
<td><span class="badge {a['status']}">{STATUS_LABELS[a['status']]}</span><p>{e(a['difference'])}</p><small>证据承载：{e(translated(a.get('support', '')))}</small><p>画面：{e(a.get('visual_evidence', ''))}<br>声音：{e(a.get('audio_evidence', ''))}</p></td></tr>''')
        cand_times = '；'.join(span_text(s) for s in a['candidate_spans']) or '无对应片段'
        row_md.append(f'| {r["id"]} [{importance}] {md(r["requirement"])} | {span_text(r["reference_span"])}：{md(a["reference_evidence"])} | {cand_times}：{md(a["candidate_evidence"])} | {STATUS_LABELS[a["status"]]}：{md(a["difference"])} |')
    limitations = [as_text(value) for value in [result['calibration']['reason'], result['material_support']['reason'],
                  *result['limitations'], *c['limitations'], *result.get('issue_audit', {}).get('limitations', [])]]
    verdicts = dict(supported='复核支持，仍属模型判断', unsupported='复核不支持，不作为确认缺陷', uncertain='复核无法确认')
    issue_status = {r['issue_id']: verdicts[r['verdict']] for r in result.get('issue_audit', {}).get('findings', [])}
    issue_html, issue_md = [], ['## 问题与不确定项', '']
    for issue in c['issues']:
        text = f'{issue["id"]} · {issue_status.get(issue["id"], "未复核")} · 原始指控（{translated(issue["severity"])}） · 参考 {span_text(issue.get("reference_span"))} / 新片 {span_text(issue.get("candidate_span"))}：{issue["description"]}'
        issue_html.append('<li>' + e(text) + '</li>')
        issue_md.append('- ' + text)
    if not c['issues']:
        issue_html.append('<li>本次模型未报告独立问题；仍需结合部分保留项和观察限制阅读。</li>')
        issue_md.append('本次模型未报告独立问题，仍需结合逐项对照和观察限制阅读。')
    counts = result['counts']
    count_text = f'关键要求满足 {counts["critical_met"]} / {counts["critical_total"]}；其中无法判断 {counts["critical_unknown"]} 项。部分保留不计作完整满足。'
    durations = result['durations']
    duration_text = f'参考 {durations["reference"]:.2f} 秒 · 新片 {durations["candidate"]:.2f} 秒 · 时长比 {result["duration_ratio"]:.3f}'
    observations = ''.join('<details><summary>' + ('参考初次观察与要求' if k == 'reference' else '新片初次混合观察（未核验底稿，可能有错误）') + '</summary><p>' + e(v['summary']) + '</p><pre>' + e(json.dumps(v, ensure_ascii=False, indent=2)) + '</pre></details>' for k, v in result['observations'].items())
    html = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>参考视频与创作视频对照评测</title><style>
*{box-sizing:border-box}body{margin:0;background:#f4f5f7;color:#17212b;font:16px/1.65 system-ui,-apple-system,sans-serif}
main{max-width:1440px;margin:auto;padding:32px}h1{font-size:28px;margin:4px 0}h2{font-size:21px;margin-top:0}h3{font-size:16px}
header{margin-bottom:24px}.eyebrow,small{color:#586777}.summary{max-width:1000px;font-size:18px}
section{background:white;border:1px solid #dde2e7;border-radius:12px;padding:24px;margin:22px 0}.players,.cards{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px}
.players video{width:100%;height:410px;background:#0d1117;border-radius:8px}.cards{grid-template-columns:repeat(3,minmax(0,1fr))}
article{border:1px solid #e2e7ec;border-radius:9px;padding:16px}article b{display:block;font-size:24px;margin-top:6px}article p{font-size:14px;margin-bottom:0}
table{border-collapse:collapse;width:100%;table-layout:fixed}td,th{padding:16px 12px;border-bottom:1px solid #e2e7ec;vertical-align:top;text-align:left}th{background:#f4f6f8}
td p{font-size:14px}button{cursor:pointer;border:1px solid #c8d4e2;background:#eef4ff;color:#184782;padding:7px 10px;border-radius:6px;margin:3px 3px 3px 0}
.badge{display:inline-block;border-radius:6px;background:#edf0f3;padding:3px 9px;font-size:14px}.preserved,.equivalent{background:#e6f3ec;color:#216240}.partial{background:#fff3d6;color:#865a00}.missing,.contradictory{background:#fce9e7;color:#a12d29}
details{margin:12px 0}summary{cursor:pointer}pre{white-space:pre-wrap;font-size:12px;overflow-wrap:anywhere}a{color:#225b9b}.tablewrap{overflow-x:auto}.notice{color:#65501c;background:#fff7df;padding:12px 16px;border-radius:8px}
@media(max-width:800px){main{padding:16px}.cards,.players{grid-template-columns:1fr}.players video{height:320px}table{min-width:900px}section{padding:16px}}
</style><main>'''
    html += f'''<header><div class="eyebrow">参考驱动改编 · 自动初评 demo</div><h1>参考视频与创作视频对照评测</h1>
<p class="eyebrow">以下为主比较原始结论，须结合问题复核阅读；模型复核也不等于人工真值。</p><p class="summary">{e(c['summary'])}</p><p>{e(duration_text)}</p><p>{e(count_text)}</p>
<p class="notice">自动初评，尚未人工校准，无综合总分。5=关键作用清楚成立；4=主要成立、局部不足；3=可懂但明显变弱；2=多处重要缺失；1=核心不成立。关键要求满足数不是验收通过率。</p></header>
<section><h2>查看实际视频</h2><div class="players"><div><h3>参考视频</h3><video id="reference" controls preload="metadata" src="media/reference.mp4"></video></div>
<div><h3>创作视频</h3><video id="candidate" controls preload="metadata" src="media/candidate.mp4"></video></div></div>
<p>点击下方时间按钮播放对应片段；另一条视频会暂停。两条视频各自从 0 秒计时。</p>
<a href="media/comparison.mp4">查看顺序对照视频</a></section>'''
    diagnostics = c.get('diagnostics', {})
    diagnostic_names = dict(character_identity='人物与角色一致', speech_delivery='对白交付与演绎', music='音乐事实与作用', editing='剪辑与节奏')
    diagnostic_html, diagnostic_md = [], ['## 具体表现与声音事实', '']
    for key, row in diagnostics.items():
        finding = row.get('evidence', '')
        status = translated(row.get('status', ''))
        if key == 'music':
            status = f"参考音乐={translated(row.get('reference_presence'))}；新片音乐={translated(row.get('candidate_presence'))}；同曲={translated(row.get('track_match'))}；作用={translated(row.get('role'))}"
        text = diagnostic_names.get(key, key)+' · '+status+'：'+finding
        diagnostic_html.append('<li>'+e(text)+'</li>')
        diagnostic_md.append('- '+text)
    html += '<section><h2>具体表现与声音事实</h2><p>同曲尚未做独立指纹验证，未知不代表不同曲；普通配乐差异不等于对白失败。</p><ul>'+''.join(diagnostic_html)+'</ul></section>'
    html += ''.join(scores) + '<section><h2>逐项参考对照</h2><div class="tablewrap"><table><thead><tr><th>参考要求</th><th>参考实际依据</th><th>新片实际依据</th><th>判定与差距</th></tr></thead><tbody>' + ''.join(rows) + '</tbody></table></div></section>'
    audit_rows = result.get('issue_audit', {}).get('findings', [])
    audit_md = ['## 问题指控的事实复核', '']
    audit_html = []
    for finding in audit_rows:
        text = finding['issue_id']+' · '+verdicts[finding['verdict']]+'：'+finding['reason']+'；画面：'+finding['seen']+'；声音：'+finding['heard']
        audit_md.append('- '+text)
        audit_html.append('<li>'+e(text)+'</li>')
    html += '<section><h2>问题指控的事实复核</h2><p>以下单独核对原始指控。未获支持的指控不作为已确认缺陷，相关原始等级标记待复核；不人工改成高分。</p><ul>'+''.join(audit_html)+'</ul></section>'
    html += '<section><h2>问题与不确定项</h2><ul>' + ''.join(issue_html) + '</ul></section>'
    reconciliation = c.get('evidence_reconciliation', [])
    html += '<section><h2>独立声画核验与观察修正</h2><ul>' + ''.join('<li>' + e(r['aspect'] + ' · ' + r['status'] + '：' + as_text(r['finding'])) + '</li>' for r in reconciliation) + ''.join('<li>' + e(x) + '</li>' for x in corrections) + '</ul><details><summary>分开听音频与看画面的原始记录</summary><pre>' + e(json.dumps(result.get('modality_checks', {}), ensure_ascii=False, indent=2)) + '</pre></details></section>'
    html += '<section><h2>素材支持与评测范围</h2><ul>' + ''.join('<li>' + e(x) + '</li>' for x in limitations) + '</ul>' + observations
    html += '<details><summary>输入与复现记录</summary><pre>' + e(json.dumps(dict(inputs=result['inputs'], model=result['model'], counts=counts, calls=result['calls']), ensure_ascii=False, indent=2)) + '</pre></details><a href="evaluation.json">完整 JSON</a> · <a href="report.md">文字报告</a></section>'
    html += '''</main><script>
function playSpan(id,start,end){for(const name of ['reference','candidate']){const v=document.getElementById(name);v.pause();v.ontimeupdate=null;}const v=document.getElementById(id);v.currentTime=start;v.ontimeupdate=()=>{if(v.currentTime>=end){v.pause();v.ontimeupdate=null}};v.play();v.scrollIntoView({behavior:'smooth',block:'center'});}
</script></html>'''
    (folder / 'report.html').write_text(html, encoding='utf-8')
    lines = ['# 参考视频与创作视频对照评测', '', '**自动模型初评，尚未人工校准。**', '', c['summary'], '',
             '以上为主比较原始结论，须结合下方问题复核阅读；复核仍是模型判断，不能当成人工真值。', '',
             duration_text, '', count_text, '', *score_md, *diagnostic_md, '', *row_md, '', *issue_md, '', *audit_md, '', '## 素材支持与评测范围', '',
             *['- ' + x for x in limitations], '', '## 独立观察修正', '',
             *['- ' + r['aspect'] + '：' + as_text(r['finding']) for r in reconciliation],
             *['- ' + x for x in corrections], '',
             '逐段视频播放见同目录 report.html，完整证据和原始调用记录保存在 evaluation.json 与 calls/。', '']
    (folder / 'report.md').write_text('\n'.join(lines), encoding='utf-8')
