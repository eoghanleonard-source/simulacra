"""Issue links are exact when known and SQL NULL when unavailable."""
from copy import deepcopy
import json

from snowflake_engine import native, schema
from test_native import Session, config


def test_issue_insert_omits_unknown_nullable_source_and_preserves_payload():
    session = Session()
    runtime = native.Runtime(session, native.validate_config(config()))
    unknown = {'code': 'global_failure', 'reason': 'No source is known'}
    original = deepcopy(unknown)
    runtime.persist_issue(unknown, 0)
    statement, params = next((sql, params) for sql, params in session.log
                             if sql.startswith('INSERT INTO ') and '.INPUT_ISSUES ' in sql)
    columns = statement.split(' (', 1)[1].split(')', 1)[0].split(', ')
    assert 'SOURCE_ID' not in columns
    assert schema.TABLE_SPECS['INPUT_ISSUES']['SOURCE_ID'] == 'VARCHAR'
    # An omitted nullable VARCHAR uses Snowflake's SQL NULL default, avoiding
    # Snowpark's treatment of an untyped Python None bind.
    assert 'None' not in params and None not in params
    assert json.loads(params[columns.index('PAYLOAD')]) == original
    assert unknown == original


def test_issue_insert_binds_known_source_and_payload_truthfully():
    session = Session()
    runtime = native.Runtime(session, native.validate_config(config()))
    issue = {'code': 'invalid_json', 'reason': 'Invalid fixture', 'source_id': 'known-source'}
    runtime.persist_issue(issue, 0)
    statement, params = next((sql, params) for sql, params in session.log
                             if sql.startswith('INSERT INTO ') and '.INPUT_ISSUES ' in sql)
    columns = statement.split(' (', 1)[1].split(')', 1)[0].split(', ')
    assert params[columns.index('SOURCE_ID')] == 'known-source'
    assert json.loads(params[columns.index('PAYLOAD')]) == issue
