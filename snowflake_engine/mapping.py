"""Configuration-only, lossless source mapping. No network or inference calls.

A raw record is one interaction envelope. ``types`` selects its field mapping;
``events`` maps one or more nested collections, or the envelope itself (path null).
No text is clipped and no timestamp, case link, speaker, or approval is inferred.
"""
from copy import deepcopy
from datetime import datetime
import re


class MappingError(ValueError):
    """The configuration cannot describe a safe source mapping."""


def _get(record, path, default=None):
    if path is None:
        return default
    value = record
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return default
        value = value[part]
    return value


def _mapped(record, spec, name, default=None):
    return _get(record, spec.get("fields", {}).get(name), spec.get("defaults", {}).get(name, default))


def _identity(value):
    return isinstance(value, str) and bool(value.strip())


def _timestamp(value):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("timestamp must be a timezone-bearing ISO string or null")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("timestamp must be a timezone-bearing ISO string or null") from exc
    if parsed.utcoffset() is None:
        raise ValueError("timestamp timezone is missing")
    return value


def _redact(text, config):
    for rule in config.get("rules", []):
        text = re.sub(rule["pattern"], rule.get("replacement", "[REDACTED]"), text,
                      flags=re.IGNORECASE if rule.get("ignore_case", False) else 0)
    return text


def _project_text(text, row, collection):
    """Add only explicitly configured context fields to the permitted evidence."""
    # JSON objects have no defined key order; match canonical warehouse transport.
    for label, path in sorted(collection.get("text_fields", {}).items()):
        value = _get(row, path)
        if value is not None and not isinstance(value, str):
            raise ValueError(f"projected context field {label} must be text or null")
        text += "\n" + label + ": " + (value if value is not None else "[unknown: not supplied]")
    return text


def _issue(index, raw_id, code, reason, raw_record, **extra):
    return {"record_index": index, "raw_id": raw_id, "code": code, "reason": reason,
            "raw_record": deepcopy(raw_record), **extra}


def _validate_mapping(mapping):
    if not isinstance(mapping, dict) or not isinstance(mapping.get("types"), dict):
        raise MappingError("mapping.types must be an object")
    for kind, spec in mapping["types"].items():
        if not isinstance(spec, dict) or not isinstance(spec.get("events"), list):
            raise MappingError(f"mapping type {kind} requires an events array")
        for events in spec["events"]:
            fields = events.get("fields", {})
            if not isinstance(fields, dict) or "text" not in fields or "event_id" not in fields:
                raise MappingError(f"mapping type {kind} events require text and event_id field paths")
            projection = events.get("text_fields", {})
            if not isinstance(projection, dict) or any(not _identity(label) for label in projection):
                raise MappingError("text_fields must map nonempty labels to field paths")
            for path in [*fields.values(), *projection.values()]:
                if path is not None and (not isinstance(path, str) or not path):
                    raise MappingError("field paths must be nonempty strings or null")
    for rule in mapping.get("redaction", {}).get("rules", []):
        try:
            re.compile(rule["pattern"])
        except (KeyError, TypeError, re.error) as exc:
            raise MappingError("invalid redaction rule") from exc


def normalize(raw_records, mapping, client_id):
    """Return canonical sources, issues and complete input/event accounting.

    Bad source rows are excluded with a retained raw record. Bad events quarantine
    their whole source, so extraction never receives a silently incomplete input.
    Missing ordinals use supplied collection order; missing times stay unknown.
    If multiple collections exist, their configured ordinals must be unique and
    determine their combined order. A mapped note always has attributed authority.
    """
    _validate_mapping(mapping)
    if not _identity(client_id):
        raise MappingError("client_id must be a nonempty string")
    if not isinstance(raw_records, (list, tuple)):
        raise MappingError("raw_records must be a list or tuple")
    sources, issues, records = [], [], []
    seen_sources = set()
    total_events = accepted_events = excluded_events = 0
    redaction = mapping.get("redaction", {})
    for index, raw in enumerate(raw_records):
        record_issue_start = len(issues)
        raw_id = _get(raw, mapping.get("raw_id_field", "record_id"))
        source_id = None
        supplied_count = 0
        if not isinstance(raw, dict):
            issues.append(_issue(index, raw_id, "malformed_record", "source row must be an object", raw))
        else:
            record_type = _get(raw, mapping.get("record_type_field", "record_type"))
            spec = mapping["types"].get(record_type) if isinstance(record_type, str) else None
            if spec is None:
                issues.append(_issue(index, raw_id, "unsupported_record_type", "no mapping for record type", raw))
            else:
                source_id = _mapped(raw, spec, "source_id")
                if not _identity(source_id):
                    issues.append(_issue(index, raw_id, "missing_source_id", "source identity is missing", raw))
                elif source_id in seen_sources:
                    issues.append(_issue(index, raw_id, "duplicate_source_id", "source identity is repeated", raw))
                for link in spec.get("required_links", []):
                    if not _identity(_get(raw, link)):
                        issues.append(_issue(index, raw_id, "unlinked_record", f"required link {link} is missing", raw))
                metadata = {name: deepcopy(_get(raw, path)) for name, path in spec.get("metadata", {}).items()}
                metadata.update(deepcopy(spec.get("metadata_defaults", {})))
                metadata["redaction_reviewed"] = redaction.get("reviewed") is True
                events, seen_events, supplied_ordinals = [], set(), set()
                for collection in spec["events"]:
                    path = collection.get("path")
                    rows = [raw] if path is None else _get(raw, path, [])
                    if not isinstance(rows, list):
                        issues.append(_issue(index, raw_id, "malformed_collection", f"event collection {path} must be an array", rows))
                        continue
                    for row_index, row in enumerate(rows):
                        supplied_count += 1
                        try:
                            if not isinstance(row, dict):
                                raise ValueError("event must be an object")
                            event_id = _mapped(row, collection, "event_id")
                            if not _identity(event_id) or event_id in seen_events:
                                raise ValueError("event identity is missing or repeated")
                            seen_events.add(event_id)
                            original = _mapped(row, collection, "original_text")
                            text = _mapped(row, collection, "text")
                            if not isinstance(text, str) or (original is not None and not isinstance(original, str)):
                                raise ValueError("event text and original text must be strings")
                            original = text if original is None else original
                            original = _project_text(original, row, collection)
                            text = _project_text(text, row, collection)
                            supplied_status = _mapped(row, collection, "redaction_status")
                            if supplied_status is None:
                                text = _redact(text, redaction)
                                status = ("not_reviewed" if not redaction.get("reviewed", False) else
                                          "redacted" if text != original else "unchanged")
                            else:
                                # Projected source fields get the same configured redaction as the body.
                                permitted = _redact(text, redaction)
                                status = "redacted" if permitted != text and supplied_status == "unchanged" else supplied_status
                                text = permitted
                                if not isinstance(status, str) or not status:
                                    raise ValueError("redaction_status must be a nonempty string")
                            ordinal = _mapped(row, collection, "ordinal", supplied_count - 1)
                            if isinstance(ordinal, bool) or not isinstance(ordinal, int) or ordinal < 0:
                                raise ValueError("ordinal must be a nonnegative integer")
                            if ordinal in supplied_ordinals:
                                raise ValueError("ordinal is repeated across source events")
                            supplied_ordinals.add(ordinal)
                            timestamp = _timestamp(_mapped(row, collection, "timestamp"))
                            kind = _mapped(row, collection, "kind", collection.get("kind", "message"))
                            authority = _mapped(row, collection, "authority", "unknown")
                            if kind == "note":
                                authority = "attributed_note"
                            elif kind == "case_context":
                                authority = "case_report"
                            event = {"event_id": event_id, "ordinal": ordinal, "timestamp": timestamp,
                                     "timing_status": _mapped(row, collection, "timing_status", "known" if timestamp else "unknown"),
                                     "kind": kind, "role": _mapped(row, collection, "role", "unknown") or "unknown",
                                     "speaker": _mapped(row, collection, "speaker", "unknown") or "unknown",
                                     "authority": authority or "unknown", "text": text, "original_text": original,
                                     "redaction_status": status}
                            for field in ("kind", "role", "speaker", "authority"):
                                if not _identity(event[field]):
                                    raise ValueError(f"event {field} must be a nonempty string")
                            if event["timing_status"] not in {"known", "unknown"}:
                                raise ValueError("timing_status must be known or unknown")
                            if event["timing_status"] == "known" and timestamp is None:
                                raise ValueError("known timing requires a supplied timestamp")
                            if status not in {"unchanged", "redacted", "supplied_redacted", "not_reviewed"}:
                                raise ValueError("unsupported redaction_status")
                            events.append(event)
                        except (ValueError, TypeError) as exc:
                            issues.append(_issue(index, raw_id, "malformed_event", str(exc), row,
                                                 source_id=source_id, collection=path, event_index=row_index))
                if not supplied_count:
                    issues.append(_issue(index, raw_id, "empty_source", "source contains no evidence events", raw))
                guidance = []
                for item in _get(raw, spec.get("guidance_path"), []) or []:
                    if not isinstance(item, dict) or item.get("authority") not in {"fictional", "client_approved"} or not isinstance(item.get("text"), str):
                        issues.append(_issue(index, raw_id, "malformed_guidance", "guidance needs explicit authority and text", item))
                    else:
                        guidance.append(deepcopy(item))
                if len(issues) == record_issue_start:
                    seen_sources.add(source_id)
                    sources.append({"source_id": source_id, "client_id": client_id,
                                    "source_type": spec.get("source_type", record_type), "metadata": metadata,
                                    "events": sorted(events, key=lambda event: event["ordinal"]), "guidance": guidance})
                    accepted_events += supplied_count
        for issue in issues[record_issue_start:]:
            issue["raw_source"] = deepcopy(raw)
        status = "mapped" if len(issues) == record_issue_start else "excluded"
        total_events += supplied_count
        if status == "excluded":
            excluded_events += supplied_count
        records.append({"record_index": index, "raw_id": raw_id, "source_id": source_id,
                        "status": status, "event_count": supplied_count,
                        "issue_codes": [issue["code"] for issue in issues[record_issue_start:]]})
    return {"sources": sources, "issues": issues, "accounting": {
        "input_record_count": len(raw_records), "mapped_source_count": len(sources),
        "excluded_record_count": len(raw_records) - len(sources), "input_event_count": total_events,
        "mapped_event_count": accepted_events, "excluded_event_count": excluded_events,
        "omitted_event_count": 0, "body_clipping": False, "records": records}}
