"""Offline scalar-revision regressions; synthetic callbacks, no semantic proof."""
from copy import deepcopy
import json

import pytest

from snowflake_engine import contracts, extraction, source_revision as revision
from tests.engine.test_capture_v3 import responses
from tests.engine.test_extraction import source


def fixture():
    data = source()
    raw = {kind: json.dumps(value, indent=2) for kind, value in responses(data).items()}
    baseline = extraction.reprocess_v3(data, raw, evidence_ordering='source_ordinal_v1')
    return data, raw, baseline


def roots(result):
    return revision._roots(result)


def review_for(data, baseline, patches=None):
    return {'review_status': 'complete', 'reviewed_component_ids': [c['object_id'] for c in roots(baseline)],
            'read_receipt': {'complete': True, 'last_event_id': data['events'][-1]['event_id']},
            'patches': patches or []}


def patch_for(data, baseline, path, value, kind='scenario'):
    component = next(c for c in roots(baseline) if c['kind'] == kind)
    return {'component_id': component['object_id'], 'path': path, 'value': value,
            'source_event_ids': [data['events'][0]['event_id']], 'reason': 'Qualify the retained claim against source wording.'}


def run(data, raw, review):
    calls = []
    def complete(messages, operation, key):
        calls.append((messages, operation, key))
        return review if isinstance(review, str) else json.dumps(review)
    result = revision.revise_retained(data, raw, complete)
    assert len(calls) == 1
    assert calls[0][1] == 'source_review_v1'
    return result, calls


def assert_rejected(result, baseline):
    assert result['correction_status'] == 'correction_rejected'
    assert result['source_revision']['patch_receipt']['errors']
    assert result['components'] == baseline['components']
    assert result['issues'] == baseline['issues']
    assert result['processing'] == baseline['processing']


@pytest.mark.parametrize(('path', 'value'), [
    (['needs', 0, 'assertion', 'actor'], 'staff'),
    (['needs', 0, 'assertion', 'recipient'], 'buyer'),
    (['needs', 0, 'assertion', 'action'], 'discuss possible lens replacement'),
    (['needs', 0, 'assertion', 'scope'], 'damaged lens only; completion remains unknown'),
    (['needs', 0, 'assertion', 'state'], 'conditional'),
    (['needs', 0, 'assertion', 'certainty'], 'reported'),
    (['needs', 0, 'summary'], 'Replacement remains requested; completion is unknown.'),
    (['needs', 0, 'status'], 'unknown'),
    (['summary'], 'Discussion of a requested replacement, with completion unknown.'),
])
def test_allowed_scalar_changes_keep_every_assertion_and_original_bytes(path, value):
    data, raw, baseline = fixture()
    if path[-1] == 'status':
        value = contracts.NEED_STATUSES[0]
    original = deepcopy((data, raw, baseline))
    patch = patch_for(data, baseline, path, value)
    result, calls = run(data, raw, review_for(data, baseline, [patch]))
    assert result['correction_status'] == 'correction_applied'
    target = next(c for c in roots(result) if c['kind'] == 'scenario')['payload']
    for step in path:
        target = target[step]
    assert target == value
    assert (data, raw, baseline) == original
    assert result['raw_response'] == result['raw_responses'] == raw
    assert [len(c['payload']['needs']) for c in roots(result) if c['kind'] == 'scenario'] == [
        len(c['payload']['needs']) for c in roots(baseline) if c['kind'] == 'scenario']
    receipt = result['source_revision']
    assert receipt['original_raw_responses'] == raw
    assert receipt['completion_callback_calls'] == 1
    assert 'inference_calls' not in receipt
    assert receipt['original_results'] == baseline
    assert receipt['model_review_raw'] == json.dumps(review_for(data, baseline, [patch]))
    assert receipt['new_canonical_generation']
    assert receipt['patch_receipt']['patches'][0]['old_value'] is not None
    assert calls[0][2] == receipt['request_key']


@pytest.mark.parametrize(('path', 'value'), [
    (['id'], 'changed-id'), (['needs'], []), (['needs', 0], None),
    (['needs', 0, 'id'], 'changed-child'), (['needs', 0, 'kind'], 'new-taxonomy'),
    (['needs', 0, 'related_ids', 0], 'other'),
    (['needs', 0, 'evidence', 0, 'event_id'], 'invented-event'),
    (['needs', 0, 'evidence', 0, 'quote'], 'invented quote'),
    (['needs', 0, 'evidence', 0, 'speaker'], 'staff'),
    (['needs', 0, 'evidence', 0, 'authority'], 'verified'),
    (['needs', 0, 'evidence', 0, 'role'], 'agent'),
    (['needs', -1, 'summary'], 'negative indexing'),
    (['needs', True, 'summary'], 'boolean indexing'),
    (['needs', 999, 'summary'], 'new child'),
    (['new_summary'], 'new field'),
    (['needs', 0, 'summary'], None),
    (['needs', 0, 'summary'], {'replacement': 'object'}),
    (['needs', 0, 'summary'], ''),
    (['needs', 0, 'assertion', 'state'], 'verified_completed'),
])
def test_identity_citation_deletion_taxonomy_and_nonexisting_scalar_rejected(path, value):
    data, raw, baseline = fixture()
    valid = patch_for(data, baseline, ['summary'], 'Valid correction must roll back too.')
    invalid = patch_for(data, baseline, path, value)
    result, _ = run(data, raw, review_for(data, baseline, [valid, invalid]))
    assert_rejected(result, baseline)


@pytest.mark.parametrize('defect', ['missing_read_receipt', 'incomplete', 'wrong_final', 'missing_root', 'duplicate_root',
                                   'unknown_root', 'no_cites', 'unknown_cite', 'duplicate_cite', 'reverse_cites',
                                   'no_reason', 'duplicate_patch', 'extra_field', 'unread', 'patch_extra_field'])
def test_complete_review_and_source_evidence_are_mandatory(defect):
    data, raw, baseline = fixture()
    review = review_for(data, baseline, [patch_for(data, baseline, ['summary'], 'Qualified summary.')])
    patch = review['patches'][0]
    if defect == 'missing_read_receipt': review.pop('read_receipt')
    elif defect == 'incomplete': review['read_receipt']['complete'] = False
    elif defect == 'wrong_final': review['read_receipt']['last_event_id'] = data['events'][0]['event_id']
    elif defect == 'missing_root': review['reviewed_component_ids'].pop()
    elif defect == 'duplicate_root': review['reviewed_component_ids'] += review['reviewed_component_ids'][:1]
    elif defect == 'unknown_root': patch['component_id'] = 'invented-root'
    elif defect == 'no_cites': patch['source_event_ids'] = []
    elif defect == 'unknown_cite': patch['source_event_ids'] = ['invented-event']
    elif defect == 'duplicate_cite': patch['source_event_ids'] *= 2
    elif defect == 'reverse_cites': patch['source_event_ids'] = [data['events'][-1]['event_id'], data['events'][0]['event_id']]
    elif defect == 'no_reason': patch['reason'] = ''
    elif defect == 'duplicate_patch': review['patches'] *= 2
    elif defect == 'extra_field': review['score'] = 100
    elif defect == 'unread': review['review_status'] = 'partial'
    elif defect == 'patch_extra_field': patch['delete'] = True
    result, _ = run(data, raw, review)
    assert_rejected(result, baseline)


@pytest.mark.parametrize('bad_raw', ['```json\n{}\n```', '{"review_status":"complete","review_status":"complete"}', 'NaN', '[]', ''])
def test_strict_json_review_rejects_fences_duplicate_keys_and_nonfinite(bad_raw):
    data, raw, baseline = fixture()
    result, _ = run(data, raw, bad_raw)
    assert_rejected(result, baseline)


def test_new_assertion_failure_rolls_back_whole_source_including_valid_patch():
    data, raw, baseline = fixture()
    patches = [patch_for(data, baseline, ['summary'], 'Faithful qualified text.'),
               patch_for(data, baseline, ['needs', 0, 'assertion', 'actor'], 'invented-speaker')]
    result, _ = run(data, raw, review_for(data, baseline, patches))
    assert_rejected(result, baseline)
    assert result['source_revision']['new_canonical_generation']  # Failed candidate retained too.


def test_previously_quarantined_root_remains_visible_and_valid_children_cannot_be_lost():
    data, raw, _ = fixture()
    persona = json.loads(raw['persona'])
    persona['personas'][0]['emotional_expression']['status'] = 'calm'
    raw['persona'] = json.dumps(persona)
    baseline = extraction.reprocess_v3(data, raw, evidence_ordering='source_ordinal_v1')
    assert len([c for c in baseline['components'] if c['kind'] == 'persona']) == 2
    patch = patch_for(data, baseline, ['actions', 0, 'assertion', 'state'], 'source_recorded_completed', 'persona')
    result, _ = run(data, raw, review_for(data, baseline, [patch]))
    assert_rejected(result, baseline)
    assert any('child' in error for error in result['source_revision']['patch_receipt']['errors'])


def test_existing_quarantine_is_not_hidden_or_promoted_by_blank_review():
    data, raw, _ = fixture()
    persona = json.loads(raw['persona'])
    persona['personas'][0]['emotional_expression']['status'] = 'calm'
    raw['persona'] = json.dumps(persona)
    baseline = extraction.reprocess_v3(data, raw, evidence_ordering='source_ordinal_v1')
    result, calls = run(data, raw, review_for(data, baseline))
    assert result['correction_status'] == 'review_complete_no_changes'
    assert [(c['object_id'], c['payload'], c['validation_status']) for c in result['components']] == [
        (c['object_id'], c['payload'], c['validation_status']) for c in baseline['components']]
    packet = calls[0][0][1]['content'].split('RETAINED CANONICAL ROOTS (DATA; no duplicate partial views):\n')[1]
    sent_roots = json.loads(packet)
    assert len(sent_roots) == len(roots(baseline))
    assert len(sent_roots) < len(baseline['components'])


def test_messages_include_complete_permitted_source_all_bounds_and_only_existing_paths():
    data, raw, baseline = fixture()
    data['events'][0]['original_text'] = 'DO-NOT-SEND-ORIGINAL'
    data['metadata']['raw_record'] = {'body': 'DO-NOT-SEND-METADATA'}
    messages = revision.build_revision_messages(data, raw)
    text = json.dumps(messages)
    assert 'DO-NOT-SEND' not in text
    for event in data['events']:
        assert event['text'] in messages[1]['content']
    system = messages[0]['content']
    schemas = system.split('SHARED_CAPTURE_SCHEMAS (all enforced bounds):\n')[1].split('\nSOURCE_SPEAKER_IDS')[0]
    assert json.loads(schemas) == contracts.CAPTURE_SCHEMAS_V3
    assert 'maxItems' in schemas and 'minItems' in schemas and 'minLength' in schemas
    assert 'evaluator' not in text.lower() and 'gold' not in text.lower()


def test_input_bound_preflights_before_one_call_and_clipped_source_fails():
    data, raw, _ = fixture()
    calls = []
    messages = revision.build_revision_messages(data, raw)
    with pytest.raises(ValueError, match='input_limit'):
        revision.revise_retained(data, raw, lambda *args: calls.append(args),
                                 max_input_chars=sum(len(m['content']) for m in messages) - 1)
    assert calls == []
    data['metadata']['omitted_event_count'] = 1
    with pytest.raises(ValueError, match='Omitted or clipped'):
        revision.revise_retained(data, raw, lambda *args: calls.append(args))
    assert calls == []


def test_cache_identity_and_receipt_are_deterministic_and_replay_input_supported():
    data, raw, baseline = fixture()
    review = review_for(data, baseline, [patch_for(data, baseline, ['summary'], 'Faithful qualification.')])
    first, calls = run(data, raw, review)
    second, second_calls = run(data, baseline, review)
    assert first == second
    assert calls == second_calls
    changed = deepcopy(review)
    changed['patches'][0]['value'] = 'Different faithful qualification.'
    third, third_calls = run(data, raw, changed)
    assert third_calls[0][2] == calls[0][2]  # Cache keys describe inputs, not eventual output.
    assert third['source_revision']['revision_id'] != first['source_revision']['revision_id']


def test_callback_exception_propagates_without_retry():
    data, raw, _ = fixture()
    calls = []
    def broken(*args):
        calls.append(args)
        raise RuntimeError('budget or provider refusal')
    with pytest.raises(RuntimeError, match='refusal'):
        revision.revise_retained(data, raw, broken)
    assert len(calls) == 1


def test_existing_unknown_and_observation_prose_can_be_qualified_without_label_changes():
    data, raw, _ = fixture()
    persona = json.loads(raw['persona'])
    persona['personas'][0]['unknowns'] = ['Cause remains unknown.']
    raw['persona'] = json.dumps(persona)
    baseline = extraction.reprocess_v3(data, raw, evidence_ordering='source_ordinal_v1')
    patches = [patch_for(data, baseline, ['unknowns', 0], 'Cause and intensity remain unknown.', 'persona'),
               patch_for(data, baseline, ['emotional_expression', 'observations', 0, 'summary'],
                         'The customer reports worry at this point; its cause is unknown.', 'persona')]
    result, _ = run(data, raw, review_for(data, baseline, patches))
    assert result['correction_status'] == 'correction_applied'
    before = next(c for c in roots(baseline) if c['kind'] == 'persona')['payload']
    after = next(c for c in roots(result) if c['kind'] == 'persona')['payload']
    assert after['unknowns'] == ['Cause and intensity remain unknown.']
    assert after['emotional_expression']['observations'][0]['labels'] == before['emotional_expression']['observations'][0]['labels']
    assert after['emotional_expression']['observations'][0]['evidence'] == before['emotional_expression']['observations'][0]['evidence']


@pytest.mark.parametrize('defect', ['coverage', 'child_inventory', 'child_delivery'])
def test_unexpected_parser_coverage_or_child_loss_rejects_entire_candidate(monkeypatch, defect):
    data, raw, baseline = fixture()
    review = review_for(data, baseline, [patch_for(data, baseline, ['summary'], 'Faithful qualification.')])
    original = extraction.reprocess_v3
    def broken(source_value, raw_value, *, evidence_ordering=None):
        parsed = original(source_value, raw_value, evidence_ordering=evidence_ordering)
        # Inject a candidate-only downstream regression, leaving the retained
        # baseline untouched. This verifies rollback if parser behavior changes.
        if raw_value != raw:
            if defect == 'coverage':
                parsed['issues'].append({'code': 'coverage_loss', 'reason': 'synthetic missing final event'})
            else:
                parent = next(c for c in roots(parsed) if c['kind'] == 'persona')
                if defect == 'child_inventory': parent['child_records'].pop()
                else: parent['child_records'][0]['delivery_eligible'] = False
        return parsed
    monkeypatch.setattr(extraction, 'reprocess_v3', broken)
    result, _ = run(data, raw, review)
    assert_rejected(result, baseline)


def normalized_run(data, raw, review_text):
    calls = []
    def complete(messages, operation, key):
        calls.append((messages, operation, key))
        return review_text
    result = revision.revise_retained(data, raw, complete, response_normalization='unique_review_json_v1')
    assert len(calls) == 1
    return result, calls


@pytest.mark.parametrize('formatting', ['json_fence', 'plain_fence', 'trailing_object'])
def test_optin_selects_one_final_complete_review_and_retains_prose_offsets(formatting):
    data, raw, baseline = fixture()
    review = review_for(data, baseline, [patch_for(data, baseline, ['summary'], 'Qualified replacement request.')])
    selected = json.dumps(review)
    prose = "I’ll check every retained root against the complete source.\n\n"
    if formatting == 'json_fence': text = prose + '```json\n' + selected + '\n```\n'
    elif formatting == 'plain_fence': text = prose + '```\n' + selected + '\n```'
    else: text = prose + selected + '\n '
    strict, strict_calls = run(data, raw, text)
    assert_rejected(strict, baseline)
    assert 'response_normalization' not in strict['source_revision']
    normalized, normalized_calls = normalized_run(data, raw, text)
    assert normalized['correction_status'] == 'correction_applied'
    receipt = normalized['source_revision']
    normalization = receipt['response_normalization']
    assert receipt['model_review_raw'] == text
    assert normalization['original_strict_parse_error'] == strict['source_revision']['patch_receipt']['errors'][0]
    assert normalization['applied'] is True
    start, end = normalization['selected_char_offsets']
    byte_start, byte_end = normalization['selected_utf8_byte_offsets']
    assert text[start:end] == selected
    assert text.encode('utf-8')[byte_start:byte_end].decode('utf-8') == selected
    assert byte_start > start  # Unicode prose offsets cannot be mistaken for byte offsets.
    assert normalized_calls == strict_calls  # Same request messages, operation and supplied key.


@pytest.mark.parametrize('wrap', [
    lambda v: v + '\n' + v,
    lambda v: '```json\n' + v + '\n```\n```json\n' + v + '\n```',
    lambda v: '```json\n' + v + '\n```\nActually, disregard that review.',
    lambda v: '```json\n' + v + '\n```\n{}',
    lambda v: '```json\n' + v + '\n' + v + '\n```',
    lambda v: '{"review_status":"complete"}\n```json\n' + v + '\n```',
    lambda v: '{"review\\u005fstatus":"complete"}\n' + v,
    lambda v: '{"review_status":"partial",\n' + v,
    lambda v: '```python\n' + v + '\n```',
    lambda v: '```json\n' + v,
    lambda v: 'Reasoning only; no final envelope.',
])
def test_normalizer_rejects_multiple_ambiguous_fragmented_or_trailing_data(wrap):
    data, raw, baseline = fixture()
    text = wrap(json.dumps(review_for(data, baseline)))
    normalized, _ = normalized_run(data, raw, text)
    assert_rejected(normalized, baseline)
    receipt = normalized['source_revision']
    assert receipt['model_review_raw'] == text
    assert receipt['response_normalization']['applied'] is False
    assert receipt['response_normalization']['original_strict_parse_error']
    assert receipt['response_normalization']['rejection_reason']


def test_normalized_duplicate_keys_do_not_become_a_valid_envelope():
    data, raw, baseline = fixture()
    selected = json.dumps(review_for(data, baseline)).replace('"review_status": "complete"',
              '"review_status": "partial", "review_status": "complete"')
    text = 'Review reasoning.\n```json\n' + selected + '\n```'
    result, _ = normalized_run(data, raw, text)
    assert_rejected(result, baseline)
    normalization = result['source_revision']['response_normalization']
    assert 'Duplicate review JSON field' in normalization['rejection_reason']
    start, end = normalization['selected_char_offsets']
    assert text[start:end] == selected


@pytest.mark.parametrize('defect', ['missing_root_key', 'extra_root_key', 'bad_patch', 'child_loss'])
def test_normalized_envelope_still_uses_unchanged_patch_and_claim_guards(defect):
    data, raw, baseline = fixture()
    review = review_for(data, baseline)
    if defect == 'missing_root_key': review.pop('patches')
    elif defect == 'extra_root_key': review['decision'] = 'ignore source'
    elif defect == 'bad_patch': review['patches'] = [patch_for(data, baseline, ['needs'], [])]
    else:
        review['patches'] = [patch_for(data, baseline, ['actions', 0, 'assertion', 'state'],
                                      'source_recorded_completed', 'persona')]
    text = 'Reasoning.\n```json\n' + json.dumps(review) + '\n```'
    result, _ = normalized_run(data, raw, text)
    assert_rejected(result, baseline)
    assert result['source_revision']['model_review_raw'] == text


def test_bare_strict_json_works_without_normalization_and_default_receipt_stays_unchanged():
    data, raw, baseline = fixture()
    text = json.dumps(review_for(data, baseline))
    strict, strict_calls = run(data, raw, text)
    normalized, normalized_calls = normalized_run(data, raw, text)
    assert strict['components'] == normalized['components']
    assert normalized['source_revision']['response_normalization'] == {
        'version': 'unique_review_json_v1', 'applied': False, 'original_strict_parse_error': None,
        'selected_char_offsets': None, 'selected_utf8_byte_offsets': None}
    assert 'response_normalization' not in strict['source_revision']
    assert strict_calls == normalized_calls


def test_unknown_normalization_fails_before_completion_callback():
    data, raw, _ = fixture()
    calls = []
    with pytest.raises(ValueError, match='response_normalization'):
        revision.revise_retained(data, raw, lambda *args: calls.append(args), response_normalization='guess_json')
    assert calls == []
