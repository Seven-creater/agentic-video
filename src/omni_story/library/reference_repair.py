"""Extract missing fields from a received repair without rewriting paid evidence."""
from copy import deepcopy
from math import isfinite

from . import contracts
from .state import json_sha


def merge_missing_fields(original, repair):
    if original['reference_sha256'] != repair['reference_sha256']:
        raise ValueError('reference_repair_source_changed')
    result = deepcopy(original)
    added = [name for name in ('strategy', 'speech_form', 'visual_form') if name not in original]
    for name in added:
        result[name] = deepcopy(repair[name])
    strategy = result.get('strategy', {})
    if not all(k in strategy for k in ('adaptation_mode', 'speech_mode', 'timing_driver')):
        raise ValueError('creative_reference_speech_strategy_required')
    contracts.reference_strategy(result, creative_audio=True)
    contracts.text(result.get('speech_form'), 'reference/speech_form')
    form = result.get('visual_form')
    if not isinstance(form, dict) or not isinstance(form.get('evidence'), list):
        raise ValueError('reference_repair_visual_form_required')
    contracts.text(form.get('description'), 'reference/visual_form/description')
    accepted, excluded = [], []
    for item in form['evidence']:
        keys = ('start_s', 'end_s', 'observed_fact')
        if (isinstance(item, dict) and all(type(item.get(k)) in (int, float) and isfinite(item[k])
                for k in ('start_s', 'end_s')) and isinstance(item.get('observed_fact'), str)
                and item['observed_fact'].strip() and any(all(item.get(k) == old[k] for k in keys)
                for old in original['visible_evidence'])):
            accepted.append({k: item[k] for k in keys})
        else:
            excluded.append(item)
    if 'visual_form' in added:
        form['evidence'] = accepted
    elif excluded:
        raise ValueError('reference_repair_cannot_change_existing_visual_evidence')
    return result, dict(policy='received_missing_reference_fields_v1', added_fields=added,
        original_reading_sha256=json_sha(original), repair_reading_sha256=json_sha(repair),
        result_sha256=json_sha(result), excluded_unbound_visual_evidence=excluded)
