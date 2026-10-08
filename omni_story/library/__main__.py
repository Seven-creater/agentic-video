"""Local movie-library execution with an active Codex official-MCP connection."""
from __future__ import annotations

import argparse
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference', type=Path, required=True)
    parser.add_argument('--library', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--no-asr', action='store_true')
    parser.add_argument('--goal-next', action='store_true',
                         help='With --continue-goal: append a next round only after the current round settles.')
    editing = parser.add_mutually_exclusive_group()
    editing.add_argument('--finecut-parent', action='store_true',
                         help='Direct finishing over the existing rough-cut parent timelines; no movie-library replanning.')
    editing.add_argument('--continue-fact-grounded', action='store_true',
                         help='Run one separately authorized facts-first reconstruction of both parent videos.')
    editing.add_argument('--prepare-slot-finecut', action='store_true',
                         help='CPU-only: bind the existing 77s and 34s parents; no model calls, render or Goal resume.')
    editing.add_argument('--continue-slot-finecut', action='store_true',
                         help='Execute/resume a separately authorized two-parent slot refinement; no automatic new grant.')
    editing.add_argument('--continue-independent', action='store_true',
                         help='Execute one separately bound independent-source round; never replay lost work.')
    editing.add_argument('--continue-goal', action='store_true',
                         help='Authorized existing task: execute/resume one evidence-driven Goal editing round.')
    editing.add_argument('--continue-finecut', action='store_true',
                         help='Existing task: run/resume one separately recorded active-finecut extension.')
    editing.add_argument('--active-finecut', action='store_true',
                         help='Unsubmitted plans only: model-owned refinement, exact final-slice audits and independent economy review.')
    editing.add_argument('--prepare-active-finecut', action='store_true',
                         help='Existing task: append a CPU-only concrete budget proposal; no model call or new render.')
    editing.add_argument('--reference-craft', action='store_true',
                         help='Append a bounded model-selected reference re-observation with generic editing knowledge; no render.')
    editing.add_argument('--semantic-audit', action='store_true',
                         help='New task: independently audit every selected slice, typed silent review and contradictions; includes editing-v2.')
    editing.add_argument('--continue-semantic',action='store_true',
                         help='Run/resume the separately authorized append-only full-reference semantic continuation.')
    editing.add_argument('--editing-v2', action='store_true',
                         help='Bind reference methods to new plans and audit their actual output; preserve locked budgets.')
    editing.add_argument('--audit-editing', action='store_true',
                         help='Measure the reference and existing renders locally; no model requests or new renders.')
    editing.add_argument('--revise-editing', action='store_true',
                         help='Run/resume one separately authorized editing revision in the original task directory.')
    args = parser.parse_args(argv)
    if args.goal_next and not args.continue_goal:
        parser.error('--goal-next requires --continue-goal')
    if args.finecut_parent:
        from .parent_cut_pipeline import execute
        execute(args.output.resolve(strict=True))
        return 0
    if args.continue_fact_grounded:
        from .fact_grounded_editing import execute
        execute(args.output.resolve(strict=True))
        return 0
    if args.prepare_slot_finecut:
        import json
        from .slot_finecut_baselines import prepare
        from .media import sha256_file
        saved_reference = args.output.resolve(strict=True) / 'reference_catalog/inventory.json'
        locked_reference = json.loads(saved_reference.read_text(encoding='utf-8'))['sources'][0]
        if sha256_file(args.reference.resolve(strict=True)) != locked_reference['sha256']:
            parser.error('reference differs from the prepared input lock')
        prepared = prepare(args.output.resolve(strict=True))
        if sha256_file(args.reference.resolve(strict=True)) != prepared['reference']['sha256']:
            parser.error('reference differs from the prepared input lock')
        print(json.dumps({'preparation_path': prepared['preparation_path'], 'execution_authorized': False,
            'parents': [{k: p[k] for k in ('baseline_id', 'duration_s', 'path', 'sha256')} for p in prepared['parents']],
            'new_model_calls': 0, 'new_movie_renders': 0}, ensure_ascii=False), flush=True)
        return 0
    if args.continue_slot_finecut:
        from .slot_finecut import execute
        execute(args.output.resolve(strict=True))
        return 0
    if args.continue_independent:
        from .independent_source_resume import execute
        execute(args.reference.resolve(strict=True), args.library.resolve(strict=True), args.output)
        return 0
    if args.continue_goal:
        from .goal_feedback_continuation import execute_goal_continuation
        execute_goal_continuation(args.reference.resolve(strict=True),args.library.resolve(strict=True),args.output,
                                  **({'start_next':True} if args.goal_next else {}))
        return 0
    if args.continue_finecut:
        from .finecut_continuation import execute_finecut_continuation
        execute_finecut_continuation(args.reference.resolve(strict=True),args.library.resolve(strict=True),args.output)
        return 0
    if args.prepare_active_finecut:
        import json
        from .active_finecut import prepare_existing_task
        print(json.dumps(prepare_existing_task(args.output.resolve(strict=True),
            reference=args.reference.resolve(strict=True),library=args.library.resolve(strict=True)),ensure_ascii=False),flush=True)
        return 0
    if args.reference_craft:
        from .reference_craft import execute_reference_craft
        execute_reference_craft(args.reference.resolve(strict=True), args.library.resolve(strict=True), args.output)
        return 0
    if args.continue_semantic:
        from .semantic_continuation import execute_semantic_continuation
        execute_semantic_continuation(args.reference.resolve(strict=True),args.library.resolve(strict=True),args.output)
        return 0
    if args.audit_editing:
        import json
        from .editing import audit_existing_editing
        report = audit_existing_editing(args.output.resolve(strict=True), args.reference.resolve(strict=True))
        print(json.dumps(report,ensure_ascii=False),flush=True)
        return 0
    if args.revise_editing:
        from .revision import execute_editing_revision
        execute_editing_revision(args.reference.resolve(strict=True),args.library.resolve(strict=True),args.output)
        return 0
    from .pipeline import execute
    execute(args.reference.resolve(strict=True), args.library.resolve(strict=True), args.output,
            asr=not args.no_asr,editing_v2=args.editing_v2,semantic_audit=args.semantic_audit,
            **({'active_finecut':True} if args.active_finecut else {}))
    return 0


if __name__ == '__main__':
    main()
