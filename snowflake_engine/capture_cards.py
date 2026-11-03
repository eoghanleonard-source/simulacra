"""Package reviewed capture evidence into the existing draft card contracts.

This pure adapter makes no model or storage calls. One conversation becomes one
situation; customer turns and their following responses become separate needs.
Listing/context and interface records remain explicitly attributed scenario
reports, never customer speech. No cross-source equivalence or training policy is
inferred. Shape validation is not semantic acceptance.
"""
from copy import deepcopy
import hashlib
import re

from .capture_contract import ENUMS, source_projection
from .capture_storage import validate_capture_export
from .contracts import canonical_json, is_direct_customer, source_hash, validate_component, validate_source

ADAPTER_VERSION = "capture-cards-v2"
_FUNCTION = {"question": "ask_information", "request": "request_action",
             "acceptance": "agree", "decline": "decline_offer",
             "acknowledgement": "positive_processing_feedback",
             "report": "provide_information", "offer": "provide_information",
             "hypothetical": "provide_information", "observed_communication": "provide_information",
             "unknown": "provide_information"}


def _digest(value):
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def _id(kind, value):
    return kind + "-" + _digest([ADAPTER_VERSION, value])[:24]


def _spoken(event):
    # The dataset marker is an explicit source declaration, not an inference
    # from the buyer/seller role, which also occurs on interface events.
    return (event["kind"] not in {"note", "case_context", "interface_action"}
            and not event["text"].startswith(("[DATASET INTERFACE ACTION; NOT SPOKEN DIALOGUE]",
                                              "[PUBLIC LISTING CONTEXT; NOT SPOKEN DIALOGUE]")))


def _citations(record, events):
    evidence = record.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        raise ValueError("Card input requires nonempty exact source evidence")
    refs = []
    for row in evidence:
        event = events.get(row.get("event_id")) if isinstance(row, dict) else None
        if (event is None or not isinstance(row.get("quote"), str) or not row["quote"]
                or row["quote"] not in event["text"]):
            raise ValueError("Card evidence must quote a retained source event exactly")
        if any(key in row and row[key] != event[key] for key in ("role", "speaker", "authority")):
            raise ValueError("Card evidence original attribution mismatch")
        if "start" in row or "end" in row:
            start, end = row.get("start"), row.get("end")
            if (type(start) is not int or type(end) is not int or not 0 <= start < end <= len(event["text"])
                    or event["text"][start:end] != row["quote"]):
                raise ValueError("Card evidence source offsets differ")
        refs.append({"event_id": event["event_id"], "quote": row["quote"],
                     **{key: event[key] for key in ("role", "speaker", "authority")}})
    return _ordered_refs(refs, events)


def _ordered_refs(refs, events):
    unique = {canonical_json(ref): ref for ref in refs}
    return sorted(unique.values(), key=lambda ref: (events[ref["event_id"]]["ordinal"],
                  events[ref["event_id"]]["text"].find(ref["quote"]), canonical_json(ref)))


def _checked_record(record, events, source, spans):
    record = deepcopy(record)
    claim = record.get("claim")
    if not isinstance(claim, dict):
        raise ValueError("Card input requires a claim")
    for key in ("kind", "actor", "action", "object", "status"):
        if not isinstance(claim.get(key), str) or not claim[key].strip():
            raise ValueError("Card claim requires " + key)
    for key, vocabulary in ENUMS.items():
        if key in claim and claim[key] not in vocabulary:
            raise ValueError("Card claim has unknown " + key)
    if claim["actor"] not in {e["speaker"] for e in events.values()} | {"unknown"}:
        raise ValueError("Card claim actor is not a source speaker")
    if (not isinstance(claim.get("conditions"), list)
            or any(not isinstance(x, str) or not x.strip() for x in claim["conditions"])):
        raise ValueError("Card claim conditions require nonempty strings")
    if (not isinstance(claim.get("functions"), list) or not claim["functions"]
            or any(x not in _FUNCTION for x in claim["functions"])):
        raise ValueError("Card claim requires known capture functions")
    refs = _citations(record, events)
    for evidence in record["evidence"]:
        if "span_id" in evidence:
            frozen = spans.get(evidence["span_id"])
            if frozen is None or any(evidence.get(key) != frozen[key] for key in ("event_id", "quote", "start", "end")):
                raise ValueError("Card evidence differs from the frozen capture span")
    primary = claim.get("primary_event_id", refs[0]["event_id"])
    if primary not in events or primary not in {r["event_id"] for r in refs}:
        raise ValueError("Card claim primary event requires its own source evidence")
    if _spoken(events[primary]) and claim["actor"] != events[primary]["speaker"]:
        raise ValueError("Spoken claim actor must match the primary event speaker")
    claim["primary_event_id"] = primary
    if claim["kind"] == "communication" and claim["actor"] not in source["metadata"].get("customer_side_speaker_ids", []):
        raise ValueError("Communication claim requires a designated customer speaker")
    record["citations"] = refs
    return record


def _describe(row, events):
    claim = row["claim"]
    event = events[claim["primary_event_id"]]
    if not _spoken(event):
        origin = ("Dataset interface record" if event["text"].startswith("[DATASET INTERFACE")
                  or event["kind"] == "interface_action" else "Source context record")
        actor = f"{origin} ({event['role']}, speaker {event['speaker']}, authority {event['authority']}); claim actor {claim['actor']}"
    else:
        roles = sorted({r["role"] for r in row["citations"] if r["speaker"] == claim["actor"]})
        actor = f"{'/'.join(roles) or 'Source-reported actor'} {claim['actor']}"
    if claim.get("recipient") and claim["recipient"] != "unknown":
        actor += " to speaker " + claim["recipient"]
    text = f"{actor}: {claim['action']}. Subject: {claim['object']}."
    if claim["conditions"]:
        text += " Conditions: " + "; ".join(claim["conditions"]) + "."
    if claim.get("time"):
        text += " Time: " + claim["time"] + "."
    # Agreed is never relabelled as completed, and a tentative proposition is
    # never silently turned into an unconditional promise.
    text += " Source status: " + claim["status"] + "."
    if claim.get("certainty"):
        text += " Certainty: " + claim["certainty"] + "."
    if claim.get("polarity"):
        text += " Polarity: " + claim["polarity"] + "."
    return text


def _need_kind(rows, events, first=False):
    if any(re.search(r"\bcorrect(?:ed|ion|s)?\b", row["claim"]["action"], re.I) for row in rows):
        return "correction"
    if any(row["claim"]["conditions"] for row in rows):
        return "material_constraint"
    if all(not _spoken(events[row["claim"]["primary_event_id"]]) for row in rows):
        return ("reported_outcome" if all(events[row["claim"]["primary_event_id"]]["text"].startswith("[DATASET INTERFACE")
                or events[row["claim"]["primary_event_id"]]["kind"] == "interface_action" for row in rows)
                else "material_constraint")
    return "primary_need" if first else "secondary_need"


def _turn_narrative(rows, events):
    """Order retained claims without mistaking retained order for adjacency.

    Uncaptured spoken events still count as intervening turns. Explicitly
    nonspoken source records do not interrupt spoken-turn adjacency. Claims
    within a single event have no inferred order.
    """
    turns = {}
    for row in rows:
        turns.setdefault(row["claim"]["primary_event_id"], []).append(row)
    spoken_order = {event["event_id"]: index for index, event in enumerate(sorted(
        (event for event in events.values() if _spoken(event)),
        key=lambda event: event["ordinal"]))}
    narrative = []
    previous = None
    for event_id in sorted(turns, key=lambda event_id: events[event_id]["ordinal"]):
        if previous is not None:
            if (previous in spoken_order and event_id in spoken_order
                    and spoken_order[event_id] == spoken_order[previous] + 1):
                narrative.append(" Next spoken source turn: ")
            else:
                narrative.append(" Later source turn: " if _spoken(events[event_id])
                                 else " Later source record: ")
        narrative.append(" Same source turn: ".join(_describe(row, events) for row in turns[event_id]))
        previous = event_id
    return "".join(narrative)


def _action_function(row):
    """Project the explicit speech act, including interrogative proposals.

    Capture can label an interrogative price proposal only as an offer, or
    put acknowledgement/report before a question. The final action vocabulary
    describes what the speaker asks the other party to do, so that distinction
    must survive the projection. No claim text or source status is rewritten.
    """
    claim = row["claim"]
    functions = set(claim["functions"])
    if re.search(r"\b(?:partial|unfinished|began to raise a) question\b",
                 claim["action"] + " " + claim["object"], re.I):
        # The final vocabulary has no unfinished-greeting label. Keep the
        # utterance in the visible exchange instead of asserting a completed
        # question or information-supplying act.
        return None
    if "request" in functions:
        return "request_action"
    if "offer" in functions and any(re.search(
            r"\b(?:(?:would|could|can) you|are you willing|what do you think about|"
            r"(?:was|am) wondering if you)\b", ref["quote"], re.I)
            for ref in row["citations"] if ref["event_id"] == claim["primary_event_id"]):
        return "request_action"
    if "question" in functions or claim.get("certainty") == "question":
        return "ask_information"
    functions = [f for f in claim["functions"] if f != "observed_communication"]
    return _FUNCTION[(functions or ["observed_communication"])[0]]


def _exchange_context(event_id, action_event_ids, events):
    """Expose the complete local exchange in the final behaviour description.

    Accepted claims are retained as the action, while exact canonical utterances
    preserve the other party's prompt and response, including uncaptured turns.
    Continue to the neighbouring retained customer actions, including any
    uncaptured intervening turns; never include interface or listing records
    as dialogue. This is source quotation, not new extraction.
    """
    spoken = sorted((event for event in events.values() if _spoken(event)),
                    key=lambda event: event["ordinal"])
    index = next(i for i, event in enumerate(spoken) if event["event_id"] == event_id)
    start, end = index, index + 1
    while start > 0 and spoken[start - 1]["event_id"] not in action_event_ids:
        start -= 1
    while end < len(spoken) and spoken[end]["event_id"] not in action_event_ids:
        end += 1
    selected = spoken[start:end]
    refs = [{"event_id": event["event_id"], "quote": event["text"],
             **{key: event[key] for key in ("role", "speaker", "authority")}}
            for event in selected]
    narrative = " ".join(
        f"Source turn {event['ordinal']} ({event['role']} {event['speaker']}) says: {event['text']!r}."
        for event in selected)
    return " Observed exchange in source turn order: " + narrative, refs


def _groups(rows, events, focal):
    """Keep each customer contribution with its answers, without topic guesses."""
    context, turns = [], []
    for row in rows:
        event = events[row["claim"]["primary_event_id"]]
        if not _spoken(event):
            context.append([row])
        elif (not turns or (event["speaker"] in focal
                and turns[-1][0]["claim"]["primary_event_id"] != event["event_id"])):
            turns.append([row])
        else:
            turns[-1].append(row)
    # Context claims from one event are one report, rather than separate cards.
    by_event = {}
    for group in context:
        by_event.setdefault(group[0]["claim"]["primary_event_id"], []).extend(group)
    return sorted([*by_event.values(), *turns],
                  key=lambda group: events[group[0]["claim"]["primary_event_id"]]["ordinal"])


def _component(kind, payload, source, lineage):
    issues = validate_component(kind, payload, source)
    if issues:
        raise ValueError("Adapter produced invalid " + kind + ": " + "; ".join(issues))
    return {"object_id": _id(kind, [source["source_id"], payload]), "source_id": source["source_id"],
            "client_id": source["client_id"], "kind": kind, "payload": payload,
            "validation_status": "valid", "issues": [], "review_status": "unreviewed",
            "eligibility_status": "requires_client_guidance", "capture_record_ids": lineage}


def _persona(speaker, rows, source, events):
    direct = [row for row in rows if row["claim"]["actor"] == speaker
              and _spoken(events[row["claim"]["primary_event_id"]])
              and is_direct_customer(events[row["claim"]["primary_event_id"]], source)]
    actions = []
    described_events = set()
    classified = [row for row in direct if _action_function(row) is not None]
    action_event_ids = {row["claim"]["primary_event_id"] for row in classified}
    for row in classified:
        functions = [f for f in row["claim"]["functions"] if f != "observed_communication"] or ["observed_communication"]
        summary = _describe(row, events) + " Observed capture functions: " + ", ".join(functions) + "."
        refs = row["citations"]
        event_id = row["claim"]["primary_event_id"]
        if event_id not in described_events:
            exchange, exchange_refs = _exchange_context(event_id, action_event_ids, events)
            summary += exchange
            refs = _ordered_refs([*refs, *exchange_refs], events)
            described_events.add(event_id)
        actions.append({"id": _id("action", row["record_id"]), "function": _action_function(row),
                        "summary": summary, "evidence": refs})
    sequences = []
    spoken = [row for row in rows if _spoken(events[row["claim"]["primary_event_id"]])]
    negotiations = [i for i, row in enumerate(spoken) if set(row["claim"]["functions"]) & {"offer", "acceptance", "decline"}]
    if negotiations:
        sequence_rows = spoken[negotiations[0]:negotiations[-1] + 1]
        refs = _ordered_refs([r for row in sequence_rows for r in row["citations"]], events)
        if (len({r["event_id"] for r in refs}) >= 2
                and any(row in direct for row in sequence_rows)):
            sequences.append({"id": _id("sequence", [speaker, [row["record_id"] for row in sequence_rows]]),
                "kind": "resistance_acceptance", "summary": "Observed proposal and response order: " +
                    _turn_narrative(sequence_rows, events) +
                    " No later completion or causal effect is inferred.", "evidence": refs})
    corrections = [i for i, row in enumerate(spoken)
                   if re.search(r"\bcorrect(?:ed|ion|s)?\b", row["claim"]["action"], re.I)]
    for index in corrections:
        # The preceding customer turn supplies the question/assumption being
        # corrected. Include full accepted claims on both turns, not a guessed
        # replacement fact extracted from wording alone.
        correction_event = spoken[index]["claim"]["primary_event_id"]
        previous = next((i for i in range(index - 1, -1, -1)
                         if spoken[i] in direct and
                         spoken[i]["claim"]["primary_event_id"] != correction_event), None)
        if previous is None:
            continue
        previous_event = spoken[previous]["claim"]["primary_event_id"]
        selected = [row for row in spoken
                    if events[previous_event]["ordinal"] <= events[row["claim"]["primary_event_id"]]["ordinal"]
                    <= events[correction_event]["ordinal"]]
        refs = _ordered_refs([r for row in selected for r in row["citations"]], events)
        if len({r["event_id"] for r in refs}) >= 2:
            sequences.append({"id": _id("repair", [speaker, [row["record_id"] for row in selected]]),
                "kind": "clarification_repair", "summary": "Observed question and correction: " +
                    _turn_narrative(selected, events) +
                    " Subsequent understanding is unknown unless explicitly stated.", "evidence": refs})
    # A repeated request is a distinct interaction pattern: the intervening
    # answer must survive with both requests. Require shared content terms and
    # the same explicit request function; this does not diagnose a hidden motive.
    requests = [row for row in direct if "request" in row["claim"]["functions"]]
    ignored = {"the", "of", "a", "an", "to", "for", "and", "on", "in", "with", "is", "are"}
    for left, right in zip(requests, requests[1:]):
        left_terms = set(re.findall(r"\w+", left["claim"]["object"].lower())) - ignored
        right_terms = set(re.findall(r"\w+", right["claim"]["object"].lower())) - ignored
        start, end = spoken.index(left), spoken.index(right)
        if len(left_terms & right_terms) < 2 or start >= end:
            continue
        selected = spoken[start:end + 1]
        refs = _ordered_refs([r for row in selected for r in row["citations"]], events)
        if len({r["event_id"] for r in refs}) >= 2:
            sequences.append({"id": _id("repeated-request", [left["record_id"], right["record_id"]]),
                "kind": "repetition_in_context", "summary": "Customer repeats a request about shared subject terms (" +
                    ", ".join(sorted(left_terms & right_terms)) + "). Observed order: " +
                    _turn_narrative(selected, events) +
                    " The reason for repeating is not inferred.", "evidence": refs})
    return {"id": _id("local-persona", [source["source_id"], speaker]), "speaker": speaker,
            "evidence_status": "observed" if actions else "insufficient_evidence",
            "actions": actions, "sequences": sequences,
            "emotional_expression": {"status": "unknown" if actions else "insufficient_evidence", "observations": []},
            "emotional_changes": [], "unknowns": [
                "Emotion labels, enduring traits, motives and emotional causes have not been reviewed for this card.",
                "Observed actions apply to this conversation only; interface actions are excluded from spoken behaviour.",
                *(["An unfinished question/greeting has no matching final action label; its exact wording remains in the observed exchange without assigning a completed speech act."]
                  if len(classified) != len(direct) else [])]}


def adapt_capture(document, amendments=None, *, canonical_sources=None, domain="sales"):
    """Return existing-engine components plus explicit evidence accounting.

    ``canonical_sources`` is mandatory: the capture export omits authority and
    event-kind fields. Originals must match its source commitments and complete
    projected histories. ``amendments`` is a source-backed development ledger,
    bound by ``input_export_content_sha256`` (or legacy ``input_export_sha256``)
    to the capture's embedded content digest with add/replace/exclude operations; it is
    never interpreted as release approval. Disputed/omission claim records are
    returned under ``unresolved`` and never promoted without an explicit add.
    Canonical spoken turns can also be quoted as attributed exchange context;
    those quotations are not new accepted capture claims.
    The caller supplies returned components/sources to aggregate/build_export.
    """
    errors = validate_capture_export(document)
    if errors:
        raise ValueError("Invalid capture export: " + "; ".join(errors))
    if not isinstance(canonical_sources, list):
        raise ValueError("Original canonical_sources are required to preserve authority")
    if not isinstance(domain, str) or not domain.strip():
        raise ValueError("domain must be nonempty")
    sources = {s["source_id"]: validate_source(s) for s in canonical_sources}
    captured_sources = {s["source_id"]: s for s in document["sources"]}
    if len(sources) != len(canonical_sources) or set(sources) != {s["source_id"] for s in document["sources"]}:
        raise ValueError("Canonical sources must identify every capture source exactly once")
    for captured in document["sources"]:
        source = sources[captured["source_id"]]
        if (source_hash(source) != captured["source_hash"]
                or source_projection(source) != {key: captured[key] for key in ("source_id", "customer_side_speaker_ids", "events")}):
            raise ValueError("Canonical source differs from original capture commitment or projection")
    original = {row["record"]["record_id"]: row["record"] for row in document["records"]}
    effective, current = {}, {}
    for sid, projection in document["current_projection"].items():
        current[sid] = list(projection["accepted_record_ids"])
        effective[sid] = {rid: deepcopy(original[rid]) for rid in current[sid]}
    ledger = []
    if amendments is not None:
        if (not isinstance(amendments, dict)
                or amendments.get("input_export_content_sha256", amendments.get("input_export_sha256")) != document["sha256"]
                or amendments.get("development_exposure") is not True
                or not isinstance(amendments.get("amendments"), list)):
            raise ValueError("Amendments require matching capture hash and explicit development exposure")
        seen = set()
        for amendment in amendments["amendments"]:
            aid, sid, operation = (amendment.get(k) for k in ("amendment_id", "source_id", "operation"))
            if not isinstance(aid, str) or not aid or aid in seen or sid not in sources or operation not in {"add", "replace", "exclude"}:
                raise ValueError("Invalid/duplicate amendment identity, source or operation")
            seen.add(aid)
            if not isinstance(amendment.get("reason"), str) or not amendment["reason"].strip():
                raise ValueError("Amendment requires a review reason")
            if "source_hash" in amendment and amendment["source_hash"] != source_hash(sources[sid]):
                raise ValueError("Amendment original source hash differs")
            target = amendment.get("target_record_id")
            if operation in {"replace", "exclude"} and target not in effective[sid]:
                raise ValueError("Amendment target must be a current same-source accepted record")
            if operation == "add" and target is not None:
                raise ValueError("Add amendment cannot silently supersede a target")
            if operation in {"replace", "exclude"}:
                del effective[sid][target]
            if operation != "exclude":
                record = deepcopy(amendment.get("record"))
                if not isinstance(record, dict):
                    raise ValueError("Add/replace amendment requires a record")
                if "source_id" in record and record["source_id"] != sid:
                    raise ValueError("Amendment record crosses source boundary")
                if "source_hash" in record and record["source_hash"] != source_hash(sources[sid]):
                    raise ValueError("Amendment record original source hash differs")
                record_id = record.get("record_id", "amendment:" + aid)
                if (not isinstance(record_id, str) or not record_id.strip() or record_id in original
                        or any(record_id in values for values in effective.values())):
                    raise ValueError("Amendment requires a distinct new record identity")
                record.update(record_id=record_id, source_id=sid)
                effective[sid][record["record_id"]] = record
            ledger.append(deepcopy(amendment))
    components, accounting, unresolved, transformations = [], [], [], []
    for sid in sorted(sources):
        source = sources[sid]
        events = {e["event_id"]: e for e in source["events"]}
        spans = {span["span_id"]: span for span in captured_sources[sid]["spans"]}
        rows = [_checked_record(row, events, source, spans) for row in effective[sid].values()]
        rows.sort(key=lambda row: (events[row["claim"]["primary_event_id"]]["ordinal"], row["record_id"]))
        focal = source["metadata"].get("customer_side_speaker_ids", [])
        groups = _groups(rows, events, focal)
        needs = []
        for group in groups:
            needs.append({"id": _id("need", [row["record_id"] for row in group]),
                "kind": _need_kind(group, events, not any(n["kind"] == "primary_need" for n in needs)),
                "summary": _turn_narrative(group, events),
                "status": "reported", "related_ids": [],
                "evidence": _ordered_refs([ref for row in group for ref in row["citations"]], events)})
        if needs:
            subjects = list(dict.fromkeys(row["claim"]["object"] for row in rows
                            if row["claim"]["actor"] in focal and _spoken(events[row["claim"]["primary_event_id"]])))
            scenario = {"id": _id("local-scenario", sid),
                "summary": "Observed customer situation: " + "; ".join(subjects[:3] or [rows[0]["claim"]["object"]]) +
                           ". Separate needs retain the full conversation's questions, answers, conditions and recorded outcomes.",
                "needs": needs, "unknowns": ["Correct handling, policy, assessment rules and completion beyond the source require client-approved guidance."]}
            components.append(_component("scenario", scenario, source, [row["record_id"] for row in rows]))
        for speaker in focal:
            persona = _persona(speaker, rows, source, events)
            components.append(_component("persona", persona, source,
                              [row["record_id"] for row in rows if row["claim"]["actor"] == speaker]))
        projection = document["current_projection"][sid]
        history_ids = projection["disputed_record_ids"] + projection["omission_record_ids"]
        if history_ids or projection["superseded_claim_ids"]:
            unresolved.append({"source_id": sid, "status": "history_outside_card_truth",
                "disputed_records": [deepcopy(original[rid]) for rid in projection["disputed_record_ids"]],
                "omission_records": [deepcopy(original[rid]) for rid in projection["omission_record_ids"]],
                "superseded_claim_ids": deepcopy(projection["superseded_claim_ids"])})
        owned = [c for c in components if c["source_id"] == sid]
        accounting.append({"source_id": sid, "source_digest": source_hash(source), "status": "processed" if needs else "excluded",
            "original_accepted_record_ids": current[sid], "effective_record_ids": [row["record_id"] for row in rows],
            "amendment_ids": [a["amendment_id"] for a in ledger if a["source_id"] == sid],
            "disputed_record_ids": projection["disputed_record_ids"], "omission_record_ids": projection["omission_record_ids"],
            "event_count": len(events), "component_ids": [c["object_id"] for c in owned],
            "event_accounting": [{"event_id": event_id,
                "status": "represented_in_card_evidence" if any(ref["event_id"] == event_id for row in rows for ref in row["citations"])
                          else "retained_source_history_without_current_claim",
                "input_record_ids": [row["record_id"] for row in rows if any(ref["event_id"] == event_id for ref in row["citations"])]}
                for event_id in events],
            "scenario_count": sum(c["kind"] == "scenario" for c in owned), "persona_count": sum(c["kind"] == "persona" for c in owned)})
        for component in owned:
            transformations.append({"object_id": component["object_id"], "source_id": sid,
                "input_record_ids": component["capture_record_ids"], "adapter_version": ADAPTER_VERSION,
                "rule": "customer_turn_with_responses" if component["kind"] == "scenario" else "direct_customer_actions_with_canonical_exchange_and_supported_turn_order",
                "canonical_exchange_event_ids": sorted({ref["event_id"]
                    for action in component["payload"].get("actions", []) for ref in action["evidence"]
                    if ref["quote"] == events[ref["event_id"]]["text"]},
                    key=lambda event_id: events[event_id]["ordinal"]),
                "unclassified_action_record_ids": [row["record_id"] for row in rows
                    if component["kind"] == "persona" and row["claim"]["actor"] == component["payload"]["speaker"]
                    and _spoken(events[row["claim"]["primary_event_id"]]) and _action_function(row) is None],
                "child_record_ids": [{"child_id": need["id"], "input_record_ids": [row["record_id"] for row in group]}
                                     for need, group in zip(needs, groups)] if component["kind"] == "scenario" else []})
    return {"sources": [sources[sid] for sid in sorted(sources)], "components": components, "guidance": [],
            "issues": [{"code": "capture_packaging_draft", "message": "Local packaging preserves accepted capture evidence; semantic quality and client guidance remain unverified."}],
            "unresolved": unresolved, "source_accounting": accounting,
            "transformation_provenance": {"adapter_version": ADAPTER_VERSION, "domain": domain,
                "input_export_sha256": document["sha256"], "amendments_sha256": _digest(amendments) if amendments is not None else None,
                "amendment_input_export_file_sha256": amendments.get("input_export_sha256") if amendments is not None else None,
                "amendments": ledger, "components": transformations,
                "grouping": "One source situation; separate customer turns with their responses; source context and interface reports remain separate needs.",
                "limitations": ["No semantic acceptance, cross-source deduplication or training-policy inference.",
                    "Emotion remains unknown; original capture labels remain visible alongside projected question/request classifications.",
                    "One action per customer event includes its surrounding canonical spoken exchange, quoted verbatim without inferred motives or emotions; these quotations are not new accepted capture claims.",
                    "Repeated-request groups use shared subject terms and retain intervening answers; semantic review remains required.",
                    "Conversation grouping preserves evidence but does not independently establish the number of useful training situations."]}}


capture_to_cards = adapt_capture
