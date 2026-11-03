"""Actual SQLite ingestion, joins, immutable custody and reconstructed export."""
from copy import deepcopy
import json
import sqlite3

import pytest

from test_export import inputs, reseal
from test_export_v2 import v2_inputs, with_practice
from test_partial_scope import _v3_partial_records
from scripts.engine.reference_consumer import IngestRejected, ReferenceConsumer
from snowflake_engine.contracts import canonical_json
from snowflake_engine.export import build_export
from snowflake_engine.practice import link_assignment_personas


@pytest.fixture
def document(v2_inputs):
    records, manifest = with_practice(v2_inputs)
    records["practice"] = link_assignment_personas(records["practice"], records["components"], manifest["version_set"])
    return build_export(records, manifest)


def test_actual_store_reopens_resolves_persona_and_exports_exact_content(tmp_path, document):
    raw = (json.dumps(document, indent=2, ensure_ascii=False) + '\n').encode()
    store = tmp_path / 'consumer.sqlite'
    consumer = ReferenceConsumer(store)
    receipt = consumer.ingest(raw)
    release_id = receipt['release_id']
    assert receipt['row_counts']['practice.assignments'] == 1
    assert receipt['resolved_join_counts'] == {'persona': 1, 'situation': 1, 'variant': 1}
    assert receipt['foreign_key_errors'] == []
    consumer.close()
    consumer = ReferenceConsumer(store)
    assignment_id, = consumer.assignments(release_id)
    result = consumer.resolve_assignment(release_id, assignment_id)
    assert result['assignment'] == document['practice']['assignments'][0]
    assert result['resolved']['situation']['card'] == document['scenarios'][0]
    assert result['resolved']['variant']['focus_need'] == document['practice']['assignments'][0]['variant']['focus_need']
    assert next(need for need in result['resolved']['situation']['card']['needs'] if need['id'] == 'late')['status'] == 'pending'
    assert result['resolved']['personas'][0]['card'] == document['personas'][0]
    assert result['resolved']['personas'][0]['source'] == document['sources'][0]
    assert result['guidance'] == document['guidance']
    assert result['release_state'] == document['release']
    assert result['assignment']['assessment_eligible'] is False
    assert result['assignment']['client_approved_resolution_criteria'] is None
    assert result['resolved']['personas'][0]['card']['unknowns'] == ['Cause and intensity are not established.']
    assert consumer.export(release_id) == canonical_json(document).encode()
    assert consumer.export(release_id, original_bytes=True) == raw
    assert consumer.ingest(raw)['status'] == 'already_present'
    consumer.close()


def test_invalid_reference_failure_bytes_retained_and_valid_release_unchanged(tmp_path, document):
    consumer = ReferenceConsumer(tmp_path / 'consumer.sqlite')
    raw = json.dumps(document).encode()
    release_id = consumer.ingest(raw)['release_id']
    bad = deepcopy(document)
    bad['practice']['assignments'][0]['persona_refs'][0]['object_id'] = 'missing-persona'
    reseal(bad)
    failed_raw = json.dumps(bad).encode()
    with pytest.raises(IngestRejected) as rejected:
        consumer.ingest(failed_raw)
    attempt = consumer.connection.execute('SELECT * FROM ingest_attempts WHERE attempt_id=?',
                                         (rejected.value.attempt_id,)).fetchone()
    assert attempt['status'] == 'rejected'
    assert attempt['raw_bytes'] == failed_raw
    assert json.loads(attempt['errors_json'])
    assert consumer.connection.execute('SELECT COUNT(*) FROM releases').fetchone()[0] == 1
    assert consumer.export(release_id, original_bytes=True) == raw
    assert consumer.resolve_assignment(release_id, consumer.assignments(release_id)[0])['resolved']['personas']


def test_database_failure_rolls_back_all_partial_rows_and_retains_failure(tmp_path, document):
    consumer = ReferenceConsumer(tmp_path / 'consumer.sqlite')
    consumer.connection.execute("""CREATE TRIGGER injected_storage_failure BEFORE INSERT ON assignment_links
        BEGIN SELECT RAISE(ABORT, 'injected disk-side insert failure'); END""")
    raw = json.dumps(document).encode()
    with pytest.raises(IngestRejected, match='injected disk-side insert failure'):
        consumer.ingest(raw)
    assert consumer.connection.execute('SELECT COUNT(*) FROM releases').fetchone()[0] == 0
    assert consumer.connection.execute('SELECT COUNT(*) FROM release_rows').fetchone()[0] == 0
    assert consumer.connection.execute('SELECT COUNT(*) FROM assignment_links').fetchone()[0] == 0
    assert consumer.connection.execute('SELECT raw_bytes FROM ingest_attempts').fetchone()[0] == raw


def test_new_release_coexists_old_release_immutable(tmp_path, document):
    consumer = ReferenceConsumer(tmp_path / 'consumer.sqlite')
    raw = json.dumps(document).encode()
    old_id = consumer.ingest(raw)['release_id']
    changed = deepcopy(document)
    changed['manifest']['consumer_fixture_note'] = 'Different release; no semantic content authored.'
    reseal(changed)
    new_id = consumer.ingest(json.dumps(changed).encode())['release_id']
    assert new_id != old_id
    assert consumer.export(old_id, original_bytes=True) == raw
    assert json.loads(consumer.export(new_id)) == changed
    with pytest.raises(sqlite3.IntegrityError, match='immutable'):
        consumer.connection.execute('UPDATE releases SET raw_bytes=? WHERE release_id=?', (b'{}', old_id))
    with pytest.raises(sqlite3.IntegrityError, match='immutable'):
        consumer.connection.execute('DELETE FROM release_rows WHERE release_id=?', (old_id,))
    with pytest.raises(sqlite3.IntegrityError, match='immutable'):
        consumer.connection.execute('INSERT INTO release_rows VALUES(?,?,?,?,?,?)', (old_id, 'issues', 999, '999', None, '{}'))


def test_capture_partial_missing_states_survive_real_sqlite_roundtrip(tmp_path, inputs):
    document = build_export(*_v3_partial_records(inputs))
    consumer = ReferenceConsumer(tmp_path / 'consumer.sqlite')
    release_id = consumer.ingest(json.dumps(document).encode())['release_id']
    retrieved = json.loads(consumer.export(release_id))
    assert retrieved == document
    assert retrieved['schema_version'] == 'engine-export-v3'
    assert retrieved['personas'][0]['emotional_expression'] == {'status': 'unknown', 'observations': []}
    assert retrieved['personas'][0]['withheld_children']
    assert retrieved['quarantined_components'] == document['quarantined_components']
    assert retrieved['scenarios'][0].get('delivery_scope') == document['scenarios'][0].get('delivery_scope')


@pytest.mark.parametrize('raw', [b'{"schema_version":"engine-export-v2","schema_version":"engine-export-v3"}', b'{"x":NaN}', b'not-json'])
def test_ambiguous_and_invalid_json_rejected_with_original_bytes(tmp_path, raw):
    consumer = ReferenceConsumer(tmp_path / 'consumer.sqlite')
    with pytest.raises(IngestRejected):
        consumer.ingest(raw)
    assert consumer.connection.execute('SELECT raw_bytes FROM ingest_attempts').fetchone()[0] == raw
    assert consumer.connection.execute('SELECT COUNT(*) FROM releases').fetchone()[0] == 0
