"""Local storage/runtime regressions; controlled provider doubles only."""
import copy
import json
from pathlib import Path

import pytest

from snowflake_engine import cli, contracts, native, schema, versioning
from test_native import Session, config, runtime, storage  # shared offline Snowpark double

REAL_ENSURE_SCHEMA_VERSION = schema.ensure_schema_version
ROOT = Path(__file__).resolve().parents[2]


def corpus():
    return (json.loads((ROOT / "fixtures/engine/synthetic_sources.json").read_text()),
            json.loads((ROOT / "configs/engine/synthetic_mapping.json").read_text()))


def test_logical_identity_survives_payload_change_but_revision_commits_to_versions():
    versions = versioning.build_version_set(config(), code_digest="local-source-digest")
    a = versioning.annotate({"object_id": "existing-compatible-id", "summary": "first"}, "scenario", versions, ["client", "source", "local-1"])
    b = versioning.annotate(dict(a, summary="corrected"), "scenario", versions, ["client", "source", "local-1"])
    changed = dict(versions, prompt="new-prompt")
    c = versioning.annotate(a, "scenario", changed, ["client", "source", "local-1"])
    assert a["logical_id"] == b["logical_id"] == c["logical_id"]
    assert len({a["revision_id"], b["revision_id"], c["revision_id"]}) == 3
    assert a["object_id"] == b["object_id"] == "existing-compatible-id"
    assert versioning.revision_id("scenario", a["logical_id"], a, versions) == a["revision_id"]
    assert set(versions) == versioning.VERSION_KEYS
    with pytest.raises(ValueError, match="missing"):
        versioning.annotate({}, "scenario", {"storage": schema.SCHEMA_VERSION}, "x")


def test_typed_marts_and_versioned_tables_expose_lineage():
    assert schema.SCHEMA_VERSION == "engine-storage-v4"
    for table in ("INPUT_RAW", "INPUT_SOURCES", "RUNS", "ATTEMPTS", "COMPONENTS", "FAMILIES", "PRACTICE", "RELEASES"):
        assert {"LOGICAL_ID", "REVISION_ID", "VERSION_SET"} <= set(schema.TABLE_SPECS[table])
    for _, columns, _ in schema.VIEW_SPECS.values():
        assert all(key in columns for key in ("LOGICAL_ID", "REVISION_ID", "VERSION_SET"))
    assert "RUN_ID" in schema.TABLE_SPECS["INPUT_RAW"]
    assert "RUN_ID" in schema.TABLE_SPECS["INPUT_SOURCES"]


def test_source_schema_is_one_explicit_same_database_relation():
    cfg = config(operation="load", source_schema="dbt_stage", source_relation="DEMO_DB.DBT_STAGE.ENGINE_RAW_RECORDS")
    assert cfg["source_relation"] == "DEMO_DB.DBT_STAGE.ENGINE_RAW_RECORDS"
    for relation in ("OTHER_DB.DBT_STAGE.ENGINE_RAW_RECORDS", "DEMO_DB.OTHER_STAGE.ENGINE_RAW_RECORDS",
                     "DEMO_DB.DBT_STAGE.X JOIN DEMO_DB.SECRET.Y"):
        with pytest.raises(ValueError):
            config(operation="load", source_schema="dbt_stage", source_relation=relation)


def test_raw_revisions_append_and_exact_batch_can_be_replayed():
    records, mapping = corpus()
    session = Session()
    first_cfg = config(operation="load", raw_records=records, mapping=mapping, run_id="load-1")
    assert native.run(session, json.dumps(first_cfg))["status"] == "complete"
    old = copy.deepcopy(session.tables["INPUT_RAW"])
    changed = copy.deepcopy(records)
    changed[0]["description"] += " Changed condition."
    result = native.run(session, json.dumps(dict(first_cfg, run_id="load-2", raw_records=changed)))
    assert result["status"] == "complete"
    assert session.tables["INPUT_RAW"][:len(old)] == old
    assert len(session.tables["INPUT_RAW"]) == 2 * len(records)
    replay = native.Runtime(session, config(input_run_id="load-1"))
    assert replay.raw_input() == (records, mapping)
    latest = native.Runtime(session, config())
    assert latest.raw_input() == (changed, mapping)
    assert session.provider_calls == 0


def test_complete_version_cache_cannot_cross_mapping_prompt_or_settings():
    engine = runtime(mapping={"source_id": "original"})
    engine.complete(["same"], "extraction", "same-source")
    initial = engine.request_key("extraction", "same-source", ["same"])
    for field, value in (("input_mapping", "new-mapping"), ("prompt", "new-prompt"), ("selection", {"policy": "new-selection"})):
        old = engine.versions[field]
        engine.versions[field] = value
        assert engine.request_key("extraction", "same-source", ["same"]) != initial
        engine.config["enable_model_calls"] = False
        with pytest.raises(native.ControlError, match="disabled"):
            engine.complete(["same"], "extraction", "same-source")
        engine.versions[field] = old
    assert engine.complete(["same"], "extraction", "same-source") == '{"generated":true}'
    with pytest.raises(native.ControlError, match="disabled"):
        engine.embed(["uncached"], "embedding-source")
    assert engine.session.session.provider_calls == 1


def test_attempt_states_are_preserved_as_immutable_revisions():
    engine = runtime()
    engine.complete(["one"], "extraction", "one")
    states = engine.session.session.tables["RECORD_REVISIONS"]
    assert [row["PAYLOAD"]["STATUS"] for row in states] == ["reserved", "succeeded"]
    assert states[0]["LOGICAL_ID"] == states[1]["LOGICAL_ID"]
    assert states[0]["REVISION_ID"] != states[1]["REVISION_ID"]
    assert states[0]["PAYLOAD"]["PAYLOAD"].get("raw_response") is None
    assert states[1]["PAYLOAD"]["PAYLOAD"]["raw_response"] == '{"generated":true}'


def guarded_runtime(ceiling=16):
    session = Session()
    session.budget["max_generation_ai_credits"] = ceiling
    return runtime(session, model="claude-sonnet-4-6", limits={"max_generation_ai_credits": ceiling})


def test_credit_guard_reserves_conservative_output_and_reports_returned_tokens():
    engine = guarded_runtime()
    engine.complete(["small request"], "extraction", "first")
    credits = engine.usage()["generation_credit_estimate"]
    assert credits["returned_input_tokens"] == 11
    assert credits["returned_output_tokens"] == 5
    assert credits["estimated_ai_credits"] == pytest.approx((11 * 1.8 + 5 * 9) / 1_000_000)
    assert not credits["unknown_attempt_ids"]
    too_small = guarded_runtime(0.001)
    with pytest.raises(native.ControlError, match="AI-credit ceiling"):
        too_small.complete(["small request"], "extraction", "first")
    assert too_small.session.session.provider_calls == 0
    assert not too_small.attempts


def test_credit_guard_stops_after_unknown_or_failed_prior_usage():
    engine = guarded_runtime()
    engine.session.session.fail_provider = True
    with pytest.raises(RuntimeError, match="synthetic"):
        engine.complete(["first"], "extraction", "first")
    engine.session.session.fail_provider = False
    with pytest.raises(native.ControlError, match="usage is failed"):
        engine.complete(["different"], "extraction", "second")
    assert engine.session.session.provider_calls == 1


def test_credit_guard_cannot_be_omitted_or_raised_after_install():
    session = Session()
    session.budget["max_generation_ai_credits"] = 1
    for limits in ({}, {"max_generation_ai_credits": 2}):
        with pytest.raises(native.ControlError, match="immutable installed"):
            runtime(session, limits=limits)
    with pytest.raises(ValueError, match="at most"):
        config(limits={"max_generation_ai_credits": 17})


def test_split_v3_is_opt_in_with_complete_candidate_versions():
    legacy = config()
    candidate = config(extraction_mode="split_v3", limits={"max_output_tokens": 6144})
    assert legacy["extraction_mode"] == "legacy"
    assert legacy["limits"]["max_output_tokens"] == 4096
    versions = versioning.build_version_set(candidate, code_digest="same-code")
    assert versions["extraction_contract"] == contracts.CAPTURE_V3_VERSIONS["schema"]
    assert versions["model_settings"]["max_output_tokens"] == 6144
    assert versions["storage"] == "engine-storage-v4"


def test_known_legacy_schema_is_readable_but_never_write_compatible():
    from test_install import Session as MetadataSession
    marker = next(marker for marker in schema.READ_ONLY_LEGACY_MANIFESTS if marker[0] == "engine-storage-v1")
    session = MetadataSession(metadata=[marker])
    assert schema.ensure_readable_schema_version(session, config()) == "engine-storage-v1"
    with pytest.raises(ValueError, match="mismatch"):
        REAL_ENSURE_SCHEMA_VERSION(session, config())
    unknown = MetadataSession(metadata=[("engine-storage-v1", "unreviewed-manifest")])
    with pytest.raises(ValueError, match="Unrecognised"):
        schema.ensure_readable_schema_version(unknown, config())
    assert all(sql.startswith("SELECT ") for sql, _ in session.calls + unknown.calls)


def test_variant_integral_numeric_settings_preserve_revision_but_source_metadata_does_not():
    versions = versioning.build_version_set(config(), code_digest="digest")
    versions["selection"] = {"threshold": 1.0}
    integer_versions = copy.deepcopy(versions)
    integer_versions["selection"]["threshold"] = 1
    first = versioning.annotate({"score": 1.0}, "scenario", versions, "same")
    assert first["revision_id"] == versioning.annotate({"score": 1.0}, "scenario", integer_versions, "same")["revision_id"]
    assert first["revision_id"] != versioning.annotate({"score": 1}, "scenario", versions, "same")["revision_id"]
    assert versioning.normalize_versions({"flag": True, "number": 1.0}) == {"flag": True, "number": 1}


def test_pilot_split_calls_twice_per_source_without_embeddings_or_planner(monkeypatch):
    from snowflake_engine import extraction, practice, export
    records, mapping = corpus()
    session = Session()
    session.budget["max_output_tokens"] = 6144
    calls = []
    def fake_split(source, complete, max_input_chars):
        # Native callback, reservation, usage and cache paths stay real.
        complete([source["source_id"], "scenario"], "extraction_v3_scenario", source["source_id"] + "-scenario")
        complete([source["source_id"], "persona"], "extraction_v3_persona", source["source_id"] + "-persona")
        calls.append(source["source_id"])
        event = source["events"][-1]
        citation = {key: event[key] for key in ("event_id", "role", "speaker", "authority")}
        citation["quote"] = event["text"]
        # Test persistence with a valid legacy-shaped scenario; v3 validator itself
        # is tested separately by capture tests, not bypassed in production.
        return {"issues": [], "components": [{"object_id": "scenario-" + source["source_id"],
            "source_id": source["source_id"], "kind": "scenario", "validation_status": "valid", "issues": [],
            "payload": {"id": "scenario-1", "summary": "Synthetic callback test", "needs": [{"id": "need-1",
                "kind": "primary_need", "summary": "Retained source", "status": "reported", "related_ids": [], "evidence": [citation]}], "unknowns": []}}]}
    monkeypatch.setattr(extraction, "extract_v3", fake_split)
    def no_planner(*args, **kwargs):
        # This verifies the runtime passes planner_requests=0 once after aggregation.
        assert args[3]["planner_requests"] == 0
        assert len(args[0]) == 5
        return {"practice_groups": [], "assignments": [], "issues": [], "versions": args[4]}
    monkeypatch.setattr(practice, "build_programme", no_planner)
    # Export shape and practice semantics have independent focused suites. This
    # orchestration double checks the callback budget and storage handoff only.
    monkeypatch.setattr(export, "build_export", lambda records, manifest: {"release": {"release_id": "pilot-draft"}, "manifest": manifest})
    monkeypatch.setattr(export, "validate_export", lambda document: [])
    cfg = config(raw_records=records, mapping=mapping, extraction_mode="split_v3", run_id="candidate-pilot", limits={"max_output_tokens": 6144},
        aggregation={"max_candidate_pairs": 0, "relationship_requests": 0}, practice={"planner_requests": 0})
    result = native.run(session, json.dumps(cfg))
    assert len(calls) == 5
    assert session.provider_calls == 10
    assert len(session.tables["COMPONENTS"]) == 5
    assert len(session.tables["PRACTICE"]) == 1
    assert not session.tables["EMBEDDINGS"]
    assert all(row["OPERATION"].startswith("extraction_v3_") for row in session.tables["ATTEMPTS"])
    assert all(row["VERSION_SET"]["extraction_contract"] == contracts.CAPTURE_V3_VERSIONS["schema"] for row in session.tables["COMPONENTS"])
    assert not result.get("error"), result


@pytest.mark.parametrize("cap", [4096, 6143, 6145, 8192])
def test_split_v3_rejects_output_limit_different_from_its_exact_contract(cap):
    with pytest.raises(ValueError, match="exact max_output_tokens=6144"):
        config(extraction_mode="split_v3", limits={"max_output_tokens": cap})
