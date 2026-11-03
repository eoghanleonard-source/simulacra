"""Single physical schema manifest for installation, runtime writes and dictionary generation."""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence

SCHEMA_VERSION = "engine-storage-v4"
IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,254}\Z")

# Nullable storage columns retain unknown values. Required identities are validated by runtime.
TABLE_SPECS = {
    "ENGINE_METADATA": {"SCHEMA_VERSION": "VARCHAR", "MANIFEST_HASH": "VARCHAR", "BUDGET": "VARIANT", "CAPTURE_CONFIG": "VARIANT", "INSTALLED_AT": "TIMESTAMP_LTZ DEFAULT CURRENT_TIMESTAMP()"},
    "INPUT_RAW": {"RAW_ID": "VARCHAR", "CLIENT_ID": "VARCHAR", "PAYLOAD": "VARIANT"},
    "INPUT_SOURCES": {"SOURCE_ID": "VARCHAR", "CLIENT_ID": "VARCHAR", "CONTENT_HASH": "VARCHAR", "PAYLOAD": "VARIANT"},
    "INPUT_ISSUES": {"ISSUE_ID": "VARCHAR", "RUN_ID": "VARCHAR", "CLIENT_ID": "VARCHAR", "SOURCE_ID": "VARCHAR", "REASON": "VARCHAR", "PAYLOAD": "VARIANT"},
    "RUNS": {"RUN_ID": "VARCHAR", "CLIENT_ID": "VARCHAR", "STATUS": "VARCHAR", "CONFIG": "VARIANT", "PAYLOAD": "VARIANT"},
    "ATTEMPTS": {"ATTEMPT_ID": "VARCHAR", "RUN_ID": "VARCHAR", "CLIENT_ID": "VARCHAR", "OPERATION": "VARCHAR", "REQUEST_KEY": "VARCHAR", "STATUS": "VARCHAR", "PAYLOAD": "VARIANT"},
    "COMPONENTS": {"OBJECT_ID": "VARCHAR", "RUN_ID": "VARCHAR", "CLIENT_ID": "VARCHAR", "SOURCE_ID": "VARCHAR", "KIND": "VARCHAR", "VALIDATION_STATUS": "VARCHAR", "PAYLOAD": "VARIANT"},
    "EMBEDDINGS": {"EMBEDDING_ID": "VARCHAR", "RUN_ID": "VARCHAR", "CLIENT_ID": "VARCHAR", "OBJECT_ID": "VARCHAR", "MODEL": "VARCHAR", "CONTENT_HASH": "VARCHAR", "PAYLOAD": "VARIANT"},
    "CANDIDATES": {"CANDIDATE_ID": "VARCHAR", "RUN_ID": "VARCHAR", "CLIENT_ID": "VARCHAR", "KIND": "VARCHAR", "PAYLOAD": "VARIANT"},
    "FAMILIES": {"FAMILY_ID": "VARCHAR", "RUN_ID": "VARCHAR", "CLIENT_ID": "VARCHAR", "KIND": "VARCHAR", "PAYLOAD": "VARIANT"},
    "RELATIONS": {"RELATION_ID": "VARCHAR", "RUN_ID": "VARCHAR", "CLIENT_ID": "VARCHAR", "PAYLOAD": "VARIANT"},
    "COMPATIBILITY": {"COMPATIBILITY_ID": "VARCHAR", "RUN_ID": "VARCHAR", "CLIENT_ID": "VARCHAR", "PAYLOAD": "VARIANT"},
    "COVERAGE": {"COVERAGE_ID": "VARCHAR", "RUN_ID": "VARCHAR", "CLIENT_ID": "VARCHAR", "SOURCE_ID": "VARCHAR", "STATUS": "VARCHAR", "PAYLOAD": "VARIANT"},
    "GUIDANCE": {"GUIDANCE_ID": "VARCHAR", "RUN_ID": "VARCHAR", "CLIENT_ID": "VARCHAR", "SOURCE_ID": "VARCHAR", "AUTHORITY": "VARCHAR", "PAYLOAD": "VARIANT"},
    "RELEASES": {"RELEASE_ID": "VARCHAR", "RUN_ID": "VARCHAR", "CLIENT_ID": "VARCHAR", "STATUS": "VARCHAR", "MANIFEST": "VARIANT", "PAYLOAD": "VARIANT"},
    "LOCKS": {"LOCK_KEY": "VARCHAR", "OWNER_RUN_ID": "VARCHAR", "ACQUIRED_AT": "TIMESTAMP_LTZ", "PAYLOAD": "VARIANT"},
}

# Expressions are part of the manifest, never caller-supplied SQL.
VIEW_SPECS = {
    "MART_SCENARIOS": ("COMPONENTS", "COMPONENTS.OBJECT_ID, COMPONENTS.RUN_ID, COMPONENTS.CLIENT_ID, COMPONENTS.SOURCE_ID, COMPONENTS.VALIDATION_STATUS, COALESCE(COMPONENTS.PAYLOAD:delivery_scope::VARCHAR, 'not_recorded') AS DELIVERY_SCOPE, COALESCE(COMPONENTS.PAYLOAD:partial_state::VARCHAR, 'not_recorded') AS PARTIAL_STATE, COALESCE(COMPONENTS.PAYLOAD:partial_layers::ARRAY, ARRAY_CONSTRUCT()) AS PARTIAL_LAYERS, COALESCE(COMPONENTS.PAYLOAD:withheld_layers::ARRAY, ARRAY_CONSTRUCT()) AS WITHHELD_CHILDREN, COMPONENTS.PAYLOAD:projection_of::VARCHAR AS PROJECTION_OF, ORIGINAL.OBJECT_ID AS ORIGINAL_OBJECT_ID, ORIGINAL.SOURCE_ID AS ORIGINAL_SOURCE_ID, ORIGINAL.LOGICAL_ID AS ORIGINAL_LOGICAL_ID, ORIGINAL.REVISION_ID AS ORIGINAL_REVISION_ID, ORIGINAL.VERSION_SET AS ORIGINAL_VERSION_SET, ORIGINAL.VALIDATION_STATUS AS ORIGINAL_VALIDATION_STATUS, COMPONENTS.PAYLOAD:payload:summary::VARCHAR AS SUMMARY, COMPONENTS.PAYLOAD:payload:needs::ARRAY AS NEEDS, COMPONENTS.PAYLOAD:payload:unknowns::ARRAY AS UNKNOWNS, 'unreviewed'::VARCHAR AS REVIEW_STATUS, CASE WHEN G.GUIDANCE_SOURCE_ID IS NOT NULL THEN 'requires_semantic_review' ELSE 'requires_client_guidance' END::VARCHAR AS ELIGIBILITY_STATUS, COMPONENTS.PAYLOAD:payload AS SCENARIO, COMPONENTS.PAYLOAD:issues::ARRAY AS ISSUES", "COMPONENTS.KIND = 'scenario'"),
    "MART_PERSONAS": ("COMPONENTS", "COMPONENTS.OBJECT_ID, COMPONENTS.RUN_ID, COMPONENTS.CLIENT_ID, COMPONENTS.SOURCE_ID, COMPONENTS.VALIDATION_STATUS, COALESCE(COMPONENTS.PAYLOAD:delivery_scope::VARCHAR, 'not_recorded') AS DELIVERY_SCOPE, COALESCE(COMPONENTS.PAYLOAD:partial_state::VARCHAR, 'not_recorded') AS PARTIAL_STATE, COALESCE(COMPONENTS.PAYLOAD:partial_layers::ARRAY, ARRAY_CONSTRUCT()) AS PARTIAL_LAYERS, COALESCE(COMPONENTS.PAYLOAD:withheld_layers::ARRAY, ARRAY_CONSTRUCT()) AS WITHHELD_CHILDREN, COMPONENTS.PAYLOAD:projection_of::VARCHAR AS PROJECTION_OF, ORIGINAL.OBJECT_ID AS ORIGINAL_OBJECT_ID, ORIGINAL.SOURCE_ID AS ORIGINAL_SOURCE_ID, ORIGINAL.LOGICAL_ID AS ORIGINAL_LOGICAL_ID, ORIGINAL.REVISION_ID AS ORIGINAL_REVISION_ID, ORIGINAL.VERSION_SET AS ORIGINAL_VERSION_SET, ORIGINAL.VALIDATION_STATUS AS ORIGINAL_VALIDATION_STATUS, COMPONENTS.PAYLOAD:payload:speaker::VARCHAR AS SPEAKER, COMPONENTS.PAYLOAD:payload:evidence_status::VARCHAR AS EVIDENCE_STATUS, COMPONENTS.PAYLOAD:payload:actions::ARRAY AS ACTIONS, COMPONENTS.PAYLOAD:payload:sequences::ARRAY AS SEQUENCES, COMPONENTS.PAYLOAD:payload:emotional_expression::OBJECT AS EMOTIONAL_EXPRESSION, COMPONENTS.PAYLOAD:payload:emotional_expression:status::VARCHAR AS EMOTIONAL_EXPRESSION_STATUS, COMPONENTS.PAYLOAD:payload:emotional_expression:observations::ARRAY AS EMOTIONAL_OBSERVATIONS, COMPONENTS.PAYLOAD:payload:emotional_changes::ARRAY AS EMOTIONAL_CHANGES, COMPONENTS.PAYLOAD:payload:unknowns::ARRAY AS UNKNOWNS, 'unreviewed'::VARCHAR AS REVIEW_STATUS, CASE WHEN G.GUIDANCE_SOURCE_ID IS NOT NULL THEN 'requires_semantic_review' ELSE 'requires_client_guidance' END::VARCHAR AS ELIGIBILITY_STATUS, COMPONENTS.PAYLOAD:payload AS PERSONA, COMPONENTS.PAYLOAD:issues::ARRAY AS ISSUES", "COMPONENTS.KIND = 'persona'"),
    "MART_FAMILIES": ("FAMILIES", "FAMILY_ID, RUN_ID, CLIENT_ID, KIND, PAYLOAD:grouping_status::VARCHAR AS GROUPING_STATUS, PAYLOAD:guidance_status::VARCHAR AS GUIDANCE_STATUS, PAYLOAD:source_ids::ARRAY AS SOURCE_IDS, PAYLOAD:member_ids::ARRAY AS MEMBER_IDS, PAYLOAD:lineage::ARRAY AS LINEAGE, PAYLOAD:prevalence:unique_source_count::NUMBER AS UNIQUE_SOURCE_COUNT, PAYLOAD:prevalence:all_source_denominator::NUMBER AS ALL_SOURCE_DENOMINATOR, PAYLOAD:prevalence:kind_valid_source_denominator::NUMBER AS KIND_VALID_SOURCE_DENOMINATOR, PAYLOAD:review_status::VARCHAR AS REVIEW_STATUS, PAYLOAD", None),
    "MART_VARIANTS": ("FAMILIES", "FAMILY_ID, RUN_ID, CLIENT_ID, KIND, V.VALUE:object_id::VARCHAR AS OBJECT_ID, V.VALUE:source_id::VARCHAR AS SOURCE_ID, V.VALUE:kind::VARCHAR AS VARIANT_KIND, V.VALUE:payload AS VARIANT_PAYLOAD", None),
    "MART_RELATIONS": ("RELATIONS", "RELATION_ID, RUN_ID, CLIENT_ID, PAYLOAD:candidate_id::VARCHAR AS CANDIDATE_ID, PAYLOAD:kind::VARCHAR AS KIND, PAYLOAD:left_object_id::VARCHAR AS LEFT_OBJECT_ID, PAYLOAD:right_object_id::VARCHAR AS RIGHT_OBJECT_ID, PAYLOAD:relation::VARCHAR AS RELATION, PAYLOAD:status::VARCHAR AS STATUS, PAYLOAD:evidence::ARRAY AS EVIDENCE, PAYLOAD:material_differences::ARRAY AS MATERIAL_DIFFERENCES, PAYLOAD", None),
    "MART_COMPATIBILITY": ("COMPATIBILITY", "COMPATIBILITY_ID, RUN_ID, CLIENT_ID, PAYLOAD:scenario_id::VARCHAR AS SCENARIO_ID, PAYLOAD:persona_id::VARCHAR AS PERSONA_ID, PAYLOAD:source_id::VARCHAR AS SOURCE_ID, PAYLOAD:speaker::VARCHAR AS SPEAKER, PAYLOAD:review_status::VARCHAR AS REVIEW_STATUS, PAYLOAD:scope::VARCHAR AS SCOPE, PAYLOAD:status::VARCHAR AS STATUS, PAYLOAD:evidence::ARRAY AS EVIDENCE, PAYLOAD", None),
    "MART_EVIDENCE": ("COMPONENTS", "OBJECT_ID, RUN_ID, CLIENT_ID, SOURCE_ID, KIND, VALIDATION_STATUS, E.VALUE:event_id::VARCHAR AS EVENT_ID, E.VALUE:quote::VARCHAR AS QUOTE, E.VALUE:role::VARCHAR AS ROLE, E.VALUE:speaker::VARCHAR AS SPEAKER, E.VALUE:authority::VARCHAR AS AUTHORITY, E.PATH::VARCHAR AS EVIDENCE_PATH", "E.VALUE:event_id IS NOT NULL AND E.VALUE:quote IS NOT NULL"),
    "MART_GUIDANCE": ("GUIDANCE", "GUIDANCE_ID, RUN_ID, CLIENT_ID, SOURCE_ID, AUTHORITY, PAYLOAD:status::VARCHAR AS STATUS, PAYLOAD:text::VARCHAR AS GUIDANCE_TEXT, PAYLOAD", None),
    "MART_COVERAGE": ("COVERAGE", "COVERAGE_ID, RUN_ID, CLIENT_ID, SOURCE_ID, STATUS, PAYLOAD:reason::VARCHAR AS REASON, PAYLOAD:component_ids::ARRAY AS COMPONENT_IDS, PAYLOAD:valid_component_count::NUMBER AS VALID_COMPONENT_COUNT, PAYLOAD:quarantined_component_count::NUMBER AS QUARANTINED_COMPONENT_COUNT, PAYLOAD:scenario_count::NUMBER AS SCENARIO_COUNT, PAYLOAD:persona_count::NUMBER AS PERSONA_COUNT, PAYLOAD:training_selection_status::VARCHAR AS TRAINING_SELECTION_STATUS, PAYLOAD", None),
    "MART_RUNS": ("RUNS", "RUN_ID, CLIENT_ID, STATUS, CONFIG:model::VARCHAR AS MODEL, CONFIG:embedding_model::VARCHAR AS EMBEDDING_MODEL, PAYLOAD:started_at::TIMESTAMP_TZ AS STARTED_AT, PAYLOAD:completed_at::TIMESTAMP_TZ AS COMPLETED_AT, PAYLOAD:usage AS USAGE, CONFIG:limits AS LIMITS, CONFIG, PAYLOAD", None),
    "MART_RELEASES": ("RELEASES", "RELEASE_ID, RUN_ID, CLIENT_ID, STATUS, PAYLOAD:schema_version::VARCHAR AS EXPORT_SCHEMA_VERSION, MANIFEST:eligibility::VARCHAR AS ELIGIBILITY, PAYLOAD:release:eligibility_status::VARCHAR AS ELIGIBILITY_STATUS, PAYLOAD:release:review_status::VARCHAR AS REVIEW_STATUS, PAYLOAD:release:client_ready::BOOLEAN AS CLIENT_READY, MANIFEST:versions AS VERSIONS, MANIFEST:source_hashes AS SOURCE_HASHES, MANIFEST:limitations::ARRAY AS LIMITATIONS, MANIFEST, PAYLOAD", None),
}


# Fresh-schema v2 only. Existing v1 schemas fail the metadata check before DDL.
# Lifecycle current rows (RUNS/ATTEMPTS) retain every state in RECORD_REVISIONS.
TABLE_SPECS["RECORD_REVISIONS"] = {"REVISION_ID": "VARCHAR", "LOGICAL_ID": "VARCHAR", "RUN_ID": "VARCHAR", "CLIENT_ID": "VARCHAR", "KIND": "VARCHAR", "VERSION_SET": "VARIANT", "PAYLOAD": "VARIANT"}
TABLE_SPECS["PRACTICE"] = {"PRACTICE_ID": "VARCHAR", "RUN_ID": "VARCHAR", "CLIENT_ID": "VARCHAR", "KIND": "VARCHAR", "PAYLOAD": "VARIANT"}
for _table, _fields in TABLE_SPECS.items():
    if _table not in ("ENGINE_METADATA", "LOCKS", "RECORD_REVISIONS"):
        _fields.update(LOGICAL_ID="VARCHAR", REVISION_ID="VARCHAR", VERSION_SET="VARIANT")
for _table in ("INPUT_RAW", "INPUT_SOURCES"):
    TABLE_SPECS[_table]["RUN_ID"] = "VARCHAR"
for _view, (_table, _columns, _predicate) in list(VIEW_SPECS.items()):
    _lineage_prefix = "COMPONENTS.LOGICAL_ID, COMPONENTS.REVISION_ID, COMPONENTS.VERSION_SET, " if _view in {"MART_SCENARIOS", "MART_PERSONAS"} else "LOGICAL_ID, REVISION_ID, VERSION_SET, "
    VIEW_SPECS[_view] = (_table, _lineage_prefix + _columns, _predicate)
VIEW_SPECS["MART_PRACTICE"] = ("PRACTICE", "PRACTICE_ID, LOGICAL_ID, REVISION_ID, VERSION_SET, RUN_ID, CLIENT_ID, KIND, PAYLOAD", None)

# Capture evidence is append-only evidence state, separate from finished cards.
# These relations belong only to a fresh v4 installation; existing views above
# retain their exact expressions and existing installations retain their marker.
for _table, _identity in (("CAPTURE_ITEMS", "ITEM_ID"), ("CAPTURE_CHECKPOINTS", "CHECKPOINT_ID")):
    TABLE_SPECS[_table] = {
        _identity: "VARCHAR", "RUN_ID": "VARCHAR", "CLIENT_ID": "VARCHAR", "SOURCE_ID": "VARCHAR",
        "RECORD_TYPE": "VARCHAR", "STATUS": "VARCHAR", "SOURCE_HASH": "VARCHAR",
        "PROFILE_VERSION": "VARCHAR", "EXECUTION_MODE": "VARCHAR", "LOGICAL_ID": "VARCHAR",
        "REVISION_ID": "VARCHAR", "VERSION_SET": "VARIANT", "PAYLOAD": "VARIANT",
    }
VIEW_SPECS["MART_CAPTURE_EVIDENCE"] = (
    "CAPTURE_ITEMS",
    "ITEM_ID, LOGICAL_ID, REVISION_ID, VERSION_SET, RUN_ID, CLIENT_ID, SOURCE_ID, RECORD_TYPE, STATUS, "
    "SOURCE_HASH, PROFILE_VERSION, EXECUTION_MODE, PAYLOAD:record:record_id::VARCHAR AS RECORD_ID, "
    "PAYLOAD:record:stage::VARCHAR AS STAGE, PAYLOAD:record:section_id::VARCHAR AS SECTION_ID, "
    "PAYLOAD:record:claim:claim_id::VARCHAR AS CLAIM_ID, PAYLOAD:record:supersedes::VARCHAR AS SUPERSEDES, "
    "TRUE::BOOLEAN AS REQUIRES_CLIENT_GUIDANCE, 'capture_evidence_only'::VARCHAR AS DELIVERY_SCOPE, PAYLOAD",
    None,
)
VIEW_SPECS["MART_CAPTURE_CHECKPOINTS"] = (
    "CAPTURE_CHECKPOINTS",
    "CHECKPOINT_ID, LOGICAL_ID, REVISION_ID, VERSION_SET, RUN_ID, CLIENT_ID, SOURCE_ID, RECORD_TYPE, STATUS, "
    "SOURCE_HASH, PROFILE_VERSION, EXECUTION_MODE, PAYLOAD:checkpoint:section_id::VARCHAR AS SECTION_ID, "
    "PAYLOAD:checkpoint:stage::VARCHAR AS STAGE, PAYLOAD:checkpoint:sequence::NUMBER AS SEQUENCE, "
    "TRUE::BOOLEAN AS REQUIRES_CLIENT_GUIDANCE, 'capture_evidence_only'::VARCHAR AS DELIVERY_SCOPE, PAYLOAD",
    None,
)


GUIDANCE_JOIN = " LEFT JOIN (SELECT DISTINCT RUN_ID AS GUIDANCE_RUN_ID, CLIENT_ID AS GUIDANCE_CLIENT_ID, SOURCE_ID AS GUIDANCE_SOURCE_ID FROM {GUIDANCE} WHERE AUTHORITY='client_approved') G ON G.GUIDANCE_CLIENT_ID=COMPONENTS.CLIENT_ID AND G.GUIDANCE_SOURCE_ID=COMPONENTS.SOURCE_ID AND G.GUIDANCE_RUN_ID=COMPONENTS.RUN_ID"
COMPONENT_SCOPE_JOIN = " LEFT JOIN {COMPONENTS} ORIGINAL ON ORIGINAL.CLIENT_ID=COMPONENTS.CLIENT_ID AND ORIGINAL.RUN_ID=COMPONENTS.RUN_ID AND ORIGINAL.SOURCE_ID=COMPONENTS.SOURCE_ID AND ORIGINAL.OBJECT_ID=COMPONENTS.PAYLOAD:projection_of::VARCHAR"
VIEW_JOINS = {
    "MART_SCENARIOS": GUIDANCE_JOIN + COMPONENT_SCOPE_JOIN,
    "MART_PERSONAS": GUIDANCE_JOIN + COMPONENT_SCOPE_JOIN,
    "MART_VARIANTS": ", LATERAL FLATTEN(INPUT => PAYLOAD:variants) V",
    "MART_EVIDENCE": ", LATERAL FLATTEN(INPUT => PAYLOAD:payload, RECURSIVE => TRUE) E",
}


def validate_identifier(value: str) -> str:
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ValueError(f"Invalid SQL identifier: {value!r}; use unquoted letters, digits and underscores")
    return value.upper()


def qualified_name(config: Mapping, name: str) -> str:
    return ".".join(validate_identifier(x) for x in (config["database"], config["schema"], name))


def manifest_hash() -> str:
    return hashlib.sha256(json.dumps({"tables": TABLE_SPECS, "views": VIEW_SPECS, "view_joins": VIEW_JOINS}, sort_keys=True).encode()).hexdigest()


def schema_ddl(config: Mapping) -> list[str]:
    namespace = ".".join(validate_identifier(config[x]) for x in ("database", "schema"))
    result = [f"CREATE SCHEMA IF NOT EXISTS {namespace}"]
    for name, fields in TABLE_SPECS.items():
        result.append(f"CREATE TABLE IF NOT EXISTS {qualified_name(config, name)} (" + ", ".join(f"{k} {v}" for k, v in fields.items()) + ")")
    for name, (table, columns, predicate) in VIEW_SPECS.items():
        columns = columns.replace("{GUIDANCE}", qualified_name(config, "GUIDANCE"))
        suffix = (VIEW_JOINS.get(name, "").replace("{GUIDANCE}", qualified_name(config, "GUIDANCE"))
                  .replace("{COMPONENTS}", qualified_name(config, "COMPONENTS")))
        result.append(f"CREATE VIEW IF NOT EXISTS {qualified_name(config, name)} AS SELECT {columns} FROM {qualified_name(config, table)}{suffix}" + (f" WHERE {predicate}" if predicate else ""))
    return result


def _row(table: str, row: Mapping) -> tuple[str, dict]:
    table = validate_identifier(table)
    if table not in TABLE_SPECS:
        raise ValueError(f"Unknown engine table: {table}")
    normalized = {validate_identifier(k): v for k, v in row.items()}
    if not normalized or set(normalized) - set(TABLE_SPECS[table]):
        raise ValueError(f"Unknown or empty columns for {table}: {sorted(normalized)}")
    if len(normalized) != len(row):
        raise ValueError("Duplicate case-insensitive column")
    return table, normalized


def _value(table, column, value):
    if TABLE_SPECS[table][column] == "VARIANT":
        return "PARSE_JSON(?)", json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) if value is not None else None
    return "?", value


def insert_row(session, config: Mapping, table: str, row: Mapping):
    table, row = _row(table, row)
    values = [_value(table, k, v) for k, v in row.items()]
    return session.sql(f"INSERT INTO {qualified_name(config, table)} ({', '.join(row)}) SELECT {', '.join(x[0] for x in values)}", params=[x[1] for x in values]).collect()


def merge_row(session, config: Mapping, table: str, row: Mapping, keys: Sequence[str]):
    """Upsert under the engine's exclusive writer lease; Snowflake standard keys are not enforced."""
    table, row = _row(table, row)
    keys = [validate_identifier(k) for k in keys]
    if not keys or set(keys) - set(row) or any(row[k] is None for k in keys):
        raise ValueError("Merge keys must be present and non-null")
    values = [_value(table, k, v) for k, v in row.items()]
    source = ", ".join(f"{v[0]} AS {k}" for k, v in zip(row, values))
    match = " AND ".join(f"t.{k}=s.{k}" for k in keys)
    updates = ", ".join(f"t.{k}=s.{k}" for k in row if k not in keys)
    update = f" WHEN MATCHED THEN UPDATE SET {updates}" if updates else ""
    sql = f"MERGE INTO {qualified_name(config, table)} t USING (SELECT {source}) s ON {match}{update} WHEN NOT MATCHED THEN INSERT ({', '.join(row)}) VALUES ({', '.join('s.' + k for k in row)})"
    return session.sql(sql, params=[x[1] for x in values]).collect()


def ensure_schema_version(session, config: Mapping):
    rows = session.sql(f"SELECT SCHEMA_VERSION, MANIFEST_HASH FROM {qualified_name(config, 'ENGINE_METADATA')}").collect()
    if len(rows) != 1 or rows[0][0] != SCHEMA_VERSION or rows[0][1] != manifest_hash():
        raise ValueError("Engine schema version or manifest mismatch; choose a new dedicated schema and preserve this installation")
    return SCHEMA_VERSION


# Reviewed baseline receipt:
# artifacts/demo/source-grounded-snowflake-01/native-plan/package/schema-manifest.json
READ_ONLY_LEGACY_MANIFESTS = {
    ("engine-storage-v3", "f69a49f3e35663e5a0a51c5a69e472a53b62f67ce8f35077f0b5a322eadba0a4"),
    ("engine-storage-v1", "10da6a5c56da3250dc0441a29b00b14045ec4485200c0a5e320e4162b62c86f7"),
    # Pre-DEMO-173 v2 installation. It remains inspectable/exportable but cannot
    # receive the new mart definitions because views are non-destructively created.
    ("engine-storage-v2", "6545b2b2d2d37678196dc35b5ef3d44bb732b173e3ab0a33f2f11f0766330a46"),
}


def ensure_readable_schema_version(session, config: Mapping):
    """Accept one known legacy schema for inspection/export; never authorise writes."""
    rows = session.sql(f"SELECT SCHEMA_VERSION, MANIFEST_HASH FROM {qualified_name(config, 'ENGINE_METADATA')}").collect()
    if len(rows) == 1:
        marker = (rows[0][0], rows[0][1])
        if marker == (SCHEMA_VERSION, manifest_hash()) or marker in READ_ONLY_LEGACY_MANIFESTS:
            return marker[0]
    raise ValueError("Unrecognised read schema version or manifest; preserve this installation")
