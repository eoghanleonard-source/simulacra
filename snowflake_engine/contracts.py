"""Standalone engine contracts: structure, custody and labels, never semantic proof.

Pure validation is adapted from training_sim.capture_contracts and the reviewed
transcript-behaviour-02 definition-only codebook. No runtime dependency on either.
"""
from copy import deepcopy
import hashlib
import json

VERSIONS = {"code": "engine-extraction-v1", "prompt": "engine-extraction-prompt-v2",
            "schema": "engine-extraction-schema-v1", "codebook": "tb02-codebook-1.0-draft"}
VALIDATION_SCOPE = "shape_vocabulary_exact_attribution_order_only; semantic_review_not_performed"
FRAMEWORK_SOURCES = {
    "actions": "https://dit.uvt.nl/",
    "iso_scope": "https://aclanthology.org/2020.lrec-1.69/",
    "emotion": "https://aclanthology.org/2020.acl-main.372/",
    "emotion_labels": "https://raw.githubusercontent.com/google-research/google-research/master/goemotions/data/emotions.txt",
    "sequence_context": "https://www.tandfonline.com/doi/abs/10.1080/08351813.2014.925663",
}
# Selected DIT++ concepts, local CA-informed sequences, GoEmotions vocabulary.
# No ISO certification, exhaustive CA taxonomy, or validated emotion detector.
FRAMEWORK_DEFINITIONS = {'action_functions': [{'label': 'ask_information',
                       'official_name': 'Question',
                       'definition': 'Seeks information the addressee is assumed to know.',
                       'source_url': 'https://dit.uvt.nl/'},
                      {'label': 'check_information',
                       'official_name': 'Check Question',
                       'definition': 'Seeks a truth judgment while tentatively expecting '
                                     'confirmation.',
                       'source_url': 'https://dit.uvt.nl/'},
                      {'label': 'provide_information',
                       'official_name': 'Inform',
                       'definition': 'Presents information as correct for the addressee.',
                       'source_url': 'https://dit.uvt.nl/'},
                      {'label': 'answer',
                       'official_name': 'Answer',
                       'definition': 'Supplies information sought by the addressee.',
                       'source_url': 'https://dit.uvt.nl/'},
                      {'label': 'confirm',
                       'official_name': 'Confirm',
                       'definition': 'Answers by supporting the proposition under check.',
                       'source_url': 'https://dit.uvt.nl/'},
                      {'label': 'disconfirm',
                       'official_name': 'Disconfirm',
                       'definition': 'Answers by rejecting the proposition under check.',
                       'source_url': 'https://dit.uvt.nl/'},
                      {'label': 'agree',
                       'official_name': 'Agreement',
                       'definition': 'Supports another contribution’s propositional content.',
                       'source_url': 'https://dit.uvt.nl/'},
                      {'label': 'disagree',
                       'official_name': 'Disagreement',
                       'definition': 'Opposes another contribution’s propositional content.',
                       'source_url': 'https://dit.uvt.nl/'},
                      {'label': 'correct_information',
                       'official_name': 'Correction',
                       'definition': 'Replaces the addressee’s presumed mistaken information.',
                       'source_url': 'https://dit.uvt.nl/'},
                      {'label': 'request_action',
                       'official_name': 'Request',
                       'definition': 'Asks an able addressee to act, subject to their consent.',
                       'source_url': 'https://dit.uvt.nl/'},
                      {'label': 'accept_request',
                       'official_name': 'AcceptRequest',
                       'definition': 'Commits to the requested action without conditions.',
                       'source_url': 'https://dit.uvt.nl/'},
                      {'label': 'decline_request',
                       'official_name': 'DeclineRequest',
                       'definition': 'Commits against performing the requested action.',
                       'source_url': 'https://dit.uvt.nl/'},
                      {'label': 'accept_offer',
                       'official_name': 'AcceptOffer',
                       'definition': 'Consents to the offered action.',
                       'source_url': 'https://dit.uvt.nl/'},
                      {'label': 'decline_offer',
                       'official_name': 'DeclineOffer',
                       'definition': 'Withholds consent to the offered action.',
                       'source_url': 'https://dit.uvt.nl/'},
                      {'label': 'positive_processing_feedback',
                       'official_name': 'AutoPositive',
                       'definition': 'Signals successful processing without specifying its level.',
                       'source_url': 'https://dit.uvt.nl/'},
                      {'label': 'negative_processing_feedback',
                       'official_name': 'AutoNegative',
                       'definition': 'Signals unsuccessful processing without specifying its '
                                     'level.',
                       'source_url': 'https://dit.uvt.nl/'},
                      {'label': 'correct_own_wording',
                       'official_name': 'Self-correction',
                       'definition': 'Repairs one’s own speaking error within the same turn.',
                       'source_url': 'https://dit.uvt.nl/'}],
 'sequence_kinds': [{'label': 'clarification_repair',
                     'definition': 'A contribution creates an evidenced understanding or wording '
                                   'problem; a participant seeks or supplies clarification, '
                                   'followed by correction, uptake, continued difficulty or an '
                                   'explicitly unknown development.'},
                    {'label': 'repetition_in_context',
                     'definition': 'A participant repeats source-specific content after a prior '
                                   'contribution; retain the intervening response and whether the '
                                   'repetition checks, repairs, reiterates or has an uncertain '
                                   'function.'},
                    {'label': 'resistance_acceptance',
                     'definition': 'A participant declines, challenges, qualifies or accepts a '
                                   'proposal/request; retain what was proposed, the response, and '
                                   'any later persistence, revision or acceptance without assuming '
                                   'persuasion caused it.'},
                    {'label': 'topic_management',
                     'definition': 'Participants introduce, return to or move between identifiable '
                                   'topics; retain the preceding topic, transition response and '
                                   'subsequent topic development.'},
                    {'label': 'closure_management',
                     'definition': 'Participants propose or enact ending an exchange, and respond '
                                   'by closing, reopening or continuing it; do not infer closure '
                                   'merely because the transcript ends.'}],
 'emotion_labels': [{'label': 'admiration',
                     'definition': 'Respectful appreciation of something impressive.'},
                    {'label': 'amusement', 'definition': 'Being entertained or finding humor.'},
                    {'label': 'anger', 'definition': 'Marked displeasure or hostility.'},
                    {'label': 'annoyance', 'definition': 'Irritation or mild anger.'},
                    {'label': 'approval', 'definition': 'A favorable evaluation.'},
                    {'label': 'caring', 'definition': 'Kindness and concern toward others.'},
                    {'label': 'confusion',
                     'definition': 'Uncertainty or difficulty understanding.'},
                    {'label': 'curiosity', 'definition': 'Eagerness to learn or know.'},
                    {'label': 'desire', 'definition': 'Strong wanting or wishing.'},
                    {'label': 'disappointment', 'definition': 'Distress over unmet expectations.'},
                    {'label': 'disapproval', 'definition': 'An unfavorable evaluation.'},
                    {'label': 'disgust',
                     'definition': 'Repulsion toward something offensive or unpleasant.'},
                    {'label': 'embarrassment',
                     'definition': 'Awkward self-consciousness or shame.'},
                    {'label': 'excitement', 'definition': 'Eager enthusiasm.'},
                    {'label': 'fear', 'definition': 'Feeling afraid or worried.'},
                    {'label': 'gratitude', 'definition': 'Thankful appreciation.'},
                    {'label': 'grief', 'definition': 'Profound sorrow, especially after a death.'},
                    {'label': 'joy', 'definition': 'Happiness and pleasure.'},
                    {'label': 'love', 'definition': 'Deep affectionate regard.'},
                    {'label': 'nervousness', 'definition': 'Anxious apprehension or worry.'},
                    {'label': 'optimism', 'definition': 'Hopeful confidence in future success.'},
                    {'label': 'pride',
                     'definition': 'Satisfaction in personal or closely associated achievements.'},
                    {'label': 'realization', 'definition': 'Coming to recognize something.'},
                    {'label': 'relief',
                     'definition': 'Relaxation or reassurance after distress subsides.'},
                    {'label': 'remorse', 'definition': 'Guilt or regret.'},
                    {'label': 'sadness', 'definition': 'Sorrow or emotional hurt.'},
                    {'label': 'surprise', 'definition': 'Astonishment at the unexpected.'},
                    {'label': 'neutral',
                     'definition': 'No emotion category confidently identified in the assessed '
                                   'text; not proof of calm.'}],
 'emotion_statuses': [{'status': 'neutral_assessed',
                       'definition': 'A particular adequate text span was deliberately assessed as '
                                     'GoEmotions neutral; emotion_labels contains only neutral. '
                                     'This is a label decision, not evidence of a calm inner '
                                     'state.'},
                      {'status': 'no_expression_detected',
                       'definition': 'The reviewed scope yielded no supported emotional-expression '
                                     'observation. Record that scope and use no emotion labels; '
                                     'this does not assign neutral to every span or prove an '
                                     'absence of feelings.'},
                      {'status': 'ambiguous',
                       'definition': 'Adequate text permits competing interpretations that cannot '
                                     'be resolved. Retain alternatives and reason without '
                                     'asserting them as labels; use no emotion labels.'},
                      {'status': 'insufficient_evidence',
                       'definition': 'Missing, unreliable or inadequate evidence prevents '
                                     'assessment. State the gap and use no emotion labels.'}]}
ACTION_FUNCTIONS = tuple(row['label'] for row in FRAMEWORK_DEFINITIONS['action_functions'])
SEQUENCE_KINDS = tuple(row['label'] for row in FRAMEWORK_DEFINITIONS['sequence_kinds'])
EMOTION_LABELS = tuple(row['label'] for row in FRAMEWORK_DEFINITIONS['emotion_labels'])
EMOTION_STATUSES = ('observed', 'unknown', 'neutral_assessed', 'no_expression_detected',
                    'ambiguous', 'insufficient_evidence')
NEED_KINDS = ('primary_need', 'secondary_need', 'late_need', 'correction',
              'material_constraint', 'reported_outcome', 'uncertainty')
NEED_STATUSES = ('requested', 'reported', 'corrected', 'pending', 'completed',
                 'reported_resolved', 'reported_unresolved', 'uncertain', 'unknown')
DIRECT_AUTHORITIES = ('human_transcript', 'participant_message', 'participant_statement',
                      'customer_statement')


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def validate_source(source):
    """Retain all supplied canonical evidence; fail on malformed or clipped input."""
    if not isinstance(source, dict):
        raise ValueError('Canonical source must be an object')
    for key in ('source_id', 'client_id', 'source_type'):
        if not isinstance(source.get(key), str) or not source[key].strip():
            raise ValueError('Canonical source requires nonempty ' + key)
    if not isinstance(source.get('metadata'), dict) or not isinstance(source.get('guidance'), list):
        raise ValueError('Canonical source requires metadata object and guidance array')
    events = source.get('events')
    if not isinstance(events, list) or not events:
        raise ValueError('Canonical source requires all nonempty ordered events')
    for container in (source, source['metadata']):
        if (container.get('truncated', False) is not False
                or container.get('body_clipping', False) is not False
                or container.get('omitted_event_count', 0) != 0
                or container.get('omitted_events', []) != []):
            raise ValueError('Omitted or clipped input evidence is prohibited')
        for key in ('source_event_count', 'included_event_count'):
            if key in container and container[key] != len(events):
                raise ValueError('Input event accounting does not match complete source')
    designated = source['metadata'].get('customer_side_speaker_ids')
    if designated is not None and (not isinstance(designated, list)
            or any(not isinstance(speaker, str) or not speaker.strip() for speaker in designated)
            or len(designated) != len(set(designated))):
        raise ValueError('Source-designated customer speakers must be unique nonempty IDs')
    seen, previous = set(), None
    for event in events:
        if not isinstance(event, dict):
            raise ValueError('Canonical event must be an object')
        for key in ('event_id', 'kind', 'role', 'speaker', 'authority', 'timing_status', 'redaction_status'):
            if not isinstance(event.get(key), str) or not event[key].strip():
                raise ValueError('Canonical event requires nonempty ' + key)
        if event['redaction_status'] not in ('unchanged', 'redacted', 'supplied_redacted', 'not_reviewed'):
            raise ValueError('Unknown redaction status')
        if event['timing_status'] not in ('known', 'unknown'):
            raise ValueError('Timing status must preserve known or unknown timing')
        ordinal = event.get('ordinal')
        if (type(ordinal) is not int or (previous is not None and ordinal <= previous)
                or event['event_id'] in seen):
            raise ValueError('Canonical events require unique IDs and strictly increasing supplied order')
        if (not isinstance(event.get('text'), str)
                or event.get('truncated', False) is not False
                or not (event.get('original_text') is None or isinstance(event['original_text'], str))
                or not (event.get('timestamp') is None or isinstance(event['timestamp'], str))):
            raise ValueError('Canonical evidence must be complete text with supplied timing only')
        previous = ordinal
        seen.add(event['event_id'])
    canonical_json(source)  # reject non-JSON/nonfinite data before hashing or requesting
    return deepcopy(source)


def source_hash(source):
    """Content identity includes provenance, originals, guidance and all events."""
    return hashlib.sha256(canonical_json(validate_source(source)).encode('utf-8')).hexdigest()


def _text(description='', values=None):
    result = {'type': 'string', 'minLength': 1}
    if description:
        result['description'] = description
    if values:
        result['enum'] = list(values)
    return result


def _object(properties, description=''):
    result = {'type': 'object', 'properties': properties, 'required': list(properties),
              'additionalProperties': False}
    if description:
        result['description'] = description
    return result


def _array(items, minimum=0, description=''):
    result = {'type': 'array', 'items': items, 'minItems': minimum}
    if description:
        result['description'] = description
    return result


EVIDENCE_SCHEMA = _object({
    'event_id': _text('Original event ID'), 'quote': _text('Exact contiguous permitted text'),
    'role': _text('Original role'), 'speaker': _text('Original speaker'),
    'authority': _text('Original authority; never upgrade'),
})
EVIDENCE = _array(EVIDENCE_SCHEMA, 1)
NEED_SCHEMA = _object({
    'id': _text('Unique local card ID'), 'kind': _text(values=NEED_KINDS),
    'summary': _text('Independent need, qualification or report'),
    'status': _text(values=NEED_STATUSES),
    'related_ids': _array(_text('Other card IDs in this scenario')),
    'evidence': EVIDENCE,
})
SCENARIO_SCHEMA = _object({
    'id': _text('Unique local scenario ID'), 'summary': _text('Situation, not approved handling'),
    'needs': _array(NEED_SCHEMA, 1, 'All meaningful needs, corrections, conditions and outcomes'),
    'unknowns': _array(_text('Narrow unsupported or pending fact')),
})
ACTION_SCHEMA = _object({'id': _text(), 'function': _text(values=ACTION_FUNCTIONS),
                         'summary': _text('Action in this source context'), 'evidence': EVIDENCE})
SEQUENCE_SCHEMA = _object({'id': _text(), 'kind': _text(values=SEQUENCE_KINDS),
                           'summary': _text('Context, response and development; missing development unknown'),
                           'evidence': _array(EVIDENCE_SCHEMA, 2, 'At least two distinct ordered events')})
EXPRESSION_SCHEMA = _object({
    'id': _text(), 'labels': _array(_text(values=EMOTION_LABELS), 1),
    'summary': _text('Expression and time scope; unknown cause/intensity remain unknown'),
    'mode': _text(values=('explicit_self_report', 'observer_interpretation')), 'evidence': EVIDENCE,
})
CHANGE_SCHEMA = _object({
    'id': _text(), 'from_id': _text('Earlier expression ID'), 'to_id': _text('Later expression ID'),
    'summary': _text('Same speaker change; temporal context is not cause'), 'evidence': EVIDENCE,
})
PERSONA_SCHEMA = _object({
    'id': _text('Unique local persona ID'), 'speaker': _text('Source-designated customer speaker, or unknown'),
    'evidence_status': _text(values=('observed', 'insufficient_evidence', 'ambiguous', 'unknown')),
    'actions': _array(ACTION_SCHEMA), 'sequences': _array(SEQUENCE_SCHEMA),
    'emotional_expression': _object({'status': _text(values=EMOTION_STATUSES),
                                     'observations': _array(EXPRESSION_SCHEMA)}),
    'emotional_changes': _array(CHANGE_SCHEMA),
    'unknowns': _array(_text('Evidence gaps and source-session limits')),
})
EXTRACTION_SCHEMA = _object({
    'scenarios': _array(SCENARIO_SCHEMA), 'personas': _array(PERSONA_SCHEMA),
    'capture_status': _text(values=('complete', 'output_limit')),
    'read_receipt': _object({'last_event_id': _text('Final supplied event ID'),
                              'complete': {'type': 'boolean'}}),
})


def shape_errors(value, schema, path='$'):
    """Small dependency-free subset of JSON Schema used by these contracts."""
    errors = []
    kind = schema['type']
    checks = {'object': lambda v: isinstance(v, dict), 'array': lambda v: isinstance(v, list),
              'string': lambda v: isinstance(v, str), 'boolean': lambda v: type(v) is bool}
    if not checks[kind](value):
        return [path + ': wrong type']
    if kind == 'object':
        properties = schema['properties']
        if set(value) != set(properties):
            errors.append(path + ': missing or extra fields')
        for key in properties.keys() & value.keys():
            errors.extend(shape_errors(value[key], properties[key], path + '.' + key))
    elif kind == 'array':
        if len(value) < schema.get('minItems', 0):
            errors.append(path + ': too few items')
        for index, item in enumerate(value):
            errors.extend(shape_errors(item, schema['items'], f'{path}[{index}]'))
    elif kind == 'string' and not value.strip():
        errors.append(path + ': nonempty text required')
    if 'enum' in schema and value not in schema['enum']:
        errors.append(path + ': invalid canonical label')
    return errors


def is_direct_customer(event, source):
    designated = source['metadata'].get('customer_side_speaker_ids')
    customer = event['speaker'] in designated if isinstance(designated, list) else event['role'] == 'customer'
    return customer and event['kind'] != 'note' and event['authority'] in DIRECT_AUTHORITIES


def _check_evidence(refs, source, path, errors, speaker=None):
    lookup = {event['event_id']: event for event in source['events']}
    positions, direct = [], False
    for index, ref in enumerate(refs):
        event = lookup.get(ref['event_id'])
        label = f'{path}[{index}]'
        if event is None:
            errors.append(label + ': unknown event_id')
            continue
        if ref['quote'] not in event['text']:
            errors.append(label + ': quote is not an exact permitted source span')
        if any(ref[key] != event[key] for key in ('role', 'speaker', 'authority')):
            errors.append(label + ': original attribution mismatch')
        positions.append(event['ordinal'])
        if is_direct_customer(event, source) and event['speaker'] == speaker:
            direct = True
    if positions != sorted(positions):
        errors.append(path + ': evidence must preserve supplied order')
    if speaker is not None and not direct:
        errors.append(path + ': direct source-designated speaker evidence required; notes remain reports')
    return positions


def validate_component(kind, payload, source):
    """Check one component independently; valid does not mean semantically accepted."""
    source = validate_source(source)
    if kind not in ('scenario', 'persona'):
        return ['Unknown component kind']
    errors = shape_errors(payload, SCENARIO_SCHEMA if kind == 'scenario' else PERSONA_SCHEMA)
    if errors:
        return errors
    if kind == 'scenario':
        ids = [card['id'] for card in payload['needs']]
        if len(ids) != len(set(ids)):
            errors.append('needs: duplicate local card ID')
        for index, card in enumerate(payload['needs']):
            if any(ref not in ids or ref == card['id'] for ref in card['related_ids']):
                errors.append(f'needs[{index}]: unknown or self related_id')
            _check_evidence(card['evidence'], source, f'needs[{index}].evidence', errors)
        return errors
    speaker = payload['speaker']
    direct = [e for e in source['events'] if is_direct_customer(e, source) and e['speaker'] == speaker]
    expression = payload['emotional_expression']
    observations = expression['observations']
    units = [*payload['actions'], *payload['sequences'], *observations, *payload['emotional_changes']]
    ids = [unit['id'] for unit in units]
    if len(ids) != len(set(ids)):
        errors.append('persona: duplicate local unit ID')
    if payload['evidence_status'] != 'observed' and units:
        errors.append('persona: unknown/ambiguous/insufficient evidence cannot assert observations')
    if payload['evidence_status'] == 'observed' and (not direct or not units):
        errors.append('persona: observed requires direct speaker evidence and observations')
    if not direct and (payload['evidence_status'] != 'insufficient_evidence'
                       or expression['status'] != 'insufficient_evidence'):
        errors.append('persona: no direct speech requires explicit insufficient_evidence')
    if payload['evidence_status'] != 'observed' and not payload['unknowns']:
        errors.append('persona: abstention requires an explicit evidence gap')
    positions = {}
    for unit in units:
        positions[unit['id']] = _check_evidence(unit['evidence'], source, unit['id'] + '.evidence', errors, speaker)
    for sequence in payload['sequences']:
        if len(set(ref['event_id'] for ref in sequence['evidence'])) < 2:
            errors.append(sequence['id'] + ': sequence requires at least two distinct ordered events')
    for observation in observations:
        labels = observation['labels']
        if len(labels) != len(set(labels)) or ('neutral' in labels and len(labels) != 1):
            errors.append(observation['id'] + ': duplicate labels or neutral mixed with other emotions')
    labels = [label for observation in observations for label in observation['labels']]
    if expression['status'] == 'observed' and (not labels or 'neutral' in labels):
        errors.append('emotional_expression: observed requires non-neutral supported labels')
    if expression['status'] == 'neutral_assessed' and (not labels or set(labels) != {'neutral'}):
        errors.append('emotional_expression: neutral_assessed requires neutral-only evidence')
    if expression['status'] not in ('observed', 'neutral_assessed') and observations:
        errors.append('emotional_expression: abstention cannot assert emotion labels')
    observation_lookup = {row['id']: row for row in observations}
    for change in payload['emotional_changes']:
        left, right = change['from_id'], change['to_id']
        if left == right or left not in observation_lookup or right not in observation_lookup:
            errors.append(change['id'] + ': change requires two distinct existing expression endpoints')
            continue
        if not positions[left] or not positions[right] or max(positions[left]) >= min(positions[right]):
            errors.append(change['id'] + ': expression endpoints must be distinct and chronological')
        refs = {(r['event_id'], r['quote']) for r in change['evidence']}
        for endpoint in (left, right):
            if not refs.intersection((r['event_id'], r['quote']) for r in observation_lookup[endpoint]['evidence']):
                errors.append(change['id'] + ': change evidence must include each expression endpoint')
    return errors


# Opt-in capture-v3 contracts. Legacy schema objects and validators above stay frozen.
CAPTURE_V3_VERSIONS = {
    "code": "engine-extraction-v3", "prompt": "engine-extraction-prompt-v3",
    "schema": "engine-extraction-schema-v3", "codebook": VERSIONS["codebook"],
    "capture_policy": "independent-layers-child-quarantine-v1",
    "assertion_contract": "source-linked-action-state-v1",
}
ASSERTION_STATES_V3 = ('offered', 'requested', 'accepted', 'declined', 'proposed',
                       'in_progress', 'pending', 'reported_completed',
                       'source_recorded_completed', 'reported', 'negated',
                       'conditional', 'uncertain', 'unknown')
ASSERTION_CERTAINTIES_V3 = ('explicit', 'reported', 'possible', 'uncertain', 'unknown')
ASSERTION_SCHEMA_V3 = _object({
    'actor': _text('Actor speaker ID, or unknown; speaker of a report can differ from actor'),
    'recipient': _text('Recipient speaker ID, or unknown'),
    'action': _text('Narrow action or proposition before composing summary'),
    'scope': _text('Object, condition, alternative, time and affected destination; retain unknowns'),
    'state': _text('Source-side action state, never independent verification of truth', ASSERTION_STATES_V3),
    'certainty': _text('Explicit means explicitly stated, not independently true', ASSERTION_CERTAINTIES_V3),
})
NEED_SCHEMA_V3 = _object({
    'id': deepcopy(NEED_SCHEMA['properties']['id']),
    'kind': deepcopy(NEED_SCHEMA['properties']['kind']),
    'assertion': ASSERTION_SCHEMA_V3,
    **{key: deepcopy(value) for key, value in NEED_SCHEMA['properties'].items()
       if key not in ('id', 'kind')},
})
ACTION_SCHEMA_V3 = _object({
    'id': deepcopy(ACTION_SCHEMA['properties']['id']),
    'function': deepcopy(ACTION_SCHEMA['properties']['function']),
    'assertion': ASSERTION_SCHEMA_V3,
    **{key: deepcopy(value) for key, value in ACTION_SCHEMA['properties'].items()
       if key not in ('id', 'function')},
})
SCENARIO_SCHEMA_V3 = deepcopy(SCENARIO_SCHEMA)
SCENARIO_SCHEMA_V3['properties']['needs']['items'] = NEED_SCHEMA_V3
PERSONA_SCHEMA_V3 = deepcopy(PERSONA_SCHEMA)
PERSONA_SCHEMA_V3['properties']['actions']['items'] = ACTION_SCHEMA_V3
# The common observed/neutral mismatch is encoded in the SAME schema consumed by
# the candidate validator and shown to the model, rather than left to prompt prose.
EMOTION_ABSTENTIONS_V3 = tuple(status for status in EMOTION_STATUSES
                               if status not in ('observed', 'neutral_assessed'))
EXPRESSION_SCHEMA_V3 = deepcopy(EXPRESSION_SCHEMA)
EXPRESSION_SCHEMA_V3['properties']['labels']['uniqueItems'] = True
EMOTION_EXPRESSION_SCHEMA_V3 = _object({
    'status': _text(values=EMOTION_STATUSES),
    'observations': _array(EXPRESSION_SCHEMA_V3),
})
EMOTION_EXPRESSION_SCHEMA_V3['oneOf'] = []
for emotion_status, emotion_labels in (
        ('observed', tuple(label for label in EMOTION_LABELS if label != 'neutral')),
        ('neutral_assessed', ('neutral',))):
    observation = deepcopy(EXPRESSION_SCHEMA_V3)
    observation['properties']['labels']['items']['enum'] = list(emotion_labels)
    EMOTION_EXPRESSION_SCHEMA_V3['oneOf'].append(_object({
        'status': _text(values=(emotion_status,)),
        'observations': _array(observation, 1),
    }))
abstention_observations = _array(EXPRESSION_SCHEMA_V3)
abstention_observations['maxItems'] = 0
EMOTION_EXPRESSION_SCHEMA_V3['oneOf'].append(_object({
    'status': _text(values=EMOTION_ABSTENTIONS_V3),
    'observations': abstention_observations,
}))
PERSONA_SCHEMA_V3['properties']['emotional_expression'] = EMOTION_EXPRESSION_SCHEMA_V3
CAPTURE_SCHEMAS_V3 = {
    kind: _object({collection: _array(schema),
                   'capture_status': deepcopy(EXTRACTION_SCHEMA['properties']['capture_status']),
                   'read_receipt': deepcopy(EXTRACTION_SCHEMA['properties']['read_receipt'])})
    for kind, collection, schema in (('scenario', 'scenarios', SCENARIO_SCHEMA_V3),
                                      ('persona', 'personas', PERSONA_SCHEMA_V3))
}


def shape_errors_v3(value, schema, path='$'):
    """Legacy shape rules plus V3 schema branches/limits, using the visible schema."""
    errors = shape_errors(value, schema, path)
    if errors:
        return errors
    if 'oneOf' in schema and sum(not shape_errors_v3(value, branch, path)
                                 for branch in schema['oneOf']) != 1:
        errors.append(path + ': emotion status and observation labels/count do not match schema branch')
    if schema['type'] == 'array':
        if 'maxItems' in schema and len(value) > schema['maxItems']:
            errors.append(path + ': too many items')
        if schema.get('uniqueItems') and len({canonical_json(item) for item in value}) != len(value):
            errors.append(path + ': duplicate items')
        for index, item in enumerate(value):
            errors.extend(shape_errors_v3(item, schema['items'], f'{path}[{index}]'))
    elif schema['type'] == 'object':
        for key, field in schema['properties'].items():
            errors.extend(shape_errors_v3(value[key], field, path + '.' + key))
    return errors


def _without_assertions_v3(kind, payload):
    value = deepcopy(payload)
    for unit in value['needs' if kind == 'scenario' else 'actions']:
        unit.pop('assertion', None)
    return value


def _assertion_errors_v3(unit, source, path):
    assertion = unit['assertion']
    errors = []
    speakers = {event['speaker'] for event in source['events']} | {'unknown'}
    for field in ('actor', 'recipient'):
        if assertion[field] not in speakers:
            errors.append(path + '.assertion.' + field + ': unknown source speaker ID')
    # A source action is a recorded operation, not an independently verified truth.
    # A conversational claim cannot be upgraded to that record type by the model.
    if assertion['state'] == 'source_recorded_completed':
        events = {event['event_id']: event for event in source['events']}
        if not any(events.get(ref['event_id'], {}).get('kind') == 'action'
                   for ref in unit['evidence']):
            errors.append(path + ': source_recorded_completed requires a cited source action record')
    return errors


def validate_component_v3(kind, payload, source):
    """V3 structural/citation validity only; explicit assertions are not semantic proof."""
    source = validate_source(source)
    if kind not in ('scenario', 'persona'):
        return ['Unknown component kind']
    errors = shape_errors_v3(payload, SCENARIO_SCHEMA_V3 if kind == 'scenario' else PERSONA_SCHEMA_V3)
    if errors:
        return errors
    errors.extend(validate_component(kind, _without_assertions_v3(kind, payload), source))
    for index, unit in enumerate(payload['needs' if kind == 'scenario' else 'actions']):
        errors.extend(_assertion_errors_v3(unit, source, f'{kind}[{index}]'))
    return errors


def validate_persona_children_v3(payload, source):
    """Validate independent persona children, retaining every invalid raw child.

    Parent identity/observation status gates every child. Emotion validation cannot
    erase valid action/sequence children; expression changes depend on valid endpoints.
    No raw object is repaired or given a blanket pass by this function.
    """
    source = validate_source(source)
    header = _object({key: deepcopy(PERSONA_SCHEMA_V3['properties'][key])
                      for key in ('id', 'speaker', 'evidence_status', 'unknowns')})
    header_value = {key: payload[key] for key in header['properties'] if isinstance(payload, dict) and key in payload}
    header_errors = shape_errors(header_value, header)
    observation_errors = list(header_errors)
    if not header_errors:
        direct = any(is_direct_customer(event, source) and event['speaker'] == payload['speaker']
                     for event in source['events'])
        if payload['evidence_status'] != 'observed' or not direct:
            observation_errors.append('persona child observations require observed status and direct speaker evidence')
    records = []
    if not isinstance(payload, dict):
        return records
    # Duplicate IDs across layers are ambiguous ownership, so quarantine each duplicate.
    all_units = []
    for key in ('actions', 'sequences', 'emotional_changes'):
        if isinstance(payload.get(key), list):
            all_units.extend(payload[key])
    expression = payload.get('emotional_expression')
    if isinstance(expression, dict) and isinstance(expression.get('observations'), list):
        all_units.extend(expression['observations'])
    ids = [unit.get('id') for unit in all_units if isinstance(unit, dict)]

    def record(kind, local_id, value, errors):
        records.append({'kind': kind, 'local_id': local_id, 'payload': deepcopy(value),
                        'validation_status': 'quarantined' if errors else 'valid', 'issues': list(errors)})

    for name, schema, kind in (('actions', ACTION_SCHEMA_V3, 'persona_action'),
                                ('sequences', SEQUENCE_SCHEMA, 'persona_sequence')):
        values = payload.get(name)
        if not isinstance(values, list):
            record(kind, None, values, [name + ': must be an array', *observation_errors])
            continue
        for index, unit in enumerate(values):
            errors = [*observation_errors, *shape_errors(unit, schema)]
            local_id = unit.get('id') if isinstance(unit, dict) else None
            if local_id is not None and ids.count(local_id) > 1:
                errors.append('persona: duplicate local unit ID')
            if not errors:
                _check_evidence(unit['evidence'], source, f'{name}[{index}].evidence', errors, payload['speaker'])
                if name == 'actions':
                    errors.extend(_assertion_errors_v3(unit, source, f'{name}[{index}]'))
                elif len({ref['event_id'] for ref in unit['evidence']}) < 2:
                    errors.append('sequence requires at least two distinct ordered events')
            record(kind, local_id, unit, errors)
    # Reuse the frozen emotion checks on a temporary validation-only container.
    # This does not modify or return a repaired source payload.
    expression_errors = [*header_errors, *shape_errors_v3(expression, EMOTION_EXPRESSION_SCHEMA_V3)]
    if not expression_errors:
        temp = {**deepcopy(header_value), 'actions': [], 'sequences': [],
                'emotional_expression': deepcopy(expression), 'emotional_changes': []}
        expression_errors.extend(error for error in validate_component('persona', temp, source)
                                 if error != 'persona: observed requires direct speaker evidence and observations')
        for unit in expression['observations']:
            if ids.count(unit['id']) > 1:
                expression_errors.append('persona: duplicate local unit ID')
    record('persona_emotional_expression', 'emotional_expression', expression, expression_errors)
    changes = payload.get('emotional_changes')
    change_errors = [*header_errors, *shape_errors(changes, PERSONA_SCHEMA['properties']['emotional_changes'])]
    if expression_errors and changes:
        change_errors.append('emotional changes require a valid expression layer and endpoints')
    if not change_errors:
        temp = {**deepcopy(header_value), 'actions': [], 'sequences': [],
                'emotional_expression': deepcopy(expression), 'emotional_changes': deepcopy(changes)}
        change_errors.extend(error for error in validate_component('persona', temp, source)
                             if error != 'persona: observed requires direct speaker evidence and observations')
        for unit in changes:
            if ids.count(unit['id']) > 1:
                change_errors.append('persona: duplicate local unit ID')
    record('persona_emotional_changes', 'emotional_changes', changes, change_errors)
    return records
