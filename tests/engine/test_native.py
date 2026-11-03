"""Offline orchestration checks. No Snowflake connection or model request is made."""
import copy
import json
from pathlib import Path

import pytest

from snowflake_engine import native, schema


class Frame:
    def __init__(self, callback):
        self.callback = callback

    def collect(self):
        return self.callback()


class Session:
    def __init__(self):
        self.tables = {key: [] for key in schema.TABLE_SPECS}
        self.budget = dict(native.DEFAULT_LIMITS)
        self.lock_owner = None
        self.log = []
        self.provider_calls = 0
        self.fail_provider = False
        self.in_transaction = False
        self.autocommit = False
        self.transaction_origin = None
        self.transaction_snapshot = None
        self.committed_reservations = set()
        self.source_relation_rows = []

    def sql(self, sql, params=None, **kwargs):
        return Frame(lambda: self.execute(sql, params or []))

    def execute(self, sql, params):
        self.log.append((sql, copy.deepcopy(params)))
        if sql == "BEGIN TRANSACTION":
            if self.in_transaction:
                raise RuntimeError("Nested or unclosed implicit transaction")
            self.transaction_snapshot = (copy.deepcopy(self.tables), self.lock_owner)
            self.in_transaction = True
            self.transaction_origin = "explicit"
        elif sql == "COMMIT":
            self.in_transaction = False
            self.transaction_origin = None
            self.transaction_snapshot = None
            self.committed_reservations.update(r["ATTEMPT_ID"] for r in self.tables["ATTEMPTS"])
        elif sql == "ROLLBACK":
            if self.transaction_snapshot is not None:
                self.tables, self.lock_owner = self.transaction_snapshot
            self.in_transaction = False
            self.transaction_origin = None
            self.transaction_snapshot = None
        elif "CURRENT_TRANSACTION()" in sql:
            return [("outside" if self.in_transaction else None,)]
        elif "SELECT BUDGET" in sql:
            return [(json.dumps(self.budget),)]
        elif "SELECT LOCK_KEY" in sql:
            return [("engine",)]
        elif "UPDATE" in sql and "OWNER_RUN_ID=?" in sql and "IS NULL" in sql:
            if self.lock_owner is not None:
                return [(0,)]
            self.lock_owner = params[0]
            return [(1,)]
        elif "UPDATE" in sql and "OWNER_RUN_ID=NULL" in sql:
            if self.lock_owner == params[0]:
                self.lock_owner = None
            return [(1,)]
        elif "SELECT *" in sql and ".ATTEMPTS" in sql:
            return copy.deepcopy(self.tables["ATTEMPTS"])
        elif "SELECT RUN_ID" in sql:
            return [(r["RUN_ID"],) for r in self.tables["RUNS"] if r["RUN_ID"] == params[0]]
        elif sql.startswith("SELECT SOURCE_ID, RAW_RECORD FROM"):
            rows = [row for row in self.source_relation_rows if not params or row.get("SOURCE_ID") in params]
            return copy.deepcopy(sorted(rows, key=lambda row: str(row.get("SOURCE_ID", "")))[:int(sql.rsplit(" ", 1)[-1])])
        elif sql.startswith("SELECT RAW_ID, PAYLOAD FROM"):
            return copy.deepcopy(sorted(self.tables["INPUT_RAW"], key=lambda row: row["RAW_ID"]))
        elif "AI_COMPLETE" in sql or "AI_EMBED" in sql:
            self.provider_calls += 1
            reservation = self.tables["ATTEMPTS"][-1]
            assert reservation["STATUS"] == "reserved"
            assert reservation["ATTEMPT_ID"] in self.committed_reservations
            assert not self.in_transaction
            if self.fail_provider:
                raise RuntimeError("synthetic native provider failure")
            if "AI_COMPLETE" in sql:
                return [(json.dumps({"choices": [{"messages": '{"generated":true}'}], "usage": {"prompt_tokens": 11, "completion_tokens": 5, "total_tokens": 16}}),)]
            assert "AI_EMBED('snowflake-arctic-embed-l-v2.0', ?)" in sql
            assert len(params) == 1
            return [(json.dumps([0.25, 0.75]),)]
        elif "LAST_QUERY_ID()" in sql:
            return [(f"native-query-{self.provider_calls}",)]
        return []


@pytest.fixture(autouse=True)
def storage(monkeypatch):
    monkeypatch.setattr(schema, "ensure_schema_version", lambda *_: schema.SCHEMA_VERSION)

    def insert(session, config, table, row):
        assert session.session.in_transaction and session.session.transaction_origin == "explicit"
        session.session.tables[table].append(copy.deepcopy(row))
        return []

    def merge(session, config, table, row, keys):
        assert session.session.in_transaction and session.session.transaction_origin == "explicit"
        rows = session.session.tables[table]
        for index, old in enumerate(rows):
            if all(old[k] == row[k] for k in keys):
                rows[index] = copy.deepcopy(row)
                break
        else:
            rows.append(copy.deepcopy(row))
        return []

    monkeypatch.setattr(schema, "insert_row", insert)
    monkeypatch.setattr(schema, "merge_row", merge)


def config(**overrides):
    return native.validate_config({**dict(database="DEMO_DB", schema="ENGINE_TEST", client_id="synthetic", model="configured-model", embedding_model="snowflake-arctic-embed-l-v2.0", enable_model_calls=True), **overrides})


def runtime(session=None, **overrides):
    result = native.Runtime(session or Session(), config(**overrides))
    result.acquire()
    return result


def test_completion_reserves_and_commits_before_provider_and_retains_usage():
    engine = runtime()
    raw = engine.complete([{"role": "user", "content": "complete evidence"}], "extraction", "source-hash")
    assert raw == '{"generated":true}'
    attempt = engine.session.session.tables["ATTEMPTS"][0]
    assert attempt["STATUS"] == "succeeded"
    assert attempt["PAYLOAD"]["usage"]["total_tokens"] == 16
    assert attempt["PAYLOAD"]["query_id"] == "native-query-1"
    assert attempt["PAYLOAD"]["raw_provider_result"]["choices"][0]["messages"] == raw
    assert engine.totals()["completions"] == 1


def test_failed_completion_counts_across_runs_and_is_not_retried():
    session = Session()
    engine = runtime(session)
    session.fail_provider = True
    with pytest.raises(RuntimeError, match="synthetic native"):
        engine.complete([], "extraction", "source")
    assert session.tables["ATTEMPTS"][0]["STATUS"] == "failed"
    engine.release()
    later = runtime(session)
    with pytest.raises(native.ControlError, match="automatic retries"):
        later.complete([], "extraction", "source")
    assert later.totals()["completions"] == 1
    assert session.provider_calls == 1


def test_successful_cache_reuses_raw_with_inference_disabled():
    session = Session()
    first = runtime(session)
    value = first.complete([], "extraction", "same")
    first.release()
    again = native.Runtime(session, dict(config(), enable_model_calls=False))
    again.acquire()
    assert again.complete([], "extraction", "same") == value
    assert again.cache_hits == 1
    assert session.provider_calls == 1
    with pytest.raises(native.ControlError, match="disabled"):
        again.complete([], "extraction", "changed")


def test_changed_content_is_a_new_cache_key():
    engine = runtime()
    engine.complete(["before"], "extraction", "same")
    engine.complete(["after"], "extraction", "same")
    assert engine.session.session.provider_calls == 2


def test_cumulative_allowance_includes_failed_and_other_client_attempts():
    session = Session()
    engine = runtime(session, limits={"max_generation_calls": 1})
    session.fail_provider = True
    with pytest.raises(RuntimeError):
        engine.complete([], "extraction", "first")
    engine.release()
    later_config = config(limits={"max_generation_calls": 1})
    later_config["client_id"] = "another-client"
    later = native.Runtime(session, later_config)
    later.acquire()
    with pytest.raises(native.ControlError, match="allowance exhausted"):
        later.complete([], "extraction", "second")
    assert session.provider_calls == 1


def test_embeddings_reserve_every_input_reuse_cache_and_bound_cumulative_inputs():
    engine = runtime(limits={"max_embedding_inputs": 2})
    assert engine.embed(["a", "b"], "batch") == [[0.25, 0.75], [0.25, 0.75]]
    assert engine.embed(["a", "b"], "batch") == [[0.25, 0.75], [0.25, 0.75]]
    assert engine.totals()["embedding_inputs"] == 2
    assert engine.session.session.provider_calls == 2
    with pytest.raises(native.ControlError, match="allowance exhausted"):
        engine.embed(["c"], "new")
    assert engine.session.session.provider_calls == 2


def test_singleton_lease_and_outer_transaction_fail_closed():
    session = Session()
    first = runtime(session)
    with pytest.raises(native.ControlError, match="owns the lease"):
        runtime(session)
    first.release()
    session.in_transaction = True
    with pytest.raises(native.ControlError, match="outside a caller transaction"):
        runtime(session)


def test_full_context_limit_fails_before_reservation_without_truncation():
    engine = runtime(limits={"max_input_chars": 20})
    with pytest.raises(native.ControlError, match="nothing was sliced"):
        engine.complete(["a" * 21], "extraction", "long")
    assert not engine.attempts
    assert engine.session.session.provider_calls == 0


def test_unknown_limits_and_budget_expansion_are_rejected():
    with pytest.raises(ValueError, match="Unknown limit"):
        config(limits={"max_generation_attempts": 12})
    with pytest.raises(ValueError, match="max_generation_calls"):
        config(limits={"max_generation_calls": native.HARD_LIMITS["max_generation_calls"] + 1})
    with pytest.raises(ValueError, match="concurrency"):
        config(limits={"concurrency": 2})


def test_sql_bound_prevents_provider_without_reservation_and_preserves_closeout():
    engine = runtime(limits={"max_sql_statements": 40})
    engine.session.statements = 20
    with pytest.raises(native.ControlError, match="Insufficient SQL"):
        engine.complete([], "extraction", "no-room")
    assert not engine.attempts
    assert engine.session.session.provider_calls == 0
    engine.session.closing = True
    engine.release()


def test_run_failure_is_persisted_and_lease_released(monkeypatch):
    session = Session()
    monkeypatch.setattr(native.Runtime, "process", lambda self: (_ for _ in ()).throw(ValueError("visible processing failure")))
    result = native.run(session, json.dumps(config()))
    assert result["status"] == "failed"
    assert "visible processing failure" in result["error"]
    assert session.tables["RUNS"][0]["STATUS"] == "failed"
    assert session.lock_owner is None


def test_run_id_is_immutable(monkeypatch):
    session = Session()
    cfg = config(run_id="fixed-run")
    monkeypatch.setattr(native.Runtime, "process", lambda self: {"status": "complete"})
    assert native.run(session, json.dumps(cfg))["status"] == "complete"
    assert native.run(session, json.dumps(cfg))["status"] == "failed"
    assert len(session.tables["RUNS"]) == 1
    assert session.tables["RUNS"][0]["STATUS"] == "complete"


def test_native_load_retains_raw_and_mapping_without_normalization_or_model():
    session = Session()
    fixture = Path(__file__).resolve().parents[2] / "fixtures/engine/synthetic_sources.json"
    mapping = Path(__file__).resolve().parents[2] / "configs/engine/synthetic_mapping.json"
    records = json.loads(fixture.read_text())
    cfg = config(operation="load", raw_records=records, mapping=json.loads(mapping.read_text()))
    result = native.run(session, json.dumps(cfg))
    assert result["status"] == "complete"
    assert result["loaded_raw_records"] == 6
    assert session.tables["INPUT_RAW"][0]["PAYLOAD"]["record"] == records[0]
    assert result["accounting"]["input_event_count"] == 25
    assert not session.tables["INPUT_SOURCES"]
    assert session.provider_calls == 0


def warehouse_input():
    root = Path(__file__).resolve().parents[2]
    mapping = json.loads((root / "configs/engine/synthetic_mapping.json").read_text())
    record = {"record_type": "typed_chat", "record_id": "raw-1", "conversation_id": "source-1",
              "messages": [{"id": "message-1", "position": 0, "body": "Exact source wording.",
                            "role": "customer", "speaker": "customer-1", "sent_at": None}]}
    return record, mapping


def load_warehouse(session, **overrides):
    _, mapping = warehouse_input()
    return native.run(session, json.dumps(config(operation="load", source_relation="DEMO_DB.ENGINE_TEST.ENGINE_RAW_RECORDS",
                                                 mapping=mapping, **overrides)))


def test_table_load_reads_bound_selection_inside_native_and_preserves_unknown_time():
    record, mapping = warehouse_input()
    hostile_id = "source-1'); DROP SCHEMA X; --"
    record["conversation_id"] = hostile_id
    session = Session()
    session.source_relation_rows = [{"SOURCE_ID": hostile_id, "RAW_RECORD": json.dumps(record)}]
    result = load_warehouse(session, source_ids=[hostile_id])
    assert result["status"] == "complete"
    assert result["loaded_raw_records"] == 1
    assert result["accounting"]["input_event_count"] == 1
    query, params = next((sql, params) for sql, params in session.log if sql.startswith("SELECT SOURCE_ID, RAW_RECORD"))
    assert query == "SELECT SOURCE_ID, RAW_RECORD FROM DEMO_DB.ENGINE_TEST.ENGINE_RAW_RECORDS WHERE SOURCE_ID IN (?) ORDER BY SOURCE_ID LIMIT 7"
    assert params == [hostile_id] and hostile_id not in query
    retained = session.tables["INPUT_RAW"][0]["PAYLOAD"]
    assert {key: retained[key] for key in ("record", "mapping")} == {"record": record, "mapping": mapping}
    assert retained["version_set"]["storage"] == "engine-storage-v4"
    from snowflake_engine import mapping as mapping_module
    event = mapping_module.normalize([retained["record"]], mapping, "synthetic")["sources"][0]["events"][0]
    assert event["timestamp"] is None and event["timing_status"] == "unknown"
    assert "RAW_RECORD" not in json.dumps(result)
    assert session.provider_calls == 0


@pytest.mark.parametrize("relation", ["ENGINE_RAW_RECORDS", "DEMO_DB.ENGINE_TEST.RAW.EXTRA", "OTHER.ENGINE_TEST.RAW",
                                      "DEMO_DB.OTHER.RAW", "DEMO_DB.ENGINE_TEST.RAW;DROP", 'DEMO_DB.ENGINE_TEST."RAW"',
                                      "DEMO_DB.ENGINE_TEST.RAW--", "DEMO_DB.ENGINE_TEST.RAW\n"])
def test_table_relation_rejects_unsafe_or_outside_namespace_before_sql(relation):
    session = Session()
    with pytest.raises(ValueError):
        native.run(session, json.dumps(config(operation="load", source_relation=relation)))
    assert session.log == []


@pytest.mark.parametrize("change, expected", [
    ("over_cap", "source allowance"), ("duplicate_id", "Duplicate source relation"),
    ("duplicate_record", "Duplicate source relation"), ("null_id", "SOURCE_ID"),
    ("null_record", "RAW_RECORD"), ("bad_columns", "SOURCE_ID"),
    ("mapped_id_mismatch", "Mapped source IDs"), ("missing_selection", "not all found")])
def test_table_load_rejects_invalid_boundary_before_writing_raw(change, expected):
    record, _ = warehouse_input()
    session = Session()
    rows = [{"SOURCE_ID": "source-1", "RAW_RECORD": record}]
    overrides = {}
    if change == "over_cap":
        rows *= 7
    elif change == "duplicate_id":
        other = copy.deepcopy(record)
        other["messages"][0]["body"] = "Different source wording."
        rows.append({"SOURCE_ID": "source-1", "RAW_RECORD": other})
    elif change == "duplicate_record":
        rows.append({"SOURCE_ID": "source-2", "RAW_RECORD": record})
    elif change == "null_id":
        rows[0]["SOURCE_ID"] = None
    elif change == "null_record":
        rows[0]["RAW_RECORD"] = None
    elif change == "bad_columns":
        rows = [{"WRONG_ID": "source-1", "WRONG_RECORD": record}]
    elif change == "mapped_id_mismatch":
        rows[0]["SOURCE_ID"] = "different-source"
    elif change == "missing_selection":
        overrides["source_ids"] = ["missing"]
    session.source_relation_rows = rows
    result = load_warehouse(session, **overrides)
    assert result["status"] == "failed" and expected in result["error"]
    assert not session.tables["INPUT_RAW"]
    assert session.provider_calls == 0 and session.lock_owner is None


def test_table_load_retains_mapping_exclusions_in_existing_accounting():
    record, _ = warehouse_input()
    record["record_type"] = "unmapped"
    session = Session()
    session.source_relation_rows = [{"SOURCE_ID": "source-1", "RAW_RECORD": record}]
    result = load_warehouse(session)
    assert result["status"] == "complete"
    assert result["accounting"]["input_record_count"] == 1
    assert result["accounting"]["excluded_record_count"] == 1
    assert result["accounting"]["records"][0]["issue_codes"] == ["unsupported_record_type"]
    assert session.tables["INPUT_RAW"][0]["PAYLOAD"]["record"] == record


def test_table_load_checks_mapping_and_complete_event_allowance_before_writes():
    record, mapping = warehouse_input()
    session = Session()
    session.source_relation_rows = [{"SOURCE_ID": "source-1", "RAW_RECORD": record}]
    cfg = config(operation="load", source_relation="DEMO_DB.ENGINE_TEST.ENGINE_RAW_RECORDS", mapping={})
    result = native.run(session, json.dumps(cfg))
    assert result["status"] == "failed" and "mapping.types" in result["error"]
    assert not session.tables["INPUT_RAW"]
    record["messages"].append({**record["messages"][0], "id": "message-2", "position": 1})
    result = load_warehouse(session, limits={"max_events": 1}, run_id="over-events")
    assert result["status"] == "failed" and "event allowance" in result["error"]
    assert not session.tables["INPUT_RAW"]


def test_table_config_rejects_ambiguous_inputs_and_bad_selection():
    for overrides in ({"raw_records": []}, {"operation": "run"}, {"source_ids": ["x", "x"]},
                      {"source_ids": [None]}, {"source_ids": [" "]}, {"source_ids": list("1234567")}):
        with pytest.raises((ValueError, native.ControlError)):
            config(**{ "operation": "load", "source_relation": "DEMO_DB.ENGINE_TEST.ENGINE_RAW_RECORDS", **overrides})
    with pytest.raises(ValueError, match="requires source_relation"):
        config(source_ids=["source-1"])


def test_retained_reprocessing_reuses_exact_raw_across_code_change_without_recounting():
    session = Session()
    first = runtime(session)
    raw = first.complete(["unchanged"], "extraction", "source-fullhash")
    first.release()
    later = native.Runtime(session, dict(config(), enable_model_calls=False, reprocess_retained=True))
    later.versions["code"] = "a-diagnosed-deterministic-fix"
    later.acquire()
    assert later.complete(["unchanged"], "extraction", "source-fullhash") == raw
    assert session.provider_calls == 1
    assert len(session.tables["ATTEMPTS"]) == 1
    assert later.usage()["retained_reprocessing"][0]["event"] == "retained_raw_reprocessing"
    with pytest.raises(native.ControlError, match="disabled"):
        later.complete(["changed prompt"], "extraction", "source-fullhash")


@pytest.mark.parametrize("intake", ["json", "table"])
def test_seed_end_to_end_persists_draft_and_unchanged_rerun_makes_zero_inference(monkeypatch, intake):
    from snowflake_engine import extraction
    session = Session()
    root = Path(__file__).resolve().parents[2]
    records = json.loads((root / "fixtures/engine/synthetic_sources.json").read_text())
    mapping_config = json.loads((root / "configs/engine/synthetic_mapping.json").read_text())

    def extract(source, complete, max_input_chars, output_style="standard"):
        # A controlled test double for extraction. Native provider calls are still
        # subject to the real reservation/cache paths; this is no semantic smoke.
        complete([{"role": "user", "content": json.dumps(source, sort_keys=True)}], "extraction", native._digest(source))
        event = source["events"][-1]
        citation = {key: event[key] for key in ("event_id", "role", "speaker", "authority")}
        citation["quote"] = event["text"]
        component = {"object_id": "scenario-" + source["source_id"], "source_id": source["source_id"], "kind": "scenario", "validation_status": "valid", "issues": [], "payload": {"id": "scenario-1", "summary": "Synthetic orchestration check", "needs": [{"id": "need-1", "kind": "primary_need", "summary": "Retained event", "status": "reported", "related_ids": [], "evidence": [citation]}], "unknowns": []}}
        return {"components": [component], "issues": [], "raw_response": "retained in attempt", "versions": {}}

    monkeypatch.setattr(extraction, "extract", extract)
    first_cfg = config(raw_records=records, mapping=mapping_config, run_id="first-seed-run")
    if intake == "table":
        from snowflake_engine import mapping
        accounting = mapping.normalize(records, mapping_config, "synthetic")["accounting"]["records"]
        session.source_relation_rows = [{"SOURCE_ID": row["source_id"] or f"excluded-{index}", "RAW_RECORD": record}
                                        for index, (record, row) in enumerate(zip(records, accounting))]
        loaded = native.run(session, json.dumps(config(operation="load", source_relation="DEMO_DB.ENGINE_TEST.ENGINE_RAW_RECORDS",
                                                       mapping=mapping_config, run_id="table-seed-load")))
        assert loaded["status"] == "complete" and loaded["loaded_raw_records"] == 6
        first_cfg = config(run_id="first-seed-run")
    first = native.run(session, json.dumps(first_cfg))
    assert first["status"] == "partial", first.get("error", first)  # deliberately excluded orphan remains visible
    assert first["release_id"]
    assert len(first["coverage"]) == 5
    assert len(session.tables["RELEASES"]) == 1
    assert first["accounting"]["input_event_count"] == 25
    assert len(session.tables["COMPONENTS"]) == 5
    calls = session.provider_calls
    second_cfg = dict(first_cfg, run_id="second-seed-run", enable_model_calls=False)
    second = native.run(session, json.dumps(second_cfg))
    assert second["status"] == "partial"
    assert second["release_id"]
    assert session.provider_calls == calls
    assert second["usage"]["cache_hits"] == 10  # five source completions + five embeddings
    assert len(session.tables["RELEASES"]) == 2
    assert len(session.tables["COMPONENTS"]) == 10
    assert session.lock_owner is None
    changed = copy.deepcopy(records)
    changed[0]["description"] += " A changed source-context fact."
    third_cfg = dict(first_cfg, run_id="changed-seed-run", raw_records=changed, mapping=mapping_config)
    third = native.run(session, json.dumps(third_cfg))
    assert third["status"] == "partial"
    assert third["release_id"]
    assert session.provider_calls == calls + 1
    extraction_attempts = [r for r in session.tables["ATTEMPTS"] if r["OPERATION"] == "extraction"]
    assert len(extraction_attempts) == 6  # five originals plus just the changed source


def test_requested_budget_increase_is_rejected_by_installed_metadata():
    session = Session()
    cfg = config(limits={"max_sources": 7, "max_generation_calls": 13})
    with pytest.raises(native.ControlError, match="immutable installed budget"):
        native.Runtime(session, cfg).acquire()
    assert session.provider_calls == 0
    assert session.lock_owner is None


def test_separately_reviewed_installation_budget_can_exceed_smoke_defaults():
    session = Session()
    session.budget.update(max_sources=20, max_events=200, max_generation_calls=40)
    cfg = config(limits={"max_sources": 20, "max_events": 200, "max_generation_calls": 40})
    engine = native.Runtime(session, cfg)
    engine.acquire()
    assert engine.installed_budget["max_sources"] == 20
    engine.release()


def test_missing_or_malformed_installed_budget_fails_closed():
    session = Session()
    session.budget = {"max_sources": 6}
    with pytest.raises(native.ControlError, match="missing or has unrecognized"):
        runtime(session)
    assert session.provider_calls == 0


def test_transaction_rolls_back_using_closeout_allowance_when_work_cap_is_reached():
    engine = runtime(limits={"max_sql_statements": 40})
    engine.session.statements = engine.session.maximum - engine.session.reserve - 1
    with pytest.raises(native.ControlError, match="SQL statement allowance"):
        engine.transaction(lambda: engine.sql("SELECT would_exceed_work_allowance"))
    assert engine.session.session.in_transaction is False
    assert engine.session.session.log[-1][0] == "ROLLBACK"
    assert engine.session.statements <= engine.session.maximum


def test_embedding_model_is_literal_and_evidence_is_bound():
    engine = runtime()
    evidence = "Customer text with 'quotes' and SQL-looking content; SELECT private_data"
    engine.embed([evidence], "literal-regression")
    calls = [(sql, args) for sql, args in engine.session.session.log if "AI_EMBED(" in sql]
    assert calls == [("SELECT TO_ARRAY(AI_EMBED('snowflake-arctic-embed-l-v2.0', ?)) AS RESULT", [evidence])]
    assert not engine.session.session.in_transaction


def test_unrecognized_embedding_literal_is_rejected_before_any_sql():
    with pytest.raises(ValueError, match="supported AI_EMBED"):
        config(embedding_model="model'); DROP TABLE ATTEMPTS; --")


def test_transaction_helper_reuses_scope_without_nested_begin():
    engine = runtime()
    before = sum(sql == "BEGIN TRANSACTION" for sql, _ in engine.session.session.log)
    engine.transaction(lambda: engine.transaction(lambda: engine.sql("SELECT nested_read")))
    after = sum(sql == "BEGIN TRANSACTION" for sql, _ in engine.session.session.log)
    assert after - before == 1
    assert not engine.session.session.in_transaction


def test_autocommit_false_provider_failure_durably_closes_attempt_without_open_scope():
    session = Session()
    engine = runtime(session)
    session.fail_provider = True
    with pytest.raises(RuntimeError, match="synthetic native"):
        engine.embed(["complete evidence"], "failed-literal-route")
    assert session.tables["ATTEMPTS"][0]["STATUS"] == "failed"
    assert session.tables["ATTEMPTS"][0]["PAYLOAD"]["query_id"] == "native-query-1"
    assert not session.in_transaction
    engine.release()
    engine.close_scope()
    assert session.lock_owner is None


def test_run_finally_completes_all_scopes_with_inherited_autocommit_false(monkeypatch):
    session = Session()

    def process(engine):
        engine.sql("SELECT processing_read")
        engine.complete(["full source"], "extraction", "stable-source")
        engine.insert("INPUT_ISSUES", {"ISSUE_ID": "visible", "RUN_ID": engine.config["run_id"], "CLIENT_ID": "synthetic", "SOURCE_ID": None, "REASON": "synthetic retained issue", "PAYLOAD": {}})
        return {"status": "partial"}

    monkeypatch.setattr(native.Runtime, "process", process)
    result = native.run(session, json.dumps(config()))
    assert result["status"] == "partial"
    assert session.tables["RUNS"][0]["STATUS"] == "partial"
    assert session.tables["ATTEMPTS"][0]["STATUS"] == "succeeded"
    assert session.tables["INPUT_ISSUES"][0]["ISSUE_ID"] == "visible"
    assert session.lock_owner is None
    assert not session.in_transaction
    assert session.log[-1][0] == "SELECT CURRENT_TRANSACTION() AS TRANSACTION_ID"


def test_final_scope_guard_rolls_back_unexpected_owned_residue():
    session = Session()
    engine = runtime(session)
    engine.sql("BEGIN TRANSACTION")
    # Simulate a control/helper creating an unreported transaction. The final
    # guard must close it without undoing the prior committed reservation/lease.
    with pytest.raises(native.ControlError, match="rolled back at closeout"):
        engine.close_scope()
    assert not session.in_transaction
    assert session.lock_owner == engine.config["run_id"]
    engine.release()


def test_operator_recovery_plan_is_exact_bound_and_never_resets_attempt_ledger():
    plan = native.operator_recovery_plan(config(), "engine-run-01", "failed-query-id", "read-only-query-history-evidence.json")
    assert plan["executed"] is False
    assert plan["attempt_ledger_mutations"] == 0
    assert plan["rollback_on_any_mismatch"] is True
    assert plan["steps"][0]["sql"] == "BEGIN TRANSACTION"
    assert plan["steps"][-1]["sql"] == "COMMIT"
    mutations = [s for s in plan["steps"] if s["sql"].startswith("UPDATE")]
    assert len(mutations) == 2
    assert all(s["expect_affected_rows"] == 1 for s in mutations)
    assert all("ATTEMPTS" not in s["sql"] for s in mutations)
    assert "WHERE RUN_ID=? AND CLIENT_ID=? AND STATUS='running'" in mutations[0]["sql"]
    assert "WHERE LOCK_KEY='engine' AND OWNER_RUN_ID=?" in mutations[1]["sql"]
    assert mutations[0]["params"][1:] == ["engine-run-01", "synthetic"]
    assert mutations[1]["params"][-1] == "engine-run-01"
    with pytest.raises(ValueError, match="inactivity_evidence"):
        native.operator_recovery_plan(config(), "engine-run-01", "failed-query-id", "")


def test_compact_selection_uses_only_last_completion_and_preserves_standard_cache(monkeypatch):
    from snowflake_engine import aggregation, extraction, mapping
    root = Path(__file__).resolve().parents[2]
    raw = json.loads((root / "fixtures/engine/synthetic_sources.json").read_text())
    mapping_config = json.loads((root / "configs/engine/synthetic_mapping.json").read_text())
    sources = mapping.normalize(raw, mapping_config, "synthetic")["sources"]
    session = Session()
    # Seed requests under the exact candidate mapping/settings version set.
    first = runtime(session, mapping=mapping_config, compact_source_ids=[sources[0]["source_id"]],
                    aggregation={"relationship_requests": 0})
    # Eleven already-reserved completions, including five standard-source cache
    # entries. The canned provider response is deliberately not a valid inventory;
    # this test checks routing/cache/accounting rather than semantic extraction.
    for source in sources:
        extraction.extract(source, first.complete)
    for index in range(6):
        first.complete(["historical request " + str(index)], "extraction", "historical-" + str(index))
    assert first.totals()["completions"] == 11
    first.release()
    selected = sources[0]["source_id"]
    seen = []
    original = extraction.extract

    def record_style(source, complete, max_input_chars, output_style="standard"):
        seen.append((source["source_id"], output_style))
        return original(source, complete, max_input_chars=max_input_chars, output_style=output_style)

    monkeypatch.setattr(extraction, "extract", record_style)
    cfg = config(raw_records=raw, mapping=mapping_config, compact_source_ids=[selected], reprocess_retained=True, aggregation={"relationship_requests": 0})
    result = native.run(session, json.dumps(cfg))
    assert dict(seen) == {source["source_id"]: "compact" if source["source_id"] == selected else "standard" for source in sources}
    assert session.provider_calls == 12
    assert result["usage"]["completions"] == 12
    assert result["usage"]["cache_hits"] == 4
    assert {item["source_id"]: item["output_style"] for item in result["usage"]["extraction_output_styles"]} == dict(seen)
    assert session.tables["RUNS"][-1]["CONFIG"]["compact_source_ids"] == [selected]
    before_rerun = session.provider_calls
    rerun = native.run(session, json.dumps(dict(cfg, run_id="compact-cache-only-rerun", enable_model_calls=False)))
    assert session.provider_calls == before_rerun
    assert rerun["usage"]["cache_hits"] == 5
    assert rerun["usage"]["completions"] == 12


@pytest.mark.parametrize("compact_ids", [None, "source", [""], ["   "], ["source", "source"], [1]])
def test_compact_source_selection_rejects_malformed_ids(compact_ids):
    with pytest.raises(ValueError, match="compact_source_ids"):
        config(compact_source_ids=compact_ids)


def test_compact_source_selection_rejects_unknown_canonical_source_before_inference():
    root = Path(__file__).resolve().parents[2]
    session = Session()
    cfg = config(raw_records=json.loads((root / "fixtures/engine/synthetic_sources.json").read_text()), mapping=json.loads((root / "configs/engine/synthetic_mapping.json").read_text()), compact_source_ids=["unknown-source"])
    result = native.run(session, json.dumps(cfg))
    assert result["status"] == "failed"
    assert "unknown IDs: unknown-source" in result["error"]
    assert session.provider_calls == 0
    assert session.tables["ATTEMPTS"] == []
    assert session.lock_owner is None


def test_model_calls_default_to_disabled_for_compact_and_standard():
    cfg = config()
    cfg.pop("enable_model_calls")
    assert native.validate_config(cfg)["enable_model_calls"] is False
    with pytest.raises(ValueError, match="explicit boolean"):
        config(enable_model_calls="true")
