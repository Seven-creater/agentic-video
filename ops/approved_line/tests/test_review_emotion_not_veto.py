import unittest
from omni_story.library.joint_dialogue import review_prompt


def scene(policy):
    return dict(visual_policy=policy, template='t', role_bindings={}, coverage_shots=[])


class ReviewEmotionNotVeto(unittest.TestCase):
    def test_adaptation_review_does_not_reject_on_emotion(self):
        text = review_prompt(scene('continuous_dialogue_adaptation_v1'), [], [], {})
        self.assertIn('原画面的表情和情绪', text)
        self.assertIn('不是排除理由', text)
        # identity, addressee and explicit-action checks stay
        self.assertIn('若明确朝另一人物说话', text)
        self.assertIn('离开、快速摇镜等具有明确动作含义', text)

    def test_strict_route_keeps_emotion_match(self):
        text = review_prompt(scene(None), [], [], {})
        self.assertNotIn('不是排除理由', text)
        self.assertIn('笑着的镜头不能配争吵', text)


if __name__ == '__main__':
    unittest.main()
