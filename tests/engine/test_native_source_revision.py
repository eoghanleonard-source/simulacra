"""Native semantic-revision integration with controlled provider doubles only."""
from copy import deepcopy
import json

import pytest

from snowflake_engine import aggregation, cli, contracts, export, extraction, mapping, native, source_revision, versioning
from test_native import Session, storage
from test_retained_import import fixture, rebind


def inputs():
    data, cfg, manifest = fixture()
    cfg.update(source_revision='source_assertion_revision_v1', enable_model_calls=True)
    return data, native.validate_config(cfg), manifest


def prepare(monkeypatch, data):
    monkeypatch.setattr(mapping, 'normalize', lambda *_: {
        'sources': [deepcopy(data)], 'issues': [], 'accounting': {'input_event_count': len(data['events'])}})
    monkeypatch.setattr(aggregation, 'aggregate', lambda *_: {
        name: [] for name in ('families', 'relations', 'compatibility', 'coverage', 'candidates', 'issues')})
    monkeypatch.setattr(export, 'build_export', lambda records, manifest: {
        'release': {'release_id': 'semantic-revision-synthetic'}, 'manifest': manifest})
    monkeypatch.setattr(export, 'validate_export', lambda _: [])


class ReviewSession(Session):
    def __init__(self, review='{}'):
        super().__init__()
        self.budget['max_output_tokens'] = 6144
        self.review = review

    def execute(self, sql, params):
        rows = super().execute(sql, params)
        if 'AI_COMPLETE' in sql:
            return [(json.dumps({'choices': [{'messages': self.review}],
                                'usage': {'prompt_tokens': 11, 'completion_tokens': 5, 'total_tokens': 16}}),)]
        return rows


def test_one_review_call_no_initial_extraction_preserves_invalid_correction_baseline_and_new_lineage(monkeypatch):
    data, cfg, manifest = inputs()
    prepare(monkeypatch, data)
    session = ReviewSession('invalid review JSON')
    result = native.run(session, json.dumps(cfg))
    assert result['status'] == 'complete', result
    assert session.provider_calls == 1
    attempts = session.tables['ATTEMPTS']
    assert len(attempts) == 1
    assert attempts[0]['OPERATION'] == 'source_review_v1'
    assert result['usage']['completions'] == 1
    assert result['usage']['imported_capture_responses'] == 2
    assert result['usage']['source_revision_stop'] is None
    assert result['usage']['source_revision_accounting'] == [{'source_id': data['source_id'], 'correction_status': 'correction_rejected'}]
    original = {a['operation'].removeprefix('extraction_v3_'): a['raw_response'] for a in manifest['sources'][0]['attempts']}
    baseline = extraction.reprocess_v3(data, original, evidence_ordering='source_ordinal_v1')
    assert [c['payload'] for c in baseline['components']] == [row['PAYLOAD']['payload'] for row in session.tables['COMPONENTS']]
    event = next(e for e in result['usage']['retained_reprocessing'] if e['event'] == 'source_assertion_revision')
    assert event['receipt']['original_raw_responses'] == original
    assert event['receipt']['model_review_raw'] == 'invalid review JSON'
    assert event['review_requests'][0]['attempt_id'] == attempts[0]['ATTEMPT_ID']
    assert event['review_requests'][0]['request_fingerprint'] == attempts[0]['PAYLOAD']['request_fingerprint']
    assert event['new_generation_attempt_ids'] == [attempts[0]['ATTEMPT_ID']]
    assert result['version_set']['processing'] == {'evidence_ordering': 'source_ordinal_v1', 'semantic_revision': 'source_assertion_revision_v1'}


@pytest.mark.parametrize('defect', ['source_hash', 'model', 'prompt', 'settings', 'fingerprint'])
def test_full_historical_identity_validation_precedes_any_paid_review(monkeypatch, defect):
    data, cfg, manifest = inputs()
    prepare(monkeypatch, data)
    attempt = manifest['sources'][0]['attempts'][0]
    if defect == 'source_hash':
        manifest['sources'][0]['source_hash'] = '0' * 64
    elif defect == 'model':
        cfg['model'] = 'changed-model'
    elif defect == 'prompt':
        attempt['versions']['prompt'] = 'changed-prompt'
    elif defect == 'settings':
        attempt['versions']['model_settings']['temperature'] = 1
    else:
        attempt['request_fingerprint'] = '0' * 64
    rebind(cfg)
    session = ReviewSession()
    if defect == 'model':
        with pytest.raises(ValueError, match='retained_capture_manifest'):
            native.run(session, json.dumps(cfg))
    else:
        result = native.run(session, json.dumps(cfg))
        assert result['status'] == 'failed'
    assert session.provider_calls == 0
    assert session.tables['ATTEMPTS'] == session.tables['COMPONENTS'] == []


def test_cache_only_revision_rerun_reuses_only_review_and_preserves_attempts(monkeypatch):
    data, cfg, _ = inputs()
    prepare(monkeypatch, data)
    session = ReviewSession('invalid JSON')
    first = native.run(session, json.dumps(cfg))
    attempts = deepcopy(session.tables['ATTEMPTS'])
    second = native.run(session, json.dumps(dict(cfg, run_id='cached-source-review', enable_model_calls=False)))
    assert first['status'] == second['status'] == 'complete'
    assert session.provider_calls == 1
    assert session.tables['ATTEMPTS'] == attempts
    assert second['usage']['cache_hits'] == 1
    event = next(e for e in second['usage']['retained_reprocessing'] if e['event'] == 'source_assertion_revision')
    assert event['new_generation_attempt_ids'] == []
    assert event['review_requests'][0]['attempt_id'] == attempts[0]['ATTEMPT_ID']


def test_whitelist_prohibits_new_extraction_relationship_and_unknown_operations():
    _, cfg, _ = inputs()
    engine = native.Runtime(ReviewSession(), cfg)
    engine.acquire()
    for operation in ('extraction_v3_scenario', 'extraction_v3_persona', 'relationship', 'unknown'):
        with pytest.raises(native.ControlError, match='prohibited|allowed_generation_operations'):
            engine.complete(['synthetic input'], operation, operation)
    assert engine.attempts == []
    assert engine.session.session.provider_calls == 0


@pytest.mark.parametrize('allowed', [[], ['source_review_v1', 'source_review_v1'], ['extraction_v3_scenario'], ['relationship'], ['unknown'], ['practice_objective_map_v2']])
def test_invalid_revision_operation_whitelists_fail_closed(allowed):
    _, cfg, _ = inputs()
    cfg['allowed_generation_operations'] = allowed
    with pytest.raises(ValueError, match='allowed_generation_operations'):
        native.validate_config(cfg)


@pytest.mark.parametrize('change', [{'source_revision': 'unknown'}, {'retained_capture_manifest': None},
    {'evidence_ordering': None}, {'reprocess_retained': False}, {'extraction_mode': 'legacy'},
    {'limits': {'max_output_tokens': 6144, 'max_input_chars': 61000}}])
def test_revision_requires_explicit_route_and_unchanged_generation_settings(change):
    _, cfg, _ = inputs()
    cfg.update(change)
    with pytest.raises(ValueError, match='source_revision'):
        native.validate_config(cfg)


def test_semantic_revision_and_objective_selection_versions_are_separate_from_old_generation_settings():
    _, cfg, manifest = inputs()
    old = manifest['sources'][0]['attempts'][0]['versions']
    versions = versioning.build_version_set(dict(cfg, practice={'strategy': 'objectives_v2'}), code_digest='new-code')
    assert versions['model_settings'] == old['model_settings']
    assert versions['selection']['version'] == 'engine-practice-objectives-v2'
    assert versions['processing'] == {'evidence_ordering': 'source_ordinal_v1', 'semantic_revision': 'source_assertion_revision_v1'}
    assert versioning.build_version_set(dict(cfg, source_revision=None), code_digest='code')['processing'] == 'source_ordinal_v1'


def test_provider_failure_stops_further_paid_requests_and_retains_case_with_failed_status(monkeypatch):
    data, cfg, _ = inputs()
    prepare(monkeypatch, data)
    session = ReviewSession()
    session.fail_provider = True
    result = native.run(session, json.dumps(cfg))
    assert result['status'] == 'failed'
    assert session.provider_calls == 1
    assert len(session.tables['ATTEMPTS']) == 1
    assert session.tables['ATTEMPTS'][0]['STATUS'] == 'failed'
    assert result['usage']['source_revision_stop']
    assert result['usage']['source_revision_accounting'][0]['correction_status'] == 'review_failed'
    assert len(session.tables['COMPONENTS']) == 2
    assert not session.tables['RELEASES']


def two_sources(monkeypatch):
    data, cfg, manifest = inputs()
    prepare(monkeypatch, data)
    second = deepcopy(data)
    second['source_id'] = 'synthetic-second'
    entry = deepcopy(manifest['sources'][0])
    entry.update(source_id=second['source_id'], source_hash=contracts.source_hash(second))
    for attempt in entry['attempts']:
        attempt['attempt_id'] += '-second'
        kind = attempt['operation'].removeprefix('extraction_v3_')
        supplied = 'extraction-v3-' + versioning.digest({'source_hash': contracts.source_hash(second),
            'versions': contracts.CAPTURE_V3_VERSIONS, 'kind': kind, 'output_style': 'compact', 'max_output_tokens': 6144})
        attempt['supplied_request_key'] = supplied
        attempt['request_fingerprint'] = versioning.digest({'operation': attempt['operation'], 'key': supplied,
            'messages': extraction.build_messages_v3(second, kind), 'model': attempt['model'], 'max_output_tokens': 6144,
            'request_versions': {key: value for key, value in attempt['versions'].items()
                                 if key not in ('code', 'package', 'code_digest', 'processing')}})
    manifest['sources'].append(entry)
    manifest['source_count'] = 2
    rebind(cfg)
    monkeypatch.setattr(mapping, 'normalize', lambda *_: {
        'sources': [deepcopy(data), deepcopy(second)], 'issues': [], 'accounting': {'input_event_count': 10}})
    return cfg


def test_first_provider_failure_prevents_second_review_and_keeps_both_histories_visible(monkeypatch):
    cfg = two_sources(monkeypatch)
    session = ReviewSession()
    session.fail_provider = True
    result = native.run(session, json.dumps(cfg))
    assert result['status'] == 'failed'
    assert session.provider_calls == 1
    assert len(session.tables['ATTEMPTS']) == 1
    assert len(session.tables['INPUT_SOURCES']) == 2
    assert len(session.tables['COMPONENTS']) == 4
    assert [row['correction_status'] for row in result['usage']['source_revision_accounting']] == [
        'review_failed', 'not_reviewed_after_terminal_failure']
    assert result['usage']['imported_capture_responses'] == 4


def test_generation_budget_exhaustion_stops_job_without_new_reservation_and_retains_both_cases(monkeypatch):
    cfg = two_sources(monkeypatch)
    cfg['limits']['max_generation_calls'] = 1
    session = ReviewSession('invalid JSON')
    session.budget['max_generation_calls'] = 1
    result = native.run(session, json.dumps(cfg))
    assert result['status'] == 'failed'
    assert session.provider_calls == 1
    assert len(session.tables['ATTEMPTS']) == 1
    assert len(session.tables['COMPONENTS']) == 4
    assert 'exhausted' in result['usage']['source_revision_stop']
    assert [row['correction_status'] for row in result['usage']['source_revision_accounting']] == [
        'correction_rejected', 'review_failed']


def test_preparation_rejection_keeps_originals_and_is_distinct_from_fatal_provider_failure(monkeypatch):
    data, cfg, _ = inputs()
    prepare(monkeypatch, data)
    def invalid(*_, **__):
        raise ValueError('Synthetic bounded preparation failure')
    monkeypatch.setattr(source_revision, 'revise_retained', invalid)
    session = ReviewSession()
    result = native.run(session, json.dumps(cfg))
    assert result['status'] == 'partial'
    assert result['usage']['source_revision_stop'] is None
    assert result['usage']['source_revision_accounting'][0]['correction_status'] == 'correction_rejected'
    assert session.provider_calls == 0
    assert len(session.tables['COMPONENTS']) == 2


def test_cli_revision_flags_record_explicit_operations_and_model_authority(tmp_path):
    _, cfg, manifest = inputs()
    base = {key: value for key, value in cfg.items() if not key.startswith('retained_capture_manifest') and key not in ('source_revision', 'evidence_ordering', 'reprocess_retained', 'allowed_generation_operations')}
    base.update(warehouse='DEMO_WH', role='DEMO_ROLE', enable_model_calls=False)
    config_path, captures, output = [tmp_path / name for name in ('config.json', 'captures.json', 'plan.json')]
    cli.write_json(config_path, base)
    cli.write_json(captures, manifest)
    assert cli.main(['run', '--config', str(config_path), '--run-id', 'bounded-review', '--retained-captures', str(captures),
        '--source-revision', 'source_assertion_revision_v1', '--enable-model-calls',
        '--allow-generation-operation', 'source_review_v1', '--allow-generation-operation', 'practice_objective_map_v2',
        '--allow-generation-operation', 'practice_objective_select_v2', '--output', str(output)]) == 0
    request = cli.read_json(output)['request']
    assert request['enable_model_calls'] is True
    assert request['source_revision'] == 'source_assertion_revision_v1'
    assert set(request['allowed_generation_operations']) == native.REVIEW_PLANNER_OPERATIONS


def test_deferred_pilot_and_final_policy_share_review_identity_and_reuse_paid_attempt(monkeypatch):
    from snowflake_engine import practice
    data, cfg, _ = inputs()
    prepare(monkeypatch, data)
    cfg.update(practice={'strategy': 'objectives_v2', 'objective_map_requests': 8, 'planner_requests': 9},
               defer_practice=True)
    planner_calls = []
    def programme(components, grouped, complete, policy, versions):
        planner_calls.append((deepcopy(policy), deepcopy(versions)))
        complete(['synthetic final planner request'], 'practice_objective_map_v2', 'controlled-map')
        return {'issues': [], 'practice_groups': [], 'assignments': []}
    monkeypatch.setattr(practice, 'build_programme', programme)
    session = ReviewSession('invalid correction JSON')
    pilot = native.run(session, json.dumps(cfg))
    assert pilot['status'] == 'partial'
    assert pilot['usage']['practice_deferred'] is True
    assert any(issue.get('practice_deferred') for issue in pilot['issues'])
    assert session.tables['RELEASES'][0]['MANIFEST']['practice_deferred'] is True
    assert planner_calls == []
    assert session.tables['PRACTICE'] == []
    assert [row['OPERATION'] for row in session.tables['ATTEMPTS']] == ['source_review_v1']
    original_review = deepcopy(session.tables['ATTEMPTS'][0])
    final_cfg = dict(cfg, defer_practice=False, run_id='final-same-policy',
                     allowed_generation_operations=sorted(native.REVIEW_PLANNER_OPERATIONS))
    final = native.run(session, json.dumps(final_cfg))
    assert final['status'] == 'complete', final
    assert final['version_set'] == pilot['version_set']
    assert final['usage']['cache_hits'] == 1
    assert len(planner_calls) == 1
    assert planner_calls[0][0] == cfg['practice']
    assert [row['OPERATION'] for row in session.tables['ATTEMPTS']] == ['source_review_v1', 'practice_objective_map_v2']
    assert session.tables['ATTEMPTS'][0] == original_review
    pilot_event = next(event for event in pilot['usage']['retained_reprocessing'] if event['event'] == 'source_assertion_revision')
    final_event = next(event for event in final['usage']['retained_reprocessing'] if event['event'] == 'source_assertion_revision')
    assert pilot_event['review_requests'][0]['request_key'] == final_event['review_requests'][0]['request_key']
    assert pilot_event['review_requests'][0]['request_fingerprint'] == final_event['review_requests'][0]['request_fingerprint']
    assert final_event['new_generation_attempt_ids'] == []
    assert 'practice_deferred' not in final['usage']


@pytest.mark.parametrize('value', [None, 1, 'true'])
def test_defer_practice_requires_an_explicit_boolean(value):
    _, cfg, _ = inputs()
    cfg['defer_practice'] = value
    with pytest.raises(ValueError, match='defer_practice'):
        native.validate_config(cfg)
    with pytest.raises(ValueError, match='defer_practice'):
        cli.validate_config(dict(cfg, warehouse='DEMO_WH', role='DEMO_ROLE'))


def test_cli_defer_flag_keeps_full_policy_in_offline_request(tmp_path):
    _, cfg, manifest = inputs()
    base = {key: value for key, value in cfg.items() if not key.startswith('retained_capture_manifest') and key not in ('source_revision', 'evidence_ordering', 'reprocess_retained', 'allowed_generation_operations')}
    base.update(warehouse='DEMO_WH', role='DEMO_ROLE', enable_model_calls=False,
                practice={'strategy': 'objectives_v2', 'planner_requests': 9, 'objective_map_requests': 8})
    config_path, captures, output = [tmp_path / name for name in ('config.json', 'captures.json', 'plan.json')]
    cli.write_json(config_path, base)
    cli.write_json(captures, manifest)
    assert cli.main(['run', '--config', str(config_path), '--run-id', 'deferred-pilot', '--retained-captures', str(captures),
        '--source-revision', 'source_assertion_revision_v1', '--enable-model-calls', '--defer-practice', '--output', str(output)]) == 0
    request = cli.read_json(output)['request']
    assert request['defer_practice'] is True
    assert request['practice'] == base['practice']
    assert request['allowed_generation_operations'] == ['source_review_v1']


@pytest.mark.parametrize('operation', sorted(native.REVIEW_PLANNER_OPERATIONS))
def test_exact_messages_are_committed_with_reservation_before_review_or_planner_provider_call(operation):
    messages = [{'role': 'system', 'content': 'Frozen construction.'},
                {'role': 'user', 'content': 'Complete permitted source: café.'}]
    class BeforeSendSession(ReviewSession):
        def execute(self, sql, params):
            if 'AI_COMPLETE' in sql:
                row = self.tables['ATTEMPTS'][-1]
                payload = row['PAYLOAD']
                assert row['STATUS'] == 'reserved'
                assert row['ATTEMPT_ID'] in self.committed_reservations
                assert not self.in_transaction
                assert payload['request_messages'] == messages
                assert payload['request_messages_sha256'] == versioning.digest(messages)
                assert payload['request_input_chars'] == len(params[1])
                assert 'sensitive-config-only-value' not in json.dumps(payload)
                reserved_revision = self.tables['RECORD_REVISIONS'][-1]['PAYLOAD']
                assert reserved_revision['STATUS'] == 'reserved'
                assert reserved_revision['PAYLOAD']['request_messages'] == messages
            return super().execute(sql, params)
    _, cfg, _ = inputs()
    cfg.update(persist_request_messages=True, allowed_generation_operations=sorted(native.REVIEW_PLANNER_OPERATIONS),
               unused_config_secret='sensitive-config-only-value')
    session = BeforeSendSession()
    engine = native.Runtime(session, native.validate_config(cfg))
    engine.acquire()
    engine.complete(messages, operation, 'frozen-request')
    assert session.provider_calls == 1
    stored = deepcopy(session.tables['ATTEMPTS'][0]['PAYLOAD']['request_messages'])
    messages[1]['content'] = 'caller mutated after return'
    assert session.tables['ATTEMPTS'][0]['PAYLOAD']['request_messages'] == stored


def test_message_persistence_defaults_off_and_does_not_change_request_identity():
    _, cfg, _ = inputs()
    messages = [{'role': 'user', 'content': 'same exact message'}]
    default = native.Runtime(ReviewSession(), cfg)
    persisted = native.Runtime(ReviewSession(), dict(cfg, persist_request_messages=True))
    assert default.versions == persisted.versions
    assert default.request_key('source_review_v1', 'same', messages) == persisted.request_key('source_review_v1', 'same', messages)
    default.acquire()
    default.complete(messages, 'source_review_v1', 'same')
    assert 'request_messages' not in default.attempts[0]['PAYLOAD']


@pytest.mark.parametrize('value', [None, 1, 'true'])
def test_request_message_persistence_requires_explicit_boolean(value):
    _, cfg, _ = inputs()
    cfg['persist_request_messages'] = value
    with pytest.raises(ValueError, match='persist_request_messages'):
        native.validate_config(cfg)
    with pytest.raises(ValueError, match='persist_request_messages'):
        cli.validate_config(dict(cfg, warehouse='DEMO_WH', role='DEMO_ROLE'))


def r3_config(cfg):
    return native.validate_config(dict(cfg, input_measurement='message_content_chars_v1',
                                       response_normalization='unique_review_json_v1'))


def escaped_messages():
    # A real-like JSON document held in a message string gains a second escaping
    # layer on transport, although its decoded content stays below 60,000 chars.
    content = ('{"quote":"' + '\\' * 20 + '","id":"x"}\n') * 1000
    return [{'role': 'system', 'content': 'Frozen instructions.'}, {'role': 'user', 'content': content}]


@pytest.mark.parametrize('operation', sorted(native.REVIEW_PLANNER_OPERATIONS))
def test_r3_measures_decoded_content_and_commits_both_sizes_before_provider(operation):
    messages = escaped_messages()
    content_chars = sum(len(message['content']) for message in messages)
    assert content_chars < 60000 < len(native._json({'messages': messages}))
    class MeasuredSession(ReviewSession):
        def execute(self, sql, params):
            if 'AI_COMPLETE' in sql:
                measurement = self.tables['ATTEMPTS'][-1]['PAYLOAD']['request_measurements']
                assert measurement == {'version': 'message_content_chars_v1', 'content_chars': content_chars,
                    'content_char_limit': 60000, 'transport_chars': len(params[1]),
                    'transport_bytes': len(params[1].encode('utf-8')), 'transport_byte_limit': 361024}
                assert self.tables['ATTEMPTS'][-1]['ATTEMPT_ID'] in self.committed_reservations
                assert self.tables['RECORD_REVISIONS'][-1]['PAYLOAD']['PAYLOAD']['request_measurements'] == measurement
            return super().execute(sql, params)
    _, cfg, _ = inputs()
    cfg = r3_config(dict(cfg, allowed_generation_operations=sorted(native.REVIEW_PLANNER_OPERATIONS)))
    session = MeasuredSession()
    engine = native.Runtime(session, cfg)
    engine.acquire()
    engine.complete(messages, operation, 'same-frozen-generation-request')
    assert session.provider_calls == 1


def test_r3_does_not_raise_content_bound_and_has_a_separate_transport_byte_guard():
    _, cfg, _ = inputs()
    engine = native.Runtime(ReviewSession(), r3_config(cfg))
    engine.acquire()
    with pytest.raises(native.ControlError, match='decoded message contents exceed'):
        engine.complete([{'role': 'user', 'content': 'x' * 60001}], 'source_review_v1', 'too-much-content')
    with pytest.raises(native.ControlError, match='transport exceeds'):
        engine.complete([{'role': 'user', 'content': 'short', 'extra': 'x' * 362000}], 'source_review_v1', 'too-much-wire')
    assert engine.attempts == []
    assert engine.session.session.provider_calls == 0


def test_r3_handles_worst_case_json_escaping_without_clipping():
    _, cfg, _ = inputs()
    session = ReviewSession()
    engine = native.Runtime(session, r3_config(cfg))
    engine.acquire()
    messages = [{'role': 'user', 'content': '\x00' * 60000}]
    engine.complete(messages, 'source_review_v1', 'worst-escaping')
    measure = engine.attempts[0]['PAYLOAD']['request_measurements']
    assert measure['content_chars'] == 60000
    assert 360000 < measure['transport_bytes'] <= 361024
    assert session.provider_calls == 1


def test_legacy_serialized_guard_remains_strict_and_other_operations_do_not_get_r3_measurement():
    _, cfg, _ = inputs()
    for current, operation in ((cfg, 'source_review_v1'), (r3_config(cfg), 'relationship')):
        engine = native.Runtime(ReviewSession(), current)
        engine.acquire()
        with pytest.raises(native.ControlError, match='permitted prompt exceeds'):
            engine.complete(escaped_messages(), operation, 'serialized-over-bound')
        assert engine.attempts == []


def test_r3_processing_upgrade_reuses_five_original_successful_fingerprints_without_repurchase():
    _, cfg, _ = inputs()
    session = ReviewSession('original exact raw response')
    first = native.Runtime(session, cfg)
    first.acquire()
    messages = [[{'role': 'user', 'content': 'Frozen source review ' + str(index)}] for index in range(5)]
    for index, request in enumerate(messages):
        first.complete(request, 'source_review_v1', 'fixed-key-' + str(index))
    first.release()
    originals = deepcopy(session.tables['ATTEMPTS'])
    later = native.Runtime(session, r3_config(dict(cfg, run_id='r3-continuation', enable_model_calls=False)))
    later.versions.update(code='new-operational-package-code', package='new-operational-package')
    later.acquire()
    for index, request in enumerate(messages):
        assert later.complete(request, 'source_review_v1', 'fixed-key-' + str(index)) == 'original exact raw response'
    assert session.provider_calls == 5
    assert session.tables['ATTEMPTS'] == originals
    assert later.usage()['completions'] == 5
    assert later.usage()['cache_hits'] == 5
    assert later.versions['processing'] == {'evidence_ordering': 'source_ordinal_v1',
        'semantic_revision': 'source_assertion_revision_v1', 'review_response_format': 'unique_review_json_v1',
        'input_measurement': 'message_content_chars_v1'}


@pytest.mark.parametrize('change', [{'input_measurement': 'unknown', 'response_normalization': 'unique_review_json_v1'},
    {'input_measurement': 'message_content_chars_v1'}, {'response_normalization': 'unique_review_json_v1'},
    {'input_measurement': 'message_content_chars_v1', 'response_normalization': 'unknown'}])
def test_r3_processing_modes_require_the_exact_complete_pair(change):
    _, cfg, _ = inputs()
    cfg.update(change)
    with pytest.raises(ValueError, match='input_measurement|response_normalization'):
        native.validate_config(cfg)


def test_r3_normalizes_cached_review_without_new_generation_or_changing_original_rejection(monkeypatch):
    data, cfg, manifest = inputs()
    prepare(monkeypatch, data)
    raw = {attempt['operation'].removeprefix('extraction_v3_'): attempt['raw_response']
           for attempt in manifest['sources'][0]['attempts']}
    baseline = extraction.reprocess_v3(data, raw, evidence_ordering='source_ordinal_v1')
    review = {'review_status': 'complete', 'reviewed_component_ids': [component['object_id']
        for component in sorted(baseline['components'], key=lambda row: (row['kind'], row['object_id']))
        if component['delivery_scope'] == 'full_component'],
        'read_receipt': {'last_event_id': data['events'][-1]['event_id'], 'complete': True}, 'patches': []}
    response = 'Review notes retained.\n```json\n' + json.dumps(review) + '\n```'
    session = ReviewSession(response)
    strict = native.run(session, json.dumps(cfg))
    old_attempts = deepcopy(session.tables['ATTEMPTS'])
    normalized = native.run(session, json.dumps(r3_config(dict(cfg, run_id='r3-normalize-cached', enable_model_calls=False))))
    assert strict['status'] == normalized['status'] == 'complete'
    assert session.tables['ATTEMPTS'] == old_attempts
    assert session.provider_calls == 1
    old_event = next(event for event in strict['usage']['retained_reprocessing'] if event['event'] == 'source_assertion_revision')
    new_event = next(event for event in normalized['usage']['retained_reprocessing'] if event['event'] == 'source_assertion_revision')
    assert old_event['receipt']['correction_status'] == 'correction_rejected'
    assert new_event['receipt']['correction_status'] == 'review_complete_no_changes'
    assert new_event['receipt']['response_normalization']['applied'] is True
    assert new_event['receipt']['response_normalization']['original_strict_parse_error']
    assert old_event['receipt']['model_review_raw'] == new_event['receipt']['model_review_raw'] == response
    assert new_event['new_generation_attempt_ids'] == []
    assert old_event['review_requests'][0]['request_fingerprint'] == new_event['review_requests'][0]['request_fingerprint']
