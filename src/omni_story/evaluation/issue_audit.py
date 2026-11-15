"""Verify negative claims against actual candidate media without seeing its grades."""
import json


def prompt(issues, reference, modality_checks, duration):
    return '''你是事实复核员。这次只播放实际创作视频，不给你原评分；下面issues是待检验的指控，不是真相。
对每条指控用本片实际画面与声音验证，输出supported/unsupported/uncertain，不能因措辞肯定就附和。
特别区分“未做同款手法”与“没完成表达作用”。参考要求允许等效改编时，不因没有同款滤镜或走路镜头直接支持失败指控。
不能用字幕证明嘴形，不能从原电影常识推断原配音语言。声称字幕错配时，seen必须逐字抄实际可见字幕，不能翻译台词冒充字幕。
只有可见动作支持动作指控；若描述未明确到能核实的事实或仅是审美推测，记uncertain。
语音/音乐检查结合原生音轨分段证据。切片边缘不是原视频截尾，须检查完整实际结尾。
全模态观察与分段证据冲突时，说明冲突，记uncertain；不要虚构“人工已听过”或“波形分析”结论。
坐标是本片0秒开始。每个issue_id恰好一次，不能重写原始issues或分数。输出中文JSON：
{"findings":[{"issue_id":"i1","verdict":"supported/unsupported/uncertain","seen":"实际可见、含必要的字幕原文与秒数",
"heard":"实际声音或分段依据","reason":"指控是否得到支持及观看影响"}],"limitations":[]}。
''' + json.dumps(dict(claims=[{k: r[k] for k in ('id', 'description', 'reference_span', 'candidate_span', 'requirement_ids')} for r in issues], reference_requirements=reference['requirements'],
        independent_evidence=modality_checks, candidate_duration_s=duration), ensure_ascii=False)


def validate(value, issues):
    rows = value.get('findings', [])
    if len(rows) != len(issues) or {r.get('issue_id') for r in rows} != {r['id'] for r in issues}:
        raise ValueError('issue_audit_must_cover_every_claim_exactly_once')
    for row in rows:
        if row.get('verdict') not in ('supported', 'unsupported', 'uncertain'):
            raise ValueError('issue_audit_verdict_required')
        if any(not isinstance(row.get(k), str) or not row[k] for k in ('seen', 'heard', 'reason')):
            raise ValueError('issue_audit_actual_evidence_required')
    if not isinstance(value.get('limitations'), list):
        raise ValueError('issue_audit_limitations_required')
    return value
