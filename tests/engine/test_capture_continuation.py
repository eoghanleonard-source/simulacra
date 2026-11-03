"""Offline checks for explicit reuse of paid capture responses across a framing fix."""
from copy import deepcopy

import pytest

from snowflake_engine import native
from test_native import config, storage
from test_native_capture import CaptureSession


def saved_capture():
    session = CaptureSession(mode="live")
    session.budget.update(max_generation_calls=60, max_generation_ai_credits=4.0)
    prior = config(operation="capture_evidence", execution_mode="live", model=native.CAPTURE_MODEL,
        run_id="failed-capture", input_run_id="frozen-input", persist_request_messages=True,
        limits=dict(session.budget))
    old = native.Runtime(session, prior)
    session.tables["RUNS"] = [{"RUN_ID": prior["run_id"], "CLIENT_ID": prior["client_id"],
        "STATUS": "failed", "CONFIG": deepcopy(prior), "PAYLOAD": {"error": "isolated response framing"}}]
    requests = []
    for stage in ("worker", "review", "reconcile"):
        operation = "capture_evidence_" + stage
        messages = [{"role": "user", "content": "complete synthetic " + stage + " request"}]
        cap = 8192 if stage == "worker" else 16384
        requests.append((messages, operation, cap))
        session.tables["ATTEMPTS"].append({"ATTEMPT_ID": "saved-" + stage,
            "RUN_ID": prior["run_id"], "CLIENT_ID": prior["client_id"], "OPERATION": operation,
            "REQUEST_KEY": "old-controller-" + stage, "STATUS": "succeeded", "PAYLOAD": {
                "versions": deepcopy(old.versions), "execution_mode": "live",
                "profile_version": native.CAPTURE_PROFILE, "capture_contract": "capture-evidence-v1",
                "model": native.CAPTURE_MODEL, "max_output_tokens": cap,
                "source_id": "source-1", "source_hash": "frozen-source-hash",
                "request_messages": deepcopy(messages), "request_messages_sha256": native._digest(messages),
                "raw_response": "saved raw " + stage, "query_id": "paid-query-" + stage,
                "usage_status": "returned", "usage": {"prompt_tokens": 100, "completion_tokens": 20}}})
    current = native.validate_config(dict(prior, run_id="continued-capture",
        reuse_capture_run_id=prior["run_id"], capture_settings={"response_framing": "single-fence-v2"}))
    return session, current, requests


def acquire(session, current):
    runtime = native.Runtime(session, current)
    runtime.acquire()
    runtime.capture_source_id, runtime.capture_source_hash = "source-1", "frozen-source-hash"
    return runtime


def test_exact_saved_requests_reuse_without_changing_paid_history_or_cumulative_budget():
    session, current, requests = saved_capture()
    history = deepcopy(session.tables)
    runtime = acquire(session, current)
    for messages, operation, cap in requests:
        assert runtime.complete(messages, operation, "current-key", max_output_tokens=cap) == "saved raw " + operation.removeprefix("capture_evidence_")
    assert session.provider_calls == 0
    assert session.tables == history
    assert runtime.totals()["completions"] == 3
    assert runtime.generation_credits()["estimated_ai_credits"] == pytest.approx(0.00108)
    assert runtime.cache_hits == 3
    events = runtime.usage()["retained_reprocessing"]
    assert {event["attempt_id"] for event in events} == {"saved-worker", "saved-review", "saved-reconcile"}
    assert all(event["source_run_id"] == "failed-capture" and event["response_framing"] == "single-fence-v2" for event in events)
    assert runtime.versions["processing"] == {"capture_response_framing": "single-fence-v2"}
    # The previous three calls still consume the installed/schema-wide allowance.
    runtime.config["limits"]["max_generation_calls"] = 3
    runtime.capture_source_id = "new-source"
    with pytest.raises(native.ControlError, match="Cumulative completion allowance"):
        runtime.complete([], "capture_evidence_worker", "new-key", max_output_tokens=8192)
    assert session.provider_calls == 0 and session.tables == history


@pytest.mark.parametrize("change", ["messages", "model", "cap", "source_hash", "temperature", "profile", "recorded", "failed", "unknown_usage"])
def test_saved_request_mismatch_fails_before_any_duplicate_call(change):
    session, current, requests = saved_capture()
    payload = session.tables["ATTEMPTS"][0]["PAYLOAD"]
    if change == "messages":
        payload["request_messages"][0]["content"] += " changed"
    elif change == "model":
        payload["model"] = "another-model"
    elif change == "cap":
        payload["max_output_tokens"] = 4096
    elif change == "source_hash":
        payload["source_hash"] = "another-source"
    elif change == "temperature":
        payload["versions"]["model_settings"]["temperature"] = 1
    elif change == "profile":
        payload["profile_version"] = "another-profile"
    elif change == "recorded":
        payload["execution_mode"] = "recorded_smoke"
    elif change == "failed":
        session.tables["ATTEMPTS"][0]["STATUS"] = "failed"
    elif change == "unknown_usage":
        payload["usage"] = None
    history = deepcopy(session.tables)
    runtime = acquire(session, current)
    messages, operation, cap = requests[0]
    with pytest.raises(native.ControlError, match="Capture continuation|Saved capture response"):
        runtime.complete(messages, operation, "current-key", max_output_tokens=cap)
    assert session.provider_calls == 0 and session.tables == history


@pytest.mark.parametrize("change", ["client", "run", "status", "input", "model", "attempt_client", "attempt_run"])
def test_prior_run_identity_and_frozen_input_are_required(change):
    session, current, _ = saved_capture()
    run = session.tables["RUNS"][0]
    if change == "client":
        current["client_id"] = "another-client"
    elif change == "run":
        current["reuse_capture_run_id"] = "another-run"
    elif change == "status":
        run["STATUS"] = "complete"
    elif change == "input":
        run["CONFIG"]["input_run_id"] = "another-input"
    elif change == "model":
        run["CONFIG"]["model"] = "another-model"
    elif change == "attempt_client":
        session.tables["ATTEMPTS"][0]["CLIENT_ID"] = "another-client"
    elif change == "attempt_run":
        for row in session.tables["ATTEMPTS"]:
            row["RUN_ID"] = "another-run"
    with pytest.raises(native.ControlError, match="Capture continuation"):
        acquire(session, current)
    assert session.provider_calls == 0


@pytest.mark.parametrize("changes", [
    {"capture_settings": {}}, {"capture_settings": {"response_framing": "unknown"}},
    {"reuse_capture_run_id": 4}, {"reuse_capture_run_id": "continued-capture"},
    {"operation": "run"}, {"execution_mode": "recorded_smoke"},
])
def test_continuation_requires_explicit_live_framing_opt_in(changes):
    _, current, _ = saved_capture()
    with pytest.raises(ValueError, match="reuse_capture_run_id"):
        native.validate_config(dict(current, **changes))


def test_default_cache_does_not_bridge_changed_controller_identity():
    session, current, requests = saved_capture()
    current.pop("reuse_capture_run_id")
    current.pop("capture_settings")
    runtime = acquire(session, current)
    messages, operation, cap = requests[0]
    # Without opt-in, the old saved messages cannot bypass the strict current key.
    assert runtime.cached(operation, runtime.request_key(operation, "current-key", messages, max_output_tokens=cap)) is None
    assert runtime.continued_capture_response(operation, messages, cap) is None
    assert runtime.cache_hits == 0 and not runtime.processing_events
    assert "processing" not in runtime.versions


def test_current_strict_cache_is_preferred_to_bridge():
    session, current, requests = saved_capture()
    runtime = acquire(session, current)
    messages, operation, cap = requests[0]
    runtime.attempts[0]["REQUEST_KEY"] = runtime.request_key(operation, "current-key", messages, max_output_tokens=cap)
    runtime.attempts[0]["PAYLOAD"]["source_hash"] = "bridge-would-reject"
    assert runtime.complete(messages, operation, "current-key", max_output_tokens=cap) == "saved raw worker"
    assert runtime.cache_hits == 1 and not runtime.processing_events and session.provider_calls == 0
