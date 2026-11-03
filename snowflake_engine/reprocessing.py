"""Pure retained-response processing: reorder references, never source or assertions."""
from copy import deepcopy

from . import contracts, versioning

EVIDENCE_ORDERING_VERSION = 'source_ordinal_v1'


def validate_evidence_ordering(config):
    """Native ordering is deliberately restricted to a cache-only split-v3 run."""
    mode = config.get('evidence_ordering')
    if mode is None:
        return
    if mode != EVIDENCE_ORDERING_VERSION:
        raise ValueError('evidence_ordering must be source_ordinal_v1 or omitted')
    if (config.get('extraction_mode') != 'split_v3'
            or config.get('reprocess_retained') is not True
            or config.get('enable_model_calls', False) is not False):
        raise ValueError('evidence_ordering requires split_v3, reprocess_retained=true and enable_model_calls=false')


def order_evidence(value, source, kind):
    """Copy JSON and stably sort only schema-recognized, well-shaped citation arrays.

    Unknown event IDs or malformed references leave their entire array untouched.
    Quotes and attribution are never repaired; unchanged validators still reject
    them. Stable sorting preserves duplicates and same-event quote order.
    """
    if kind not in contracts.CAPTURE_SCHEMAS_V3:
        raise ValueError('kind must be scenario or persona')
    source = contracts.validate_source(source)
    positions = {event['event_id']: event['ordinal'] for event in source['events']}
    result = deepcopy(value)
    changes, skipped = [], []

    def visit(node, schema, path):
        if schema.get('type') == 'array' and isinstance(node, list):
            item_schema = schema.get('items', {})
            if item_schema == contracts.EVIDENCE_SCHEMA:
                if any(contracts.shape_errors(ref, contracts.EVIDENCE_SCHEMA, path)
                       or ref['event_id'] not in positions for ref in node):
                    skipped.append({'path': path, 'reason': 'malformed_reference_or_unknown_event'})
                    return
                before = deepcopy(node)
                node.sort(key=lambda ref: positions[ref['event_id']])
                if node != before:
                    changes.append({'path': path,
                                    'original_event_ids': [ref['event_id'] for ref in before],
                                    'processed_event_ids': [ref['event_id'] for ref in node]})
            else:
                for index, child in enumerate(node):
                    visit(child, item_schema, path + '[' + str(index) + ']')
        elif schema.get('type') == 'object' and isinstance(node, dict):
            for key, child_schema in schema.get('properties', {}).items():
                if key in node:
                    visit(node[key], child_schema, path + '.' + key)

    visit(result, contracts.CAPTURE_SCHEMAS_V3[kind], kind)
    return result, {'changed_arrays': changes, 'skipped_arrays': skipped}


def processing_record(source, raw_responses, processed_responses, original_results, changes):
    """An immutable content-addressed receipt; full originals remain inspectable."""
    record = {'processing_version': EVIDENCE_ORDERING_VERSION,
              'source_hash': contracts.source_hash(source),
              'original_raw_responses': deepcopy(raw_responses),
              'processed_raw_responses': deepcopy(processed_responses),
              'original_results': deepcopy(original_results),
              'changes': deepcopy(changes), 'semantic_review_performed': False,
              'inference_calls': 0}
    record['logical_id'] = 'processing-' + versioning.digest({
        'source_hash': record['source_hash'], 'raw_responses': raw_responses})
    record['revision_id'] = 'processing-revision-' + versioning.digest(record)
    return record
