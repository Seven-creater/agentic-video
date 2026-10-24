"""Offline exam-bank construction checks, with no model, network or media dependencies."""
import json
import unittest

from omni_story.evaluation.exam_bank import bgm_eligibility, build_mixed_story, story_set_metrics


def case(case_id, paragraphs=(), targets=(), name=None, error=None):
    labels = dict(story_paragraphs=list(paragraphs), story_target_indices=list(targets))
    if error is not None:
        labels['story_preparation_error'] = error
    return dict(id=case_id, name=name or f'01_{case_id}', labels=labels)


def sample_manifest():
    return dict(cases=[
        case('t_a', ['a0', 'a1', 'a2'], [1], name='01_x'),
        case('t_b', ['b0', 'b1', 'b2', 'b3'], [0, 3], name='02_y'),
        case('t_c', ['c0'], [], name='03_z'),
        case('t_d', ['d0', 'd1'], [0], name='04_w', error='story length or target range invalid'),
    ])


class BuildMixedStoryTests(unittest.TestCase):
    def test_same_seed_is_reproducible_and_different_seeds_reshuffle(self):
        first, second, third = (build_mixed_story(sample_manifest(), seed=seed)
                                for seed in (7, 7, 8))
        self.assertEqual(first, second)
        self.assertNotEqual([p['text'] for p in first['paragraphs']],
                            [p['text'] for p in third['paragraphs']])
        self.assertEqual(first['seed'], 7)
        self.assertEqual(first['policy'], 'mixed_story_v1')

    def test_paragraph_ids_renumber_and_texts_are_the_collected_multiset(self):
        story = build_mixed_story(sample_manifest(), seed=1)
        self.assertEqual([p['id'] for p in story['paragraphs']],
                         [f'p{i:03d}' for i in range(8)])
        self.assertEqual(sorted(p['text'] for p in story['paragraphs']),
                         sorted(['a0', 'a1', 'a2', 'b0', 'b1', 'b2', 'b3', 'c0']))
        self.assertEqual(sorted(story['source_map']), [f'p{i:03d}' for i in range(8)])

    def test_gold_maps_back_to_original_target_indices(self):
        story = build_mixed_story(sample_manifest(), seed=3)
        expected = {'t_a': {'a1'}, 't_b': {'b0', 'b3'}}
        self.assertEqual(sorted(story['gold']), ['t_a', 't_b'])
        for case_id, texts in expected.items():
            mapped = {story['source_map'][paragraph_id]['index'] for paragraph_id in story['gold'][case_id]}
            self.assertEqual({story['source_map'][paragraph_id]['case_id']
                              for paragraph_id in story['gold'][case_id]}, {case_id})
            self.assertEqual(mapped, {i for case in sample_manifest()['cases']
                                      if case['id'] == case_id
                                      for i in case['labels']['story_target_indices']})
            self.assertEqual({next(p['text'] for p in story['paragraphs'] if p['id'] == paragraph_id)
                              for paragraph_id in story['gold'][case_id]}, texts)

    def test_preparation_error_cases_are_absent_and_targetless_cases_are_excluded(self):
        story = build_mixed_story(sample_manifest(), seed=5)
        self.assertNotIn('t_c', story['gold'])
        self.assertNotIn('t_d', story['gold'])
        self.assertEqual(story['excluded'], [dict(case_id='t_c', reason='no_target_paragraphs')])
        self.assertNotIn('t_d', {entry['case_id'] for entry in story['source_map'].values()})
        self.assertNotIn('d0', [p['text'] for p in story['paragraphs']])

    def test_paragraph_rows_leak_no_case_template_or_target_identity(self):
        story = build_mixed_story(sample_manifest(), seed=11)
        serialized = json.dumps(story['paragraphs'])
        for secret in ('t_a', 't_b', 't_c', 't_d', '01_x', '02_y', '03_z', '04_w',
                       'story_target', 'gold'):
            self.assertNotIn(secret, serialized)
        for paragraph in story['paragraphs']:
            self.assertEqual(set(paragraph), {'id', 'text'})

    def test_empty_target_indices_key_is_also_excluded(self):
        manifest = dict(cases=[dict(id='t_e', name='05_v', labels=dict(story_paragraphs=['e0']))])
        story = build_mixed_story(manifest, seed=0)
        self.assertEqual(story['gold'], {})
        self.assertEqual(story['excluded'], [dict(case_id='t_e', reason='no_target_paragraphs')])
        self.assertEqual([p['text'] for p in story['paragraphs']], ['e0'])


class StorySetMetricsTests(unittest.TestCase):
    def test_perfect_selection_scores_one_and_partial_selection_scores_two_thirds(self):
        self.assertEqual(story_set_metrics(['p000', 'p001'], ['p001', 'p000']),
                         dict(precision=1.0, recall=1.0, f1=1.0, exact=True))
        self.assertEqual(story_set_metrics(['p000', 'p001', 'p002'], ['p000', 'p001', 'p003']),
                         dict(precision=2 / 3, recall=2 / 3, f1=2 / 3, exact=False))

    def test_disjoint_selection_has_zero_f1_and_duplicates_collapse(self):
        self.assertEqual(story_set_metrics(['p004'], ['p000']),
                         dict(precision=0.0, recall=0.0, f1=0.0, exact=False))
        self.assertEqual(story_set_metrics(['p000', 'p000'], ['p000']),
                         dict(precision=1.0, recall=1.0, f1=1.0, exact=True))

    def test_empty_selection_has_none_precision_and_empty_gold_has_none_recall(self):
        self.assertEqual(story_set_metrics([], ['p000']),
                         dict(precision=None, recall=0.0, f1=None, exact=False))
        self.assertEqual(story_set_metrics(['p000'], []),
                         dict(precision=0.0, recall=None, f1=None, exact=False))
        self.assertEqual(story_set_metrics([], []),
                         dict(precision=None, recall=None, f1=None, exact=True))


def bgm_row(case_id, query_id, duration_s=3.0, music_rms_db=-30.0, speech_words=0):
    return dict(case_id=case_id, query_id=query_id, duration_s=duration_s,
                music_rms_db=music_rms_db, speech_words=speech_words)


class BgmEligibilityTests(unittest.TestCase):
    def test_thresholds_are_inclusive_and_every_failure_reason_is_listed(self):
        result = bgm_eligibility([
            bgm_row('t_a', 'q1', duration_s=2.0, music_rms_db=-45.0, speech_words=0),
            bgm_row('t_b', 'q1', duration_s=1.9),
            bgm_row('t_b', 'q2', music_rms_db=-45.1),
            bgm_row('t_b', 'q3', speech_words=1),
            bgm_row('t_c', 'q1', duration_s=1.0, music_rms_db=-60.0, speech_words=9)])
        self.assertEqual(result['policy'], 'bgm_eligibility_v1')
        self.assertEqual(result['queries'][0],
                         dict(case_id='t_a', query_id='q1', eligible=True, reasons=[]))
        self.assertEqual(result['queries'][1]['reasons'], ['duration_below_min'])
        self.assertEqual(result['queries'][2]['reasons'], ['music_rms_below_min'])
        self.assertEqual(result['queries'][3]['reasons'], ['speech_words_above_max'])
        self.assertEqual(result['queries'][4]['reasons'],
                         ['duration_below_min', 'music_rms_below_min', 'speech_words_above_max'])
        self.assertTrue(all(q['eligible'] == (not q['reasons']) for q in result['queries']))

    def test_templates_need_the_minimum_eligible_query_count(self):
        result = bgm_eligibility([
            bgm_row('t_a', 'q1'), bgm_row('t_a', 'q2'),
            bgm_row('t_b', 'q1'), bgm_row('t_b', 'q2', duration_s=1.0),
            bgm_row('t_c', 'q1', music_rms_db=-99.0)])
        self.assertEqual(result['templates'], {
            't_a': dict(eligible_queries=2, status='included'),
            't_b': dict(eligible_queries=1, status='excluded_at_design'),
            't_c': dict(eligible_queries=0, status='excluded_at_design')})
        relaxed = bgm_eligibility([bgm_row('t_b', 'q1'), bgm_row('t_b', 'q2', duration_s=1.0)],
                                  min_queries_per_template=1)
        self.assertEqual(relaxed['templates']['t_b'],
                         dict(eligible_queries=1, status='included'))

    def test_custom_thresholds_move_the_eligibility_boundary(self):
        rows = [bgm_row('t_a', 'q1', duration_s=4.0, music_rms_db=-20.0, speech_words=2),
                bgm_row('t_a', 'q2', duration_s=4.0, music_rms_db=-20.0, speech_words=2)]
        strict = bgm_eligibility(rows, max_speech_words=2, min_duration_s=5.0)
        self.assertEqual([q['reasons'] for q in strict['queries']],
                         [['duration_below_min'], ['duration_below_min']])
        tolerant = bgm_eligibility(rows, max_speech_words=2, min_music_rms_db=-25.0)
        self.assertEqual([q['eligible'] for q in tolerant['queries']], [True, True])
        self.assertEqual(tolerant['templates']['t_a']['status'], 'included')


if __name__ == '__main__':
    unittest.main()
