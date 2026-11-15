import unittest
from omni_story.library.schedule_audit import audit_schedule

class ScheduleAuditTests(unittest.TestCase):
    def audit(self, options, uses):
        lines=[dict(line_id=str(i),shot_ids=[o[0]],shot_options=[dict(shot_id=x,reason='observed') for x in o]) for i,o in enumerate(options)]
        rows=[dict(line_id=str(i),slot_id=s,source_sha256='source',first=a,last=b) for i,s,a,b in uses]
        return audit_schedule(dict(lines=lines),lines,rows,{})
    def test_detects_lost_variation(self):
        self.assertTrue(self.audit([['a','b'],['b','a']],[(0,'a',0,30),(1,'a',0,30)])['collapsed_plan'])
    def test_intentional_single_composition_is_allowed(self):
        self.assertFalse(self.audit([['a'],['a']],[(0,'a',0,30),(1,'a',0,30)])['collapsed_plan'])
    def test_rejects_unapproved_fallback(self):
        with self.assertRaises(ValueError):self.audit([['a']],[(0,'b',0,30)])
    def test_repetition_counts_overlapping_source_not_ids(self):
        r=self.audit([['a'],['b']],[(0,'a',0,30),(1,'b',15,45)])
        self.assertAlmostEqual(r['repeated_source_fraction'],.25)
        self.assertFalse(r['collapsed_plan'])
    def test_substitution_has_reason(self):
        r=self.audit([['a','b']],[(0,'b',0,30)])
        self.assertEqual(r['lines'][0]['substitutions'],[dict(shot_id='b',reason='observed')])

    def test_only_authorized_opening_permissions_are_forwarded(self):
        from omni_story.library.schedule_audit import bind_performance_permissions
        lock=dict(scenes=[dict(scene_id='scene',role_bindings=dict(role=dict(role_id='actor'))) ])
        lines=[dict(line_id='line',scene_id='scene',role='role',visual_anchor=True)]
        rows=[dict(line_id='line',slot_id='shot')]
        bind_performance_permissions(lock,lines,rows,dict(shot=dict(stylized_dub_ids=['actor'],dub_speaker_ids=['actor'])))
        self.assertTrue(lines[0]['allows_stylized_intro'])
        self.assertFalse(lines[0]['allows_occluded_intro'])
        bind_performance_permissions(lock,lines,rows,dict(shot=dict(stylized_dub_ids=['other'])))
        self.assertFalse(lines[0]['allows_stylized_intro'])

    def test_unresolved_variation_keeps_artifact_and_cannot_pass_quality(self):
        from omni_story.library.schedule_audit import apply_schedule_quality
        result=dict(artifact_ready=True,quality_status='passed',content_delivery_ready=True,joint_quality_gate=True)
        apply_schedule_quality(result,dict(collapsed_plan=True))
        self.assertTrue(result['artifact_ready'])
        self.assertFalse(result['content_delivery_ready'])
        self.assertEqual(result['quality_status'],'needs_improvement')
    def test_valid_schedule_keeps_existing_verdict(self):
        from omni_story.library.schedule_audit import apply_schedule_quality
        result=dict(quality_status='needs_improvement',artifact_ready=True)
        before=dict(result)
        apply_schedule_quality(result,dict(collapsed_plan=False))
        self.assertEqual(result,before)

if __name__=='__main__':unittest.main()
