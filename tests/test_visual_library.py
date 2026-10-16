from copy import deepcopy

import pytest

from omni_story.library.visual_library import VisualLibrary, candidate_groups, compact_candidates


SHA = 'a' * 64


def observed(ident, start=0, end=10, *, entity=None, fact='人物摆手，递出的物件被收回', uses=None):
    return {'observation_id': ident,
        'evidence': {'status': 'received', 'ledger_path': '/fixture/ledger.json', 'call_id': ident,
                     'request_sha256': 'b' * 64, 'response_sha256': 'c' * 64},
        'window': {'window_id': 'window_' + ident, 'source_id': 'film_a', 'source_sha256': SHA,
            'source_start_s': start, 'source_end_s': end, 'status': 'watched',
            'source_visual_policy': 'silent_subtitle_masked',
            'observation': {'roles': [{'role_id': 'A', 'entity_id': entity, 'state': '穿蓝衣的人物',
                'identity_confirmed': True, 'identity_evidence': '蓝衣和正脸可见'}],
                'events': [{'local_start_s': 0, 'local_end_s': end - start,
                    'observed_fact': fact, 'role_ids': ['A']}],
                'usable_ranges': [], 'uncertainties': [], 'affordances': uses or []}}}


def test_received_record_is_immutable_and_rebuilds_without_media(tmp_path):
    store = VisualLibrary(tmp_path)
    record = observed('first')
    assert store.add_observation(record) == 'first'
    assert store.add_observation(deepcopy(record)) == 'first'
    changed = deepcopy(record)
    changed['window']['observation']['events'][0]['observed_fact'] = 'another fact'
    with pytest.raises(ValueError, match='immutable_observation_changed'):
        store.add_observation(changed)
    assert store.get('first') == record
    store.database.unlink()
    reopened = VisualLibrary(tmp_path)
    assert reopened.get('first') == record
    assert reopened.search('摆手')[0]['window_id'] == 'window_first'


def test_unknown_or_nonvisual_input_never_becomes_searchable(tmp_path):
    store = VisualLibrary(tmp_path)
    record = observed('unknown')
    record['evidence']['status'] = 'uncertain'
    with pytest.raises(ValueError, match='received_evidence_required'):
        store.add_observation(record)
    record['evidence']['status'] = 'received'
    record['window']['source_visual_policy'] = 'original_audio'
    with pytest.raises(ValueError, match='pure_visual_window_required'):
        store.add_observation(record)
    assert store.search('') == []
    assert list(store.records.glob('*.json')) == []


def test_coverage_merges_overlaps_and_keeps_unwatched_gaps(tmp_path):
    store = VisualLibrary(tmp_path)
    for record in (observed('a', 0, 10), observed('b', 8, 15), observed('c', 20, 25)):
        store.add_observation(record)
    result = store.coverage(SHA, 30)
    assert result['observed_intervals_s'] == [[0, 15], [20, 25]]
    assert result['gaps_s'] == [[15, 20], [25, 30]]
    assert result['covered_s'] == 20
    assert result['observation_count'] == 3


def test_same_appearance_does_not_merge_people_and_known_ids_keep_exemplars(tmp_path):
    import json
    from PIL import Image
    from omni_story.library.media import sha256_file
    from omni_story.library.state import LibraryState, json_sha
    from omni_story.library.visual_indexer import _check_exemplars, _exemplars, validate_observation
    store = VisualLibrary(tmp_path)
    for record in (observed('local_a'), observed('local_b', 10, 20)):
        store.add_observation(record)
    assert len(store.entities(SHA)) == 2
    known = SHA + ':person_1'
    first = observed('known_a', 20, 30, entity=known)
    image = tmp_path / 'synthetic_person.png'
    Image.new('RGB', (80, 60), 'blue').save(image)
    ref = {'entity_id': known, 'path': str(image), 'sha256': sha256_file(image),
           'source_time_s': 21, 'description': '实际参照帧',
           'source_frame_path': str(image), 'source_frame_sha256': sha256_file(image)}
    first['window']['entity_exemplars'] = [ref]
    state = LibraryState(tmp_path / 'reference_check', {'synthetic_fixture': True}, max_requests=2)
    class RejectingModel:
        def call(self, name, prompt, media, validator, *, image=False, scope=None):
            assert image is True and name.startswith('fine_') and len(name) == 21
            value = {'references': [{'entity_id': known, 'approved': False,
                'visible_appearance': '合成蓝色方块，不是人物', 'reason': '不能支持所提人物外形'}]}
            validator(value)
            request = dict(tool='analyze_image', arguments=dict(image_source=str(media), prompt=prompt),
                media_sha256=sha256_file(media), observation_scope=scope)
            call, _ = state.begin_call(name, request)
            state.complete_call(call, {'status': 'complete', 'result': {'content': [
                {'type': 'text', 'text': json.dumps(value)}]}})
            return value
    original = deepcopy(first['window']['observation'])
    _check_exemplars(first['window'], store.entities(SHA), tmp_path / 'media', RejectingModel(), state)
    assert first['window']['observation'] == original
    assert ref['reference_check']['approved'] is False
    assert ref['reference_check']['evidence']['status'] == 'received'
    # Received film2 call37: 0.2 > 1 - 0.8 only because of float subtraction.
    crop_window = deepcopy(first['window'])
    crop_window['duration_s'] = 10
    crop_window['observation'].update(window_id=crop_window['window_id'], source_id=crop_window['source_id'])
    role = crop_window['observation']['roles'][0]
    role.update(exemplar_local_s=1, exemplar_box=[0.8, 0.1, 0.2, 0.6])
    validate_observation(crop_window['observation'], crop_window, [{'entity_id': known}])
    assert crop_window['observation']['roles'][0]['exemplar_box'] == [0.8, 0.1, 0.2, 0.6]
    crop_cache = tmp_path / 'bbox_media'
    frame = crop_cache / 'characters' / json_sha([crop_window['window_id'], 'A'])[:20] / 'exemplar.jpg'
    frame.parent.mkdir(parents=True)
    Image.new('RGB', (80, 60), 'blue').save(frame)
    crop = _exemplars(crop_window, crop_cache)[0]
    with Image.open(crop['path']) as picture:
        assert picture.size == (16, 36)
    assert crop['model_estimated_box'] == [0.8, 0.1, 0.2, 0.6]
    for box in ([0.8, 0.1, 0.2 + 5e-10, 0.6], [0.1, 0.8, 0.6, 0.2 + 5e-10]):
        crop_window['observation']['roles'][0]['exemplar_box'] = box
        validate_observation(crop_window['observation'], crop_window, [{'entity_id': known}])
    for box in ([0.8, 0.1, 0.201, 0.6], [0.1, 0.8, 0.6, 0.201]):
        crop_window['observation']['roles'][0]['exemplar_box'] = box
        with pytest.raises(ValueError, match='exemplar_box_outside_base_video'):
            validate_observation(crop_window['observation'], crop_window, [{'entity_id': known}])
    store.add_observation(first)
    store.add_observation(observed('known_b', 30, 40, entity=known))
    entity = next(row for row in store.entities(SHA) if row['entity_id'] == known)
    assert len(entity['appearances']) == 2
    assert entity['appearances'][0]['state'] == '穿蓝衣的人物'
    assert entity['exemplars'] == [ref]
    assert not any(item.get('reference_check', {}).get('approved') is True for item in entity['exemplars'])
    assert len(store.search('摆手', entity_ids=[known])) == 2
    assert store.entities('f' * 64) == []


def test_chinese_search_labels_conditional_uses_and_diversifies_overlapping_candidates(tmp_path):
    store = VisualLibrary(tmp_path)
    uses = [{'event_indices': [0], 'expression': '拒绝', 'possible_uses': ['拒绝诱惑'],
             'requires': ['对白交代物件意义'], 'conflicts': ['不能断言物件是现金']}]
    for record in (observed('a', 0, 10, uses=uses), observed('b', 1, 11, uses=uses),
                   observed('c', 50, 60, uses=uses)):
        store.add_observation(record)
    cards = store.search('拒绝诱惑', limit=3)
    assert [row['observation_id'] for row in cards] == ['a', 'c', 'b']
    assert [row['kind'] for row in cards[0]['reasons']] == ['conditional_use']
    assert {'拒绝', '诱惑'} <= set(cards[0]['reasons'][0]['matched_terms'])
    assert cards[0]['affordances'][0]['requires'] == ['对白交代物件意义']
    assert store.search('摆手', source_sha256s=['f' * 64]) == []
    assert store.search('摆手')[0]['reasons'][0]['kind'] == 'visual_fact'


def test_search_excludes_seen_before_limit_and_marks_only_single_character_hits_as_weak(tmp_path):
    store = VisualLibrary(tmp_path)
    for record in (observed('first', fact='人物面向正前方'),
                   observed('second', 20, 30, fact='人物面对挑战'),
                   observed('third', 40, 50, fact='人物面对挑战')):
        store.add_observation(record)
    cards = store.search('面对挑战', limit=3)
    assert [row['observation_id'] for row in cards] == ['second', 'third', 'first']
    assert cards[-1]['match_strength'] == 'weak_single_character'
    assert cards[-1]['score'] < cards[0]['score']
    assert store.search('面对挑战', limit=1, exclude_observation_ids=['second'])[0]['observation_id'] == 'third'


def test_candidate_groups_keep_entities_sources_and_optional_material_separate():
    other_sha = 'f' * 64
    person_a, person_b, person_c = [SHA + ':' + suffix for suffix in ('A', 'B', 'C')]
    foreign_a = other_sha + ':A'
    def card(ident, entities, roles, *, source_sha256=SHA):
        return {'observation_id': ident, 'source_sha256': source_sha256,
            'source_id': 'same_display_name', 'source_start_s': 0, 'source_end_s': 60,
            'entities': entities, 'entity_bindings': [{'entity_id': entity, 'role_id': str(index),
                'appearance': ident, 'identity_status': 'observed', 'state': ident,
                'identity_evidence': 'Full identity evidence remains in the original card'}
                for index, entity in enumerate(entities)],
            'scene': {'continuity': 'Different shots within this window; co-framing is not established'},
            'usable_ranges': [{'local_in_s': index, 'local_out_s': index + 1, 'shot_role': role}
                              for index, role in enumerate(roles)],
            'evidence': {'status': 'received'}}
    cards = [card('two_people', [person_a, person_b], ['action', 'reaction']),
             card('a_detail', [person_a], ['detail']), card('b_response', [person_b], ['reaction']),
             card('different_person', [person_c], ['action']),
             card('foreign', [foreign_a], ['reaction'], source_sha256=other_sha),
             card('environment', [], ['establishing'])]
    cards.append(deepcopy(cards[0]))
    cards.append(dict(card('unknown', [person_a], ['action']), evidence={'status': 'uncertain'}))
    cards[0].update(summary='完整观察过程与结果。' * 300,
        uncertainties=['远景表情无法确认'], limitations=['不能证明同框'],
        affordances=[{'expression': '选择后的反应', 'possible_uses': ['面对新选择'],
            'requires': ['声音需交代选择对象'], 'event_indices': [0], 'conflicts': ['不是原剧情证明']}],
        retrieval_matches=[{'query': '动作后反应', 'must_have': ['完整动作可见']}])
    cards[0]['evidence'].update(ledger_path='/fixture/ledger.json', request_sha256='b' * 64)
    cards[0]['usable_ranges'].extend({'local_in_s': index, 'local_out_s': index + .5,
        'shot_role': 'action', 'role_ids': ['0'], 'limitations': ['边界仍需复核'],
        'continuity_notes': '持物状态与前一段不同', 'speech_fit': '只适合画外音', 'event_indices': [0],
        'camera': 'Original camera details remain in the full card'} for index in range(2, 19))
    original = deepcopy(cards)
    groups = candidate_groups(cards)
    by_key = {(group['source_sha256'], tuple(group['entity_ids'])): group for group in groups}
    assert len(groups) == len(by_key) == 6
    pair = by_key[(SHA, (person_a, person_b))]
    assert pair['observation_ids'] == ['two_people', 'a_detail', 'b_response']
    assert pair['shot_roles'] == ['action', 'detail', 'reaction']
    assert by_key[(SHA, (person_a,))]['observation_ids'] == ['two_people', 'a_detail']
    assert by_key[(SHA, (person_c,))]['observation_ids'] == ['different_person']
    assert by_key[(other_sha, (foreign_a,))]['observation_ids'] == ['foreign']
    assert by_key[(SHA, ())]['observation_ids'] == ['environment']
    assert by_key[(SHA, ())]['shot_roles'] == ['establishing']
    compact = compact_candidates(cards)
    assert [row['observation_id'] for row in compact] == [row['observation_id'] for row in cards]
    first = compact[0]
    for key in ('source_id', 'source_sha256', 'source_start_s', 'source_end_s', 'summary', 'entities',
                'uncertainties', 'limitations', 'retrieval_matches'):
        assert first[key] == cards[0][key]
    assert first['entity_bindings'][0] == {'entity_id': person_a, 'role_id': '0',
        'appearance': 'two_people', 'identity_status': 'observed'}
    assert len(first['usable_ranges']) == len(cards[0]['usable_ranges']) == 19
    assert first['usable_ranges'][-1] == {'local_in_s': 18, 'local_out_s': 18.5,
        'shot_role': 'action', 'role_ids': ['0'], 'limitations': ['边界仍需复核'],
        'continuity_notes': '持物状态与前一段不同', 'speech_fit': '只适合画外音',
        'camera': cards[0]['usable_ranges'][-1]['camera']}
    assert first['affordances'] == [{'expression': '选择后的反应', 'possible_uses': ['面对新选择'],
        'requires': ['声音需交代选择对象']}]
    assert first['evidence'] == {'status': 'received'}
    first['usable_ranges'][-1]['limitations'].append('Changed compact copy')
    first['retrieval_matches'][0]['must_have'].append('Changed compact copy')
    assert cards == original


def identity_reply(tmp_path, label, old, new, *, declare_merge=True):
    """Synthetic received provider records; no API call or identity-quality claim."""
    import json
    from omni_story.library.media import sha256_file
    from omni_story.library.state import LibraryState
    from omni_story.library.prompts import POLICY_VERSION
    media = tmp_path / (label + '.png')
    media.write_bytes(b'Synthetic image bytes; this fixture is not decoded.')
    state = LibraryState(tmp_path / label, {'synthetic_identity_fixture': {'old_id': old, 'new_id': new}}, max_requests=2)
    request = dict(tool='analyze_image', arguments=dict(image_source=str(media), prompt='Synthetic visual identity fixture'),
        media_sha256=sha256_file(media), provider='official_vision_mcp_in_codex', policy_version=POLICY_VERSION,
        observation_scope=dict(kind='sparse_contact_sheet', source_sha256=SHA, source_start_s=0, source_end_s=30))
    call, _ = state.begin_call('identity_fixture', request)
    result = {'identity_merges': [{'old_id': old, 'new_id': new, 'evidence': 'Synthetic explicit visual match'}]}
    if not declare_merge:
        result = {'summary': 'No identity decision in this received reply'}
    state.complete_call(call, {'status': 'complete', 'result': {'content': [{'type': 'text', 'text': json.dumps(result)}]}})
    call = state.data['calls'][-1]
    return dict(status='received', ledger_path=str(state.path), call_id=call['id'],
                request_sha256=call['request_sha256'], response_sha256=call['response_sha256'])


def test_identity_merge_changes_new_views_but_preserves_raw_and_frozen_snapshots(tmp_path):
    store = VisualLibrary(tmp_path / 'store')
    old, middle, final = [SHA + ':' + suffix for suffix in ('person_old', 'person_middle', 'person_final')]
    original = observed('old', entity=old)
    for record in (original, observed('middle', 20, 30, entity=middle), observed('final', 40, 50, entity=final)):
        store.add_observation(record)
    before = store._record_path('old').read_bytes()
    first = store.merge_entities(old, middle, identity_reply(tmp_path, 'first_merge', old, middle))
    frozen = [first]
    assert store.get('old') == original
    assert store._record_path('old').read_bytes() == before
    assert store.effective_window(original, [])['observation']['roles'][0]['entity_id'] == old
    assert store.effective_window(original, frozen)['observation']['roles'][0]['entity_id'] == middle
    store.merge_entities(middle, final, identity_reply(tmp_path, 'second_merge', middle, final))
    assert store.effective_window(original, frozen)['observation']['roles'][0]['entity_id'] == middle
    assert store.effective_window(original)['observation']['roles'][0]['entity_id'] == final
    frozen_card = next(card for card in store.search('摆手', revision_ids=frozen) if card['observation_id'] == 'old')
    latest_card = next(card for card in store.search('摆手') if card['observation_id'] == 'old')
    assert frozen_card['entities'] == [middle]
    assert latest_card['entities'] == [final]
    assert len(store.entities(SHA)) == 1
    assert len(store.entities(SHA, revision_ids=[])) == 3
    assert store.get('old') == original
    store.rebuild()
    assert store.get('old') == original
    assert len(store.entities(SHA)) == 1


def test_identity_merge_requires_explicit_received_visual_decision_not_client_labels(tmp_path):
    store = VisualLibrary(tmp_path / 'store')
    old, new = SHA + ':old', SHA + ':new'
    store.add_observation(observed('old', entity=old))
    store.add_observation(observed('new', 20, 30, entity=new))
    evidence = identity_reply(tmp_path, 'no_decision', old, new, declare_merge=False)
    evidence['identity_merges'] = [{'old_id': old, 'new_id': new, 'evidence': 'A client assertion is not a model reply'}]
    with pytest.raises(ValueError, match='explicit_received_identity_merge_required'):
        store.merge_entities(old, new, evidence)
    evidence['status'] = 'uncertain'
    with pytest.raises(ValueError, match='received_identity_evidence_required'):
        store.merge_entities(old, new, evidence)
    with pytest.raises(ValueError, match='distinct_same_source_entities'):
        store.merge_entities(old, 'f' * 64 + ':new', evidence)
    assert store.entity_revision_ids() == []


def test_identity_merge_rejects_cycles_and_changed_revision_files(tmp_path):
    import json
    store = VisualLibrary(tmp_path / 'store')
    old, new = SHA + ':old', SHA + ':new'
    store.add_observation(observed('old', entity=old))
    store.add_observation(observed('new', 20, 30, entity=new))
    ident = store.merge_entities(old, new, identity_reply(tmp_path, 'first', old, new))
    with pytest.raises(ValueError, match='entity_revision_cycle'):
        store.merge_entities(new, old, identity_reply(tmp_path, 'reverse', new, old))
    path = store.revisions / (ident + '.json')
    changed = json.loads(path.read_text(encoding='utf-8'))
    changed['received_identity_merge']['evidence'] = 'Modified after receipt'
    path.write_text(json.dumps(changed), encoding='utf-8')
    with pytest.raises(ValueError, match='entity_revision_changed'):
        store.effective_window(store.get('old'), [ident])
