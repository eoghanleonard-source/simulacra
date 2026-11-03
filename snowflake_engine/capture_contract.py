"""Deterministic source projection, span catalogue and strict item validation."""
import hashlib
import json
import re


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


CLAIM_FIELDS = {'primary_event_id', 'kind', 'actor', 'recipient', 'action', 'object',
                'conditions', 'time', 'certainty', 'polarity', 'status', 'functions', 'evidence_span_ids'}
FUNCTIONS = {'report', 'offer', 'question', 'hypothetical', 'request', 'acknowledgement',
             'acceptance', 'decline', 'observed_communication', 'unknown'}
ENUMS = {'kind': {'issue', 'communication'},
         'certainty': {'asserted', 'tentative', 'question', 'unknown'},
         'polarity': {'positive', 'negative', 'unknown'},
         'status': {'observed', 'proposed', 'requested', 'agreed', 'declined', 'reported_completed', 'unknown'}}


def source_projection(source):
    """Only focal speaker metadata is allowed; original_text/guidance remain excluded."""
    metadata = source.get('metadata') if isinstance(source.get('metadata'), dict) else {}
    focal = source.get('customer_side_speaker_ids', metadata.get('customer_side_speaker_ids', []))
    return {'source_id': source['source_id'], 'customer_side_speaker_ids': list(focal), 'events': [
        {k: event[k] for k in ('event_id', 'ordinal', 'speaker', 'role', 'text')}
        for event in source['events']]}


def catalogue(source):
    """Partition exact text into contiguous sentence/350-character spans; never rewrite it."""
    spans, events = {}, []
    for event in source['events']:
        text, start, parts = event['text'], 0, []
        while start < len(text):
            limit = min(start + 350, len(text))
            boundaries = [m.end() for m in re.finditer(r'(?<=[.!?])\s+|\n+', text[start:limit])]
            end = start + boundaries[-1] if boundaries else limit
            if end < len(text) and not boundaries:
                space = text.rfind(' ', start, end)
                if space > start:
                    end = space + 1
            span_id = event['event_id'] + 's' + str(len(parts) + 1).zfill(2)
            row = {'span_id': span_id, 'event_id': event['event_id'], 'start': start,
                   'end': end, 'quote': text[start:end]}
            spans[span_id] = row
            parts.append([span_id, text[start:end]])
            start = end
        events.append({'event_id': event['event_id'], 'speaker': event['speaker'],
                       'role': event['role'], 'parts': parts})
    return {'source_id': source['source_id'],
            'customer_side_speaker_ids': source['customer_side_speaker_ids'], 'events': events}, spans


def validate_catalogue(source, spans):
    for event in source['events']:
        rows = [s for s in spans.values() if s['event_id'] == event['event_id']]
        cursor = 0
        for span in sorted(rows, key=lambda x: x['start']):
            if (span['start'] != cursor or span['end'] <= cursor
                    or event['text'][span['start']:span['end']] != span['quote']):
                raise ValueError('Span catalogue no longer slices exact source text')
            cursor = span['end']
        if cursor != len(event['text']):
            raise ValueError('Span catalogue omits source text')


def references(value, spans, *, nonempty=False):
    if (not isinstance(value, list) or (nonempty and not value)
            or any(not isinstance(x, str) or x not in spans for x in value)
            or len(set(value)) != len(value)):
        raise ValueError('Evidence must use distinct frozen span IDs')


def claim(value, source, spans, primary):
    if not isinstance(value, dict) or set(value) != CLAIM_FIELDS:
        raise ValueError('Claim fields differ from the frozen atomic contract')
    if value['primary_event_id'] not in primary:
        raise ValueError('Claim is outside this section primary ownership')
    speakers = {e['speaker'] for e in source['events']} | {'unknown'}
    if value['actor'] not in speakers or value['recipient'] not in speakers:
        raise ValueError('Actor/recipient must be a stable source speaker ID or unknown')
    if value['kind'] == 'communication' and value['actor'] not in source['customer_side_speaker_ids']:
        raise ValueError('Communication target must be an explicitly designated customer-side speaker')
    for key in ('action', 'object'):
        if not isinstance(value[key], str) or not 1 <= len(value[key]) <= 600:
            raise ValueError(key + ' must contain 1..600 characters')
    if (not isinstance(value['conditions'], list) or len(value['conditions']) > 8
            or any(not isinstance(x, str) or not 1 <= len(x) <= 400 for x in value['conditions'])):
        raise ValueError('Conditions must be a list of at most eight bounded strings')
    if value['time'] is not None and (not isinstance(value['time'], str) or not 1 <= len(value['time']) <= 300):
        raise ValueError('Time must be null or a bounded source-relative statement')
    for key, choices in ENUMS.items():
        if value[key] not in choices:
            raise ValueError('Invalid ' + key)
    functions = value['functions']
    if (not isinstance(functions, list) or not 1 <= len(functions) <= 4
            or any(not isinstance(x, str) or x not in FUNCTIONS for x in functions)
            or len(set(functions)) != len(functions)):
        raise ValueError('Functions must contain 1..4 distinct source speech-act labels')
    references(value['evidence_span_ids'], spans, nonempty=True)
    if not any(spans[s]['event_id'] == value['primary_event_id'] for s in value['evidence_span_ids']):
        raise ValueError('Primary event must supply evidence')
    return dict(value, claim_id='cl-' + digest({'source': source['source_id'], 'claim': value})[:24])


def coverage(value, primary):
    if not isinstance(value, list) or len(value) != len(primary):
        raise ValueError('Coverage must account for every primary event exactly once')
    seen = []
    for row in value:
        if (not isinstance(row, dict) or set(row) != {'event_id', 'disposition', 'reason'}
                or row['disposition'] not in {'claim', 'context_only', 'no_material'}
                or not isinstance(row['reason'], str) or not 1 <= len(row['reason']) <= 400):
            raise ValueError('Invalid event coverage record')
        seen.append(row['event_id'])
    if len(set(seen)) != len(seen) or set(seen) != set(primary):
        raise ValueError('Coverage duplicates or omits a primary event')


def parse_claims(payload, source, spans, primary, *, changes=False, known_ids=()):
    """Quarantine a bad neighbour without discarding independently valid claims."""
    valid, errors = [], []
    expected = {'changes', 'coverage', 'unresolved'} if changes else {'claims', 'coverage'}
    if not isinstance(payload, dict) or set(payload) != expected:
        return [], [{'index': None, 'reason': 'Invalid response object/fields'}]
    try:
        coverage(payload['coverage'], primary)
    except (ValueError, TypeError) as error:
        errors.append({'index': None, 'reason': str(error)})
    rows = payload['changes' if changes else 'claims']
    if not isinstance(rows, list) or len(rows) > 40:
        return [], errors + [{'index': None, 'reason': 'Require at most 40 claim records'}]
    ids, superseded = set(), set()
    for index, row in enumerate(rows):
        try:
            previous = None
            if changes:
                if not isinstance(row, dict) or set(row) != {'supersedes', 'claim'}:
                    raise ValueError('Change requires supersedes and claim')
                previous = row['supersedes']
                if previous is not None and (previous not in known_ids or previous in superseded):
                    raise ValueError('Unknown or duplicate superseded claim')
                row = row['claim']
            parsed = claim(row, source, spans, primary)
            if parsed['claim_id'] in ids:
                raise ValueError('Duplicate atomic claim')
            ids.add(parsed['claim_id'])
            if previous is not None:
                superseded.add(previous)
            valid.append({'claim': parsed, 'supersedes': previous})
        except (ValueError, TypeError, KeyError) as error:
            errors.append({'index': index, 'reason': str(error)})
    if changes:
        unresolved = payload['unresolved']
        if not isinstance(unresolved, list):
            errors.append({'index': None, 'reason': 'Invalid unresolved list'})
        else:
            for row in unresolved:
                try:
                    if (not isinstance(row, dict) or set(row) != {'reason', 'evidence_span_ids'}
                            or not isinstance(row['reason'], str) or not 1 <= len(row['reason']) <= 600):
                        raise ValueError('Invalid unresolved record')
                    references(row['evidence_span_ids'], spans)
                except (ValueError, TypeError) as error:
                    errors.append({'index': None, 'reason': str(error)})
    return valid, errors


def _base_parse_review(payload, candidates, source, spans, primary):
    """Decisions are strictly checked per item; missing/invalid judgments abstain."""
    decisions, errors = {}, []
    if not isinstance(payload, dict) or set(payload) != {'source_pass', 'decisions', 'omissions'}:
        return {}, [], [{'reason': 'Invalid reviewer response fields'}]
    first = payload['source_pass']
    source_pass_valid = False
    try:
        if not isinstance(first, dict) or set(first) != {'read_event_ids', 'material_findings'}:
            raise ValueError('Missing independent source-first scan')
        if first['read_event_ids'] != primary:
            raise ValueError('Source-first scan must list every primary event exactly once in source order')
        findings = first['material_findings']
        if not isinstance(findings, list) or len(findings) > 12:
            raise ValueError('Source-first scan permits at most twelve compact material findings')
        for row in findings:
            if (not isinstance(row, dict) or set(row) != {'evidence_span_ids', 'meaning'}
                    or not isinstance(row['meaning'], str) or not 1 <= len(row['meaning']) <= 160):
                raise ValueError('Invalid compact source-first material finding')
            references(row['evidence_span_ids'], spans, nonempty=True)
            if len(row['evidence_span_ids']) > 4:
                raise ValueError('Compact source scan permits at most four span anchors per finding')
        source_pass_valid = True
    except (ValueError, TypeError, KeyError) as error:
        errors.append({'reason': str(error)})
    ids = {c['claim']['claim_id'] for c in candidates}
    rows = payload['decisions']
    if not isinstance(rows, list):
        rows = []
        errors.append({'reason': 'Invalid decisions list'})
    duplicates = set()
    for row in rows:
        try:
            if (not isinstance(row, dict) or set(row) != {'claim_id', 'verdict', 'reason', 'evidence_span_ids', 'counter_span_ids'}
                    or row['claim_id'] not in ids or row['verdict'] not in {'accept', 'reject', 'unresolved'}
                    or not isinstance(row['reason'], str) or not 1 <= len(row['reason']) <= 800):
                raise ValueError('Invalid item judgment')
            references(row['evidence_span_ids'], spans, nonempty=row['verdict'] == 'accept')
            references(row['counter_span_ids'], spans)
            if row['claim_id'] in decisions:
                duplicates.add(row['claim_id'])
                raise ValueError('Duplicate item judgment')
            decisions[row['claim_id']] = row
        except (ValueError, TypeError, KeyError) as error:
            errors.append({'reason': str(error)})
    for cid in duplicates:
        decisions.pop(cid, None)
    omissions = []
    rows = payload['omissions']
    if not isinstance(rows, list):
        errors.append({'reason': 'Invalid omissions list'})
    else:
        for row in rows:
            try:
                if (not isinstance(row, dict) or set(row) != {'claim', 'reason'}
                        or not isinstance(row['reason'], str) or not 1 <= len(row['reason']) <= 600):
                    raise ValueError('Invalid omission proposal')
                omissions.append({'claim': claim(row['claim'], source, spans, primary), 'reason': row['reason']})
            except (ValueError, TypeError, KeyError) as error:
                errors.append({'reason': str(error)})
    # Without a valid source pass the review cannot establish source-first review.
    if not source_pass_valid:
        decisions = {}
        omissions = []
    return decisions, omissions, errors

# Vendored accepted atomic claim contract; original SHA256 is retained below.
ORIGINAL_CLAIM_SHA256 = 'b0a34a79b67f8b0848d9cbc9b75dcaf2d91aa0cfd340393c1196371666900bae'


def parse_review(payload, candidates, source, spans, primary):
    """Accepted v4/v8/v9 controls, flattened without historical file dependencies."""
    from copy import deepcopy
    clean, errors = deepcopy(payload), []
    try:
        if (not isinstance(clean, dict) or set(clean) != {'source_pass', 'decisions', 'omissions'}
                or next(iter(clean), None) != 'source_pass'
                or not isinstance(clean['decisions'], list) or not isinstance(clean['omissions'], list)):
            raise ValueError('Review must begin with source_pass and contain decision/omission lists')
        first = clean['source_pass']
        if not isinstance(first, dict) or set(first) != {'read_event_ids', 'material_findings'}:
            raise ValueError('Missing independent source-first scan')
        if first['read_event_ids'] != primary:
            raise ValueError('Source-first scan must list every primary event exactly once in source order')
        findings = first['material_findings']
        if not isinstance(findings, list) or len(findings) > 12:
            raise ValueError('Source-first scan permits at most twelve compact material findings')
        # All anchors are checked before any auxiliary length/count quarantine.
        for index, row in enumerate(findings):
            if not isinstance(row, dict) or set(row) != {'evidence_span_ids', 'meaning'}:
                raise ValueError('Invalid source-first finding fields/anchors at index ' + str(index))
            references(row['evidence_span_ids'], spans, nonempty=True)
        good = []
        for index, row in enumerate(findings):
            if (len(row['evidence_span_ids']) > 4 or not isinstance(row['meaning'], str)
                    or not 1 <= len(row['meaning']) <= 160):
                errors.append({'component': 'source_pass.material_findings', 'index': index,
                    'reason': 'Invalid compact source-first material finding',
                    'disposition': 'quarantined_auxiliary_finding_not_an_accepted_claim'})
            else:
                good.append(row)
        first['material_findings'] = good
    except (ValueError, TypeError, KeyError) as error:
        return {}, [], errors + [{'component': 'source_pass', 'reason': str(error)}]
    flags, good, seen, duplicates = {}, [], set(), set()
    for index, row in enumerate(clean['decisions']):
        cid = row.get('claim_id') if isinstance(row, dict) else None
        if isinstance(cid, str):
            if cid in seen:
                duplicates.add(cid)
            seen.add(cid)
        if (not isinstance(row, dict) or not isinstance(cid, str)
                or type(row.get('request_repair')) is not bool
                or not isinstance(row.get('reason'), str) or not 1 <= len(row['reason']) <= 240
                or not isinstance(row.get('evidence_span_ids'), list) or len(row['evidence_span_ids']) > 4
                or not isinstance(row.get('counter_span_ids'), list) or len(row['counter_span_ids']) > 4):
            errors.append({'component': 'decisions', 'index': index,
                           'reason': 'Invalid compact decision; candidate abstains'})
            continue
        flags[cid] = row.pop('request_repair')
        good.append(row)
    clean['decisions'] = good
    decisions, omissions, other = _base_parse_review(clean, candidates, source, spans, primary)
    errors.extend(other)
    for cid in duplicates:
        decisions.pop(cid, None)
        errors.append({'component': 'decisions', 'claim_id': cid, 'reason': 'Duplicate item judgment'})
    for candidate in candidates:
        raw = candidate['claim']
        cid = raw['claim_id']
        invalid = candidate.get('invalid_record', False)
        try:
            if claim({k: v for k, v in raw.items() if k != 'claim_id'}, source, spans, primary) != raw:
                invalid = True
        except (ValueError, TypeError, KeyError):
            invalid = True
        decision = decisions.get(cid)
        if decision is not None:
            decision['request_repair'] = flags.get(cid, False)
            if invalid and decision['verdict'] == 'accept':
                decision.update(verdict='unresolved',
                    reason='Invalid retained claim requires an explicit new semantic version and source check',
                    request_repair=False)
                errors.append({'claim_id': cid, 'reason': 'Attempt to accept invalid retained claim blocked'})
        else:
            decisions[cid] = {'claim_id': cid, 'verdict': 'unresolved',
                'reason': 'Missing/invalid source-first decision', 'evidence_span_ids': [],
                'counter_span_ids': [], 'request_repair': False}
    return decisions, omissions, errors


def normalize_message(message, stage):
    """Accepted v5 wire normalization: one finite body, no body-byte repairs."""
    expected = ({'source_pass', 'decisions', 'omissions'}
                if stage in {'review', 'repair_verify', 'reconcile_verify'} else
                {'claims', 'coverage'} if stage == 'worker' else
                {'changes', 'coverage', 'unresolved'} if stage in {'repair', 'reconcile'} else None)
    if expected is None:
        raise ValueError('Unknown response stage')

    def strict(raw):
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError('Require nonempty strict JSON response text')
        body, normalizations = raw.strip(), []
        if body.startswith('```'):
            match = re.fullmatch(r'```(json)?\r?\n([\s\S]*?)\r?\n```', body)
            if match is None or re.search(r'(?m)^[ \t]*```', match.group(2)):
                raise ValueError('Only one exact outer JSON code fence is accepted')
            body = match.group(2)
            normalizations.append('removed_single_outer_json_fence' if match.group(1)
                                  else 'removed_single_outer_code_fence')
        def unique(pairs):
            value = {}
            for key, item in pairs:
                if key in value:
                    raise ValueError('Duplicate JSON field: ' + key)
                value[key] = item
            return value
        def nonfinite(_):
            raise ValueError('Nonfinite JSON number')
        value = json.loads(body, object_pairs_hook=unique, parse_constant=nonfinite)
        canonical(value)
        return value, normalizations
    try:
        payload, normalizations = strict(message)
    except (ValueError, TypeError):
        if not isinstance(message, str) or message.count('```') != 2:
            raise ValueError('Require exactly one unambiguous JSON body/fence')
        match = re.search(r'(?m)^```(?:json)?\r?\n[\s\S]*?\r?\n```[ \t]*(?=\r?\n|$)', message)
        if match is None:
            raise ValueError('Require one clearly delimited standalone JSON fence')
        external = message[:match.start()] + message[match.end():]
        if any(token in external for token in ('{', '}', '[', ']', '```')):
            raise ValueError('Ambiguous additional JSON/fence outside delimited body')
        payload, normalizations = strict(match.group(0).rstrip())
        normalizations = ['removed_external_prose_around_single_json_fence_v5'] + normalizations
    if not isinstance(payload, dict) or set(payload) != expected:
        raise ValueError('Response body differs from frozen stage object fields')
    return {'payload': payload, 'parse_error': None, 'normalizations': normalizations}


def record_digest(record):
    """Legacy v9 record identity hashes all body fields except record_id."""
    return digest({key: value for key, value in record.items() if key != 'record_id'})


def validate_record_id(record):
    if not isinstance(record, dict) or record.get('record_id') != 'item-' + record_digest(record):
        raise ValueError('Immutable capture record identity differs from its complete body')
    return True
