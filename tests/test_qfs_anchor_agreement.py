"""Received close/fine evidence must agree with the derived cast contract."""
import json
from copy import deepcopy
from pathlib import Path

import pytest

from omni_story.library import contracts, pipeline, prompts, question_first as qfs


FIXTURES = Path(__file__).parent / 'fixtures/e2e_anchor_agreement_20261030'


def evidence():
    response = json.loads((FIXTURES / 'glm_173_fine_response.json').read_text())
    raw = json.loads(response['result']['content'][0]['text'])
    candidate = json.loads((FIXTURES / 'close_004_candidate.json').read_text())
    role = raw['roles'][0]
    # The evidence omits the request/anchor board. Reconstruct only the fields
    # consumed here; entity and prior agreement come from the received artifacts.
    identity = dict(entity_id=role['qfs_anchor_entity_id'], canonical_name=candidate['who'][0],
                    aliases=candidate['who'], anchor_still_path='reconstructed-anchor.jpg',
                    same_character_as_anchor=candidate['same_character_as_anchor'])
    window = dict(window_id=raw['window_id'], source_id=raw['source_id'],
                  source_sha256=identity['entity_id'].split(':')[0], status='watched',
                  source_start_s=candidate['film_in_s'] - 2,
                  source_end_s=candidate['film_out_s'] + 2, qfs_identity=[identity])
    return raw, identity, window


def test_received_fine_can_confirm_an_unverified_close_without_changing_golden_agreement():
    raw, identity, window = evidence()
    assert identity['same_character_as_anchor'] is None
    original = deepcopy(raw)
    observed = pipeline._fine_navigation_points(raw, window)
    assert observed['roles'][0]['identity_confirmed'] is True
    assert observed['roles'][0]['entity_id'] == identity['entity_id']
    assert observed['roles'][1]['identity_confirmed'] is False
    assert observed['usable_ranges'][0]['role_ids'] == ['A']
    assert contracts.validate_fine(observed, window) is observed
    assert raw == original
    window['observation'] = observed
    assert contracts.plan_field_contract(windows=[window])['cast']['allowed_bindings'] == [
        dict(window_id=window['window_id'], role_id='A', entity_id=identity['entity_id'])]

    # Golden-path close=true and close=null now derive the same observation,
    # while neither case invents an identity for the unconfirmed second actor.
    golden = deepcopy(window)
    golden['qfs_identity'][0]['same_character_as_anchor'] = True
    assert pipeline._fine_navigation_points(raw, golden) == observed
    assert pipeline._fine_navigation_points(observed, window) == observed

    context = qfs.observation_context([identity])
    contract = qfs.observed_identity_contract()
    assert context['qfs_identity_contract'] == contract
    prompt = prompts.fine_prompt(window, context)
    assert json.dumps(contract, ensure_ascii=False) in prompt


@pytest.mark.parametrize('disagreement', ['close', 'fine', 'duplicate'])
def test_received_stale_agreement_is_replaced_when_binding_is_rejected(disagreement):
    raw, identity, window = evidence()
    identity['same_character_as_anchor'] = True
    if disagreement == 'close':
        identity['same_character_as_anchor'] = False
        target = raw['roles'][0]
    elif disagreement == 'fine':
        raw['roles'][0]['same_character_as_anchor'] = False
        target = raw['roles'][0]
    else:
        duplicate = dict(raw['roles'][0], role_id='B')
        raw['roles'][1] = duplicate
        target = duplicate
    original = deepcopy(raw)
    observed = pipeline._fine_navigation_points(raw, window)
    rejected = next(role for role in observed['roles'] if role['role_id'] == target['role_id'])
    assert rejected['identity_status'] == 'qfs_anchor_binding_rejected'
    assert rejected['identity_confirmed'] is False and 'entity_id' not in rejected
    assert observed['usable_ranges'][0]['role_ids'] == (['A'] if disagreement == 'duplicate' else [])
    assert observed['events'] == raw['events']
    assert contracts.validate_fine(observed, window) is observed
    assert raw == original
    window['observation'] = observed
    with pytest.raises(ValueError, match='cast_entity_not_supported_by_observation') as caught:
        contracts.validate_task_cast([dict(cast_id='rejected', entity_id=identity['entity_id'],
            bindings=[dict(window_id=window['window_id'], role_id=target['role_id'])])], [window])
    assert {'path', 'value', 'expected', 'repair_hint'} <= caught.value.violation.keys()


def test_received_cast_exposes_real_duplicate_after_fine_confirmation():
    raw, identity, window = evidence()
    window['observation'] = pipeline._fine_navigation_points(raw, window)
    cast = json.loads((FIXTURES / 'glm_174_plan_cast.json').read_text())
    # Isolate this received window; the other binding's fine reply is absent.
    cast[0]['bindings'] = [row for row in cast[0]['bindings'] if row['window_id'] == window['window_id']]
    assert len(cast[0]['bindings']) == 1
    with pytest.raises(ValueError, match='plan:duplicate_cast_entity') as caught:
        contracts.validate_task_cast(cast, [window])
    assert caught.value.violation['path'] == '$.cast[1]'
    assert {'path', 'value', 'expected', 'repair_hint'} <= caught.value.violation.keys()
    assert contracts.validate_task_cast(cast[:1], [window])['cast_elder']['entity_id'] == identity['entity_id']
