"""Synthetic integrity tests; semantic grouping quality needs separate evidence."""
from copy import deepcopy
import json

import pytest

from snowflake_engine.practice import build_programme, validate_programme
from snowflake_engine import versioning
from snowflake_engine.contracts import canonical_json


def scenario(oid, source=None, count=2):
    return {"object_id": oid, "source_id": source or "source-" + oid, "kind": "scenario",
            "validation_status": "valid", "payload": {"id": oid, "summary": "Source circumstance " + oid,
            "unknowns": ["Outcome unconfirmed"], "needs": [
                {"id": "n" + str(i), "kind": "primary_need" if i == 0 else "secondary_need",
                 "summary": "Reported request " + str(i), "status": "pending", "related_ids": [],
                 "evidence": [{"event_id": "event-" + str(i), "quote": "Original words " + str(i),
                               "speaker": "customer", "role": "customer", "authority": "customer_statement"}]}
                for i in range(count)]}}


def ref(oid, nid="n0"):
    return {"object_id": oid, "need_id": nid}


def pair(left, right=None):
    return {"situation_ref": ref(left), "variant_ref": ref(right or left, "n0" if right else "n1"),
            "consequential_need_refs": []}


def group(members, candidates=None, label="Related source situations"):
    return {"label": label, "member_ids": members,
            "candidates": candidates if candidates is not None else [pair(members[0])]}


class Planner:
    def __init__(self, groups=None, unassigned=None, raw=None):
        self.groups, self.unassigned, self.raw = groups or [], unassigned or [], raw
        self.calls = []

    def __call__(self, messages, operation, request_key):
        self.calls.append((messages, operation, request_key))
        return self.raw if self.raw is not None else json.dumps({"groups": self.groups, "unassigned_object_ids": self.unassigned})


def test_concrete_pair_copies_every_original_fact_and_never_invents_guidance():
    components = [scenario("a"), scenario("b")]
    original = deepcopy(components)
    planner = Planner([group(["a", "b"], [pair("a", "b")], label="Refunds always approved")])
    result = build_programme(components, {}, planner)
    assert len(result["assignments"]) == 1
    row = result["assignments"][0]
    assert row["situation"]["payload"] == components[0]["payload"]
    assert row["variant"]["payload"] == components[1]["payload"]
    assert row["situation"]["context_need_ids"] == ["n1"]
    assert row["guidance_status"] == "requires_client_guidance"
    assert row["assessment_eligible"] is False
    assert row["client_approved_resolution_criteria"] is None
    assert row["semantic_review_status"] == "unreviewed"
    assert result["practice_groups"][0]["label_status"] == "advisory_model_proposal"
    assert result["practice_groups"][0]["equivalence_status"] == "not_asserted"
    assert validate_programme(result, components) == []
    assert components == original


@pytest.mark.parametrize("mutation", ["unknown_member", "missing_member", "duplicate_member", "unknown_need", "same_variant", "absent_variant", "invented_guidance"])
def test_unknown_ids_missing_or_lost_variants_and_new_prose_cannot_become_exercises(mutation):
    proposed = group(["a", "b"], [pair("a", "b")])
    if mutation == "unknown_member":
        proposed["member_ids"][1] = "missing-object"
    elif mutation == "missing_member":
        proposed["member_ids"] = ["a"]
    elif mutation == "duplicate_member":
        proposed["member_ids"].append("a")
    elif mutation == "unknown_need":
        proposed["candidates"][0]["variant_ref"]["need_id"] = "invented-need"
    elif mutation == "same_variant":
        proposed["candidates"][0]["variant_ref"] = ref("a")
    elif mutation == "absent_variant":
        del proposed["candidates"][0]["variant_ref"]
    else:
        proposed["candidates"][0]["approved_handling"] = "Refund every order"
    planner = Planner([proposed])
    result = build_programme([scenario("a"), scenario("b")], {}, planner)
    assert not result["assignments"]
    assert len(result["object_accounting"]) == 2
    assert all(not r["assignment_ids"] for r in result["object_accounting"])
    assert len(planner.calls) == 1
    assert result["issues"]


def test_nontransitive_equivalence_is_not_inferred_from_related_group_membership():
    components = [scenario("a"), scenario("b"), scenario("c")]
    relations = [
        {"relation_id": "ab", "left_object_id": "a", "right_object_id": "b", "relation": "equivalent", "status": "model_supported"},
        {"relation_id": "bc", "left_object_id": "b", "right_object_id": "c", "relation": "equivalent", "status": "model_supported"},
        {"relation_id": "ac", "left_object_id": "a", "right_object_id": "c", "relation": "distinct", "status": "model_supported"}]
    result = build_programme(components, {"relations": relations}, Planner([group(["a", "b", "c"], [pair("a", "b")])]))
    retained = result["practice_groups"][0]
    assert retained["equivalence_status"] == "not_asserted"
    assert [(r["relation_id"], r["relation"]) for r in retained["member_relationships"]] == [("ab", "equivalent"), ("bc", "equivalent"), ("ac", "distinct")]
    assert result["assignments"][0]["situation"]["payload"] == components[0]["payload"]
    assert all(row["component_digest"] for row in result["object_accounting"])


def test_up_to_twenty_by_marginal_coverage_without_forcing_nineteen_groups():
    components = [scenario(f"s{i:02}") for i in range(25)]
    groups = [group([c["object_id"]]) for c in components]
    result = build_programme(components, {}, Planner(groups))
    assert len(result["assignments"]) == 20
    assert len(result["practice_groups"]) == 25
    assert result["selection_summary"]["variant_count"] == 20
    assert sum(r["status"] == "grouped_not_selected" for r in result["object_accounting"]) == 5
    reverse = build_programme(list(reversed(components)), {}, Planner(list(reversed(groups))))
    assert result["assignments"] == reverse["assignments"]
    assert result["practice_groups"] == reverse["practice_groups"]
    assert validate_programme(result, components) == []


def test_caps_are_checked_and_requests_do_not_retry_or_spend_allocation_automatically():
    components = [scenario("a")]
    planner = Planner([group(["a"])])
    result = build_programme(components, {}, planner, {"planner_requests": 4, "max_output_tokens": 2048})
    assert len(planner.calls) == 1
    assert result["planner"]["completion_count"] == 1
    payload = json.loads(planner.calls[0][0][1]["content"])
    assert payload["max_output_tokens"] == 2048
    assert planner.calls[0][1] == "practice_planner"
    for key, value in [("planner_requests", 5), ("max_assignments", 21), ("max_output_tokens", 6145), ("max_assignments", True)]:
        with pytest.raises(ValueError, match=key):
            build_programme(components, {}, planner, {key: value})
    failed_calls = []

    def failed(*args):
        failed_calls.append(args)
        raise RuntimeError("Caller allowance exhausted")

    failed_result = build_programme(components, {}, failed, {"planner_requests": 4})
    assert len(failed_calls) == 1
    assert failed_result["planner"]["status"] == "failed"
    assert not failed_result["assignments"]
    assert failed_result["object_accounting"][0]["status"] == "unassigned"


def test_must_covers_never_reach_model_and_context_is_not_exercised_coverage():
    components = [scenario("a", count=3), scenario("b")]
    candidate = pair("a", "b")
    candidate["consequential_need_refs"] = [ref("a", "n2")]
    planner = Planner([group(["a", "b"], [candidate], label="Broad all-issues topic")])
    must_covers = [{"must_cover_id": "GOLD_SECRET", "need_refs": [ref("a", "n2")]},
                   {"must_cover_id": "exercised", "need_refs": [ref("a")]},
                   {"must_cover_id": "capture-lost", "need_refs": [ref("unknown-object", "critical")] }]
    result = build_programme(components, {}, planner, {"must_covers": must_covers,
                            "references": "DO_NOT_SEND", "frozen_gold": "HELD_OUT"})
    request = json.dumps(planner.calls[0][0])
    assert "GOLD_SECRET" not in request and "DO_NOT_SEND" not in request and "HELD_OUT" not in request
    assert [(r["must_cover_id"], r["status"]) for r in result["must_cover_accounting"]] == [
        ("GOLD_SECRET", "missing"), ("exercised", "exercised"), ("capture-lost", "missing")]
    assert result["selection_summary"]["missing_must_cover_count"] == 2
    assert result["assignments"][0]["consequential_need_refs"] == [ref("a", "n2")]
    assert result["must_cover_accounting"][2]["unavailable_need_refs"] == [ref("unknown-object", "critical")]
    without_gold = build_programme(components, {}, Planner(planner.groups))
    assert without_gold["assignments"] == result["assignments"]
    assert without_gold["planner"]["request_key"] == result["planner"]["request_key"]


def test_every_source_and_object_accounted_including_failed_quarantined_and_persona():
    valid = scenario("a", "same-source")
    quarantine = scenario("q")
    quarantine["validation_status"] = "quarantined"
    persona = {"object_id": "p", "source_id": "same-source", "kind": "persona", "validation_status": "valid", "payload": {"id": "p"}}
    components = [valid, deepcopy(valid), quarantine, persona]
    planner = Planner([group(["a"])])
    result = build_programme(components, {"coverage": [{"source_id": "failed-source", "status": "failed"}]}, planner)
    assert [r["status"] for r in result["object_accounting"]] == ["selected", "duplicate_input", "quarantined_component", "persona_inventory_only"]
    by_source = {r["source_id"]: r for r in result["coverage"]}
    assert set(by_source) == {"same-source", "source-q", "failed-source"}
    assert by_source["same-source"]["valid_component_count"] == 2
    assert by_source["source-q"]["status"] == "quarantined"
    assert by_source["failed-source"]["status"] == "failed"
    assert len(planner.calls) == 1
    payload = json.loads(planner.calls[0][0][1]["content"])
    assert [c["object_id"] for c in payload["components"]] == ["a"]
    assert validate_programme(result, components) == []


def test_no_callback_or_disabled_allocation_never_fabricates_a_variant():
    planner = Planner([group(["a"])])
    for complete, config in [(None, {}), (planner, {"planner_requests": 0}), (planner, {"max_assignments": 0})]:
        result = build_programme([scenario("a")], {}, complete, config)
        assert not result["assignments"] and not result["practice_groups"]
        assert result["object_accounting"][0]["status"] == "unassigned"
    assert not planner.calls


def test_duplicate_pair_reversal_keeps_all_context_and_is_deterministic():
    first = pair("a", "b")
    second = {"situation_ref": ref("b"), "variant_ref": ref("a"), "consequential_need_refs": [ref("b", "n1")]}
    components = [scenario("a"), scenario("b")]
    a = build_programme(components, {}, Planner([group(["a", "b"], [first, second])]))
    b = build_programme(components, {}, Planner([group(["a", "b"], [second, first])]))
    assert a["assignments"] == b["assignments"]
    assert len(a["assignments"]) == 1
    assert a["assignments"][0]["consequential_need_refs"] == [ref("b", "n1")]


def test_validator_rejects_changed_facts_variant_removal_and_expanded_background_credit():
    components = [scenario("a")]
    original = build_programme(components, {}, Planner([group(["a"])]))
    changed = deepcopy(original)
    changed["assignments"][0]["situation"]["payload"]["summary"] = "Invented approved refund"
    assert any("unchanged source" in error for error in validate_programme(changed, components))
    changed = deepcopy(original)
    del changed["assignments"][0]["variant"]
    assert any("variant" in error for error in validate_programme(changed, components))
    changed = deepcopy(original)
    changed["assignments"][0]["exercised_need_refs"].append(ref("a", "background"))
    assert any("credit" in error for error in validate_programme(changed, components))
    changed = deepcopy(original)
    changed["assignments"][0]["assessment_eligible"] = True
    assert any("approved handling" in error for error in validate_programme(changed, components))


def test_native_versions_annotate_stable_logical_identity_and_immutable_revision():
    versions = {key: "v1" for key in versioning.VERSION_KEYS}
    components = [scenario("a")]
    a = build_programme(components, {}, Planner([group(["a"])]), versions=versions)
    for row, kind in [(a, "practice"), (a["practice_groups"][0], "practice_group"), (a["assignments"][0], "practice_assignment")]:
        assert row["version_set"] == versions
        assert row["revision_id"] == versioning.revision_id(kind, row["logical_id"], row, versions)
    changed = dict(versions, selection="v2")
    b = build_programme(components, {}, Planner([group(["a"])]), versions=changed)
    assert a["assignments"][0]["logical_id"] == b["assignments"][0]["logical_id"]
    assert a["assignments"][0]["revision_id"] != b["assignments"][0]["revision_id"]


def test_malformed_planner_response_is_retained_without_retries():
    planner = Planner(raw="Not JSON")
    result = build_programme([scenario("a")], {}, planner)
    assert result["planner"]["raw_response"] == "Not JSON"
    assert result["planner"]["status"] == "failed"
    assert len(planner.calls) == 1
    assert not result["assignments"]


def test_duplicate_identity_conflict_is_not_silently_overwritten():
    original, changed = scenario("a"), scenario("a")
    changed["payload"]["summary"] = "Different source meaning"
    with pytest.raises(ValueError, match="conflicting"):
        build_programme([original, changed], {}, None)


def test_hundred_source_six_hundred_need_inventory_is_sharded_without_lost_facts():
    components = [scenario(f"s{i:03}", count=6) for i in range(100)]
    for component in components:
        for need in component["payload"]["needs"]:
            need["summary"] = "The customer reports an earlier request remains pending and asks whether it can be changed."
            need["assertion"] = {"actor": "customer", "recipient": "agent", "action": "asks about a change",
                                 "scope": "earlier request remains pending", "state": "requested", "certainty": "explicit"}
    delivered, sizes = {}, []

    def grouped_shard(messages, operation, key):
        sizes.append(len(canonical_json({"messages": messages})))
        request = json.loads(messages[1]["content"])
        shard = request["components"]
        for row in shard:
            assert row["object_id"] not in delivered
            decoded = [dict(zip(request["need_columns"], need)) for need in row["payload"]["needs"]]
            for need in decoded:
                if "assertion" in need:
                    need["assertion"] = dict(zip(request["assertion_columns"], need["assertion"]))
            row["payload"]["needs"] = decoded
            delivered[row["object_id"]] = row
        return json.dumps({"groups": [group([row["object_id"]]) for row in shard], "unassigned_object_ids": []})

    result = build_programme(components, {}, grouped_shard, {"planner_requests": 4})
    assert 1 < len(sizes) <= 4 and max(sizes) <= 59000
    assert set(delivered) == {c["object_id"] for c in components}
    assert sum(len(c["payload"]["needs"]) for c in delivered.values()) == 600
    for component in components:
        projected = delivered[component["object_id"]]["payload"]
        assert projected["summary"] == component["payload"]["summary"]
        assert projected["unknowns"] == component["payload"]["unknowns"]
        assert projected["needs"] == [{k: v for k, v in n.items() if k != "evidence"} for n in component["payload"]["needs"]]
    assert len(result["assignments"]) == 20
    assert len(result["object_accounting"]) == 100
    assert result["planner"]["status"] == "proposal_validated"
    assert all(g["related_group_basis"].startswith("same_advisory_label_only") for g in result["practice_groups"])


def test_shard_overflow_fails_before_paid_calls_and_oversize_object_is_not_clipped():
    components = [scenario(f"s{i:03}", count=6) for i in range(100)]
    planner = Planner()
    overflow = build_programme(components, {}, planner, {"planner_requests": 1, "max_input_chars": 4000})
    assert not planner.calls and overflow["planner"]["completion_count"] == 0
    assert overflow["planner"]["required_shard_count"] > 4
    assert overflow["planner"]["status"] == "failed"
    assert len(overflow["object_accounting"]) == 100
    assert "no paid calls" in overflow["issues"][0]["message"]
    oversized = build_programme([scenario("a")], {}, planner, {"planner_requests": 4, "max_input_chars": 10})
    assert not planner.calls
    assert "no needs were clipped" in oversized["issues"][0]["message"]


def test_one_failed_shard_does_not_retry_or_erase_other_source_assignments():
    components = [scenario(str(i), count=3) for i in range(6)]
    calls = []

    def one_bad(messages, *args):
        shard = json.loads(messages[1]["content"])["components"]
        calls.append(shard)
        if len(calls) == 1:
            raise RuntimeError("Caller rejected this shard")
        return json.dumps({"groups": [group([row["object_id"]]) for row in shard], "unassigned_object_ids": []})

    result = build_programme(components, {}, one_bad, {"planner_requests": 4, "max_input_chars": 4500})
    assert 1 < len(calls) <= 4
    assert len(calls) == result["planner"]["completion_count"]
    assert result["planner"]["status"] == "partial"
    failed_ids = {row["object_id"] for row in calls[0]}
    assert all(row["status"] == "unassigned" for row in result["object_accounting"] if row["object_id"] in failed_ids)
    assert result["assignments"]
