"""Offline capture-state integrity checks; no provider or warehouse connection."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from snowflake_engine import capture_contract, capture_storage, contracts, mapping, schema, versioning

ROOT = Path(__file__).resolve().parents[2]
PROFILE = "worker-reviewer-v9-dev-20261101"


def fixture():
    raw = json.loads((ROOT / "fixtures/engine/synthetic_sources.json").read_text())
    config = json.loads((ROOT / "configs/engine/synthetic_mapping.json").read_text())
    source = mapping.normalize(raw, config, "synthetic")["sources"][0]
    source["metadata"]["customer_side_speaker_ids"] = [source["events"][0]["speaker"]]
    versions = versioning.build_version_set({"operation": "capture_evidence", "capture": {
        "profile_version": PROFILE, "execution_mode": "recorded_smoke"}, "mapping": config,
        "model": "claude-sonnet-4-6", "limits": {"max_output_tokens": 16384, "max_input_chars": 90000}},
        code_digest="synthetic-code")
    args = dict(run_id="capture-run", client_id="synthetic", source_hash=contracts.source_hash(source),
                versions=versions, profile_version=PROFILE, execution_mode="recorded_smoke")
    return source, args


def record(source, args, kind="accepted", *, stage="review", claim=None, **extra):
    if claim is None and kind in {"accepted", "disputed"}:
        projection = capture_contract.source_projection(source)
        _, spans = capture_contract.catalogue(projection)
        first = source["events"][0]
        claim = capture_contract.claim(dict(primary_event_id=first["event_id"], kind="issue", actor=first["speaker"],
            recipient="unknown", action="reports", object="a fictional issue", conditions=[], time=None,
            certainty="asserted", polarity="positive", status="observed", functions=["report"],
            evidence_span_ids=[next(iter(spans))]), projection, spans, {first["event_id"]})
    value = dict(schema="capture-evidence-v1", source_id=source["source_id"], source_hash=args["source_hash"],
                 profile_version=PROFILE, versions=args["versions"], stage=stage, section_id="section-1", record_kind=kind)
    if claim is not None:
        value.update(claim=claim, supersedes=None)
    value.update(extra)
    return dict(value, record_id="item-" + versioning.digest(value))


def checkpoint(source, args, records, sequence=1):
    from snowflake_engine.capture_evidence import current_projection
    view = current_projection(records)
    body = dict(source_id=source["source_id"], source_hash=args["source_hash"], profile_version=PROFILE,
                versions=args["versions"], stage="review", section_id="section-1", status="judged", sequence=sequence,
                request_keys=["request-1"], current_projection={
                    "accepted_record_ids": [r["record_id"] for r in view["accepted"]],
                    "disputed_record_ids": [r["record_id"] for r in view["disputed"]],
                    "omission_record_ids": [r["record_id"] for r in view["omissions"]],
                    "superseded_claim_ids": view["superseded_claim_ids"]})
    return dict(body, checkpoint_id="checkpoint-" + versioning.digest(body))


def export(source, args, records, checkpoints=None):
    return capture_storage.build_capture_export(**{k: v for k, v in args.items() if k != "source_hash"},
        records=records, checkpoints=checkpoints or [], sources=[source])


def test_capture_rows_preserve_lineage_and_runtime_wrapper_contract():
    source, args = fixture()
    raw = record(source, args)
    row = capture_storage.item_row(raw, **args)
    assert set(row) == set(schema.TABLE_SPECS["CAPTURE_ITEMS"])
    assert row["PAYLOAD"]["record"] == raw
    assert row["VERSION_SET"] == args["versions"]
    payload = row["PAYLOAD"]
    assert versioning.revision_id("capture_record", payload["logical_id"], payload, args["versions"]) == row["REVISION_ID"]
    assert capture_storage.item_row(raw, **args) == row
    assert capture_storage.item_row(raw, **dict(args, run_id="another-run"))["ITEM_ID"] != row["ITEM_ID"]
    point = capture_storage.checkpoint_row(checkpoint(source, args, [raw]), **args)
    assert set(point) == set(schema.TABLE_SPECS["CAPTURE_CHECKPOINTS"])


def test_export_roundtrip_preserves_disputes_omissions_supersession_and_checkpoints():
    source, args = fixture()
    disputed = record(source, args, "disputed")
    first = record(source, args)
    changed_claim = deepcopy(first["claim"])
    changed_claim["action"] = "reports a corrected detail"
    changed_claim["claim_id"] = "cl-" + capture_contract.digest({"source": source["source_id"],
        "claim": {k: v for k, v in changed_claim.items() if k != "claim_id"}})[:24]
    replacement = record(source, args, claim=changed_claim, stage="repair_verify", supersedes=first["claim"]["claim_id"])
    omission = record(source, args, "omission", reason="unresolved source meaning", evidence_span_ids=[])
    history = [disputed, first, omission, replacement]
    points = [checkpoint(source, args, history)]
    rows = [capture_storage.item_row(raw, **args) for raw in history]
    point_rows = [capture_storage.checkpoint_row(raw, **args) for raw in points]
    result = export(source, args, rows, point_rows)
    assert not capture_storage.validate_capture_export(json.loads(json.dumps(result)))
    assert len(result["records"]) == 4
    current = result["current_projection"][source["source_id"]]
    assert current["accepted_record_ids"] == [replacement["record_id"]]
    assert current["disputed_record_ids"] == [disputed["record_id"]]
    assert current["omission_record_ids"] == [omission["record_id"]]
    assert current["superseded_claim_ids"] == [first["claim"]["claim_id"]]
    assert result["requires_client_guidance"] is True and result["client_ready"] is False
    assert export(source, args, history, points) == result


def test_invalid_disputed_neighbor_survives_without_becoming_accepted():
    source, args = fixture()
    invalid = record(source, args, "disputed", claim={"claim_id": "invalid-broken", "action": 17},
                     invalid_record=True, original_body={"action": 17})
    result = export(source, args, [invalid])
    assert result["records"][0]["record"]["original_body"] == {"action": 17}
    assert result["current_projection"][source["source_id"]]["accepted_record_ids"] == []
    invalid["record_kind"] = "accepted"
    with pytest.raises(ValueError, match="must remain disputed"):
        capture_storage.item_row(invalid, **args)


def test_fail_closed_record_mutation_context_and_duplicate_ids():
    source, args = fixture()
    raw = record(source, args)
    changed = deepcopy(raw)
    changed["claim"]["action"] = "changed"
    with pytest.raises(ValueError, match="hash changed"):
        capture_storage.item_row(changed, **args)
    with pytest.raises(ValueError, match="source_hash differs"):
        capture_storage.item_row(raw, **dict(args, source_hash="0" * 64))
    with pytest.raises(ValueError, match="Duplicate capture record"):
        export(source, args, [raw, raw])
    row = capture_storage.item_row(raw, **args)
    row["CLIENT_ID"] = "other-client"
    with pytest.raises(ValueError, match="storage row changed"):
        export(source, args, [row])


def test_fail_closed_sources_and_public_export_boundary():
    source, args = fixture()
    raw = record(source, args)
    wrong_client = deepcopy(source)
    wrong_client["client_id"] = "other-client"
    with pytest.raises(ValueError, match="different client"):
        export(wrong_client, args, [raw])
    changed = deepcopy(source)
    changed["events"][0]["text"] += " Changed."
    with pytest.raises(ValueError, match="source_hash differs"):
        export(changed, args, [raw])
    result = export(source, args, [raw])
    text = json.dumps(result)
    assert '"original_text"' not in text and '"guidance"' not in text and '"messages"' not in text
    result["sources"][0]["events"][0]["text"] += " Changed."
    assert capture_storage.validate_capture_export(result)


def test_stored_canonical_source_lineage_is_not_part_of_original_source_hash():
    source, args = fixture()
    wrapped = versioning.annotate(source, "source", args["versions"], ["synthetic", source["source_id"]])
    result = capture_storage.build_capture_export(**{k: v for k, v in args.items() if k != "source_hash"},
        sources=[{"PAYLOAD": json.dumps(wrapped), "CONTENT_HASH": args["source_hash"]}],
        records=[record(source, args)], checkpoints=[])
    assert result["manifest"]["source_hashes"][source["source_id"]] == args["source_hash"]


def test_checkpoint_hash_duplicate_and_cross_source_references_fail_closed():
    source, args = fixture()
    raw = record(source, args)
    point = checkpoint(source, args, [raw])
    with pytest.raises(ValueError, match="Duplicate capture checkpoint"):
        export(source, args, [raw], [point, point])
    changed = deepcopy(point)
    changed["request_keys"].append("different-request")
    with pytest.raises(ValueError, match="checkpoint_id hash changed"):
        capture_storage.checkpoint_row(changed, **args)
    bad = checkpoint(source, args, [raw])
    bad["current_projection"]["accepted_record_ids"] = ["unknown-record"]
    bad["checkpoint_id"] = "checkpoint-" + versioning.digest({k: v for k, v in bad.items() if k != "checkpoint_id"})
    with pytest.raises(ValueError, match="unknown or cross-source"):
        export(source, args, [raw], [bad])


def test_export_does_not_depend_on_physical_table_row_order():
    source, args = fixture()
    first = record(source, args, request_cache_key="request-1")
    disputed = record(source, args, "disputed", request_cache_key="request-2")
    point1 = checkpoint(source, args, [first])
    point2 = checkpoint(source, args, [first, disputed], sequence=2)
    point2["request_keys"].append("request-2")
    point2["checkpoint_id"] = "checkpoint-" + versioning.digest({k: v for k, v in point2.items() if k != "checkpoint_id"})
    expected = export(source, args, [first, disputed], [point1, point2])
    assert export(source, args, [disputed, first], [point2, point1]) == expected
    broken = deepcopy(point2)
    broken["request_keys"] = ["request-2"]
    broken["checkpoint_id"] = "checkpoint-" + versioning.digest({k: v for k, v in broken.items() if k != "checkpoint_id"})
    with pytest.raises(ValueError, match="cumulative request provenance"):
        export(source, args, [first, disputed], [point1, broken])


def test_capture_versions_are_opt_in_and_generic_export_pin_is_unchanged():
    basic = {"limits": {"max_output_tokens": 8192, "max_input_chars": 90000}}
    ordinary = versioning.build_version_set(basic, code_digest="code")
    assert set(ordinary) == versioning.VERSION_KEYS
    assert ordinary["export"] == "engine-export-v3"
    capture = versioning.build_version_set(dict(basic, operation="capture_evidence"), code_digest="code")
    assert capture["capture_contract"] == "capture-evidence-v1"
    assert capture["capture_export"] == "capture-export-v1"
    assert capture["export"] == ordinary["export"]


def test_v4_fresh_schema_guard_and_capture_marts_compile_through_manifest():
    class Frame:
        def __init__(self, rows): self.rows = rows
        def collect(self): return self.rows
    class FakeSession:
        def __init__(self, marker): self.marker, self.calls = marker, []
        def sql(self, sql, params=None):
            self.calls.append((sql, params))
            return Frame([self.marker] if sql.startswith("SELECT SCHEMA_VERSION") else [])
    config = {"database": "D", "schema": "FRESH_CAPTURE"}
    old = ("engine-storage-v3", "f69a49f3e35663e5a0a51c5a69e472a53b62f67ce8f35077f0b5a322eadba0a4")
    session = FakeSession(old)
    with pytest.raises(ValueError, match="new dedicated schema"):
        schema.ensure_schema_version(session, config)
    assert not any(sql.startswith("CREATE") for sql, _ in session.calls)
    assert schema.ensure_readable_schema_version(session, config) == "engine-storage-v3"
    fresh = FakeSession((schema.SCHEMA_VERSION, schema.manifest_hash()))
    assert schema.ensure_schema_version(fresh, config) == "engine-storage-v4"
    for statement in schema.schema_ddl(config):
        fresh.sql(statement).collect()
    marts = [sql for sql, _ in fresh.calls if "CREATE VIEW" in sql and "MART_CAPTURE" in sql]
    assert len(marts) == 2 and all("REQUIRES_CLIENT_GUIDANCE" in sql for sql in marts)
    assert "CAPTURE_CONFIG" in schema.TABLE_SPECS["ENGINE_METADATA"]
    assert all("IF NOT EXISTS" in sql for sql, _ in fresh.calls if sql.startswith("CREATE"))
