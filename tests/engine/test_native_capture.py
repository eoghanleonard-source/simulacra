"""Native capture controls with recorded responses and an in-memory Snowpark double."""
import copy
import json

import pytest

from snowflake_engine import capture_evidence, cli, mapping, native
from test_capture_evidence import Scripted, claim, review
from test_native import Session, config, storage, warehouse_input


class CaptureSession(Session):
    def __init__(self, mode="recorded_smoke", allowed=True):
        super().__init__()
        self.budget.update(max_output_tokens=16384, max_input_chars=90000, max_sql_statements=1000)
        self.capture_config = {"capture_enabled": allowed, "capture_profile": native.CAPTURE_PROFILE,
                               "permitted_execution_mode": mode, "allow_recorded_capture": allowed}

    def execute(self, sql, params):
        if "SELECT CAPTURE_CONFIG" in sql:
            self.log.append((sql, copy.deepcopy(params)))
            return [(json.dumps(self.capture_config),)]
        for table in ("CAPTURE_ITEMS", "CAPTURE_CHECKPOINTS", "INPUT_SOURCES", "RUNS"):
            if sql.startswith("SELECT * FROM") and "." + table + " WHERE " in sql:
                self.log.append((sql, copy.deepcopy(params)))
                rows = self.tables[table]
                if "WHERE RUN_ID=? AND CLIENT_ID=?" in sql:
                    return copy.deepcopy([r for r in rows if r["RUN_ID"] == params[0] and r["CLIENT_ID"] == params[1]])
                field = sql.split(" WHERE ")[1].split("=")[0]
                return copy.deepcopy([r for r in rows if r[field] == params[0]])
        return super().execute(sql, params)


def capture_input():
    raw, spec = warehouse_input()
    raw.update(conversation_id="synthetic-capture-01")
    raw["messages"][0].update(id="turn-0", speaker="buyer", body="Please inspect the blue lens.")
    spec["types"]["typed_chat"]["metadata_defaults"] = {"customer_side_speaker_ids": ["buyer"]}
    canonical = mapping.normalize([raw], spec, "synthetic")["sources"][0]
    return raw, spec, canonical


def recorded_fixture(canonical):
    """Source -> stage -> raw responses in zero-based per-stage call order."""
    previous, responses = {}, {}
    def mutate(stage, primary, payload, response):
        if stage == "review":
            previous["old"] = payload["CANDIDATES"][0]["claim"]["claim_id"]
            return review(primary, payload["CANDIDATES"], "reject", True)
        if stage == "repair":
            response["changes"] = [{"supersedes": previous["old"], "claim": claim(action="requests lens inspection")}]
        if stage == "repair_verify":
            previous["repaired"] = payload["CANDIDATES"][0]["claim"]["claim_id"]
        if stage == "reconcile":
            response["changes"] = [{"supersedes": previous["repaired"], "claim": claim(action="requests inspection of blue lens")}]
        return response
    scripted = Scripted(mutate)
    def record(messages, stage, key, *, max_output_tokens):
        raw = scripted(messages, stage, key, max_output_tokens=max_output_tokens)
        responses.setdefault(stage, []).append(raw)
        return raw
    capture_evidence.capture_source(canonical, record, settings={}, versions={})
    return {canonical["source_id"]: responses}


def capture_config(**changes):
    raw, spec, canonical = capture_input()
    values = dict(operation="capture_evidence", model=native.CAPTURE_MODEL, enable_model_calls=False,
                  execution_mode="recorded_smoke", raw_records=[raw], mapping=spec,
                  persist_request_messages=True, recorded_responses=recorded_fixture(canonical),
                  limits=dict(CaptureSession().budget))
    values.update(changes)
    return config(**values)


def test_recorded_native_all_stages_persistence_cache_and_readonly_export():
    session = CaptureSession()
    cfg = capture_config(run_id="capture-1")
    first = native.run(session, cfg)
    assert first["status"] == "partial"  # historical dispute remains visible after repair
    assert first["provider_model_calls"] == session.provider_calls == 0
    assert first["usage"]["generation_credit_estimate"]["estimated_ai_credits"] == 0
    assert {r["OPERATION"] for r in session.tables["ATTEMPTS"]} == native.RECORDED_CAPTURE_OPERATIONS
    assert len(session.tables["ATTEMPTS"]) == 6
    assert session.tables["CAPTURE_ITEMS"] and session.tables["CAPTURE_CHECKPOINTS"]
    assert not session.tables["COMPONENTS"] and not session.tables["RELEASES"] and not session.tables["EMBEDDINGS"]
    for attempt in session.tables["ATTEMPTS"]:
        payload = attempt["PAYLOAD"]
        assert payload["execution_mode"] == "recorded_smoke" and payload["provider_model_calls"] == 0
        assert payload["query_id"] is None and "raw_provider_result" not in payload
        assert payload["request_messages"] and payload["usage"]["total_tokens"] == 0
        assert payload["max_output_tokens"] == (8192 if attempt["OPERATION"].endswith("_worker") else 16384)
    second = native.run(session, dict(cfg, run_id="capture-2"))
    assert second["status"] == "partial" and second["usage"]["cache_hits"] == 6
    assert len(session.tables["ATTEMPTS"]) == 6 and session.provider_calls == 0
    before = copy.deepcopy(session.tables)
    exported = native.run(session, config(operation="capture_export", capture_run_id="capture-1"))
    assert exported["schema_version"] == "capture-export-v1"
    assert exported["records"] and exported["checkpoints"]
    assert session.tables == before
    repeated = native.run(session, cfg)
    assert repeated["status"] == "failed" and "run_id already exists" in repeated["error"]


def test_recorded_requires_installed_capability_and_missing_response_never_falls_back():
    denied = CaptureSession(allowed=False)
    result = native.run(denied, capture_config(allow_recorded_capture=True))
    assert result["status"] == "failed" and "immutable installation" in result["error"]
    assert denied.provider_calls == 0 and not denied.tables["ATTEMPTS"]
    cfg = capture_config()
    cfg["recorded_responses"]["synthetic-capture-01"].pop("repair")
    session = CaptureSession()
    result = native.run(session, cfg)
    assert result["status"] == "failed" and "Missing recorded response" in result["error"]
    assert session.provider_calls == 0
    assert any(r["PAYLOAD"]["record"]["record_kind"] == "capture_failure" for r in session.tables["CAPTURE_ITEMS"])
    assert session.tables["CAPTURE_CHECKPOINTS"][-1]["STATUS"] == "failed"


def test_live_and_recorded_modes_cannot_mix_and_stage_cap_is_bounded():
    session = CaptureSession()
    cfg = capture_config()
    cfg.pop("recorded_responses")
    cfg.update(execution_mode="live", enable_model_calls=True)
    result = native.run(session, cfg)
    assert result["status"] == "failed" and session.provider_calls == 0
    live = CaptureSession(mode="live")
    engine = native.Runtime(live, native.validate_config(cfg))
    engine.acquire()
    raw = engine.complete([{"role": "user", "content": "evidence"}], "capture_evidence_worker", "key", max_output_tokens=8192)
    assert raw and live.provider_calls == 1
    assert live.tables["ATTEMPTS"][0]["PAYLOAD"]["max_output_tokens"] == 8192
    provider = [params for sql, params in live.log if "AI_COMPLETE" in sql][0]
    assert json.loads(provider[2])["max_tokens"] == 8192
    for cap in (16385, 0, True):
        with pytest.raises(native.ControlError, match="cap exceeds"):
            engine.complete([], "capture_evidence_worker", "denied", max_output_tokens=cap)
    assert live.provider_calls == 1


def test_source_validation_and_recorded_logical_allowance_precede_provider():
    cfg = capture_config()
    cfg["mapping"]["types"]["typed_chat"]["metadata_defaults"] = {}
    session = CaptureSession()
    result = native.run(session, cfg)
    assert result["status"] == "failed" and "customer_side_speaker_ids" in result["error"]
    assert not session.tables["ATTEMPTS"] and session.provider_calls == 0
    session = CaptureSession()
    cfg = capture_config()
    cfg["limits"]["max_generation_calls"] = 1
    with pytest.raises(ValueError, match="logical stage allowance"):
        native.run(session, cfg)
    cfg = capture_config()
    session.tables["ATTEMPTS"] = [{"ATTEMPT_ID": "prior-" + str(i), "RUN_ID": "prior", "CLIENT_ID": "another-tenant",
        "OPERATION": "capture_recorded_worker", "REQUEST_KEY": "old-" + str(i), "STATUS": "succeeded",
        "PAYLOAD": {"execution_mode": "recorded_smoke"}} for i in range(11)]
    result = native.run(session, cfg)
    assert result["status"] == "failed" and "Cumulative completion allowance" in result["error"]
    assert len(session.tables["ATTEMPTS"]) == 12 and session.provider_calls == 0


def test_cli_capture_configuration_and_plan(tmp_path):
    path = tmp_path / "config.json"
    assert cli.main(["configure", "--database", "YOUR_DATABASE", "--schema", "ENGINE_CAPTURE_DEMO",
        "--warehouse", "YOUR_WAREHOUSE", "--role", "ACCOUNTADMIN", "--client-id", "synthetic",
        "--model", native.CAPTURE_MODEL, "--embedding-model", "snowflake-arctic-embed-l-v2.0",
        "--capture-evidence", "--permitted-execution-mode", "recorded_smoke", "--allow-recorded-capture", "--output", str(path)]) == 0
    cfg = json.loads(path.read_text())
    assert cfg["limits"]["max_output_tokens"] == 16384 and cfg["enable_model_calls"] is False
    out = tmp_path / "export.json"
    assert cli.main(["capture-export", "--config", str(path), "--run-id", "capture-1", "--output", str(out)]) == 0
    assert json.loads(out.read_text())["request"]["operation"] == "capture_export"
    assert {"capture_evidence.py", "capture_contract.py", "capture_prompts.py", "capture_storage.py"} <= cli.REQUIRED_MODULES


def test_capture_append_refuses_changed_identity_and_live_cannot_replay_recording():
    session = CaptureSession()
    cfg = capture_config(run_id="recorded-original")
    assert native.run(session, cfg)["status"] == "partial"
    row = copy.deepcopy(session.tables["CAPTURE_ITEMS"][0])
    engine = native.Runtime(session, native.validate_config(dict(cfg, run_id="new-run")))
    engine.acquire()
    before = len(session.tables["CAPTURE_ITEMS"])
    engine.append_capture("CAPTURE_ITEMS", row, "ITEM_ID")
    assert len(session.tables["CAPTURE_ITEMS"]) == before
    row["STATUS"] = "changed"
    with pytest.raises(native.ControlError, match="Changed duplicate"):
        engine.append_capture("CAPTURE_ITEMS", row, "ITEM_ID")
    engine.release()
    # A separately authorized live installation would still need a provider call;
    # recorded operation/cache provenance cannot satisfy it.
    session.capture_config["permitted_execution_mode"] = "live"
    live_config = dict(cfg, run_id="live-separate", execution_mode="live", enable_model_calls=True)
    live_config.pop("recorded_responses")
    result = native.run(session, live_config)
    assert result["status"] == "failed"  # fake provider is not a capture fixture
    assert session.provider_calls == 1
    assert session.tables["ATTEMPTS"][-1]["OPERATION"] == "capture_evidence_worker"
