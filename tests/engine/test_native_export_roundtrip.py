"""Stored numeric readback must preserve original seals and reject tampering."""
from copy import deepcopy
import json

import pytest

from test_export import inputs
from test_export_v2 import v2_inputs, v3_records
from snowflake_engine import cli, schema
from snowflake_engine.aggregation import aggregate
from snowflake_engine.export import build_export, read_stored_export, validate_export
from snowflake_engine.versioning import annotate, LINEAGE_KEYS


def variant_roundtrip(value):
    if isinstance(value, dict):
        return {k: variant_roundtrip(v) for k, v in value.items()}
    if isinstance(value, list):
        return [variant_roundtrip(v) for v in value]
    if type(value) is float and value.is_integer():
        return int(value)
    return value


@pytest.fixture(params=["v2", "v3"])
def stored_release(request, v2_inputs, inputs):
    records, manifest = deepcopy(v2_inputs) if request.param == "v2" else v3_records(inputs)
    if request.param == "v3":
        versions = manifest["version_set"]
        versions["export"] = "engine-export-v3"
        manifest["versions"] = deepcopy(versions)
        records["components"] = [annotate({k: v for k, v in row.items() if k not in LINEAGE_KEYS},
            row["kind"], versions, [row["object_id"]]) for row in records["components"]]
        records.update(aggregate(records["components"], None, None,
            {"source_digests": manifest["source_hashes"], "expected_sources": ["source-1"]}))
    manifest["usage"]["generation_credit_estimate"] = {
        "estimated_ai_credits": 0.0, "returned_input_tokens": 0,
        "returned_output_tokens": 0, "unknown_attempt_ids": [],
        "rates_model": "claude-sonnet-4-6",
        "rates_ai_credits_per_million": {"input": 1.8, "output": 9.0}}
    original = build_export(records, manifest)
    return original, variant_roundtrip(original)


def test_native_readback_recovers_only_credit_estimate_representation(stored_release):
    original, stored = stored_release
    before = json.dumps(stored, sort_keys=True)
    assert validate_export(original) == []
    assert len(validate_export(stored)) == 4
    recovered = read_stored_export(stored)
    assert recovered is not stored
    assert validate_export(recovered) == []
    assert recovered["release"] == original["release"]
    assert recovered["manifest"]["content_sha256"] == original["manifest"]["content_sha256"]
    assert json.dumps(stored, sort_keys=True) == before
    expected = deepcopy(stored)
    for name in ("manifest", "run_status"):
        expected[name]["usage"]["generation_credit_estimate"]["estimated_ai_credits"] = 0.0
    assert json.dumps(recovered, sort_keys=True) == json.dumps(expected, sort_keys=True)


def test_valid_document_is_returned_untouched(stored_release):
    original, _ = stored_release
    before = json.dumps(original, sort_keys=True)
    assert read_stored_export(original) is original
    assert json.dumps(original, sort_keys=True) == before


@pytest.mark.parametrize("mutate", [
    lambda d: d["manifest"]["usage"]["generation_credit_estimate"].update(estimated_ai_credits=1),
    lambda d: d["run_status"]["usage"]["generation_credit_estimate"].update(estimated_ai_credits="0"),
    lambda d: d["run_status"]["usage"]["generation_credit_estimate"].update(estimated_ai_credits=False),
    lambda d: d["release"].update(content_sha256="0" * 64),
    lambda d: d["manifest"].update(release_id="release_" + "0" * 24),
    lambda d: d["scenarios"][0].update(summary="Altered source meaning."),
    lambda d: d["sources"][0]["metadata"].update(unrelated_numeric_field=0),
])
def test_representation_recovery_never_accepts_tampering(stored_release, mutate):
    _, stored = stored_release
    mutate(stored)
    before = json.dumps(stored, sort_keys=True)
    with pytest.raises(ValueError, match="Stored release failed export validation"):
        read_stored_export(stored)
    assert json.dumps(stored, sort_keys=True) == before


def test_cli_export_reads_existing_stored_release_without_writes(stored_release, tmp_path, monkeypatch):
    original, stored = stored_release
    config = {"database": "DEMO_DB", "schema": "ENGINE_V1", "warehouse": "DEMO_WH",
              "role": "ENGINE_OPERATOR", "client_id": "client-1", "model": "configured-model",
              "embedding_model": "configured-embedding"}
    config_path, output = tmp_path / "config.json", tmp_path / "export.json"
    config_path.write_text(json.dumps(config))

    class Session:
        def __init__(self):
            self.calls = []
            self.closed = False

        def sql(self, sql, params=None):
            self.calls.append((sql, params))
            if sql.startswith("SELECT SCHEMA_VERSION"):
                rows = [(schema.SCHEMA_VERSION, schema.manifest_hash())]
            elif sql.startswith("SELECT PAYLOAD"):
                rows = [(json.dumps(stored),)]
            else:
                rows = []
            class Query:
                def collect(self):
                    return rows
            return Query()

        def close(self):
            self.closed = True

    session = Session()
    monkeypatch.setattr(cli, "connect", lambda *a: session)
    assert cli.main(["export", "--config", str(config_path), "--release-id", original["release"]["release_id"],
                     "--output", str(output), "--execute"]) == 0
    exported = json.loads(output.read_text())
    assert validate_export(exported) == []
    assert exported["release"] == original["release"]
    assert session.closed
    assert all(sql.startswith(("SELECT ", "ALTER SESSION SET ")) for sql, _ in session.calls)
