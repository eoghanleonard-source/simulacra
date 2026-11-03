"""Frozen capture import checks with local Snowpark doubles; no live calls."""
from copy import deepcopy
import hashlib
import json

import pytest

from snowflake_engine import aggregation, cli, contracts, export, extraction, mapping, native, schema, versioning
from tests.engine.test_capture_v3 import responses
from tests.engine.test_extraction import source
from test_native import Session, config, storage


def fixture():
    data = source()
    data['client_id'] = 'synthetic'
    values = responses(data)
    values['persona']['personas'][0]['sequences'][0]['evidence'].reverse()
    cfg = config(run_id='retained-import', mapping={'synthetic': 'mapping'},
                 raw_records=[{'id': data['source_id'], 'original': 'full original source custody'}],
                 extraction_mode='split_v3', enable_model_calls=False, reprocess_retained=True,
                 evidence_ordering='source_ordinal_v1', limits={'max_output_tokens': 6144})
    versions = versioning.build_version_set(cfg, code_digest='original-code')
    # Cross-storage reuse is explicit. No installed schema guard is changed.
    versions.update(storage='engine-storage-v2', package='original-package')
    attempts = []
    for kind in ('scenario', 'persona'):
        operation = 'extraction_v3_' + kind
        messages = extraction.build_messages_v3(data, kind)
        supplied = 'extraction-v3-' + versioning.digest({'source_hash': contracts.source_hash(data),
            'versions': contracts.CAPTURE_V3_VERSIONS, 'kind': kind, 'output_style': 'compact',
            'max_output_tokens': 6144})
        fingerprint = versioning.digest({'operation': operation, 'key': supplied, 'messages': messages,
            'model': cfg['model'], 'max_output_tokens': 6144,
            'request_versions': {key: value for key, value in versions.items()
                                 if key not in ('code', 'package', 'code_digest', 'processing')}})
        attempts.append({'attempt_id': 'original-' + kind, 'operation': operation,
            'raw_response': json.dumps(values[kind], indent=2), 'model': cfg['model'],
            'max_output_tokens': 6144, 'request_fingerprint': fingerprint,
            'versions': deepcopy(versions), 'supplied_request_key': supplied,
            'original_attempt_row': {'query_id': 'original-query', 'usage': {'total_tokens': 123}}})
    manifest = {'format': 'retained-capture-v1', 'source_count': 1,
        'sources': [{'source_id': data['source_id'], 'source_hash': contracts.source_hash(data),
                     'attempts': attempts, 'original_raw_record': deepcopy(cfg['raw_records'][0])}],
        'provenance': [{'path': 'frozen-stage-a-output.json', 'sha256': 'f' * 64}]}
    cfg.update(retained_capture_manifest=manifest, retained_capture_manifest_sha256=versioning.digest(manifest))
    return data, cfg, manifest


def validate(data, cfg):
    return native.validate_retained_capture_manifest(cfg, [data], versioning.build_version_set(cfg, code_digest='new-code'))


def rebind(cfg):
    cfg['retained_capture_manifest_sha256'] = versioning.digest(cfg['retained_capture_manifest'])


def test_original_identity_recomputes_across_explicit_storage_change_without_mutation():
    data, cfg, manifest = fixture()
    before = deepcopy((data, cfg, manifest))
    selected = validate(data, cfg)
    assert selected[data['source_id']] == manifest['sources'][0]
    assert (data, cfg, manifest) == before
    native.validate_config(cfg)


@pytest.mark.parametrize('defect', ['raw_mutation', 'hash_binding', 'source_set', 'source_hash',
    'source_count', 'duplicate_source', 'duplicate_attempt', 'missing_layer', 'model', 'max_output',
    'supplied_key', 'fingerprint', 'old_model', 'prompt', 'mapping', 'settings', 'provenance', 'versions'])
def test_invalid_or_mismatched_frozen_import_fails_before_processing(defect):
    data, cfg, manifest = fixture()
    entry = manifest['sources'][0]
    attempt = entry['attempts'][0]
    if defect == 'raw_mutation':
        attempt['raw_response'] += ' changed after digest binding'
    elif defect == 'hash_binding':
        cfg['retained_capture_manifest_sha256'] = '0' * 64
    elif defect == 'source_set':
        data['source_id'] = 'different-source'
    elif defect == 'source_hash':
        entry['source_hash'] = '0' * 64
    elif defect == 'source_count':
        manifest['source_count'] = 2
    elif defect == 'duplicate_source':
        manifest['sources'].append(deepcopy(entry))
    elif defect == 'duplicate_attempt':
        entry['attempts'][1]['attempt_id'] = attempt['attempt_id']
    elif defect == 'missing_layer':
        entry['attempts'].pop()
    elif defect == 'model':
        attempt['model'] = 'different-model'
    elif defect == 'max_output':
        attempt['max_output_tokens'] = 4096
    elif defect == 'supplied_key':
        attempt['supplied_request_key'] = 'wrong-key'
    elif defect == 'fingerprint':
        attempt['request_fingerprint'] = '0' * 64
    elif defect == 'old_model':
        attempt['versions']['model'] = 'different-model'
    elif defect in ('prompt', 'mapping'):
        attempt['versions']['prompt' if defect == 'prompt' else 'input_mapping'] = 'different'
    elif defect == 'settings':
        attempt['versions']['model_settings']['temperature'] = 1
    elif defect == 'provenance':
        manifest['provenance'] = []
    else:
        del attempt['versions']['ontology']
    if defect not in ('raw_mutation', 'hash_binding'):
        rebind(cfg)
    with pytest.raises(ValueError, match='retained_capture_manifest'):
        validate(data, cfg)


def configure_processing(monkeypatch, data):
    monkeypatch.setattr(mapping, 'normalize', lambda *_: {
        'sources': [deepcopy(data)], 'issues': [], 'accounting': {'input_event_count': len(data['events'])}})
    monkeypatch.setattr(aggregation, 'aggregate', lambda *_: {
        name: [] for name in ('families', 'relations', 'compatibility', 'coverage', 'candidates', 'issues')})
    monkeypatch.setattr(export, 'build_export', lambda records, manifest: {
        'release': {'release_id': 'synthetic-import-release'}, 'manifest': manifest})
    monkeypatch.setattr(export, 'validate_export', lambda _: [])
    def forbidden(*_):
        raise AssertionError('Imported extraction must never invoke generation callbacks')
    monkeypatch.setattr(native.Runtime, 'complete', forbidden)


def test_native_import_persists_originals_and_receipt_without_copying_paid_attempts(monkeypatch):
    data, cfg, manifest = fixture()
    configure_processing(monkeypatch, data)
    session = Session()
    session.budget['max_output_tokens'] = 6144
    before = deepcopy(manifest)
    result = native.run(session, json.dumps(cfg))
    assert result['status'] == 'complete', result
    assert session.provider_calls == 0
    assert session.tables['ATTEMPTS'] == []
    assert result['usage']['completions'] == 0
    assert result['usage']['imported_capture_responses'] == 2
    assert session.tables['RUNS'][0]['CONFIG']['retained_capture_manifest'] == before
    assert session.tables['INPUT_RAW'][0]['PAYLOAD']['record'] == cfg['raw_records'][0]
    event = next(event for event in result['usage']['retained_reprocessing'] if event['event'] == 'retained_capture_import')
    receipt = event['receipt']
    assert receipt['original_source_entry'] == before['sources'][0]
    assert receipt['manifest_sha256'] == cfg['retained_capture_manifest_sha256']
    assert any('preserve supplied order' in issue
               for component in receipt['processing']['original_results']['components'] for issue in component['issues'])
    assert all(row['PAYLOAD']['retained_import_revision_id'] == receipt['revision_id'] for row in session.tables['COMPONENTS'])
    assert receipt['revision_id'] == versioning.revision_id('retained_capture_import', receipt['logical_id'], receipt, receipt['version_set'])
    assert len(session.tables['RECORD_REVISIONS']) >= 2
    assert manifest == before


def test_entire_manifest_validated_before_any_component_or_model_callback(monkeypatch):
    data, cfg, _ = fixture()
    configure_processing(monkeypatch, data)
    # Source hash is a valid digest string, but belongs to a different source.
    cfg['retained_capture_manifest']['sources'][0]['source_hash'] = 'a' * 64
    rebind(cfg)
    session = Session()
    session.budget['max_output_tokens'] = 6144
    result = native.run(session, json.dumps(cfg))
    assert result['status'] == 'failed'
    assert 'source_hash mismatch' in result['error']
    assert session.tables['COMPONENTS'] == session.tables['INPUT_SOURCES'] == session.tables['ATTEMPTS'] == []
    assert session.provider_calls == 0
    assert result['usage']['imported_capture_responses'] == 0


def test_import_does_not_bypass_same_schema_installation_guard(monkeypatch):
    _, cfg, _ = fixture()
    def mismatch(*_):
        raise ValueError('Installed schema mismatch')
    monkeypatch.setattr(schema, 'ensure_schema_version', mismatch)
    session = Session()
    result = native.run(session, json.dumps(cfg))
    assert result['status'] == 'failed'
    assert result['error'] == 'Installed schema mismatch'
    assert session.tables['ATTEMPTS'] == []
    assert session.provider_calls == 0


def test_cli_embeds_exact_artifact_binds_both_digests_and_stays_offline(tmp_path):
    _, cfg, manifest = fixture()
    config_path, artifact_path, output = (tmp_path / name for name in ('config.json', 'captures.json', 'plan.json'))
    base = {key: value for key, value in cfg.items() if not key.startswith('retained_capture_manifest')}
    base.update(warehouse='DEMO_WH', role='DEMO_ROLE')
    cli.write_json(config_path, base)
    cli.write_json(artifact_path, manifest)
    assert cli.main(['run', '--config', str(config_path), '--run-id', 'offline-import',
                     '--retained-captures', str(artifact_path), '--output', str(output)]) == 0
    plan = cli.read_json(output)
    request = plan['request']
    assert plan['executed'] is False
    assert request['retained_capture_manifest'] == manifest
    assert request['retained_capture_manifest_sha256'] == versioning.digest(manifest)
    assert request['retained_capture_manifest_file_sha256'] == hashlib.sha256(artifact_path.read_bytes()).hexdigest()
    assert request['enable_model_calls'] is False
    assert request['evidence_ordering'] == 'source_ordinal_v1'


@pytest.mark.parametrize('change', [{'enable_model_calls': True}, {'operation': 'load'}, {'extraction_mode': 'legacy'},
                                   {'evidence_ordering': None}, {'reprocess_retained': False}])
def test_import_requires_explicit_cache_only_route(change):
    data, cfg, _ = fixture()
    cfg.update(change)
    with pytest.raises(ValueError, match='retained_capture_manifest'):
        validate(data, cfg)


def test_default_run_configuration_and_usage_have_no_import_state():
    engine = native.Runtime(Session(), config())
    assert native.validate_retained_capture_manifest(engine.config) is None
    assert 'imported_capture_responses' not in engine.usage()
    assert 'retained_capture_manifest_sha256' not in engine.usage()


def test_import_keeps_embedding_budget_cache_only_without_new_reservation():
    _, cfg, _ = fixture()
    session = Session()
    session.budget['max_output_tokens'] = 6144
    engine = native.Runtime(session, cfg)
    engine.acquire()
    with pytest.raises(native.ControlError, match='disabled'):
        engine.embed(['uncached aggregation text'], 'new-aggregation')
    assert session.tables['ATTEMPTS'] == []
    assert session.provider_calls == 0
