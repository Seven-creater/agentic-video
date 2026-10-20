"""Semantic accounting checks, with no model, network or media dependencies."""
import csv
import json
import tempfile
import unittest
from pathlib import Path

from omni_story.evaluation.report import QUALITY_DIMENSIONS, summarize, write_reports


def manifest(splits=("test", "test"), methods=("full",)):
    cases = [dict(case_id=f"case{i}", split=split) for i, split in enumerate(splits)]
    return dict(experiment_id="frozen", cases=cases, methods=list(methods), planned_runs=[
        dict(run_id=f"frozen/{case['case_id']}/{method}", case_id=case["case_id"], method_id=method)
        for case in cases for method in methods])


def record(index=0, method="full", **changes):
    return dict(run_id=f"frozen/case{index}/{method}", status="completed", technical_complete=True,
                production_delivered=True,
                quality_pass=True, quality_evaluator="independent", quality_scope="final_full_video",
                manual_intervention=False, quality_scores={name: 4 for name in QUALITY_DIMENSIONS}, severe_defects=[],
                started_at="2026-10-19T00:00:00+00:00", finished_at="2026-10-19T00:01:00+00:00",
                cost={"amount": 2.0, "currency": "CNY", "complete": True}) | changes


class EvaluationReportTests(unittest.TestCase):
    def test_frozen_denominator_keeps_pending_without_calling_it_failure(self):
        report = summarize(manifest(), [record()])
        group = report["groups"][0]
        self.assertEqual(report["missing_records"], 1)
        self.assertEqual(group["counts"]["pending"], 1)
        self.assertEqual(group["counts"]["failed"], 0)
        self.assertEqual(group["rates"]["automatic_qualified"], .5)
        self.assertEqual(group["rates"]["attempt_coverage"], .5)
        self.assertEqual(group["rates"]["evaluation_coverage"], .5)
        self.assertIsNone(group["throughput"]["worker_qualified_per_hour"])

    def test_manual_repair_or_internal_or_partial_review_cannot_be_automatic_pass(self):
        for changes in ({"manual_intervention": True}, {"quality_evaluator": "internal"},
                        {"production_delivered": False}, {"production_delivered": None},
                        {"quality_scope": "candidate_full_video"},
                        {"quality_scope": "selected_frames"}, {"quality_scope": None}):
            with self.subTest(changes=changes):
                group = summarize(manifest(("development",)), [record(**changes)])["groups"][0]
                self.assertEqual(group["counts"]["technical_complete"], 1)
                self.assertEqual(group["counts"]["automatic_qualified"], 0)
        group = summarize(manifest(("development",)), [record(manual_intervention=True)])["groups"][0]
        self.assertEqual(group["counts"]["manual_intervention"], 1)
        without_manual_claim = record()
        del without_manual_claim["manual_intervention"]
        self.assertEqual(summarize(manifest(("test",)), [without_manual_claim])["groups"][0]["counts"]["automatic_qualified"], 0)
        invalid_quality = [dict(quality_scores={"reference_intent": 5}), dict(severe_defects=["cut-off speech"]),
                           dict(severe_defects=None), dict(quality_scores={name: 3 for name in QUALITY_DIMENSIONS}),
                           dict(quality_scores={name: 2 if name == "reference_intent" else 5 for name in QUALITY_DIMENSIONS})]
        for changes in invalid_quality:
            with self.subTest(changes=changes):
                group = summarize(manifest(("test",)), [record(**changes)])["groups"][0]
                self.assertEqual(group["counts"]["automatic_qualified"], 0)

    def test_failed_cost_and_partial_known_cost_are_both_retained(self):
        records = [record(status="failed", technical_complete=False, quality_pass=False,
                          cost={"amount": 1.5, "currency": "CNY", "complete": True}),
                   record(1, status="unknown", cost={"amount": 2, "currency": "CNY", "complete": False})]
        group = summarize(manifest(), records)["groups"][0]
        self.assertEqual(group["cost"]["known_amounts"], {"CNY": 3.5})
        self.assertEqual(group["cost"]["unknown_runs"], 1)
        self.assertIsNone(group["cost"]["known_amount_per_automatic_qualified"]["CNY"])
        self.assertEqual(group["counts"]["unknown"], 1)

    def test_unpriced_tokens_and_budget_never_become_currency(self):
        group = summarize(manifest(("test",)), [record(cost=None, tokens=12000, budget_cny=10)])["groups"][0]
        self.assertEqual(group["cost"]["known_amounts"], {})
        self.assertEqual(group["cost"]["unknown_runs"], 1)

    def test_parallel_wall_time_is_a_union_and_includes_idle_gaps_in_span(self):
        records = [record(), record(1, started_at="2026-10-19T00:00:30+00:00",
                                    finished_at="2026-10-19T00:01:30+00:00"),
                   record(2, started_at="2026-10-19T00:02:00+00:00",
                          finished_at="2026-10-19T00:03:00+00:00")]
        group = summarize(manifest(("test",)*3), records)["groups"][0]
        self.assertEqual(group["time"]["active_wall_union_seconds"], 150)
        self.assertEqual(group["time"]["wall_span_seconds"], 180)
        self.assertEqual(group["throughput"]["worker_qualified_per_hour"], 60)

    def test_batch_wall_includes_judging_and_code_deviation_is_not_manual_edit(self):
        frozen = manifest(('test',))
        timing = dict(started_at='2026-10-19T00:00:00+00:00', finished_at='2026-10-19T00:03:00+00:00')
        report = summarize(frozen, [record()], execution=timing)
        self.assertEqual(report['groups'][0]['throughput']['worker_qualified_per_hour'], 60)
        self.assertEqual(report['execution']['automatic_qualified_per_hour'], 20)
        report = summarize(frozen, [record(protocol_deviation=True, manual_intervention=None)], execution=timing)
        counts = report['groups'][0]['counts']
        self.assertEqual(counts['protocol_deviation'], 1)
        self.assertEqual(counts['manual_intervention'], 0)
        self.assertEqual(counts['manual_intervention_unknown'], 1)
        self.assertEqual(counts['automatic_qualified'], 0)
        unassessed = record(artifact_path='/actual/candidate.mp4', quality_evaluator=None)
        self.assertIsNone(summarize(frozen, [unassessed], execution=timing)['execution']['automatic_qualified_per_hour'])

    def test_scope_separation_pairs_and_missing_costs_do_not_invent_comparisons(self):
        frozen = manifest(('test',)*4, ('baseline_single_pass', 'full'))
        failed = dict(status='failed', production_delivered=False, quality_evaluator=None, quality_scope=None)
        records = [record(0, 'baseline_single_pass', quality_scores={k: 3 for k in QUALITY_DIMENSIONS}),
            record(0), record(1, 'baseline_single_pass'),
            record(1, status='candidate', production_delivered=False, technical_complete=False,
                quality_scope='candidate_full_video', quality_pass=False, cost=None,
                severe_defects=['missing audio', 'unrelated ending'], quality_scores={k: 1 for k in QUALITY_DIMENSIONS}),
            record(2, 'baseline_single_pass', **failed), record(2, **failed), record(3, 'baseline_single_pass')]
        summary = summarize(frozen, records)
        full = next(g for g in summary['groups'] if g['method_id'] == 'full')
        self.assertEqual(full['quality_by_scope']['final_full_video']['evaluated_n'], 1)
        candidate = full['quality_by_scope']['candidate_full_video']
        self.assertEqual(candidate['evaluated_n'], 1)
        self.assertEqual(candidate['severe_defect_video_rate'], 1)
        self.assertEqual(candidate['scores']['reference_intent']['distribution'], {1: 1})
        self.assertEqual(full['quality_all_artifacts']['severe_defect_video_rate'], .5)
        self.assertEqual(full['cost']['attempted_cost_coverage'], 2/3)
        paired = summary['paired_comparisons'][0]
        self.assertEqual(paired['planned_pairs'], 4)
        self.assertEqual(paired['production_delivery_counts'], dict(both_delivered=1, baseline_only=1,
            full_only=0, neither_delivered=1, unresolved=1))
        self.assertEqual(paired['quality_by_scope']['final_full_video']['paired_n'], 1)
        self.assertEqual(paired['quality_by_scope']['final_full_video']['mean_full_minus_baseline'],
            {key: 1 for key in QUALITY_DIMENSIONS})
        self.assertIsNone(paired['cases'][1]['full_minus_baseline'])

    def test_wall_endpoint_covers_later_collection_but_not_an_active_review(self):
        import os
        from datetime import datetime
        from omni_story.evaluation.collect import execution_interval
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            jobs = [dict(run_id='frozen/case0/full')]
            files = {'experiment.json': dict(started_at='2026-10-19T00:00:00+00:00'),
                'batch_result.json': dict(jobs=jobs, finished_at='2026-10-19T00:03:00+00:00'),
                'progress.json': dict(jobs=jobs, updated_at='2026-10-19T00:02:00+00:00'),
                'run_records.json': [record()]}
            for name, data in files.items():
                (root/name).write_text(json.dumps(data))
            endpoint = datetime.fromisoformat('2026-10-19T00:06:00+00:00').timestamp()
            os.utime(root/'run_records.json', (endpoint, endpoint))
            collected = record(technical_checked_at='2026-10-19T00:06:00+00:00',
                               quality_received_at='2026-10-19T00:05:00+00:00')
            timing = execution_interval(root, [collected])
            self.assertEqual(timing['finished_at'], '2026-10-19T00:06:00+00:00')
            self.assertEqual(summarize(manifest(('test',)), [record()], execution=timing)['execution']['wall_seconds'], 360)
            os.utime(root/'run_records.json', (endpoint+3600, endpoint+3600))
            self.assertEqual(execution_interval(root, [collected]), timing)
            ledger = root/'runs/manual_repair/library_state.json';ledger.parent.mkdir(parents=True)
            ledger.write_text(json.dumps(dict(calls=[dict(completed_at='2026-10-19T00:08:00+00:00')])))
            self.assertEqual(execution_interval(root, [collected])['finished_at'], '2026-10-19T00:08:00+00:00')
            (root/'progress.json').write_text(json.dumps(dict(jobs=jobs, updated_at='2026-10-19T00:07:00+00:00')))
            self.assertIsNone(execution_interval(root, [record()])['finished_at'])

    def test_development_test_and_methods_are_separate_and_internal_scores_excluded(self):
        frozen = manifest(("development", "test"), ("baseline_single_pass", "full"))
        records = [record(0, "full", manual_intervention=True), record(1, "full"),
                   record(1, "baseline_single_pass", quality_evaluator="internal")]
        groups = {(g["split"], g["method_id"]): g for g in summarize(frozen, records)["groups"]}
        self.assertEqual(len(groups), 4)
        self.assertEqual(groups[("development", "full")]["counts"]["automatic_qualified"], 0)
        self.assertEqual(groups[("test", "full")]["quality_scores"]["narrative_coherence"], {"mean": 4, "n": 1})
        self.assertEqual(groups[("test", "baseline_single_pass")]["quality_scores"], {})

    def test_duplicate_unknown_records_and_incomplete_manifest_are_rejected(self):
        with self.assertRaises(ValueError):
            summarize(manifest(), [record(), record()])
        with self.assertRaises(ValueError):
            summarize(manifest(), [record(9)])
        frozen = manifest()
        frozen["planned_runs"].pop()
        with self.assertRaises(ValueError):
            summarize(frozen, [])

    def test_reports_roundtrip_utf8_csv_json_and_markdown(self):
        summary = summarize(manifest(("test",)), [record(failure_reason="中文, 保留\n原因",
                                                        cost={"amount": 1, "currency": "USD", "complete": True})])
        with tempfile.TemporaryDirectory() as folder:
            paths = write_reports(summary, folder)
            self.assertEqual(json.loads(Path(paths["json"]).read_text())["planned_runs"], 1)
            with Path(paths["csv"]).open(newline="", encoding="utf-8") as stream:
                row = next(csv.DictReader(stream))
            self.assertEqual(row["failure_reason"], "中文, 保留\n原因")
            self.assertIn("active interval union", Path(paths["markdown"]).read_text())
            self.assertTrue(all(Path(path).is_absolute() for path in paths.values()))


if __name__ == "__main__":
    unittest.main()
