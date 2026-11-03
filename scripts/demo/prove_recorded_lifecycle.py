#!/usr/bin/env python3
"""Wholly fictional, offline lifecycle proof through the ordinary native engine.

Only SQL storage/transaction execution is simulated. native.run, schema writes,
normalization, extraction validators, durable cache, aggregation, versioning and
export are the production implementations. No engine callback is monkeypatched.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests/engine'))
from snowflake_engine import __version__, contracts, export, mapping, native, schema, versioning
from test_native import Session

DEFAULT_OUTPUT = ROOT / 'artifacts/demo/qualification-v2-delivery-20261102/lifecycle'
CLIENT = 'fictional-lifecycle-only'


def json_text(value):
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + '\n'


class LocalSession(Session):
    """Existing Snowpark double plus the exact INSERT/MERGE SQL schema emits."""
    def __init__(self):
        super().__init__()
        self.budget.update(max_output_tokens=6144, max_sql_statements=1000)
        self.capture_config = {'allow_recorded_run': True,
                               'permitted_run_execution_mode': 'recorded_smoke',
                               'recorded_run_contract': 'recorded-run-responses-v1'}

    @staticmethod
    def table_name(sql):
        match = re.search(r'(?:INTO|FROM) [A-Z_0-9]+\.[A-Z_0-9]+\.([A-Z_0-9]+)', sql)
        if not match:
            raise AssertionError('Unrecognized storage statement: ' + sql)
        return match[1]

    def execute(self, sql, params):
        if 'AI_COMPLETE' in sql or 'AI_EMBED' in sql:
            self.provider_calls += 1
            raise AssertionError('Provider SQL is prohibited in this offline proof')
        if sql.startswith('SELECT SCHEMA_VERSION, MANIFEST_HASH'):
            self.log.append((sql, deepcopy(params)))
            return [(schema.SCHEMA_VERSION, schema.manifest_hash())]
        if 'SELECT CAPTURE_CONFIG' in sql:
            self.log.append((sql, deepcopy(params)))
            return [(json.dumps(self.capture_config),)]
        if sql.startswith(('INSERT INTO ', 'MERGE INTO ')):
            self.log.append((sql, deepcopy(params)))
            assert self.in_transaction and self.transaction_origin == 'explicit'
            table = self.table_name(sql)
            if sql.startswith('INSERT'):
                columns = sql.split(' (', 1)[1].split(')', 1)[0].split(', ')
            else:
                columns = re.findall(r' AS ([A-Z_0-9]+)', sql.split(') s ON ', 1)[0])
            assert len(columns) == len(params), sql
            row = {column: json.loads(value) if schema.TABLE_SPECS[table][column] == 'VARIANT'
                   and value is not None else value for column, value in zip(columns, params)}
            if sql.startswith('MERGE'):
                keys = re.findall(r't\.([A-Z_0-9]+)=s\.', sql.split(' WHEN ', 1)[0])
                for index, old in enumerate(self.tables[table]):
                    if all(old[key] == row[key] for key in keys):
                        self.tables[table][index] = deepcopy(row)
                        return []
            self.tables[table].append(deepcopy(row))
            return []
        return super().execute(sql, params)


def seed_mapping():
    """Explicit fictional input mapping; no real source or evaluation data."""
    return {'mapping_version': 'fictional-lifecycle-mapping-v1',
            'record_type_field': 'record_type', 'raw_id_field': 'record_id',
            'redaction': {'reviewed': True, 'scope': 'Wholly fictional lifecycle fixture only', 'rules': []},
            'types': {'fictional_chat': {
                'source_type': 'typed_chat', 'fields': {'source_id': 'history_id'},
                'metadata': {'domain': 'domain', 'customer_side_speaker_ids': 'customer_side_speaker_ids'},
                'metadata_defaults': {'fictional': True}, 'guidance_path': 'guidance',
                'events': [{'path': 'messages', 'kind': 'message',
                            'fields': {'event_id': 'id', 'ordinal': 'position', 'role': 'role',
                                       'speaker': 'speaker', 'text': 'body'},
                            'defaults': {'authority': 'participant_message'}}]}}}


def seed_records(change='baseline'):
    histories = [
        ('fictional-service-01', 'service', [
            'Please inspect the cracked telescope lens.',
            'Inspection is pending. No replacement has shipped.',
            'Please email the inspection estimate; the repair decision is still pending.']),
        ('fictional-sales-01', 'sales', [
            'Please quote six fictional observatory lamps.',
            'A quote is being prepared. No order has been accepted.',
            'Please include delivery to the imaginary north dome.'])]
    if change == 'changed':
        histories[0][2][2] = 'Please email the inspection estimate and cancel the spare cap; the repair decision is still pending.'
    if change == 'failed':
        histories[0][2][2] = 'Please email the inspection estimate and cancel two spare caps; the repair decision is still pending.'
    records = []
    for sid, domain, texts in histories:
        records.append({'record_id': sid, 'history_id': sid, 'record_type': 'fictional_chat',
                        'domain': domain, 'customer_side_speaker_ids': ['customer'],
                        'guidance': [{'guidance_id': 'fictional-guidance-' + domain,
                                      'text': 'Fictional example only: client handling guidance remains unresolved.',
                                      'authority': 'fictional', 'status': 'unresolved'}],
                        'messages': [{'id': f'{sid}-event-{i}', 'position': i,
                                      'speaker': 'staff' if i == 1 else 'customer',
                                      'role': 'agent' if i == 1 else 'customer', 'body': text}
                                     for i, text in enumerate(texts)]})
    return records


def citation(source, ordinal):
    event = source['events'][ordinal]
    return {key: event[key] for key in ('event_id', 'role', 'speaker', 'authority')} | {'quote': event['text']}


def predetermined_responses(source):
    """Author fixture claims from fictional text; never represent model inference."""
    service = source['metadata']['domain'] == 'service'
    action = 'inspect telescope lens' if service else 'quote six lamps'
    scope = 'Inspection requested; repair decision and shipment remain pending.' if service else 'Quote only; no accepted order.'
    assertion = {'actor': 'customer', 'recipient': 'staff', 'action': action,
                 'scope': scope, 'state': 'requested', 'certainty': 'explicit'}
    needs = [
        {'id': 'need-1', 'kind': 'primary_need', 'summary': source['events'][0]['text'],
         'status': 'requested', 'related_ids': [], 'evidence': [citation(source, 0)], 'assertion': assertion},
        {'id': 'need-2', 'kind': 'late_need', 'summary': source['events'][2]['text'],
         'status': 'requested', 'related_ids': [], 'evidence': [citation(source, 2)],
         'assertion': dict(assertion, action='email inspection estimate' if service else 'include north dome delivery',
                           scope=source['events'][2]['text'])}]
    if 'cancel' in source['events'][2]['text']:
        needs.append({'id': 'need-3', 'kind': 'secondary_need', 'summary': 'Cancel the spare cap.',
                      'status': 'requested', 'related_ids': [], 'evidence': [citation(source, 2)],
                      'assertion': dict(assertion, action='cancel spare cap', scope='Requested cancellation; completion unknown.')})
    scenario = {'id': 'scenario-1', 'summary': 'Fictional inspection history.' if service else 'Fictional lamp quotation history.',
                'needs': needs, 'unknowns': ['Completion and client-approved handling remain unknown.']}
    persona = {'id': 'persona-1', 'speaker': 'customer', 'evidence_status': 'observed',
               'actions': [{'id': 'action-1', 'function': 'request_action', 'summary': source['events'][0]['text'],
                            'evidence': [citation(source, 0)], 'assertion': assertion}],
               'sequences': [], 'emotional_expression': {'status': 'unknown', 'observations': []},
               'emotional_changes': [], 'unknowns': ['No emotion assessment or enduring traits supplied.']}
    receipt = {'last_event_id': source['events'][-1]['event_id'], 'complete': True}
    return {'extraction_v3_scenario': [json.dumps({'scenarios': [scenario], 'capture_status': 'complete', 'read_receipt': receipt})],
            'extraction_v3_persona': [json.dumps({'personas': [persona], 'capture_status': 'complete', 'read_receipt': receipt})]}


def request(change, run_id):
    spec, records = seed_mapping(), seed_records(change)
    normalized = mapping.normalize(records, spec, CLIENT)
    assert not normalized['issues'], normalized['issues']
    entries = []
    for source in normalized['sources']:
        responses = predetermined_responses(source)
        if change == 'failed' and source['source_id'] == 'fictional-service-01':
            responses = {operation: ['{"deliberately_invalid_fictional_response":'] for operation in responses}
        entries.append({'source_id': source['source_id'], 'source_hash': contracts.source_hash(source),
                        'operations': responses})
    return {'database': 'FICTIONAL_DEMO_DB', 'schema': 'FICTIONAL_LIFECYCLE', 'client_id': CLIENT,
            'run_id': run_id, 'operation': 'run', 'model': 'recorded-fictional-fixtures',
            'embedding_model': 'snowflake-arctic-embed-l-v2.0', 'enable_model_calls': False,
            'execution_mode': 'recorded_smoke', 'extraction_mode': 'split_v3',
            'persist_request_messages': True, 'mapping': spec, 'raw_records': records,
            'aggregation': {'max_candidate_pairs': 0, 'relationship_requests': 0},
            'limits': {'max_output_tokens': 6144, 'max_sql_statements': 1000},
            'recorded_run_responses': {'format': 'recorded-run-responses-v1', 'sources': entries}}


def rows_for(session, table, run_id):
    return [deepcopy(row) for row in session.tables[table] if row.get('RUN_ID') == run_id]


def component_revisions(session, run_id):
    return {row['SOURCE_ID'] + ':' + row['KIND']: row['REVISION_ID']
            for row in rows_for(session, 'COMPONENTS', run_id)}


def revision_changes(session, table, earlier, later):
    before = {row['LOGICAL_ID']: row for row in rows_for(session, table, earlier)}
    after = {row['LOGICAL_ID']: row for row in rows_for(session, table, later)}
    shared = set(before) & set(after)
    return {'preserved': sum(before[key]['REVISION_ID'] == after[key]['REVISION_ID'] for key in shared),
            'revised': sum(before[key]['REVISION_ID'] != after[key]['REVISION_ID'] for key in shared),
            'new_logical_records': len(set(after) - set(before)),
            'prior_logical_records_not_in_current_run': len(set(before) - set(after))}


def git_provenance():
    """Return repository provenance when available without blocking a source archive."""
    try:
        return {
            'git_head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True,
                                                stderr=subprocess.DEVNULL).strip(),
            'git_branch': subprocess.check_output(['git', 'branch', '--show-current'], cwd=ROOT, text=True,
                                                  stderr=subprocess.DEVNULL).strip(),
            'git_worktree_dirty': bool(subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT, text=True,
                                                                stderr=subprocess.DEVNULL).strip()),
            'git_provenance_status': 'available',
        }
    except (OSError, subprocess.CalledProcessError):
        return {
            'git_head': None,
            'git_branch': None,
            'git_worktree_dirty': None,
            'git_provenance_status': 'unavailable: source archive has no Git metadata',
        }


def prove(output=DEFAULT_OUTPUT):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    session, runs, snapshots, requests = LocalSession(), [], {}, []
    for stage, change in [('baseline', 'baseline'), ('unchanged', 'baseline'), ('changed', 'changed'), ('failed', 'failed')]:
        run_id = 'fictional-lifecycle-' + stage
        cfg = request(change, run_id)
        requests.append(deepcopy(cfg))
        (output / (stage + '-request.json')).write_text(json_text(cfg))
        before = len(session.tables['ATTEMPTS'])
        result = native.run(session, cfg)
        assert result['usage']['provider_model_calls'] == 0, result
        assert result['usage']['completions'] == result['usage']['embedding_inputs'] == 0, result
        assert result['usage']['fixture_attempts_this_run'] == len(session.tables['ATTEMPTS']) - before, result
        run_rows = rows_for(session, 'RUNS', run_id)
        release_rows = rows_for(session, 'RELEASES', run_id)
        snapshots[stage] = {'run': result, 'run_rows': run_rows,
                            'component_revisions': component_revisions(session, run_id),
                            'input_raw': rows_for(session, 'INPUT_RAW', run_id),
                            'input_sources': rows_for(session, 'INPUT_SOURCES', run_id),
                            'attempts': rows_for(session, 'ATTEMPTS', run_id),
                            'input_issues': rows_for(session, 'INPUT_ISSUES', run_id),
                            'coverage': rows_for(session, 'COVERAGE', run_id),
                            'release_rows': release_rows}
        for release in release_rows:
            errors = export.validate_export(release['PAYLOAD'])
            assert not errors, errors
        runs.append({'stage': stage, 'run_id': run_id, 'status': result['status'],
                     'new_recorded_fixture_attempts': len(session.tables['ATTEMPTS']) - before,
                     'cache_hits': result['usage']['cache_hits'], 'release_id': result.get('release_id'),
                     'real_hosted_model_calls': session.provider_calls})
        (output / (stage + '.json')).write_text(json_text(snapshots[stage]))
    baseline, unchanged, changed, failed = [snapshots[stage] for stage in ('baseline', 'unchanged', 'changed', 'failed')]
    assert runs[0]['new_recorded_fixture_attempts'] == 4, runs
    assert runs[1]['new_recorded_fixture_attempts'] == 0 and runs[1]['cache_hits'] == 4, runs
    assert runs[2]['new_recorded_fixture_attempts'] == 2 and runs[2]['cache_hits'] == 2, runs
    assert all(run['status'] == 'complete' for run in runs[:3]), runs
    assert baseline['component_revisions'] == unchanged['component_revisions']
    keys = baseline['component_revisions']
    unaffected = [key for key in keys if key.startswith('fictional-sales-01:')]
    affected = [key for key in keys if key.startswith('fictional-service-01:')]
    assert all(changed['component_revisions'][key] == keys[key] for key in unaffected)
    # Changing the service source revises its scenario; unchanged persona content is allowed to retain its revision.
    assert changed['component_revisions']['fictional-service-01:scenario'] != keys['fictional-service-01:scenario']
    assert baseline['release_rows'] == rows_for(session, 'RELEASES', runs[0]['run_id'])
    assert unchanged['release_rows'] == rows_for(session, 'RELEASES', runs[1]['run_id'])
    assert changed['release_rows'] == rows_for(session, 'RELEASES', runs[2]['run_id'])
    assert runs[2]['release_id'] != runs[0]['release_id']
    assert runs[3]['new_recorded_fixture_attempts'] == 2 and runs[3]['cache_hits'] == 2, runs
    assert len(failed['input_raw']) == len(failed['input_sources']) == 2
    failed_coverage = [row for row in failed['coverage'] if row['SOURCE_ID'] == 'fictional-service-01']
    assert len(failed_coverage) == 1 and failed_coverage[0]['PAYLOAD']['valid_component_count'] == 0
    assert failed['input_issues'] and len(failed['attempts']) == 2
    assert all(row['PAYLOAD'].get('raw_response') == '{"deliberately_invalid_fictional_response":' for row in failed['attempts'])
    assert all(failed['component_revisions'][key] == changed['component_revisions'][key] for key in unaffected)
    assert session.provider_calls == 0 and not session.tables['EMBEDDINGS']
    revision_summary = {}
    for stage in ('unchanged', 'changed'):
        revision_summary[stage] = {table: revision_changes(session, table, runs[0]['run_id'],
                                                         'fictional-lifecycle-' + stage)
                                   for table in ('INPUT_SOURCES', 'COMPONENTS', 'FAMILIES',
                                                 'COMPATIBILITY', 'GUIDANCE', 'COVERAGE')}
    for table in ('INPUT_SOURCES', 'COMPONENTS', 'FAMILIES', 'COMPATIBILITY', 'GUIDANCE'):
        assert revision_summary['unchanged'][table]['revised'] == 0
        assert revision_summary['unchanged'][table]['new_logical_records'] == 0
    # Derived family and join identities follow their member object identities.
    # Preserve each sales family/join verbatim even when service membership changes.
    for table in ('FAMILIES', 'COMPATIBILITY'):
        before = {row['LOGICAL_ID']: row['REVISION_ID'] for row in rows_for(session, table, runs[0]['run_id'])
                  if ('fictional-sales-01' in row['PAYLOAD'].get('source_ids', [])
                      or row['PAYLOAD'].get('source_id') == 'fictional-sales-01')}
        after = {row['LOGICAL_ID']: row['REVISION_ID'] for row in rows_for(session, table, runs[2]['run_id'])}
        assert before and all(after[key] == value for key, value in before.items())
    for stage, snapshot in snapshots.items():
        assert len(snapshot['input_raw']) == len(snapshot['input_sources']) == 2
        assert len({row['SOURCE_ID'] for row in snapshot['input_sources']}) == 2
        assert len(snapshot['component_revisions']) == len(rows_for(session, 'COMPONENTS', 'fictional-lifecycle-' + stage))
        assert len(snapshot['run_rows']) == 1
    code_paths = ['snowflake_engine/__init__.py', 'snowflake_engine/cli.py',
                  'snowflake_engine/native.py', 'snowflake_engine/schema.py', 'snowflake_engine/extraction.py',
                  'snowflake_engine/aggregation.py', 'snowflake_engine/export.py', 'snowflake_engine/versioning.py',
                  'tests/engine/test_native.py', 'scripts/demo/prove_recorded_lifecycle.py',
                  'tests/engine/test_recorded_lifecycle.py']
    code_hashes = {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in code_paths}
    report = {'schema': 'fictional-recorded-lifecycle-proof-v1', 'scope': 'local ordinary native.run with simulated Snowpark SQL',
              'engine_entrypoint': 'snowflake_engine.native.run', 'fixture_histories': 2,
              'engine_package_version': __version__,
              'fictional_domains': ['service', 'sales'], 'runs': runs,
              'total_recorded_fixture_attempts': len(session.tables['ATTEMPTS']),
              'real_hosted_model_calls': 0, 'real_hosted_embedding_calls': 0,
              'failed_input_category': 'Predetermined malformed response JSON; transport fixture attempts succeed, parser rejects retained responses.',
              'retained_failure_issue_rows': len(failed['input_issues']),
              'old_release_immutable': True, 'unchanged_source_component_revisions_preserved': True,
              'changed_source_component_keys': [key for key in affected if changed['component_revisions'][key] != keys[key]],
              'revision_changes_from_baseline': revision_summary,
              'operational_revision_limit': 'COVERAGE revisions change even on unchanged refresh because processed becomes cached; run and release receipts also describe each execution.',
              'sql_storage_harness': 'Existing Session imported from tests/engine/test_native.py; subclass interprets emitted INSERT/MERGE and installation metadata SQL.',
              'code_sha256': code_hashes,
              'unverified': ['Real Snowflake SQL execution, metering and deployment.',
                             'Unattended inference quality or semantic correctness.',
                             'External client/platform mapping and client-approved handling.',
                             'Source semantic review: fixtures exercise split_v3 extraction validation only.']}
    report.update(git_provenance())
    (output / 'requests.json').write_text(json_text(requests))
    (output / 'mapping.json').write_text(json_text(seed_mapping()))
    (output / 'ledger.json').write_text(json_text(session.tables))
    (output / 'proof.json').write_text(json_text(report))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    report = prove(args.output)
    print(json_text({key: report[key] for key in ('scope', 'runs', 'total_recorded_fixture_attempts', 'real_hosted_model_calls')}), end='')


if __name__ == '__main__':
    main()
