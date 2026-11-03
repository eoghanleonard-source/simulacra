"""Ordinary recorded execution exercises the real pipeline without provider SQL."""
import copy
import json

import pytest

from snowflake_engine import cli, contracts, mapping, native
from test_capture_v3 import responses
from test_native import Session, config, storage, warehouse_input
from test_extraction import source


class RecordedSession(Session):
    def __init__(self, allowed=True):
        super().__init__()
        self.budget.update(max_output_tokens=6144, max_sql_statements=1000)
        self.capability = native.capture_install_config({
            "allow_recorded_run": allowed,
            **({"permitted_run_execution_mode": "recorded_smoke"} if allowed else {})})

    def execute(self, sql, params):
        if "SELECT CAPTURE_CONFIG" in sql:
            self.log.append((sql, copy.deepcopy(params)))
            return [(json.dumps(self.capability),)]
        return super().execute(sql, params)


def request(run_id="recorded-baseline", records=None):
    template, spec = warehouse_input()
    spec["types"]["typed_chat"]["metadata_defaults"]["customer_side_speaker_ids"] = ["buyer"]
    if records is None:
        records = []
        for index in range(2):
            raw = copy.deepcopy(template)
            raw.update(record_id=f"raw-{index}", conversation_id=f"history-{index}")
            raw["messages"] = [{"id": event["event_id"], "position": event["ordinal"],
                "body": event["text"], "role": event["role"], "speaker": event["speaker"],
                "sent_at": None} for event in source()["events"]]
            records.append(raw)
    sources = mapping.normalize(records, spec, "synthetic")["sources"]
    entries = [{"source_id": row["source_id"], "source_hash": contracts.source_hash(row),
                "operations": {"extraction_v3_" + kind: [json.dumps(value)]
                               for kind, value in responses(row).items()}} for row in sources]
    return config(operation="run", execution_mode="recorded_smoke", enable_model_calls=False,
        extraction_mode="split_v3", run_id=run_id, raw_records=records, mapping=spec,
        persist_request_messages=True, aggregation={"max_candidate_pairs": 0},
        recorded_run_responses={"format": native.RECORDED_RUN_FORMAT, "sources": entries},
        limits=dict(RecordedSession().budget))


def test_two_history_ordinary_pipeline_cache_changed_history_and_retained_failure():
    session = RecordedSession()
    baseline = request()
    first = native.run(session, baseline)
    assert first["status"] == "complete", first
    assert first["usage"]["fixture_attempts_this_run"] == 4
    assert len(session.tables["COMPONENTS"]) == len(session.tables["FAMILIES"]) == 4
    initial_release = copy.deepcopy(session.tables["RELEASES"][0])
    initial_revisions = {row["SOURCE_ID"]: [] for row in session.tables["COMPONENTS"]}
    for row in session.tables["COMPONENTS"]:
        initial_revisions[row["SOURCE_ID"]].append(row["REVISION_ID"])
    unchanged = native.run(session, dict(baseline, run_id="recorded-unchanged"))
    assert unchanged["status"] == "complete"
    assert unchanged["usage"]["fixture_attempts_this_run"] == 0
    assert unchanged["usage"]["cache_hits"] == 4
    assert len(session.tables["ATTEMPTS"]) == 4
    for sid, revisions in initial_revisions.items():
        assert [r["REVISION_ID"] for r in session.tables["COMPONENTS"]
                if r["SOURCE_ID"] == sid and r["RUN_ID"] == "recorded-unchanged"] == revisions
    changed_raw = copy.deepcopy(baseline["raw_records"])
    changed_raw[0]["messages"][0]["body"] += " Please send an estimate."
    changed = request("recorded-changed", changed_raw)
    third = native.run(session, changed)
    assert third["status"] == "complete", third
    assert third["usage"]["fixture_attempts_this_run"] == 2
    assert third["usage"]["cache_hits"] == 2
    new_rows = session.tables["COMPONENTS"][-4:]
    assert [r["REVISION_ID"] for r in new_rows if r["SOURCE_ID"] == "history-1"] == initial_revisions["history-1"]
    assert [r["REVISION_ID"] for r in new_rows if r["SOURCE_ID"] == "history-0"] != initial_revisions["history-0"]
    assert session.tables["RELEASES"][0] == initial_release
    failed = copy.deepcopy(changed)
    failed["run_id"] = "recorded-invalid-response"
    failed["recorded_run_responses"]["sources"][0]["operations"] = {
        "extraction_v3_scenario": ["{bad scenario"], "extraction_v3_persona": ["{bad persona"]}
    fourth = native.run(session, failed)
    assert fourth["status"] == "partial", fourth
    assert fourth["usage"]["fixture_attempts_this_run"] == 2
    assert fourth["usage"]["cache_hits"] == 2
    assert any(row["SOURCE_ID"] == "history-0" and row["STATUS"] == "quarantined"
               for row in session.tables["COVERAGE"] if row["RUN_ID"] == failed["run_id"])
    assert len([r for r in session.tables["INPUT_ISSUES"] if r["RUN_ID"] == failed["run_id"]]) == 2
    failed_issues = [r for r in session.tables["INPUT_ISSUES"] if r["RUN_ID"] == failed["run_id"]]
    assert all(r["SOURCE_ID"] == r["PAYLOAD"]["source_id"] == "history-0" for r in failed_issues)
    assert any(r["RUN_ID"] == failed["run_id"] for r in session.tables["INPUT_RAW"])
    assert session.tables["RELEASES"][0] == initial_release
    for result in (first, unchanged, third, fourth):
        assert result["usage"]["completions"] == result["usage"]["embedding_inputs"] == 0
        assert result["usage"]["provider_model_calls"] == 0
        assert result["usage"]["generation_credit_estimate"]["unknown_attempt_ids"] == []
    assert session.provider_calls == 0
    assert not any("AI_COMPLETE" in sql or "AI_EMBED" in sql for sql, _ in session.log)
    assert all(row["PAYLOAD"]["usage_status"] == "recorded_zero_provider_usage" for row in session.tables["ATTEMPTS"])
    assert all(row["PAYLOAD"]["request_messages_sha256"] for row in session.tables["ATTEMPTS"])
    assert initial_release["MANIFEST"]["execution_mode"] == "recorded_smoke"
    assert initial_release["MANIFEST"]["unattended_inference_qualified"] is False


def test_install_capability_cannot_be_supplied_by_caller_and_missing_source_fails_visibly():
    denied = RecordedSession(allowed=False)
    result = native.run(denied, dict(request(), allow_recorded_run=True))
    assert result["status"] == "failed" and "immutable installation" in result["error"]
    assert denied.provider_calls == len(denied.tables["ATTEMPTS"]) == 0
    session = RecordedSession()
    cfg = request()
    cfg["recorded_run_responses"]["sources"].pop(0)
    result = native.run(session, cfg)
    assert result["status"] == "partial"
    assert any(row["SOURCE_ID"] == "history-0" and row["STATUS"] == "failed"
               for row in session.tables["COVERAGE"])
    assert any("exact source_id/source_hash" in row["REASON"] for row in session.tables["INPUT_ISSUES"])
    assert len(session.tables["ATTEMPTS"]) == 2 and session.provider_calls == 0


@pytest.mark.parametrize("mutation", [
    lambda c: c.update(enable_model_calls=True),
    lambda c: c.update(execution_mode="live"),
    lambda c: c.update(aggregation={"max_candidate_pairs": 1}),
    lambda c: c["recorded_run_responses"]["sources"][0]["operations"].update(source_review_v1=["{}"]),
    lambda c: c["recorded_run_responses"]["sources"][0]["operations"]["extraction_v3_scenario"].append("{}"),
])
def test_recorded_contract_rejects_live_mixing_unknown_operations_and_extra_calls(mutation):
    cfg = request()
    mutation(cfg)
    with pytest.raises(ValueError):
        native.validate_config(cfg)


def test_request_hash_mismatch_and_embeddings_cannot_fall_back_to_live():
    session = RecordedSession()
    cfg = request()
    for entry in cfg["recorded_run_responses"]["sources"]:
        entry["operations"]["extraction_v3_scenario"] = [{"raw_response": "{}", "request_messages_sha256": "0" * 64}]
    result = native.run(session, cfg)
    assert result["status"] == "failed"
    assert len(session.tables["ATTEMPTS"]) == 0 and session.provider_calls == 0
    engine = native.Runtime(session, request(run_id="embedding-denied"))
    engine.acquire()
    with pytest.raises(native.ControlError, match="prohibit embeddings"):
        engine.embed(["fixture"], "key")
    engine.release()


def test_additive_install_and_cli_run_plan(tmp_path):
    assert native.capture_install_config({}) == {}
    cfg = request()
    fixture_path = tmp_path / "responses.json"
    fixture_path.write_text(json.dumps(cfg.pop("recorded_run_responses")))
    cfg.pop("execution_mode")
    cfg.update(warehouse="DEMO_WH", role="DEMO_ROLE", allow_recorded_run=True,
               permitted_run_execution_mode="recorded_smoke")
    config_path, output = tmp_path / "config.json", tmp_path / "plan.json"
    config_path.write_text(json.dumps(cfg))
    assert cli.main(["run", "--config", str(config_path), "--run-id", "planned-recorded",
        "--execution-mode", "recorded_smoke", "--recorded-run-responses", str(fixture_path), "--output", str(output)]) == 0
    plan = json.loads(output.read_text())
    assert plan["executed"] is False
    assert plan["request"]["enable_model_calls"] is False
    assert plan["request"]["recorded_run_responses"]["format"] == native.RECORDED_RUN_FORMAT
