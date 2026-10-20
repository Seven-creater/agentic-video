"""Offline evaluation accounting. Planned runs, never successful files, set denominators."""
from __future__ import annotations

import csv
import io
import json
import math
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean, median
from math import ceil


STATUSES = {"pending", "running", "completed", "candidate", "rejected", "failed", "unknown"}
FINISHED = {"completed", "candidate", "rejected", "failed"}
OUTCOMES = ("pending", "running", "failed", "unknown", "candidate", "rejected",
            "manual_intervention", "protocol_deviation", "automatic_qualified")
QUALITY_DIMENSIONS = ("reference_intent", "narrative_coherence", "audio_visual_alignment",
                      "editing_rhythm", "viewing_experience")


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _timestamp(value):
    if value is None:
        return None
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.utcoffset() is None:
        raise ValueError("timestamps must include a timezone")
    return result.timestamp()


def _normalize(planned, case, record):
    record = record or {"status": "pending"}
    for key in ("case_id", "method_id"):
        if key in record and record[key] != planned[key]:
            raise ValueError(f"record {planned['run_id']} disagrees with manifest: {key}")
    status = record.get("status", "unknown")
    if status not in STATUSES:
        raise ValueError(f"unsupported run status: {status}")
    technical = record.get("technical_complete") is True
    delivered = record.get('production_delivered') is True and status in {'completed', 'candidate', 'rejected'}
    manual = record.get("manual_intervention") is True
    deviation = record.get('protocol_deviation') is True
    independent = (record.get("quality_evaluator") == "independent"
                   and record.get("quality_scope") in {"final_full_video", "candidate_full_video"})
    scores = record.get("quality_scores", {})
    if not isinstance(scores, dict) or any(not isinstance(k, str) or not _number(v) or not 1 <= v <= 5
                                           for k, v in scores.items()):
        raise ValueError("quality_scores must map dimensions to finite 1–5 scores")
    defects = record.get("severe_defects")
    if defects is not None and not isinstance(defects, list):
        raise ValueError("severe_defects must be an explicit list")
    evaluated = (independent and status in FINISHED and isinstance(defects, list)
                 and all(name in scores for name in QUALITY_DIMENSIONS))
    quality_pass = (evaluated and record.get("quality_pass") is True and not defects
                    and min(scores[name] for name in QUALITY_DIMENSIONS) >= 3
                    and mean(scores[name] for name in QUALITY_DIMENSIONS) >= 3.5)
    if status in {"pending", "running", "failed", "unknown"}:
        outcome = status
    elif status == "rejected" or (independent and record.get("quality_pass") is False):
        outcome = "rejected"
    elif status == 'candidate':
        outcome = 'candidate'
    elif manual:
        outcome = "manual_intervention"
    elif deviation:
        outcome = 'protocol_deviation'
    elif (status == "completed" and technical and quality_pass and record.get("manual_intervention") is False
          and delivered and record.get('quality_scope') == 'final_full_video'):
        outcome = "automatic_qualified"
    else:
        outcome = "candidate"

    started, finished = _timestamp(record.get("started_at")), _timestamp(record.get("finished_at"))
    if finished is not None and (started is None or finished < started):
        raise ValueError(f"invalid run interval: {planned['run_id']}")
    interval = [started, finished] if started is not None and finished is not None else None
    cost = record.get("cost") or {}
    if not isinstance(cost, dict):
        raise ValueError("cost must be an accounting object")
    amount, currency = cost.get("amount"), cost.get("currency")
    if amount is not None and (not _number(amount) or amount < 0 or not isinstance(currency, str) or not currency):
        raise ValueError("known cost requires a nonnegative finite amount and currency")
    cost_complete = cost.get("complete") is True and amount is not None
    cost_unknown = (status != "pending" or bool(cost)) and not cost_complete
    return dict(**planned, split=case["split"], status=status, outcome=outcome,
                technical_complete=technical, independent_quality_pass=quality_pass,
                production_delivered=delivered,
                production_status=record.get('production_status'),
                candidate_external_quality_pass=(quality_pass and not delivered),
                independent_whole_video_evaluated=evaluated,
                automatic_qualified=outcome == "automatic_qualified", manual_intervention=manual,
                manual_intervention_known=type(record.get('manual_intervention')) is bool,
                protocol_deviation=deviation,
                generation_code_sha256=record.get('generation_code_sha256'),
                source_continuation=record.get('source_continuation'),
                quality_evaluator=record.get("quality_evaluator"), quality_scope=record.get("quality_scope"),
                reviewer_model=record.get("reviewer_model"), shares_model_with_internal=record.get("shares_model_with_internal"),
                operations=record.get("operations"),
                quality_scores=scores, severe_defects=defects,
                official_quality_scores={name: scores[name] for name in QUALITY_DIMENSIONS} if evaluated else {},
                started_at=record.get("started_at"), finished_at=record.get("finished_at"),
                technical_checked_at=record.get('technical_checked_at'), quality_received_at=record.get('quality_received_at'),
                interval=interval, duration_seconds=finished-started if interval else None,
                cost_amount=amount, cost_currency=currency, cost_complete=cost_complete,
                cost_unknown=cost_unknown, failure_reason=record.get("failure_reason"),
                artifact_path=record.get("artifact_path"), artifact_sha256=record.get('artifact_sha256'),
                evidence_paths=record.get("evidence_paths", []))


def _union_seconds(intervals):
    total, end = 0.0, None
    for start, stop in sorted(intervals):
        uncovered_start = max(start, end) if end is not None else start
        total += max(0, stop-uncovered_start)
        end = max(end, stop) if end is not None else stop
    return total


def _quality(rows):
    evaluated = [row for row in rows if row['independent_whole_video_evaluated']]
    defective = sum(bool(row['severe_defects']) for row in evaluated)
    return dict(evaluated_n=len(evaluated), severe_defect_videos=defective,
        severe_defect_video_rate=defective/len(evaluated) if evaluated else None,
        scores={name: dict(mean=mean(row['official_quality_scores'][name] for row in evaluated),
            n=len(evaluated), distribution=dict(sorted(Counter(
                row['official_quality_scores'][name] for row in evaluated).items())))
            for name in QUALITY_DIMENSIONS} if evaluated else {})


def _paired_comparisons(rows, methods):
    if not {'baseline_single_pass', 'full'}.issubset(methods):
        return []
    grouped = defaultdict(dict)
    for row in rows:
        grouped[(row['split'], row['case_id'])][row['method_id']] = row
    by_split = defaultdict(list)
    for (split, case), runs in sorted(grouped.items()):
        baseline, full = runs['baseline_single_pass'], runs['full']
        settled = all(row['status'] in FINISHED for row in (baseline, full))
        delivery = ('both_delivered' if baseline['production_delivered'] and full['production_delivered'] else
            'baseline_only' if baseline['production_delivered'] else
            'full_only' if full['production_delivered'] else 'neither_delivered') if settled else 'unresolved'
        common_scope = (baseline['quality_scope'] if
            all(row['independent_whole_video_evaluated'] for row in (baseline, full)) and
            baseline['quality_scope'] == full['quality_scope'] else None)
        by_split[split].append(dict(case_id=case, production_delivery=delivery,
            baseline_run_id=baseline['run_id'], full_run_id=full['run_id'],
            baseline_status=baseline['status'], full_status=full['status'],
            baseline_quality_scope=baseline['quality_scope'], full_quality_scope=full['quality_scope'],
            common_quality_scope=common_scope,
            full_minus_baseline={name: full['official_quality_scores'][name]-baseline['official_quality_scores'][name]
                for name in QUALITY_DIMENSIONS} if common_scope else None))
    result = []
    for split, pairs in sorted(by_split.items()):
        quality = {}
        for scope in ('final_full_video', 'candidate_full_video'):
            common = [pair for pair in pairs if pair['common_quality_scope'] == scope]
            quality[scope] = dict(paired_n=len(common), mean_full_minus_baseline={
                name: mean(pair['full_minus_baseline'][name] for pair in common)
                for name in QUALITY_DIMENSIONS} if common else {})
        deliveries = Counter(pair['production_delivery'] for pair in pairs)
        result.append(dict(split=split, planned_pairs=len(pairs),
            production_delivery_counts={key: deliveries[key] for key in
                ('both_delivered', 'baseline_only', 'full_only', 'neither_delivered', 'unresolved')},
            quality_by_scope=quality, cases=pairs,
            interpretation='Descriptive same-reference pairs only; missing or mixed-scope scores have no delta. Not a significance or causal claim.'))
    return result


def _group(rows, split, method):
    planned = len(rows)
    counts = dict(Counter(row["outcome"] for row in rows))
    counts = {name: counts.get(name, 0) for name in OUTCOMES}
    counts.update(planned=planned, attempted=sum(row["status"] != "pending" for row in rows),
                  resolved=sum(row["status"] in FINISHED for row in rows),
                  technical_complete=sum(row["technical_complete"] for row in rows),
                  production_delivered=sum(row['production_delivered'] for row in rows),
                  candidate_external_quality_pass=sum(row['candidate_external_quality_pass'] for row in rows),
                  independent_whole_video_evaluated=sum(row["independent_whole_video_evaluated"] for row in rows),
                  independent_quality_pass=sum(row["independent_quality_pass"] for row in rows),
                  protocol_deviation=sum(row['protocol_deviation'] for row in rows),
                  manual_intervention_unknown=sum(not row['manual_intervention_known'] and row['status'] != 'pending' for row in rows),
                  manual_intervention=sum(row["manual_intervention"] for row in rows))
    rates = {name: counts[name]/planned for name in
             ("technical_complete", "production_delivered", "automatic_qualified", "manual_intervention", "protocol_deviation", "failed", "unknown")}
    rates.update(attempt_coverage=counts["attempted"]/planned, resolved_coverage=counts["resolved"]/planned)
    rates["evaluation_coverage"] = counts["independent_whole_video_evaluated"]/planned
    dimensions = defaultdict(list)
    costs = defaultdict(float)
    intervals = []
    timed_qualified = 0
    for row in rows:
        for name, value in row["official_quality_scores"].items():
            dimensions[name].append(value)
        if row["cost_amount"] is not None:
            costs[row["cost_currency"]] += row["cost_amount"]
        if row["interval"]:
            intervals.append(row["interval"])
            timed_qualified += row["automatic_qualified"]
    span = max(end for _, end in intervals)-min(start for start, _ in intervals) if intervals else None
    active = _union_seconds(intervals) if intervals else None
    durations = [row["duration_seconds"] for row in rows if row["duration_seconds"] is not None]
    operation_rows = [row['operations'] for row in rows if row.get('operations') is not None]
    timing_complete = len(intervals) == planned
    cost_unknown = sum(row["cost_unknown"] for row in rows)
    return dict(split=split, method_id=method, counts=counts, rates=rates,
                operations=dict(recorded_runs=len(operation_rows),
                    totals={name:sum(row.get(name, 0) for row in operation_rows) for name in
                            ('total', 'vision_stage', 'tts', 'qwen_av', 'reserved', 'independent_evaluation_reserved')},
                    scope='business_ledger_operations; not_all_provider_http_calls'),
                quality_scores={name: {"mean": mean(values), "n": len(values)} for name, values in sorted(dimensions.items())},
                quality_scores_scope='all_evaluated_actual_artifacts_including_candidates; see quality_by_scope',
                quality_all_artifacts=_quality(rows),
                quality_by_scope={scope: _quality([row for row in rows if row['quality_scope'] == scope])
                    for scope in ('final_full_video', 'candidate_full_video')},
                time=dict(interval_count=len(intervals), timing_complete=timing_complete,
                          wall_span_seconds=span, active_wall_union_seconds=active,
                          mean_run_seconds=mean(durations) if durations else None,
                          median_run_seconds=median(durations) if durations else None,
                          p90_run_seconds=sorted(durations)[ceil(.9*len(durations))-1] if durations else None,
                          p90_method='nearest_rank'),
                throughput=dict(complete=timing_complete,
                                basis="qualified runs with finished intervals / observed wall span; gaps included, concurrent time counted once",
                                worker_observed_qualified_per_hour=timed_qualified*3600/span if span else None,
                                worker_qualified_per_hour=counts["automatic_qualified"]*3600/span if span and timing_complete else None),
                cost=dict(known_amounts=dict(sorted(costs.items())), unknown_runs=cost_unknown,
                          attempted_cost_complete=cost_unknown == 0,
                          complete_attempted_runs=sum(row['status'] != 'pending' and row['cost_complete'] for row in rows),
                          attempted_cost_coverage=(sum(row['status'] != 'pending' and row['cost_complete'] for row in rows)
                              /counts['attempted'] if counts['attempted'] else None),
                          scope='recorded_run_monetary_evidence; library_build_and_subscription_costs_not_collected',
                          known_amount_per_automatic_qualified={currency: amount/counts["automatic_qualified"]
                              if counts["automatic_qualified"] else None for currency, amount in sorted(costs.items())}))


def summarize(manifest: dict, records: list[dict], *, execution=None) -> dict:
    """Summarize every frozen case×method run, with development/test kept separate.

    Methods may be strings or {method_id: ...}. Records use run_id, status,
    technical_complete, quality_pass, manual_intervention, quality_evaluator,
    quality_scope, quality_scores, started_at, finished_at, cost and evidence_paths.
    Cost is {amount: known monetary portion or null, currency, complete: bool};
    no currency conversion, price inference or budget substitution is performed.
    """
    cases = {}
    for case in manifest["cases"]:
        case = dict(case, split=case.get("split", manifest.get("split")))
        if not case.get("case_id") or case["case_id"] in cases or not case["split"]:
            raise ValueError("cases require unique case_id and an explicit or default split")
        cases[case["case_id"]] = case
    methods = [m if isinstance(m, str) else m["method_id"] for m in manifest["methods"]]
    if not methods or len(set(methods)) != len(methods) or not cases:
        raise ValueError("nonempty cases and unique methods are required")
    planned = manifest["planned_runs"]
    ids = [p["run_id"] for p in planned]
    pairs = [(p["case_id"], p["method_id"]) for p in planned]
    if len(set(ids)) != len(ids) or len(set(pairs)) != len(pairs):
        raise ValueError("planned run IDs and case/method pairs must be unique")
    if set(pairs) != {(case, method) for case in cases for method in methods}:
        raise ValueError("planned_runs must cover exactly the frozen cases×methods")
    by_id = {}
    for record in records:
        run_id = record["run_id"]
        if run_id not in ids or run_id in by_id:
            raise ValueError("unexpected or duplicate run record: " + run_id)
        by_id[run_id] = record
    rows = [_normalize(p, cases[p["case_id"]], by_id.get(p["run_id"])) for p in planned]
    grouped = defaultdict(list)
    for row in rows:
        grouped[(row["split"], row["method_id"])].append(row)
    execution = execution or {}
    start, finish = _timestamp(execution.get('started_at')), _timestamp(execution.get('finished_at'))
    if finish is not None and (start is None or finish < start):
        raise ValueError('invalid experiment wall interval')
    settled = not any(row['status'] in {'pending', 'running', 'unknown'} for row in rows)
    eligible = [row for row in rows if row['artifact_path']]
    assessments_complete = all(row['independent_whole_video_evaluated'] for row in eligible)
    span = finish-start if start is not None and finish is not None else None
    return dict(schema_version=1, experiment_id=manifest["experiment_id"], planned_runs=len(rows),
                execution=dict(**execution, wall_seconds=span, all_planned_runs_settled=settled,
                    evaluable_artifacts=len(eligible),
                    evaluated_artifacts=sum(row['independent_whole_video_evaluated'] for row in eligible),
                    all_evaluable_artifacts_assessed=assessments_complete,
                    automatic_qualified_per_hour=(sum(row['automatic_qualified'] for row in rows)*3600/span
                        if span and settled and assessments_complete else None),
                    observed_qualified_per_hour=(sum(row['automatic_qualified'] for row in rows)*3600/span
                        if span else None),
                    basis='original experiment start through the reported settled coordinator/collected evidence endpoint; observed throughput can rise or fall'),
                recorded_runs=len(records), missing_records=len(rows)-len(records),
                score_scale=[1, 5], evaluation_policy=dict(dimensions=list(QUALITY_DIMENSIONS),
                    min_dimension_score=3, min_mean_score=3.5, severe_defects_allowed=0),
                groups=[_group(items, *key) for key, items in sorted(grouped.items())],
                paired_comparisons=_paired_comparisons(rows, methods),
                runs=rows, notes=[
                    "Every frozen planned run remains in completion and qualification denominators; pending is not failure.",
                    "Qualification requires production_delivered=true, independent final_full_video review, all five scores ≥3, mean ≥3.5, an explicit empty severe_defects list and quality_pass=true; manual_intervention must be explicitly false.",
                    "A production-rejected candidate can pass blind assessment but remains candidate_external_quality_pass; it is not an automatic delivery.",
                    "candidate_full_video scores describe the entire actual candidate, which may lack final audio/subtitles; they do not establish final_full_video qualification. No-audio candidates retain their original technical failure even when the assessment transport carries silence.",
                    "Legacy quality_scores pools all evaluated actual artifacts, including candidates. quality_by_scope separates final_full_video from candidate_full_video, with counts, distributions and severe-defect video rates.",
                    "Independent means a fresh method-blind assessment request. The current Qwen reviewer shares its model with internal AV review; model-family independence and human agreement are not established.",
                    "Code/protocol deviation excludes automatic qualification but is not evidence that every case was manually edited. Per-case manual intervention can remain unknown.",
                    "Known monetary amounts include failed runs; unknown amounts are not zero, and currencies are not converted. Cost evidence coverage is separate from list-price risk estimates; library build and subscription costs are not collected here.",
                    "Paired score differences use only the same case with two complete assessments of the same artifact scope. Unresolved pairs are not counted as two failures, and mixed final/candidate scores are not paired.",
                    "Observed throughput is not a lower bound on final throughput. Complete system throughput also requires all planned runs settled and every evaluable artifact assessed; development and test are separate."])


def _markdown(summary):
    lines = [f"# Evaluation: {summary['experiment_id']}", "",
             f"Frozen planned runs: {summary['planned_runs']}; records: {summary['recorded_runs']}; missing: {summary['missing_records']}.", "",
             "| Split | Method | Planned | Technical | Automatic qualified | Candidate | Rejected | Pending | Running | Failed | Unknown | Manual confirmed | Manual unknown | Code deviation | Attempt coverage | Evaluation coverage |",
             "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    safe = lambda value: str(value).replace("|", "\\|").replace("\n", " ")
    for group in summary["groups"]:
        c, r = group["counts"], group["rates"]
        values = [group["split"], group["method_id"], c["planned"], c["technical_complete"],
                  f"{c['automatic_qualified']} ({r['automatic_qualified']:.1%})", c["candidate"], c["rejected"], c["pending"], c["running"],
                  c["failed"], c["unknown"], c["manual_intervention"], c['manual_intervention_unknown'],
                  c['protocol_deviation'], f"{r['attempt_coverage']:.1%}",
                  f"{r['evaluation_coverage']:.1%}"]
        lines.append("| " + " | ".join(map(safe, values)) + " |")
    for group in summary["groups"]:
        timing, cost = group["time"], group["cost"]
        lines += ["", f"## {safe(group['split'])} / {safe(group['method_id'])}", "",
                  f"Quality by artifact scope (1–5, actual whole-video assessments): {json.dumps(group['quality_by_scope'], ensure_ascii=False, sort_keys=True)}",
                  f"Production deliveries: {group['counts']['production_delivered']}; candidates passing blind review: {group['counts']['candidate_external_quality_pass']} (excluded from automatic delivery yield).",
                  f"Business operations / reservations: {json.dumps(group['operations'], sort_keys=True)}",
                  f"Observed wall span: {timing['wall_span_seconds']} s; active interval union: {timing['active_wall_union_seconds']} s; timing complete: {timing['timing_complete']}.",
                  f"Worker-interval qualified/hour (excludes batch judging): {group['throughput']['worker_qualified_per_hour']}; observed subset: {group['throughput']['worker_observed_qualified_per_hour']}.",
                  f"Known costs (all runs, including failures): {json.dumps(cost['known_amounts'], sort_keys=True)}; unknown-cost runs: {cost['unknown_runs']}; attempted cost-evidence coverage: {cost['attempted_cost_coverage']}. Library build/subscription costs are not collected here."]
    for comparison in summary['paired_comparisons']:
        lines += ['', f"## Same-reference pairs / {safe(comparison['split'])}", '',
            f"Planned pairs: {comparison['planned_pairs']}; production delivery: {json.dumps(comparison['production_delivery_counts'], sort_keys=True)}.",
            f"Same-scope paired scores: {json.dumps(comparison['quality_by_scope'], sort_keys=True)}", '',
            '| Case | Baseline status | Full status | Production delivery | Common score scope | Full minus baseline |',
            '|---|---|---|---|---|---|']
        for pair in comparison['cases']:
            lines.append('| '+' | '.join(safe(pair[key]) for key in ('case_id', 'baseline_status', 'full_status',
                'production_delivery', 'common_quality_scope', 'full_minus_baseline'))+' |')
    lines += ['', f"Reported experiment wall interval: {summary['execution']['wall_seconds']} s; qualified/hour: {summary['execution']['automatic_qualified_per_hour']}; observed subset: {summary['execution']['observed_qualified_per_hour']}; evaluable artifacts assessed: {summary['execution']['evaluated_artifacts']}/{summary['execution']['evaluable_artifacts']}."]
    lines += ["", "## Accounting rules", ""] + ["- " + note for note in summary["notes"]]
    return "\n".join(lines) + "\n"


def write_reports(summary: dict, output_dir) -> dict:
    """Write JSON summary, per-planned-run CSV and readable Markdown, offline."""
    folder = Path(output_dir).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    paths = {kind: folder/f"evaluation_report.{extension}" for kind, extension in
             (("json", "json"), ("csv", "csv"), ("markdown", "md"))}
    paths["json"].write_text(json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False)+"\n", encoding="utf-8")
    columns = ["run_id", "case_id", "split", "method_id", "status", "outcome", "technical_complete",
               "independent_quality_pass", "independent_whole_video_evaluated", "automatic_qualified", "manual_intervention", "manual_intervention_known", "protocol_deviation", "quality_evaluator", "quality_scope",
               "started_at", "finished_at", "technical_checked_at", "quality_received_at", "duration_seconds", "cost_currency", "cost_amount", "cost_complete",
               "cost_unknown", "failure_reason", "artifact_path", "artifact_sha256", "generation_code_sha256", "source_continuation", "quality_scores", "severe_defects", "evidence_paths", "operations", "reviewer_model", "shares_model_with_internal", "production_delivered", "production_status", "candidate_external_quality_pass"]
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for row in summary["runs"]:
        row = dict(row)
        for key in ("quality_scores", "severe_defects", "evidence_paths", "operations"):
            row[key] = json.dumps(row[key], ensure_ascii=False, sort_keys=True)
        writer.writerow(row)
    paths["csv"].write_text(buffer.getvalue(), encoding="utf-8")
    paths["markdown"].write_text(_markdown(summary), encoding="utf-8")
    return {kind: str(path) for kind, path in paths.items()}
