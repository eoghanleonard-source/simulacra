"""Partial-delivery scope is explicit in v3, without changing historic readers."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

import pytest

from snowflake_engine import schema
from snowflake_engine.aggregation import aggregate
from snowflake_engine.export import EXPORT_SCHEMA_VERSION_V3, build_export, validate_export
from snowflake_engine.versioning import annotate
from test_export import inputs
from test_export_v2 import v2_inputs, v3_records, version_set


def _v3_partial_records(inputs, *, processing=None):
    records, manifest = v3_records(inputs, invalid_emotion=True)
    versions = deepcopy(manifest["version_set"])
    versions["export"] = EXPORT_SCHEMA_VERSION_V3
    if processing is not None:
        versions["processing"] = processing
    records["components"] = [
        annotate(row, row["kind"], versions, [row["object_id"]])
        for row in records["components"]
    ]
    records.update(aggregate(records["components"], None, None, {
        "source_digests": manifest["source_hashes"], "expected_sources": ["source-1"],
    }))
    manifest["version_set"] = versions
    manifest["versions"] = deepcopy(versions)
    return records, manifest


def test_v3_does_not_infer_complete_delivery_from_a_legacy_valid_component(inputs):
    records, manifest = deepcopy(inputs)
    versions = version_set()
    versions["export"] = EXPORT_SCHEMA_VERSION_V3
    records["components"] = [
        annotate(row, row["kind"], versions, [row["object_id"]])
        for row in records["components"]
    ]
    records.update(aggregate(records["components"], None, None, {
        "source_digests": manifest["source_hashes"], "expected_sources": ["source-1"],
    }))
    manifest["version_set"] = versions
    manifest["versions"] = deepcopy(versions)

    document = build_export(records, manifest)

    assert validate_export(document) == []
    assert {row["delivery_scope"] for row in document["scenarios"] + document["personas"]} == {"not_recorded"}
    assert document["accounting"]["scope_not_recorded_count"] == 2


def test_v3_partial_projection_has_explicit_scope_and_retained_original_lineage(inputs):
    document = build_export(*_v3_partial_records(inputs))

    assert validate_export(document) == []
    assert document["schema_version"] == EXPORT_SCHEMA_VERSION_V3
    scenario = document["scenarios"][0]
    assert scenario["delivery_scope"] == "full_component"
    assert scenario["partial_state"] == "complete"
    assert scenario["partial_layers"] == scenario["withheld_children"] == []
    assert scenario["projection_of"] is scenario["projection_lineage"] is None

    persona = document["personas"][0]
    original = document["quarantined_components"][0]
    assert persona["delivery_scope"] == "validated_children_only"
    assert persona["partial_state"] == "partial"
    assert persona["partial_layers"] and persona["withheld_children"]
    assert persona["projection_of"] == original["object_id"]
    assert persona["projection_lineage"] == {
        key: original[key] for key in (
            "object_id", "source_id", "logical_id", "revision_id", "version_set",
            "storage_logical_id", "storage_revision_id",
        )
    }
    assert document["accounting"]["partial_persona_delivery_count"] == 1
    assert document["accounting"]["full_valid_persona_count"] == 0


def test_v3_replay_processing_version_roundtrips_with_exact_portable_identity(inputs):
    document = build_export(*_v3_partial_records(inputs, processing="source_ordinal_v1"))
    retrieved = json.loads(json.dumps(document))

    assert document["manifest"]["version_set"]["processing"] == "source_ordinal_v1"
    assert all(row["version_set"]["processing"] == "source_ordinal_v1"
               for row in document["scenarios"] + document["personas"])
    assert validate_export(retrieved) == []
    assert retrieved["release"] == document["release"]


def test_v3_stage_b_processing_dictionary_roundtrips_with_exact_portable_identity(inputs):
    processing = {
        "evidence_ordering": "source_ordinal_v1",
        "semantic_revision": "source_assertion_revision_v1",
    }
    document = build_export(*_v3_partial_records(inputs, processing=processing))
    retrieved = json.loads(json.dumps(document))

    assert document["manifest"]["version_set"]["processing"] == processing
    assert all(row["version_set"]["processing"] == processing
               for row in document["scenarios"] + document["personas"])
    assert validate_export(retrieved) == []
    assert retrieved["release"] == document["release"]


def test_v3_r3_processing_dictionary_roundtrips_with_exact_portable_identity(inputs):
    processing = {
        "evidence_ordering": "source_ordinal_v1",
        "semantic_revision": "source_assertion_revision_v1",
        "review_response_format": "unique_review_json_v1",
        "input_measurement": "message_content_chars_v1",
    }
    document = build_export(*_v3_partial_records(inputs, processing=processing))
    retrieved = json.loads(json.dumps(document))

    assert document["manifest"]["version_set"]["processing"] == processing
    assert all(row["version_set"]["processing"] == processing
               for row in document["scenarios"] + document["personas"])
    assert validate_export(retrieved) == []
    assert retrieved["release"] == document["release"]


@pytest.mark.parametrize("key,value,fragment", [
    ("processing", "unreviewed_rewrite_v1", "processing: must be source_ordinal_v1 or an exact registered processing contract"),
    ("processing", {"evidence_ordering": "source_ordinal_v1"}, "processing: must be source_ordinal_v1 or an exact registered processing contract"),
    ("processing", {"evidence_ordering": "source_ordinal_v1", "semantic_revision": "wrong"}, "processing: must be source_ordinal_v1 or an exact registered processing contract"),
    ("processing", {"evidence_ordering": "source_ordinal_v1", "semantic_revision": "source_assertion_revision_v1", "review_response_format": "unique_review_json_v1"}, "processing: must be source_ordinal_v1 or an exact registered processing contract"),
    ("processing", {"evidence_ordering": "source_ordinal_v1", "semantic_revision": "source_assertion_revision_v1", "review_response_format": "unique_review_json_v1", "input_measurement": "wire_chars_v1"}, "processing: must be source_ordinal_v1 or an exact registered processing contract"),
    ("processing", {"evidence_ordering": "source_ordinal_v1", "semantic_revision": "source_assertion_revision_v1", "unapproved": "x"}, "processing: must be source_ordinal_v1 or an exact registered processing contract"),
    ("unapproved_stage", "x", "exact stage keys required"),
])
def test_v3_replay_versions_reject_unknown_provenance_contracts(inputs, key, value, fragment):
    records, manifest = _v3_partial_records(inputs)
    manifest["version_set"][key] = value
    manifest["versions"] = deepcopy(manifest["version_set"])

    with pytest.raises(ValueError, match=fragment):
        build_export(records, manifest)


def test_v2_does_not_accept_the_v3_replay_processing_key(v2_inputs):
    records, manifest = deepcopy(v2_inputs)
    manifest["version_set"]["processing"] = "source_ordinal_v1"
    manifest["versions"] = deepcopy(manifest["version_set"])

    with pytest.raises(ValueError, match="exact stage keys required"):
        build_export(records, manifest)


def test_v3_rejects_a_partial_persona_relabelled_as_complete_even_after_reseal(inputs):
    document = build_export(*_v3_partial_records(inputs))
    persona = document["personas"][0]
    persona["delivery_scope"] = "full_component"
    persona["partial_state"] = "complete"
    persona["partial_layers"] = []
    persona["withheld_children"] = []
    persona["projection_of"] = None
    persona["projection_lineage"] = None
    _reseal(document)

    assert any("partial original requires exactly one explicit delivery projection" in error
               for error in validate_export(document))


def test_typed_marts_project_scope_and_safe_original_lineage_join():
    for name in ("MART_SCENARIOS", "MART_PERSONAS"):
        columns = schema.VIEW_SPECS[name][1]
        join = schema.VIEW_JOINS[name]
        for field in ("DELIVERY_SCOPE", "PARTIAL_STATE", "PARTIAL_LAYERS", "WITHHELD_CHILDREN",
                      "PROJECTION_OF", "ORIGINAL_OBJECT_ID", "ORIGINAL_REVISION_ID"):
            assert field in columns
        assert "ORIGINAL.CLIENT_ID=COMPONENTS.CLIENT_ID" in join
        assert "ORIGINAL.RUN_ID=COMPONENTS.RUN_ID" in join
        assert "ORIGINAL.SOURCE_ID=COMPONENTS.SOURCE_ID" in join
        assert "ORIGINAL.OBJECT_ID=COMPONENTS.PAYLOAD:projection_of::VARCHAR" in join
    assert "MART_VARIANTS" in schema.VIEW_SPECS


def test_typed_mart_self_join_qualifies_every_base_component_reference_in_ddl():
    config = {"database": "DEMO_DB", "schema": "ENGINE_V3"}
    ddl = schema.schema_ddl(config)
    base_columns = ("LOGICAL_ID", "REVISION_ID", "VERSION_SET", "OBJECT_ID", "RUN_ID",
                    "CLIENT_ID", "SOURCE_ID", "VALIDATION_STATUS", "PAYLOAD")
    for name in ("MART_SCENARIOS", "MART_PERSONAS"):
        _, columns, predicate = schema.VIEW_SPECS[name]
        statement = next(sql for sql in ddl if f".{name} AS SELECT " in sql)
        for column in base_columns:
            assert f"COMPONENTS.{column}" in columns
            assert not re.search(rf"(?<!\.)\b{column}\b", columns)
        assert predicate == f"COMPONENTS.KIND = '{'scenario' if name == 'MART_SCENARIOS' else 'persona'}'"
        assert f"SELECT {columns} FROM DEMO_DB.ENGINE_V3.COMPONENTS" in statement
        assert "ORIGINAL.CLIENT_ID=COMPONENTS.CLIENT_ID" in statement
        assert "ORIGINAL.RUN_ID=COMPONENTS.RUN_ID" in statement
        assert "ORIGINAL.SOURCE_ID=COMPONENTS.SOURCE_ID" in statement
        assert "ORIGINAL.OBJECT_ID=COMPONENTS.PAYLOAD:projection_of::VARCHAR" in statement
        assert f"WHERE {predicate}" in statement


def test_legacy_v1_reader_and_digest_remain_unchanged():
    path = Path(__file__).resolve().parents[2] / "artifacts/demo/source-grounded-snowflake-01/evidence/full-export.json"
    if not path.exists():
        return
    raw = path.read_bytes()
    document = json.loads(raw)

    assert validate_export(document) == []
    assert hashlib.sha256(raw).hexdigest() == "82f93640c8f7e4a9ca7412ba543e428d32f2c86a87e996cf24dafe1068c9a489"


def _reseal(document):
    from snowflake_engine.contracts import canonical_json
    unsigned = deepcopy(document)
    for key in ("release", "manifest"):
        unsigned[key].pop("release_id", None)
        unsigned[key].pop("content_sha256", None)
    digest = hashlib.sha256(canonical_json(unsigned).encode()).hexdigest()
    for key in ("release", "manifest"):
        document[key]["content_sha256"] = digest
        document[key]["release_id"] = "release_" + digest[:24]
