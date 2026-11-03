"""Synthetic deterministic replay regressions; no semantic benchmark or paid calls."""
from copy import deepcopy
import json

import pytest

from snowflake_engine import cli, contracts, extraction, native, reprocessing, versioning
from tests.engine.test_capture_v3 import responses
from tests.engine.test_extraction import cite, source
from test_native import Session, config, runtime, storage


def retained():
    data = source()
    values = responses(data)
    values['scenario']['scenarios'][0]['needs'][0]['evidence'] = [cite(data, 4), cite(data, 0)]
    values['persona']['personas'][0]['sequences'][0]['evidence'].reverse()
    return data, values, {kind: json.dumps(value, indent=2) for kind, value in values.items()}


def replay(data, raw):
    return extraction.reprocess_v3(data, raw, evidence_ordering='source_ordinal_v1')


def test_original_rejections_raw_bytes_and_source_are_retained_with_new_processing_revision():
    data, values, raw = retained()
    before = deepcopy((data, values, raw))
    original = extraction.reprocess_v3(data, raw)
    result = replay(data, raw)
    assert (data, values, raw) == before
    assert result['raw_response'] == result['raw_responses'] == raw
    receipt = result['processing']
    assert receipt['original_raw_responses'] == raw
    assert receipt['original_results'] == original
    assert any('preserve supplied order' in issue for component in original['components'] for issue in component['issues'])
    assert all(c['validation_status'] == 'valid' for c in result['components'])
    assert result['versions']['processing'] == receipt['processing_version'] == 'source_ordinal_v1'
    assert result['processing'] == replay(data, raw)['processing']
    for component in result['components']:
        assert component['processing_revision_id'] == receipt['revision_id']
    versions = versioning.build_version_set(config(), code_digest='synthetic-code')
    first = versioning.annotate(original['components'][0], 'scenario', versions, ['same-local-key'])
    second = versioning.annotate(result['components'][0], 'scenario', dict(versions, processing='source_ordinal_v1'), ['same-local-key'])
    assert first['logical_id'] == second['logical_id']
    assert first['revision_id'] != second['revision_id']


def test_schema_directed_sort_preserves_every_other_value_duplicates_and_equal_event_order():
    data, values, _ = retained()
    value = values['scenario']
    refs = [cite(data, 4), cite(data, 0, 'I feel worried.'), cite(data, 0, 'Please replace'), cite(data, 4)]
    value['scenarios'][0]['needs'][0]['evidence'] = deepcopy(refs)
    # Unknown properties and fields named evidence outside the contract are not a repair surface.
    value['extra'] = {'evidence': deepcopy(refs)}
    value['scenarios'][0]['needs'][0]['assertion']['action'] = 'Arbitrary asserted text remains exactly unchanged.'
    original = deepcopy(value)
    ordered, changes = reprocessing.order_evidence(value, data, 'scenario')
    assert len(changes['changed_arrays']) == 1
    actual_refs = ordered['scenarios'][0]['needs'][0]['evidence']
    assert actual_refs == [refs[1], refs[2], refs[0], refs[3]]
    ordered['scenarios'][0]['needs'][0]['evidence'] = refs
    assert ordered == original == value


@pytest.mark.parametrize('defect', ['unknown_event', 'malformed', 'extra_property'])
def test_unrecognized_reference_arrays_remain_exact_and_quarantined(defect):
    data, values, raw = retained()
    refs = values['scenario']['scenarios'][0]['needs'][0]['evidence']
    if defect == 'unknown_event':
        refs[0]['event_id'] = 'unknown-event'
    elif defect == 'malformed':
        refs[0]['quote'] = 7
    else:
        refs[0]['invented'] = 'unchanged'
    raw['scenario'] = json.dumps(values['scenario'])
    result = replay(data, raw)
    scenario = next(c for c in result['components'] if c['kind'] == 'scenario')
    assert scenario['payload'] == values['scenario']['scenarios'][0]
    assert scenario['validation_status'] == 'quarantined'
    assert result['processing']['processed_raw_responses']['scenario'] == raw['scenario']
    assert result['processing']['changes']['scenario']['skipped_arrays']


@pytest.mark.parametrize('defect', ['quote', 'role', 'authority', 'speaker', 'assertion', 'endpoint'])
def test_ordering_cannot_remove_nonordering_validation_failures(defect):
    data, values, raw = retained()
    persona = values['persona']['personas'][0]
    if defect in ('quote', 'role', 'authority', 'speaker'):
        persona['sequences'][0]['evidence'][0][defect] = 'not the supplied value'
    elif defect == 'assertion':
        persona['actions'][0]['assertion']['certainty'] = 'invented-certainty'
    else:
        change = persona['emotional_changes'][0]
        change['from_id'], change['to_id'] = change['to_id'], change['from_id']
        change['evidence'].reverse()
    raw['persona'] = json.dumps(values['persona'])
    result = replay(data, raw)
    parent = next(c for c in result['components'] if c['kind'] == 'persona' and c['delivery_scope'] == 'full_component')
    assert parent['validation_status'] == 'quarantined'
    assert any('preserve supplied order' not in error for error in parent['issues'])
    # Restore only the known reference permutations: every asserted value is identical.
    expected = deepcopy(persona)
    expected['sequences'][0]['evidence'].sort(key=lambda ref: next(e['ordinal'] for e in data['events'] if e['event_id'] == ref['event_id']))
    expected['emotional_changes'][0]['evidence'].sort(key=lambda ref: next(e['ordinal'] for e in data['events'] if e['event_id'] == ref['event_id']))
    assert parent['payload'] == expected


def test_invalid_json_is_retained_and_other_layer_replays_without_retry():
    data, _, raw = retained()
    raw['persona'] = '{"personas":['
    result = replay(data, raw)
    assert result['processing']['processed_raw_responses']['persona'] == raw['persona']
    assert result['issues'][0]['code'] == 'invalid_json'
    assert [c['kind'] for c in result['components']] == ['scenario']
    assert result['components'][0]['validation_status'] == 'valid'


def test_default_unchanged_and_processing_is_idempotent_without_changing_generation_request():
    data, values, raw = retained()
    default = extraction.reprocess_v3(data, raw)
    assert 'processing' not in default
    assert 'processing' not in default['versions']
    first = replay(data, raw)
    second = replay(data, first['processing']['processed_raw_responses'])
    assert [c['payload'] for c in first['components']] == [c['payload'] for c in second['components']]
    assert not any(c['changed_arrays'] for c in second['processing']['changes'].values())
    calls = []
    def complete(messages, operation, key):
        calls.append((messages, operation, key))
        return raw[operation.removeprefix('extraction_v3_')]
    extraction.extract_v3(data, complete)
    original_requests = deepcopy(calls)
    calls.clear()
    extraction.extract_v3(data, complete, evidence_ordering='source_ordinal_v1')
    assert calls == original_requests


def replay_config(**overrides):
    return config(extraction_mode='split_v3', reprocess_retained=True, enable_model_calls=False,
                  evidence_ordering='source_ordinal_v1', limits={'max_output_tokens': 6144}, **overrides)


def test_native_reuses_exact_generation_identity_without_new_attempt_and_fails_closed_on_miss():
    session = Session()
    session.budget['max_output_tokens'] = 6144
    first = runtime(session, extraction_mode='split_v3', limits={'max_output_tokens': 6144})
    raw = first.complete(['same complete source'], 'extraction_v3_scenario', 'source-full-hash')
    first.release()
    attempts = deepcopy(session.tables['ATTEMPTS'])
    later = native.Runtime(session, replay_config())
    later.acquire()
    assert later.versions['processing'] == 'source_ordinal_v1'
    assert later.complete(['same complete source'], 'extraction_v3_scenario', 'source-full-hash') == raw
    assert session.tables['ATTEMPTS'] == attempts
    assert session.provider_calls == 1  # Only the synthetic initial call.
    assert later.processing_events[0]['attempt_id'] == attempts[0]['ATTEMPT_ID']
    with pytest.raises(native.ControlError, match='disabled'):
        later.complete(['changed complete source'], 'extraction_v3_scenario', 'source-full-hash')
    later.versions['prompt'] = 'different-prompt'
    with pytest.raises(native.ControlError, match='disabled'):
        later.complete(['same complete source'], 'extraction_v3_scenario', 'source-full-hash')
    assert session.tables['ATTEMPTS'] == attempts


@pytest.mark.parametrize('change', [{'enable_model_calls': True}, {'extraction_mode': 'legacy'}, {'reprocess_retained': False}, {'evidence_ordering': 'unknown'}])
def test_native_and_cli_reject_unsafe_or_unknown_optin(change):
    value = {**replay_config(), **change}
    with pytest.raises(ValueError, match='evidence_ordering'):
        native.validate_config(value)
    value.update(warehouse='DEMO_WH', role='DEMO_ROLE')
    with pytest.raises(ValueError, match='evidence_ordering'):
        cli.validate_config(value)


def test_native_same_schema_guard_is_still_required(monkeypatch):
    def reject(*_):
        raise ValueError('Different installed schema/manifest')
    monkeypatch.setattr(native.schema, 'ensure_schema_version', reject)
    engine = native.Runtime(Session(), replay_config())
    with pytest.raises(ValueError, match='Different installed schema'):
        engine.acquire()
    assert engine.session.session.provider_calls == 0
