from omni_story.library import resource_guard
from omni_story.library.state import LibraryState


def capture(monkeypatch):
    seen = []
    monkeypatch.setattr(resource_guard, 'before_request', lambda **k: seen.append(k['provider']))
    return seen


def test_hunter_glm_channel_is_counted_as_coding_plan(monkeypatch, tmp_path):
    seen = capture(monkeypatch)
    state = LibraryState(tmp_path / 'glm', dict(configuration=dict(provider='zai_anthropic', vision_model='glm-5.3-flash'), request={}), max_requests=1)
    state.begin_call('messages', dict(a=1))
    assert seen == ['glm_coding_plan']


def test_other_channels_keep_list_price(monkeypatch, tmp_path):
    seen = capture(monkeypatch)
    state = LibraryState(tmp_path / 'other', dict(configuration=dict(provider='dashscope'), request={}), max_requests=1)
    state.begin_call('messages', dict(a=1))
    assert seen == ['list_price']
