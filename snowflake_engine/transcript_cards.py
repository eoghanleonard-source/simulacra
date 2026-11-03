"""Pure, source-bound packaging of retained transcript inventories.

This seam accepts parsed legacy packets, never opens files or calls a provider.
Callers must select original packets by the explicit source allowlist first.
Legacy inventories may be mixed: only rows citing selected IDs are adapted.
Exact evidence/shape validation is not semantic acceptance of historical drafts.
"""
from copy import deepcopy
from datetime import datetime
import hashlib
import re

from .contracts import (ACTION_FUNCTIONS, NEED_STATUSES, SEQUENCE_KINDS,
                        canonical_json, is_direct_customer, source_hash,
                        validate_component, validate_source)

ADAPTER_VERSION = "retained-transcript-cards-v2"
CONTEXT_RENDER_VERSION = "complete-source-context-v1"


def _digest(value):
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _id(kind, value):
    return kind + "-" + _digest([ADAPTER_VERSION, value])[:24]


def canonical_transcript_source(packet, *, client_id="retained-transcript-development"):
    """Keep original text, event IDs, supplied order, roles, speakers and clocks."""
    if not isinstance(packet, dict):
        raise ValueError("Transcript packet must be an object")
    if "metadata" in packet and "source_type" in packet:
        return validate_source(packet)
    events, coverage = packet.get("events"), packet.get("coverage", {})
    if (packet.get("truncated", False) is not False or packet.get("body_clipping", False) is not False
            or not isinstance(events, list) or not events or not isinstance(coverage, dict)
            or any(type(coverage.get(k)) is not int for k in
                   ("source_event_count", "included_event_count", "omitted_event_count"))
            or tuple(coverage[k] for k in ("source_event_count", "included_event_count", "omitted_event_count"))
            != (len(events), len(events), 0)
            or coverage.get("body_clipping") is not False or coverage.get("omitted_events", []) != []):
        raise ValueError("Complete, unclipped transcript packet and event accounting required")
    speakers = packet.get("customer_side_speaker_ids")
    if not isinstance(speakers, list) or any(s == "unknown" for s in speakers):
        raise ValueError("Explicit customer-side speakers required; [] means unknown")
    mapped = []
    for event in events:
        if not isinstance(event, dict) or "created_at" not in event:
            raise ValueError("Transcript events require supplied created_at; null means unknown")
        timestamp = event["created_at"]
        if timestamp is not None:
            if not isinstance(timestamp, str) or datetime.fromisoformat(timestamp.replace("Z", "+00:00")).tzinfo is None:
                raise ValueError("Supplied transcript clock must include timezone")
        # Keep timing metadata and source segment IDs as custody, not evidence.
        mapped.append({"event_id": event.get("evidence_id"), "ordinal": event.get("chronology_index"),
            "kind": event.get("kind", "utterance"), "role": event.get("role"), "speaker": event.get("speaker"),
            "authority": event.get("authority"), "text": event.get("body"),
            "original_text": event.get("body"), "timestamp": timestamp,
            "timing_status": "known" if timestamp is not None else "unknown",
            "redaction_status": "not_reviewed", "raw_record": deepcopy(event)})
    source = validate_source({"source_id": packet.get("source_id"), "client_id": client_id,
        "source_type": "transcript", "metadata": {"customer_side_speaker_ids": deepcopy(speakers),
            "channel": packet.get("channel", "unknown"), "source_event_count": len(events),
            "included_event_count": len(events), "omitted_event_count": 0, "body_clipping": False,
            "source_metadata": deepcopy(packet.get("source_metadata", {})),
            "source_packet_sha256": _digest(packet), "adapter_version": ADAPTER_VERSION},
        "events": mapped, "guidance": []})
    if not set(speakers) <= {event["speaker"] for event in mapped}:
        raise ValueError("Designated customer speaker absent from source")
    return source


def _source_ids(value):
    """Inspect citation identity only before selecting inventory bodies."""
    if isinstance(value, dict):
        if "source_id" in value and ("evidence_id" in value or "event_id" in value):
            yield value["source_id"]
        else:
            for child in value.values():
                yield from _source_ids(child)
    elif isinstance(value, list):
        for child in value:
            yield from _source_ids(child)


def _refs(assertion, source):
    if not isinstance(assertion, dict) or not isinstance(assertion.get("text"), str) or not assertion["text"].strip():
        raise ValueError("Legacy assertion requires nonempty text")
    refs = assertion.get("evidence")
    if not isinstance(refs, list) or not refs:
        raise ValueError("Legacy assertion requires exact evidence")
    events = {event["event_id"]: event for event in source["events"]}
    result = []
    for ref in refs:
        if not isinstance(ref, dict) or ref.get("source_id") != source["source_id"]:
            raise ValueError("Legacy citation crosses source boundary")
        event = events.get(ref.get("evidence_id", ref.get("event_id")))
        quote = ref.get("quote")
        if event is None or not isinstance(quote, str) or not quote or quote not in event["text"]:
            raise ValueError("Legacy quote is not an exact original source span")
        if any(ref.get(key) != event[key] for key in ("role", "speaker", "authority")):
            raise ValueError("Legacy citation changes original attribution")
        result.append({"event_id": event["event_id"], "quote": quote,
                       **{key: event[key] for key in ("role", "speaker", "authority")}})
    if [events[r["event_id"]]["ordinal"] for r in result] != sorted(events[r["event_id"]]["ordinal"] for r in result):
        raise ValueError("Legacy evidence must preserve original order")
    return result


def _summary(assertion):
    status = assertion.get("status")
    if not isinstance(status, str) or not status.strip():
        raise ValueError("Legacy assertion requires explicit source status")
    return assertion["text"] + " [Source assertion status: " + status + ".]"


def _action_function(assertion):
    """Translate only explicit vocabulary/prose; unsupported categories abstain."""
    explicit = assertion.get("function")
    if explicit is not None:
        if explicit not in ACTION_FUNCTIONS:
            raise ValueError("Legacy action function has no canonical equivalent")
        return explicit
    text = assertion["text"].lower()
    # "Supplied requested details" describes an answer, not a request. Compare
    # the narrated acts before interpreting a request noun/modifier elsewhere.
    supplied = re.search(r"\b(suppli(?:ed|es)|provid(?:ed|es)|answers?|answered)\b", text)
    requested = re.search(r"\b(request(?:s|ed)?|asks? for|need for)\b", text)
    if supplied and (requested is None or supplied.start() < requested.start()):
        return "provide_information"
    if assertion.get("status") == "requested" or re.search(r"\b(request(?:s|ed)?|asks? for|need for)\b", text):
        return "request_action"
    if re.search(r"\b(asks? (?:to check|about)|inquir(?:y|es)|enquir(?:y|es))\b", text):
        return "ask_information"
    if re.search(r"\b(suppli(?:ed|es)|provid(?:ed|es)|stated|states|says|specified|report(?:s|ed)?)\b", text):
        return "provide_information"
    raise ValueError("Legacy prose has no explicit canonical dialogue-action equivalent")


def _interaction_context(refs, source):
    """Expose the complete exchange as context, without extending the assertion.

    Citation boundaries do not identify conversational boundaries. In particular,
    a response can follow several intervening questions or a resumed request.
    Without an authored exchange boundary, retain all supplied events rather than
    guess where the supported exchange ends. Only the authored description names
    a behaviour; incidental words in this history do not create another pattern.
    """
    events = source["events"]
    positions = {event["event_id"]: i for i, event in enumerate(events)}
    context = [{"event_id": event["event_id"], "quote": event["text"],
                **{key: event[key] for key in ("role", "speaker", "authority")}}
               for event in events if event["text"].strip()]
    merged = {canonical_json(ref): ref for ref in refs + context}
    evidence = sorted(merged.values(), key=lambda ref: (
        positions[ref["event_id"]], events[positions[ref["event_id"]]]["text"].find(ref["quote"])))
    text = (" Complete source history as context for the description above; quotations do not add behavioural claims. "
            "Source exchange in supplied turn order (no finer within-turn order or unstated cause inferred): ") + " ".join(
        "[" + event["event_id"] + "; " + event["kind"] + "] " + event["role"] + " " + event["speaker"] +
        ": " + canonical_json(event["text"]) for event in events)
    return evidence, text


def adapt_transcript_inventory(sources, scenarios, personas, *, source_ids,
                              domain="customer_service", client_id="retained-transcript-development",
                              supplementary_personas=None):
    """Package selected retained legacy rows into existing engine components.

    No semantic repair is performed. Failed children remain in ``unresolved`` and
    child accounting; valid siblings survive. Legacy rejection remains recorded.
    Transformation child entries expose original ``scenario_id``/``persona_id``
    and field ``path`` for a runner to bind approved plan intents to final IDs.
    Optional supplementary personas use the same assertion schema and require
    provenance with method, source_id, source_digest and evidence_source. Their
    IDs must be new; their custody never upgrades review or approval status.
    """
    if (not isinstance(source_ids, (list, tuple, set)) or not source_ids
            or any(not isinstance(sid, str) or not sid for sid in source_ids)
            or len(source_ids) != len(set(source_ids)) or not isinstance(domain, str) or not domain.strip()
            or not isinstance(client_id, str) or not client_id.strip()):
        raise ValueError("Explicit unique source IDs, domain and client_id required")
    allowed = set(source_ids)
    if (not isinstance(sources, list) or any(not isinstance(s, dict) for s in sources)
            or len(sources) != len(allowed) or {s.get("source_id") for s in sources} != allowed):
        raise ValueError("Caller must supply every selected original packet exactly once, with no other bodies")
    if supplementary_personas is None:
        supplementary_personas = []
    if not isinstance(scenarios, list) or not isinstance(personas, list) or not isinstance(supplementary_personas, list):
        raise ValueError("Legacy inventories must be arrays")
    canonical = {s["source_id"]: canonical_transcript_source(s, client_id=client_id) for s in sources}
    components, unresolved, lineage = [], [], []
    accounting = {sid: {"source_id": sid, "source_digest": source_hash(source), "status": "excluded",
        "event_count": len(source["events"]), "component_ids": [], "input_scenario_ids": [],
        "input_persona_ids": [], "child_accounting": [], "original_semantic_status": "historical_rejected"}
        for sid, source in canonical.items()}
    inventory = []

    def child(assertion, source, identity, path, operation):
        entry = {**identity, "path": path, "status": "quarantined", "child_id": None}
        accounting[source["source_id"]]["child_accounting"].append(entry)
        try:
            refs = _refs(assertion, source)
            result = operation(refs)
            entry.update(status="represented", child_id=result["id"])
            return result
        except (ValueError, KeyError, TypeError) as exc:
            entry["reason"] = str(exc)
            unresolved.append({"source_id": source["source_id"], "status": "quarantined_child",
                **identity, "path": path, "reason": str(exc), "raw_record": deepcopy(assertion)})
            return None

    def emit(kind, payload, source, identity, row, *, behaviour_descriptions=None):
        errors = validate_component(kind, payload, source)
        if errors:
            raise ValueError("Adapter produced invalid " + kind + ": " + "; ".join(errors))
        oid = _id(kind, [source["source_id"], identity, payload])
        component = {"object_id": oid, "source_id": source["source_id"], "client_id": source["client_id"],
            "kind": kind, "payload": payload, "validation_status": "valid", "issues": [],
            "review_status": "unreviewed", "eligibility_status": "requires_client_guidance",
            "capture_record_ids": list(identity.values())}
        components.append(component)
        entries = [e for e in accounting[source["source_id"]]["child_accounting"]
                   if all(e.get(k) == v for k, v in identity.items()) and e["status"] == "represented"]
        lineage.append({"object_id": oid, "source_id": source["source_id"], **identity,
            "input_record_ids": list(identity.values()), "input_record_sha256": _digest(row),
            "adapter_version": ADAPTER_VERSION, "original_semantic_status": "historical_rejected",
            "child_record_ids": [{"child_id": e["child_id"], "input_record_ids": list(identity.values()),
                "input_refs": [{**identity, "path": e["path"]}]} for e in entries]})
        if row_origin == "source_evidence_supplement":
            lineage[-1].update(input_origin=row_origin, supplementary_provenance=deepcopy(row["provenance"]),
                               original_semantic_status="source_evidence_supplement_unreviewed")
        if kind == "persona":
            lineage[-1].update(context_render_version=CONTEXT_RENDER_VERSION,
                source_context_projection="Complete supplied source history, including earlier and later events, is context only; behavioural claims remain the explicit descriptions with their original citations.",
                source_context_event_ids=[event["event_id"] for event in source["events"]],
                behaviour_descriptions=deepcopy(behaviour_descriptions))
        accounting[source["source_id"]]["component_ids"].append(oid)

    for kind, rows in (("scenario", [(row, "retained_inventory") for row in scenarios]),
                       ("persona", [(row, "retained_inventory") for row in personas] +
                        [(row, "source_evidence_supplement") for row in supplementary_personas])):
        seen = set()
        for row, row_origin in rows:
            cited = set(_source_ids(row))
            selected = cited & allowed
            key = kind + "_id"
            rid = row.get(key) if isinstance(row, dict) else None
            if not selected:
                inventory.append({"kind": kind, "input_id": rid, "status": "outside_selected_scope"})
                continue
            if not isinstance(rid, str) or not rid or rid in seen:
                raise ValueError("Selected inventory requires unique nonempty " + key)
            seen.add(rid)
            identity = {key: rid}
            if row_origin == "source_evidence_supplement":
                provenance = row.get("provenance")
                if (not isinstance(provenance, dict) or len(cited) != 1
                        or provenance.get("method") != "manual_source_review"
                        or provenance.get("source_id") != next(iter(selected))
                        or provenance.get("source_digest") != source_hash(canonical[next(iter(selected))])
                        or not isinstance(provenance.get("evidence_source"), str)
                        or not provenance["evidence_source"].strip()):
                    raise ValueError("Supplementary persona requires selected source-bound manual review provenance")
            inventory.append({"kind": kind, "input_id": rid, "source_ids": sorted(selected),
                              **({"input_sha256": _digest(row)} if cited <= allowed else {}), "status": "accounted"})
            if row_origin == "source_evidence_supplement":
                inventory[-1].update(input_origin=row_origin, supplementary_provenance=deepcopy(provenance))
            for sid in selected:
                accounting[sid]["input_" + kind + "_ids"].append(rid)
            if len(cited) != 1:
                unresolved.append({"source_ids": sorted(selected), **identity, "status": "quarantined_inventory_row",
                    "reason": "Cross-source inventory requires explicit source-specific decomposition"})
                continue
            sid = next(iter(selected))
            source = canonical[sid]
            unknowns = row.get("unknowns", [])
            if not isinstance(unknowns, list) or any(not isinstance(x, str) or not x.strip() for x in unknowns):
                raise ValueError("Selected inventory unknowns require nonempty strings")
            if kind == "scenario":
                needs = []
                fields = [("situation", "secondary_need"), ("objective_or_concern", "primary_need"),
                          ("material_constraints", "material_constraint"), ("corrections", "correction"),
                          ("reported_outcomes", "reported_outcome")]
                for field, need_kind in fields:
                    values = row.get(field, []) if field in {"material_constraints", "corrections", "reported_outcomes"} else [row.get(field)]
                    if not isinstance(values, list):
                        raise ValueError("Legacy scenario array required: " + field)
                    for index, assertion in enumerate(values):
                        path = field + (f"[{index}]" if field in {"material_constraints", "corrections", "reported_outcomes"} else "")
                        def need(refs, a=assertion, p=path, nk=need_kind):
                            status = a.get("status")
                            mapped = {"observed": "reported", "proposed": "pending", "reported_completed": "reported"}.get(status, status)
                            if mapped not in NEED_STATUSES:
                                mapped = "reported" if status in {"complaint", "hesitation", "concern", "decision"} else "unknown"
                            return {"id": _id("need", [rid, p]), "kind": nk, "summary": _summary(a),
                                    "status": mapped, "related_ids": [], "evidence": refs}
                        unit = child(assertion, source, identity, path, need)
                        if unit:
                            needs.append(unit)
                # Legacy uncertainty is already a retained assertion, not a new
                # conclusion from missing dialogue. Context citations locate the
                # situation; they never prove noncompletion or another absence.
                context = {canonical_json(ref): ref for n in needs
                           if n["kind"] in {"secondary_need", "primary_need", "reported_outcome"}
                           for ref in n["evidence"]}
                events = {e["event_id"]: e for e in source["events"]}
                context = sorted(context.values(), key=lambda ref: (
                    events[ref["event_id"]]["ordinal"], events[ref["event_id"]]["text"].find(ref["quote"])))
                for index, text in enumerate(unknowns):
                    path = f"unknowns[{index}]"
                    assertion = {"text": text, "status": "unknown", "evidence": [
                        {"source_id": sid, **ref} for ref in context]}
                    def uncertainty(refs, value=text, p=path):
                        return {"id": _id("need", [rid, p]), "kind": "uncertainty", "status": "unknown",
                            "summary": "Retained source uncertainty: " + value +
                                " [Citations provide source context; they do not prove absence or noncompletion.]",
                            "related_ids": [], "evidence": refs}
                    unit = child(assertion, source, identity, path, uncertainty)
                    if unit:
                        needs.append(unit)
                if needs:
                    summary = next((n["summary"] for n in needs if n["kind"] == "secondary_need"), needs[0]["summary"])
                    emit(kind, {"id": _id("local-scenario", rid), "summary": summary, "needs": needs,
                        "unknowns": unknowns + ["Historical assertions do not establish correct handling; client-approved guidance is required."]}, source, identity, row)
            else:
                events = {e["event_id"]: e for e in source["events"]}
                for speaker in source["metadata"].get("customer_side_speaker_ids", []):
                    actions, sequences, behaviour_descriptions = [], [], []
                    assertions = [(f"profile_clauses[{i}]", a) for i, a in enumerate(row.get("profile_clauses", []))]
                    for i, observation in enumerate(row.get("observations", [])):
                        assertions.append((f"observations[{i}].pattern", observation.get("pattern")))
                    for path, assertion in assertions:
                        def behaviour(refs, a=assertion, p=path):
                            if not any(is_direct_customer(events[r["event_id"]], source) and r["speaker"] == speaker for r in refs):
                                raise ValueError("Persona child has no direct designated customer evidence")
                            text = a["text"].lower()
                            seq_kind = a.get("kind")
                            if seq_kind is None and re.search(r"\brepeat(?:ed|s|ing|ition)?\b", text):
                                seq_kind = "repetition_in_context"
                            if seq_kind is None and ("excuse me" in text or "clarification" in text):
                                seq_kind = "clarification_repair"
                            if seq_kind in SEQUENCE_KINDS and len({r["event_id"] for r in refs}) >= 2:
                                return {"id": _id("sequence", [rid, speaker, p]), "kind": seq_kind,
                                    "summary": _summary(a) + " Further development and causes remain unknown unless stated.", "evidence": refs}
                            return {"id": _id("action", [rid, speaker, p]), "function": _action_function(a),
                                    "summary": _summary(a), "evidence": refs}
                        unit = child(assertion, source, identity, path, behaviour)
                        if unit:
                            # Agent-only context is valid *context*, not an independent
                            # customer observation. Bind it to this direct-speaker unit.
                            match = re.fullmatch(r"observations\[(\d+)\]\.pattern", path)
                            if match:
                                observation = row["observations"][int(match[1])]
                                for field in ("contextual_triggers", "changes"):
                                    for j, context in enumerate(observation.get(field, [])):
                                        context_path = path.rsplit(".", 1)[0] + f".{field}[{j}]"
                                        def retain_context(refs, a=context):
                                            summary = _summary(a)
                                            merged = {canonical_json(r): r for r in unit["evidence"] + refs}
                                            unit["evidence"] = sorted(merged.values(), key=lambda r: (
                                                events[r["event_id"]]["ordinal"], events[r["event_id"]]["text"].find(r["quote"])))
                                            unit["summary"] += " " + field.replace("_", " ").capitalize() + ": " + summary
                                            return unit
                                        child(context, source, identity, context_path, retain_context)
                            behaviour_descriptions.append({"child_id": unit["id"], "input_path": path,
                                "summary": unit["summary"], "evidence": deepcopy(unit["evidence"])})
                            unit["evidence"], exchange = _interaction_context(unit["evidence"], source)
                            unit["summary"] += exchange
                            (sequences if "kind" in unit else actions).append(unit)
                    direct = any(is_direct_customer(e, source) and e["speaker"] == speaker for e in source["events"])
                    gaps = unknowns + row.get("evidence_limits", []) + ["Retained descriptions are interaction-bound drafts; emotion labels and causes have no compatible reviewed mapping."]
                    emit(kind, {"id": _id("local-persona", [rid, speaker]), "speaker": speaker,
                        "evidence_status": "observed" if actions or sequences else "insufficient_evidence",
                        "actions": actions, "sequences": sequences, "emotional_expression": {
                            "status": "unknown" if direct else "insufficient_evidence", "observations": []},
                        "emotional_changes": [], "unknowns": gaps}, source, identity, row,
                        behaviour_descriptions=behaviour_descriptions)
    for sid, row in accounting.items():
        owned = [c for c in components if c["source_id"] == sid]
        row.update(status="processed" if owned else "excluded", scenario_count=sum(c["kind"] == "scenario" for c in owned),
                   persona_count=sum(c["kind"] == "persona" for c in owned))
        row["scenario_status"] = "retained_draft_packaged" if row["scenario_count"] else "no_usable_selected_scenario"
        usable_persona = any(c["kind"] == "persona" and (c["payload"]["actions"] or c["payload"]["sequences"]) for c in owned)
        row["persona_status"] = ("no_usable_selected_persona" if row["persona_count"] and not usable_persona else
            "source_evidence_supplement_packaged" if row["persona_count"] and any(
            c.get("input_origin") == "source_evidence_supplement" and c["source_id"] == sid for c in lineage) else
            "retained_draft_packaged" if row["persona_count"] else
            "no_retained_persona_inventory" if not row["input_persona_ids"] else "no_usable_selected_persona")
        row["behaviour_completeness"] = "unverified"
        used = {r["event_id"] for c in owned for r in _all_refs(c["payload"])}
        row["event_accounting"] = [{"event_id": e["event_id"], "status": "represented_in_card_evidence" if e["event_id"] in used
            else "retained_source_history_without_current_claim"} for e in canonical[sid]["events"]]
    return {"sources": [canonical[sid] for sid in sorted(canonical)], "components": components, "guidance": [],
        "issues": [{"code": "retained_transcript_packaging_draft", "message": "Historical rejection remains unchanged; exact evidence and shape checks do not establish semantic acceptance."}],
        "unresolved": unresolved, "source_accounting": [accounting[sid] for sid in sorted(accounting)],
        "transformation_provenance": {"adapter_version": ADAPTER_VERSION, "domain": domain,
            "source_ids": sorted(allowed), "selected_input_sha256": _digest([entry["input_sha256"] for entry in inventory if "input_sha256" in entry]),
            "original_semantic_status": "historical_rejected", "inventory_accounting": inventory, "components": lineage,
            "limitations": ["No provider extraction or semantic acceptance; source-reviewed supplements retain their manual provenance.",
                "Explicit legacy prose is mapped conservatively to canonical action/sequence labels; review remains required.",
                "Emotion remains unknown; unsupported children remain quarantined with their original content.",
                "No approved policy is inferred from historical speech; client guidance remains required."]}}


def _all_refs(value):
    if isinstance(value, dict):
        if "event_id" in value and "quote" in value:
            yield value
        else:
            for child in value.values():
                yield from _all_refs(child)
    elif isinstance(value, list):
        for child in value:
            yield from _all_refs(child)
