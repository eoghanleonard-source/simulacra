"""Offline safety and reproducibility checks; never connects to Snowflake."""
import io
import json
import zipfile

import pytest

from snowflake_engine import cli, schema


CONFIG = {"database": "DEMO_DB", "schema": "ENGINE_V1", "warehouse": "DEMO_WH",
          "role": "ENGINE_OPERATOR", "client_id": "synthetic", "model": "configured-model",
          "embedding_model": "configured-embedding"}


class Result:
    def __init__(self, rows):
        self.rows = rows

    def collect(self):
        return self.rows


class Session:
    def __init__(self, metadata=None, objects=None):
        self.calls = []
        self.metadata = metadata or [(schema.SCHEMA_VERSION, schema.manifest_hash())]
        self.objects = objects or []

    def sql(self, sql, params=None):
        self.calls.append((sql, params))
        if 'INFORMATION_SCHEMA.TABLES' in sql:
            return Result(self.objects)
        if sql.startswith('SELECT BUDGET'):
            return Result([(dict(cli.DEFAULT_LIMITS),)])
        if sql.startswith('SELECT SCHEMA_VERSION'):
            return Result(self.metadata)
        return Result([])


def package_dir(tmp_path):
    for name in cli.REQUIRED_MODULES:
        (tmp_path / name).write_text('"""fixture module"""\n')
    (tmp_path / '.env').write_text('SECRET=never_package')
    (tmp_path / 'private.key').write_text('never_package')
    return tmp_path


@pytest.mark.parametrize('value', ['DB;DROP SCHEMA X', 'db.schema', '"quoted"', 'name space', 'name--', '', '1bad', 'x\n'])
def test_reject_sql_identifiers(value):
    with pytest.raises(ValueError):
        schema.qualified_name({**CONFIG, 'schema': value}, 'RUNS')


def test_ddl_is_canonical_non_destructive_and_typed():
    ddl = schema.schema_ddl(CONFIG)
    joined = '\n'.join(ddl)
    assert len(ddl) == 1 + len(schema.TABLE_SPECS) + len(schema.VIEW_SPECS)
    assert all('IF NOT EXISTS' in statement for statement in ddl)
    assert not any(word in joined.upper() for word in ('DROP ', 'TRUNCATE ', 'GRANT ', 'CREATE OR REPLACE', 'ALTER WAREHOUSE'))
    assert 'INPUT_RAW' in joined and 'LOCKS' in joined
    assert 'LATERAL FLATTEN(INPUT => PAYLOAD:payload, RECURSIVE => TRUE)' in joined
    assert 'PAYLOAD:prevalence:unique_source_count::NUMBER' in joined
    assert 'MART_RELATIONS' in joined


def test_archive_is_deterministic_and_excludes_secrets(tmp_path):
    root = package_dir(tmp_path)
    first = cli.package_engine(root)
    assert cli.package_engine(root) == first
    with zipfile.ZipFile(io.BytesIO(first)) as archive:
        assert set(archive.namelist()) == {'snowflake_engine/' + n for n in cli.REQUIRED_MODULES}
        assert b'never_package' not in b''.join(archive.read(n) for n in archive.namelist())
    plan = cli.install_plan(CONFIG, first, tmp_path / 'archive.zip')
    assert plan['archive_sha256'].upper().startswith(plan['procedure'].removeprefix('RUN_ENGINE_V2_'))
    assert "(CONFIG_JSON VARCHAR) RETURNS VARIANT" in plan['statements'][-1]
    assert "RUNTIME_VERSION='3.11'" in plan['statements'][-1]
    assert 'EXECUTE AS CALLER' in plan['statements'][-1]
    assert not plan['executed']


def test_incomplete_archive_fails(tmp_path):
    with pytest.raises(ValueError, match='Incomplete native package'):
        cli.package_engine(tmp_path)


def test_value_sql_is_bound_and_variant_encoded():
    session = Session()
    hostile = "x'); DROP SCHEMA X; --"
    schema.insert_row(session, CONFIG, 'INPUT_RAW', {'raw_id': hostile, 'client_id': 'c', 'payload': {'text': hostile}})
    sql, params = session.calls[-1]
    assert hostile not in sql
    assert 'PARSE_JSON(?)' in sql and params[0] == hostile
    assert json.loads(params[-1]) == {'text': hostile}
    schema.merge_row(session, CONFIG, 'RUNS', {'run_id': hostile, 'client_id': 'c', 'payload': {}}, ['run_id'])
    assert hostile not in session.calls[-1][0]
    with pytest.raises(ValueError):
        schema.insert_row(session, CONFIG, 'RUNS', {'evil;drop': 1})
    with pytest.raises(ValueError):
        schema.merge_row(session, CONFIG, 'RUNS', {'run_id': None}, ['run_id'])


def test_version_and_unrelated_object_stop_before_ddl():
    session = Session(metadata=[('engine-storage-old', 'old')], objects=[('RELATION', 'ENGINE_METADATA')])
    with pytest.raises(ValueError, match='version or manifest mismatch'):
        cli._preflight_install(session, cli.validate_config(CONFIG))
    assert all(not sql.startswith('CREATE') for sql, _ in session.calls)
    with pytest.raises(ValueError, match='unversioned'):
        cli._preflight_install(Session(objects=[('STAGE', 'EXISTING_STAGE')]), cli.validate_config(CONFIG))
    with pytest.raises(ValueError, match='unrelated'):
        cli._preflight_install(Session(objects=[('RELATION', 'ENGINE_METADATA'), ('RELATION', 'CUSTOMER_DATA')]), cli.validate_config(CONFIG))


def test_install_default_never_connects(tmp_path, monkeypatch):
    root = tmp_path / 'package'
    root.mkdir()
    package_dir(root)
    config_path = tmp_path / 'config.json'
    cli.write_json(config_path, CONFIG)
    monkeypatch.setattr(cli, 'connect', lambda *a: pytest.fail('dry-run connected'))
    output = tmp_path / 'plan'
    assert cli.main(['install', '--config', str(config_path), '--package-dir', str(root), '--output', str(output)]) == 0
    plan = cli.read_json(output / 'install-plan.json')
    assert plan['executed'] is False
    assert plan['config']['procedure'] == plan['procedure']
    assert (output / 'install.sql').is_file()
    assert (output / 'schema-manifest.json').is_file()


def test_limits_reject_silent_unknowns_and_raise_caps():
    for limits in ({'max_generation_attempts': 12}, {'max_generation_calls': cli.HARD_LIMITS['max_generation_calls'] + 1}, {'concurrency': 2}, {'max_output_tokens': True}):
        with pytest.raises(ValueError):
            cli.validate_config({**CONFIG, 'limits': limits})


def test_config_and_load_preserve_raw_for_native(tmp_path, monkeypatch):
    config_path = tmp_path / 'config.json'
    cli.write_json(config_path, CONFIG)
    raw_path, mapping_path = tmp_path / 'raw.json', tmp_path / 'mapping.json'
    cli.write_json(raw_path, [{'raw_text': 'unaltered'}])
    cli.write_json(mapping_path, {'source_id': 'id'})
    monkeypatch.setattr(cli, 'connect', lambda *a: pytest.fail('dry-run connected'))
    output = tmp_path / 'load.json'
    cli.main(['load', '--config', str(config_path), '--input', str(raw_path), '--mapping', str(mapping_path), '--run-id', 'load-1', '--output', str(output)])
    result = cli.read_json(output)
    assert result['request']['raw_records'] == [{'raw_text': 'unaltered'}]
    assert result['request']['operation'] == 'load'
    assert not result['executed']


def test_table_load_plan_sends_relation_and_mapping_without_reading_source_rows(tmp_path, monkeypatch):
    config_path, mapping_path, output = tmp_path / 'config.json', tmp_path / 'mapping.json', tmp_path / 'load.json'
    cli.write_json(config_path, CONFIG)
    cli.write_json(mapping_path, {'types': {}})
    monkeypatch.setattr(cli, 'connect', lambda *a: pytest.fail('dry-run connected'))
    assert cli.main(['load-table', '--config', str(config_path), '--relation', 'demo_db.engine_v1.engine_raw_records',
                     '--mapping', str(mapping_path), '--run-id', 'load-table-1', '--source-id', 'source-2',
                     '--source-id', 'source-1', '--output', str(output)]) == 0
    result = cli.read_json(output)
    request = result['request']
    assert result['operation'] == 'load-table' and not result['executed']
    assert request['operation'] == 'load'
    assert request['source_relation'] == 'DEMO_DB.ENGINE_V1.ENGINE_RAW_RECORDS'
    assert request['source_ids'] == ['source-1', 'source-2']
    assert request['mapping'] == {'types': {}}
    assert 'raw_records' not in request


def test_table_load_execution_calls_native_without_local_source_select(tmp_path, monkeypatch):
    config_path, mapping_path, output = tmp_path / 'config.json', tmp_path / 'mapping.json', tmp_path / 'load.json'
    cli.write_json(config_path, CONFIG)
    cli.write_json(mapping_path, {'types': {}})
    class NativeOnlySession:
        closed = False
        def close(self):
            self.closed = True
        def sql(self, *_args, **_kwargs):
            pytest.fail('CLI fetched source records')
    session = NativeOnlySession()
    monkeypatch.setattr(cli, 'connect', lambda *a: session)
    def native_call(actual_session, config, request):
        assert actual_session is session
        assert request['source_relation'] == 'DEMO_DB.ENGINE_V1.ENGINE_RAW_RECORDS'
        assert 'raw_records' not in request
        return {'status': 'complete', 'loaded_raw_records': 1}
    monkeypatch.setattr(cli, '_native_call', native_call)
    assert cli.main(['load-table', '--config', str(config_path), '--relation', 'DEMO_DB.ENGINE_V1.ENGINE_RAW_RECORDS',
                     '--mapping', str(mapping_path), '--run-id', 'load-table-1', '--output', str(output), '--execute']) == 0
    assert cli.read_json(output) == {'status': 'complete', 'loaded_raw_records': 1}
    assert session.closed


@pytest.mark.parametrize('relation', ['DEMO_DB.OTHER.RAW', 'DEMO_DB.ENGINE_V1.RAW;DROP', 'RAW'])
def test_table_load_cli_rejects_relation_before_connection(tmp_path, monkeypatch, relation):
    config_path, mapping_path = tmp_path / 'config.json', tmp_path / 'mapping.json'
    cli.write_json(config_path, CONFIG)
    cli.write_json(mapping_path, {'types': {}})
    monkeypatch.setattr(cli, 'connect', lambda *a: pytest.fail('invalid relation connected'))
    with pytest.raises(ValueError):
        cli.main(['load-table', '--config', str(config_path), '--relation', relation, '--mapping', str(mapping_path),
                  '--run-id', 'invalid', '--output', str(tmp_path / 'out.json'), '--execute'])


def test_explicit_env_file_supports_multiline_without_interpolation(tmp_path, monkeypatch):
    for key in ('SNOWFLAKE_ACCOUNT', 'SNOWFLAKE_USER', 'SNOWFLAKE_PRIVATE_KEY'):
        monkeypatch.delenv(key, raising=False)
    env = tmp_path / '.env'
    env.write_text('SNOWFLAKE_ACCOUNT=example\nSNOWFLAKE_USER="operator"\nSNOWFLAKE_PRIVATE_KEY="-----BEGIN PRIVATE KEY-----\nsynthetic${NO_INTERPOLATION}\n-----END PRIVATE KEY-----"\nUNRELATED_SECRET=excluded\n')
    values = cli.credential_values(env)
    assert values['account'] == 'example'
    assert values['private_key'] == '-----BEGIN PRIVATE KEY-----\nsynthetic${NO_INTERPOLATION}\n-----END PRIVATE KEY-----'
    assert 'unrelated_secret' not in values
    monkeypatch.setenv('SNOWFLAKE_ACCOUNT', 'environment_wins')
    assert cli.credential_values(env)['account'] == 'environment_wins'


def test_inline_key_accepts_pem_and_existing_base64_der_without_exposure():
    import base64
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    der = key.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    assert cli.private_key_der(base64.b64encode(der).decode()) == der
    assert cli.private_key_der(pem.replace('\n', '\\n')) == der
    bad = 'synthetic-invalid-private-key'
    with pytest.raises(ValueError) as error:
        cli.private_key_der(bad)
    assert bad not in str(error.value)


def test_extraction_mart_paths_and_types_follow_actual_capture_contract():
    import re
    from snowflake_engine import contracts
    sql_types = {'string': 'VARCHAR', 'array': 'ARRAY', 'object': 'OBJECT'}
    for name, contract in [('MART_SCENARIOS', contracts.SCENARIO_SCHEMA), ('MART_PERSONAS', contracts.PERSONA_SCHEMA)]:
        expression = schema.VIEW_SPECS[name][1]
        paths = re.findall(r'PAYLOAD:payload:([a-z_:]+)::(VARCHAR|ARRAY|OBJECT)', expression)
        assert paths
        for path, sql_type in paths:
            node = contract
            for part in path.split(':'):
                node = node['properties'][part]
            assert sql_type == sql_types[node['type']], path
    assert ' AS SUMMARY' in schema.VIEW_SPECS['MART_SCENARIOS'][1]
    assert '{GUIDANCE}' not in '\n'.join(schema.schema_ddl(CONFIG))
    assert 'CONFIG:limits AS LIMITS' in schema.VIEW_SPECS['MART_RUNS'][1]


def test_reviewed_general_profile_can_exceed_smoke_but_installed_budget_is_immutable():
    general = cli.validate_config({**CONFIG, 'limits': {**cli.DEFAULT_LIMITS, 'max_sources': 100, 'max_generation_calls': 200}})
    assert general['limits']['max_sources'] == 100
    assert cli.validate_config(CONFIG)['limits'] == cli.DEFAULT_LIMITS
    with pytest.raises(ValueError, match='Installed allowance differs'):
        cli._preflight_install(Session(objects=[('RELATION', 'ENGINE_METADATA')]), general)


@pytest.mark.parametrize('run_seconds, child_seconds, expected_call_seconds', [(900, 120, 1080), (60, 15, 135)])
def test_native_call_timeout_covers_bounded_run_and_closeout_without_raising_child_limit(run_seconds, child_seconds, expected_call_seconds):
    class NativeSession(Session):
        def sql(self, sql, params=None):
            if sql.startswith('CALL '):
                self.calls.append((sql, params))
                return Result([('{"status":"complete"}',)])
            return super().sql(sql, params)

    config = cli.validate_config({**CONFIG, 'procedure': 'RUN_ENGINE_V1_ABCDEF123456',
                                 'limits': {**cli.DEFAULT_LIMITS, 'max_run_seconds': run_seconds,
                                            'statement_timeout_seconds': child_seconds}})
    original_limits = dict(config['limits'])
    request = {**config, 'operation': 'run', 'run_id': 'bounded-timeout'}
    session = NativeSession()
    assert cli._native_call(session, config, request) == {'status': 'complete'}
    assert session.calls[-2][0] == f'ALTER SESSION SET STATEMENT_TIMEOUT_IN_SECONDS={expected_call_seconds}'
    call_sql, call_params = session.calls[-1]
    assert call_sql.startswith('CALL DEMO_DB.ENGINE_V1.RUN_ENGINE_V1_ABCDEF123456(?)')
    assert json.loads(call_params[0])['limits']['statement_timeout_seconds'] == child_seconds
    assert json.loads(call_params[0])['limits']['max_run_seconds'] == run_seconds
    assert config['limits'] == original_limits
    assert session.calls[1][0] == f'ALTER SESSION SET STATEMENT_TIMEOUT_IN_SECONDS={child_seconds}'


def test_regular_cli_session_preparation_keeps_child_statement_timeout():
    session = Session()
    cli._prepare_session(session, cli.validate_config(CONFIG))
    assert session.calls[0][0] == 'ALTER SESSION SET STATEMENT_TIMEOUT_IN_SECONDS=120'
    assert cli.DEFAULT_LIMITS['max_generation_calls'] == 12
    assert cli.DEFAULT_LIMITS['max_embedding_inputs'] == 128
