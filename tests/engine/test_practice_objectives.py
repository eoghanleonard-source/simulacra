"""Synthetic callback regressions; these establish integrity, not semantic quality."""
from copy import deepcopy
import json

import pytest

from snowflake_engine.contracts import canonical_json
from snowflake_engine.practice import build_programme, validate_programme
from snowflake_engine import practice_objectives as objective


def scenario(oid, count=2, padding=""):
    return {"object_id": oid, "source_id": "source-" + oid, "kind": "scenario", "validation_status": "valid",
            "payload": {"id": oid, "summary": "Customer source " + oid, "unknowns": ["Completion remains unconfirmed"],
                        "needs": [{"id": "need-" + str(i), "summary": ("Replacement" if i % 2 == 0 else "Collection") + " request " + oid + padding,
                                   "status": "pending", "kind": "primary_need" if i == 0 else "secondary_need",
                                   "scope": "Only this item " + oid, "assertion": {"actor": "customer", "certainty": "reported", "state": "requested"},
                                   "evidence": [{"event_id": "event-" + str(i), "quote": "Immutable quote " + oid,
                                                 "speaker": "customer", "role": "customer", "authority": "customer_statement"}]}
                                  for i in range(count)]}}


class Model:
    def __init__(self, map_mutation=None, select_mutation=None):
        self.calls = []
        self.map_mutation, self.select_mutation = map_mutation, select_mutation

    def __call__(self, messages, operation, request_key):
        self.calls.append((deepcopy(messages), operation, request_key))
        data = json.loads(messages[1]["content"])
        if operation == objective.MAP_OPERATION:
            rows = []
            for alias, source, obj, need in data["needs"]:
                rows.append([alias, "Explain " + need["summary"].split()[0].lower(), need["scope"]])
            answer = {"needs": rows}
            if self.map_mutation:
                self.map_mutation(answer)
        else:
            assert operation == objective.SELECT_OPERATION
            classes = {}
            for alias, source, obj, oi, si in data["needs"]:
                classes.setdefault(data["objective_strings"][oi], []).append(alias)
            answer = {"objectives": [{"exercise_objective": label, "need_aliases": aliases} for label, aliases in sorted(classes.items())],
                      "pairs": [{"situation_alias": aliases[0], "variant_alias": aliases[1], "consequential_aliases": []}
                                for _, aliases in sorted(classes.items()) if len(aliases) > 1]}
            if self.select_mutation:
                self.select_mutation(answer)
        return canonical_json(answer)


def build(components, model=None, **config):
    return build_programme(components, {}, model, {"strategy": "objectives_v2", **config})


def test_source_can_have_multiple_objectives_without_merging_or_losing_original_needs():
    components = [scenario("a"), scenario("b")]
    original = deepcopy(components)
    model = Model()
    result = build(components, model)
    assert validate_programme(result, components) == []
    assert len(result["assignments"]) == 2
    assert len(result["practice_groups"]) == 2
    assert all(g["member_ids"] == ["a", "b"] for g in result["practice_groups"])
    assert len(result["need_accounting"]) == 4
    assert all(r["mapping_status"] == "mapped_advisory" for r in result["need_accounting"])
    for assignment in result["assignments"]:
        assert assignment["guidance_status"] == "requires_client_guidance"
        assert assignment["assessment_eligible"] is False
        assert len(assignment["exercised_need_refs"]) == 2
        for role in ("situation", "variant"):
            assert assignment[role]["payload"] == next(c["payload"] for c in components if c["object_id"] == assignment[role]["object_id"])
    assert all(g["equivalence_status"] == "not_asserted" for g in result["practice_groups"])
    assert result["source_components"] == original == components
    assert [call[1] for call in model.calls] == [objective.MAP_OPERATION, objective.SELECT_OPERATION]


def test_balanced_shards_account_for_every_need_once_and_retain_all_fact_fields():
    components = [scenario("a", 6, " x" * 200), scenario("b", 6)]
    config = {"strategy": "objectives_v2", "max_input_chars": 7000}
    requests = objective.preview_mapping_requests(components, config)
    aliases = [a for request in requests for a in request["need_aliases"]]
    assert 1 < len(requests) <= 8
    assert len(aliases) == len(set(aliases)) == 12
    assert max(len(r["need_aliases"]) for r in requests) - min(len(r["need_aliases"]) for r in requests) <= 4
    assert all(r["input_chars"] == len(canonical_json({"messages": r["messages"]})) <= 7000 for r in requests)
    factual = [row[3] for r in requests for row in json.loads(r["messages"][1]["content"])["needs"]]
    assert sorted(canonical_json(n) for n in factual) == sorted(canonical_json({k: v for k, v in n.items() if k not in {"evidence", "id"}}) for c in components for n in c["payload"]["needs"])
    assert all("Completion remains unconfirmed" in r["messages"][1]["content"] for r in requests)
    assert all("Immutable quote" not in r["messages"][1]["content"] for r in requests)


def test_unfittable_whole_inventory_makes_no_calls_and_preserves_all_accounting():
    model = Model()
    components = [scenario("a", 1, "x" * 60000)]
    result = build(components, model)
    assert not model.calls
    assert result["planner"]["status"] == "failed"
    assert len(result["need_accounting"]) == 1
    assert result["source_components"] == components
    assert validate_programme(result, components) == []


def test_mapping_and_global_requests_are_both_bounded_and_never_retry():
    model = Model(map_mutation=lambda answer: answer["needs"].pop())
    components = [scenario("a"), scenario("b")]
    result = build(components, model)
    assert len(model.calls) == 1
    assert result["planner"]["status"] == "failed"
    assert len(result["need_accounting"]) == 4
    assert not result["assignments"]
    no_allocation = Model()
    assert build(components, no_allocation, planner_requests=1)["planner"]["completion_count"] == 0
    assert not no_allocation.calls


def test_global_input_budget_is_checked_before_global_callback():
    components = [scenario(str(i), 1) for i in range(20)]
    def long_labels(answer):
        for alias, _, _ in answer["needs"]:
            index = int(alias[1:])
            answer["needs"][index - 1][1:] = [str(index) + "x" * 78, str(index) + "y" * 158]
    model = Model(map_mutation=long_labels)
    result = build(components, model, max_input_chars=6500)
    assert all(call[1] == objective.MAP_OPERATION for call in model.calls)
    assert result["planner"]["status"] == "failed"
    assert len(result["need_accounting"]) == 20
    assert all(len(canonical_json({"messages": call[0]})) <= 6500 for call in model.calls)


def test_invalid_pairs_remain_failed_visible_and_do_not_replace_with_later_cases():
    def mutate(answer):
        valid = deepcopy(answer["pairs"][0])
        answer["pairs"] = [dict(valid, variant_alias="invented"), valid]
    result = build([scenario("a"), scenario("b")], Model(select_mutation=mutate), max_assignments=1)
    assert not result["assignments"]
    assert len(result["proposal_accounting"]) == 2
    assert all(r["status"] == "failed" for r in result["proposal_accounting"])
    assert "recognised" in result["proposal_accounting"][0]["omission_reason"]
    assert "limit" in result["proposal_accounting"][1]["omission_reason"]
    assert validate_programme(result, result["source_components"]) == []


def test_twenty_limit_preserves_extra_proposals_as_failed():
    def mutate(answer):
        aliases = [a for group in answer["objectives"] for a in group["need_aliases"]]
        answer["pairs"] = [{"situation_alias": aliases[0], "variant_alias": a, "consequential_aliases": []} for a in aliases[1:]]
    components = [scenario(str(i), 1) for i in range(24)]
    result = build(components, Model(select_mutation=mutate))
    assert len(result["assignments"]) == 20
    assert len(result["proposal_accounting"]) == 23
    assert all(r["status"] == "failed" for r in result["proposal_accounting"][20:])
    assert validate_programme(result, components) == []


@pytest.mark.parametrize("bad", ["cross_objective", "context_outside_sources", "same_need", "same_signature"])
def test_bad_references_and_nonvariants_are_not_selected(bad):
    components = [scenario("a"), scenario("b"), scenario("c")]
    def mutate(answer):
        answer["pairs"] = [{"situation_alias": "n0001", "variant_alias": "n0003", "consequential_aliases": []}]
        if bad == "cross_objective":
            answer["pairs"][0]["variant_alias"] = "n0002"
        elif bad == "context_outside_sources":
            answer["pairs"][0]["consequential_aliases"] = ["n0005"]
        elif bad == "same_need":
            answer["pairs"][0]["variant_alias"] = "n0001"
    if bad == "same_signature":
        components[1]["payload"]["needs"][0]["scope"] = components[0]["payload"]["needs"][0]["scope"]
    result = build(components, Model(select_mutation=mutate))
    assert not result["assignments"]
    assert result["proposal_accounting"][0]["status"] == "failed"


def test_context_is_visible_but_receives_no_extra_exercised_credit():
    def mutate(answer):
        answer["pairs"] = [{"situation_alias": "n0001", "variant_alias": "n0003", "consequential_aliases": ["n0002", "n0004"]}]
    components = [scenario("a"), scenario("b")]
    result = build(components, Model(select_mutation=mutate))
    assert len(result["assignments"][0]["consequential_need_refs"]) == 2
    assert result["selection_summary"]["unique_exercised_need_count"] == 2
    assert sum(bool(n["assignment_ids"]) for n in result["need_accounting"]) == 2
    assert validate_programme(result, components) == []


def test_quarantines_personas_expected_failures_and_all_original_need_objects_remain():
    components = [scenario("a"), scenario("bad")]
    components[1]["validation_status"] = "quarantined"
    components.append({"object_id": "persona", "source_id": "source-a", "kind": "persona", "validation_status": "valid", "payload": {}})
    result = build(components, Model(), expected_sources=["source-a", "source-bad", "source-no-output"])
    assert len(result["object_accounting"]) == 3
    assert len(result["coverage"]) == 3
    assert len(result["need_accounting"]) == 4
    assert sum(n["mapping_status"] == "inventory_only" for n in result["need_accounting"]) == 2
    assert validate_programme(result, components) == []


def test_configuration_and_version_changes_invalidate_request_keys_without_gold_prompt_exposure():
    components = [scenario("a"), scenario("b")]
    baseline = objective.preview_mapping_requests(components)[0]
    changed = deepcopy(components)
    changed[0]["payload"]["needs"][0]["scope"] = "Only changed source scope"
    assert objective.preview_mapping_requests(changed)[0]["request_key"] != baseline["request_key"]
    evidence_changed = deepcopy(components)
    evidence_changed[0]["payload"]["needs"][0]["evidence"][0]["quote"] = "Different retained citation"
    changed_request = objective.preview_mapping_requests(evidence_changed)[0]
    assert changed_request["messages"] == baseline["messages"]
    assert changed_request["request_key"] != baseline["request_key"]
    assert objective.preview_mapping_requests(components, {"generation_settings": {"temperature": 0.1}})[0]["request_key"] != baseline["request_key"]
    assert objective.preview_mapping_requests(components, versions={"selection": objective.PRACTICE_VERSION, "code": "other"})[0]["request_key"] != baseline["request_key"]
    model = Model()
    result = build(components, model, must_covers=[{"must_cover_id": "EVALUATOR_SECRET", "need_refs": [{"object_id": "missing", "need_id": "also-missing"}]}])
    assert "EVALUATOR_SECRET" not in canonical_json(model.calls)
    assert result["must_cover_accounting"][0]["status"] == "missing"
    assert result["configuration_lineage"]["practice_version"] == objective.PRACTICE_VERSION


def test_native_version_set_and_mutated_need_membership_are_validated():
    components = [scenario("a"), scenario("b")]
    result = build_programme(components, {}, Model(), {"strategy": "objectives_v2"}, {"selection": {"version": objective.PRACTICE_VERSION, "config": {"strategy": "objectives_v2"}}})
    assert validate_programme(result, components) == []
    result["practice_groups"][0]["need_refs"][0] = {"object_id": "a", "need_id": "need-unknown"}
    assert validate_programme(result, components)


def test_global_class_failure_keeps_every_pair_and_mapping_visible():
    def mutate(answer):
        answer["objectives"][0]["need_aliases"].pop()
    components = [scenario("a"), scenario("b")]
    result = build(components, Model(select_mutation=mutate))
    assert not result["assignments"]
    assert len(result["proposal_accounting"]) == 2
    assert all(r["status"] == "failed" for r in result["proposal_accounting"])
    assert all(r["mapping_status"] == "mapped_advisory" for r in result["need_accounting"])
    assert validate_programme(result, components) == []


def test_successful_programme_cannot_erase_need_groups_or_change_configuration_digest():
    components = [scenario("a"), scenario("b")]
    result = build(components, Model())
    result["practice_groups"] = []
    assert validate_programme(result, components)
    result = build(components, Model())
    result["configuration_lineage"]["component_inventory_digest"] = "changed"
    assert validate_programme(result, components)


def test_old_default_retains_old_operation_and_version():
    calls = []
    def complete(messages, operation, key):
        calls.append(operation)
        return '{"groups":[],"unassigned_object_ids":["a"]}'
    result = build_programme([scenario("a")], {}, complete)
    assert calls == ["practice_planner"]
    assert result["versions"] == {"selection": "engine-practice-v1"}
    assert validate_programme(result, [scenario("a")]) == []
