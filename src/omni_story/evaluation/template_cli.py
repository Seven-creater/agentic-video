"""Template evaluation: uploads → frozen inputs → model answers → scores."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..library.state import write_json
from . import template_protocol as protocol
from .template_metrics import summarize


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    scan = commands.add_parser('inventory', help='Hash, probe and decode uploads; no model calls')
    scan.add_argument('--root', type=Path, required=True)
    scan.add_argument('--output', type=Path, required=True)
    prepare = commands.add_parser('prepare', help='Draft only; review split, duplicate groups and rubric before freezing')
    prepare.add_argument('--inventory', type=Path, required=True)
    prepare.add_argument('--experiment-id', required=True)
    prepare.add_argument('--support-count', type=int, default=4)
    prepare.add_argument('--query-count', type=int, default=2)
    prepare.add_argument('--output', type=Path, required=True)
    freeze = commands.add_parser('freeze')
    freeze.add_argument('--draft', type=Path, required=True)
    freeze.add_argument('--output', type=Path, required=True)
    check = commands.add_parser('check')
    check.add_argument('--manifest', type=Path, required=True)
    batch = commands.add_parser('run', help='Resume a frozen server batch under delegated systemd')
    batch.add_argument('--manifest', type=Path, required=True)
    batch.add_argument('--output', type=Path, required=True)
    batch.add_argument('--home', type=Path, required=True)
    batch.add_argument('--workers', type=int, default=2)
    for name in ['understand', 'judge', 'report']:
        command = commands.add_parser(name)
        command.add_argument('--manifest', type=Path, required=True)
        command.add_argument('--output', type=Path, required=True)
        if name == 'report':
            command.add_argument('--records', type=Path, required=True)
        else:
            command.add_argument('--template-id', required=True)
            command.add_argument('--method', choices=['single_reference', 'multi_reference'], required=True)
            if name == 'judge':
                command.add_argument('--prediction', type=Path, required=True)
                command.add_argument('--home', type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == 'inventory':
        result = protocol.inventory(args.root)
        write_json(args.output, result)
        result = dict(templates=len(result['templates']), videos=sum(len(t['assets']) for t in result['templates']),
                      errors=result['errors'], output=str(args.output), model_requests_sent=0)
    elif args.command == 'prepare':
        result = protocol.draft(protocol.read(args.inventory), args.experiment_id,
            support_count=args.support_count, query_count=args.query_count)
        write_json(args.output, result)
        result = dict(readiness=result['readiness'], templates=len(result['templates']), output=str(args.output))
    elif args.command == 'freeze':
        result = protocol.freeze(protocol.read(args.draft), args.output)
        result = dict(readiness='frozen', protocol_sha256=result['protocol_sha256'])
    elif args.command == 'check':
        value = protocol.load(args.manifest)
        result = dict(readiness='frozen', templates=len(value['templates']),
            planned_runs=len(value['templates']) * len(value['methods']), model_requests_sent=0)
    elif args.command == 'run':
        from .template_batch import run
        return run(args)
    elif args.command in {'understand', 'judge'}:
        from .template_runner import understand, judge
        result = understand(args) if args.command == 'understand' else judge(args)
    else:
        # Historical reporting remains usable after explicitly authorized media
        # retirement, but its frozen protocol bytes must still match.
        value = protocol.load(args.manifest, verify_files=False)
        records = ([protocol.read(p) for p in sorted(args.records.rglob('record.json'))]
                   if args.records.is_dir() else protocol.read(args.records))
        from .template_runner import verify_record
        for row in records:
            verify_record(row)
        result = summarize(value, records)
        write_json(args.output / 'report.json', result)
        text = ['# 模板理解评测', '', f"实验：{value['experiment_id']}", '',
                '| 方法 | 计划数 | 已评分 | 覆盖率 | 全计划平均模板分 |',
                '|---|---:|---:|---:|---:|']
        if result['synthetic_smoke_only']:
            text[2:2] = ['仅为合成素材接入检查，不是真实模板成绩。', '']
        for g in result['groups']:
            text.append(f"| {g['method']} | {g['planned']} | {g['evaluated']} | "
                        f"{g['evaluation_coverage']:.1%} | {g['mean_template_score_all_planned']:.2f} |")
        text += ['', '| 模板 | 方法 | 状态 | 模板分 | 顺序正确率 | 无证据主张率 | 主张核验覆盖率 |',
                 '|---|---|---|---:|---:|---:|---:|']
        names = {t['template_id']: t.get('display_name', t['template_id']).replace('|', '\\|')
                 for t in value['templates']}
        def percent(number):
            return 'N/A' if number is None else f'{number:.1%}'
        for row in result['rows']:
            text.append(f"| {names[row['template_id']]} | {row['method']} | {row['status']} | "
                f"{row['template_score']:.2f} | {percent(row['order_accuracy'])} | "
                f"{percent(row['unsupported_rate'])} | {percent(row['claim_verification_coverage'])} |")
        text += ['', '未完成项保留在分母；这些分数评估模板理解，不能替代实际出片率。',
                 '要素按 0/1/2 打分，必需项权重 2、可选项权重 1；模板分为加权得分占加权满分的百分比。',
                 '无证据主张率只计算已核定的 supported/unsupported，unknown 影响核验覆盖率；N/A 不代表零错误。',
                 '批次目录的 execution.json（如有）给出运行耗时及费用预占；预占不是实际账单。']
        (args.output / 'report.md').write_text('\n'.join(text) + '\n', encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
