"""Versioned single-fence response framing, selected explicitly by the caller."""
import hashlib
import json
import math
import re

from .capture_contract import normalize_message as BASELINE

FRAMING_VERSION = 'single-fence-v2'
VERSION = FRAMING_VERSION
FRAMING_ERROR = 'Ambiguous additional JSON/fence outside delimited body'
MARKER = '[DATASET INTERFACE ACTION; NOT SPOKEN DIALOGUE]'
FENCE = re.compile(r'(?m)^```(?:json)?\r?\n([\s\S]*?)\r?\n```[ \t]*(?=\r?\n|$)')
INLINE = re.compile(r'`([^`\r\n]*)`')


def sha(raw):
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def strict_citation(raw):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError('Duplicate citation field')
            value[key] = item
        return value

    def nonfinite(_):
        raise ValueError('Nonfinite citation number')

    value = json.loads(raw, object_pairs_hook=unique, parse_constant=nonfinite)
    if not isinstance(value, dict) or set(value) != {'action', 'data'}:
        return False
    if value['action'] in ('accept', 'reject', 'quit'):
        return value['data'] is None
    if value['action'] != 'offer':
        return False
    data = value['data']
    if not isinstance(data, dict) or set(data) != {'price', 'sides'}:
        return False
    price, sides = data['price'], data['sides']
    finite_price = type(price) is int or (type(price) is float and math.isfinite(price))
    return finite_price and isinstance(sides, str) and not any(token in sides for token in '{}[]`')


def normalize_message(message, stage):
    """Try frozen baseline first; only its precise external framing error can extend."""
    try:
        return BASELINE(message, stage)
    except ValueError as error:
        if str(error) != FRAMING_ERROR:
            raise
    if message.count('```') != 2:
        raise ValueError('Candidate requires exactly one outer fence')
    matches = list(FENCE.finditer(message))
    if len(matches) != 1:
        raise ValueError('Candidate requires exactly one standalone fenced body')
    match = matches[0]
    external = message[:match.start()] + message[match.end():]
    citations = []

    def ignore_inline(found):
        text = found.group(1)
        if text == MARKER:
            citations.append('dataset_interface_marker')
        elif any(token in text for token in '{}[]'):
            if not strict_citation(text):
                raise ValueError('Candidate rejects non-allowlisted structured inline citation')
            citations.append('dataset_interface_action')
        # Unstructured single-line labels/IDs are ordinary prose, never instructions.
        return ''

    residual = INLINE.sub(ignore_inline, external)
    if any(token in residual for token in '{}[]`'):
        raise ValueError('Candidate rejects remaining external structure or backticks')
    body = match.group(1)  # Exact slice: no strip, repair, JSON reserialization or rewrite.
    result = BASELINE(body, stage)
    return dict(result,
        normalizations=[VERSION + ':ignored_allowlisted_inline_source_citations',
                        VERSION + ':removed_external_prose_and_single_outer_fence']
                       + result['normalizations'],
        framing_provenance={'candidate_version': VERSION, 'opt_in': True,
            'baseline_error': FRAMING_ERROR, 'raw_sha256': sha(message),
            'body_sha256': sha(body), 'body_bytes_changed': False,
            'ignored_structured_citations': citations, 'new_provider_calls': 0})
