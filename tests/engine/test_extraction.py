"""Synthetic mock responses only: no genuine model generation or semantic benchmark."""
from copy import deepcopy
import json
import hashlib
import unittest

from snowflake_engine import contracts, extraction


def source(note_only=False):
    texts = ['Please replace the damaged lens. I feel worried.',
             'We can inspect it; is the delivery address the same?',
             'Yes. Actually the blue lens, not red. Also email the invoice.',
             'Inspection is pending; no replacement has shipped.',
             'One more thing: cancel the extra cap. I feel relieved now.']
    events = []
    for index, text in enumerate(texts):
        customer = index % 2 == 0
        events.append({'event_id': f'e{index}', 'ordinal': index, 'timestamp': None,
                       'timing_status': 'unknown', 'kind': 'note' if note_only else 'message',
                       'role': 'agent' if note_only or not customer else 'customer',
                       'speaker': 'staff' if note_only or not customer else 'buyer',
                       'authority': 'attributed_note' if note_only else 'participant_message',
                       'text': text, 'original_text': text, 'redaction_status': 'unchanged'})
    return {'source_id': 'synthetic-01', 'client_id': 'fictional', 'source_type': 'case' if note_only else 'typed_chat',
            'metadata': {'customer_side_speaker_ids': [] if note_only else ['buyer']},
            'events': events, 'guidance': []}


def cite(data, index, quote=None):
    event = data['events'][index]
    return {key: event[key] for key in ('event_id', 'role', 'speaker', 'authority')} | {'quote': quote or event['text']}


def scenario(data, identifier='s1'):
    cards = [
        ('n1', 'primary_need', 'Replace the damaged lens.', 'requested', 0, []),
        ('n2', 'correction', 'The blue lens, not red.', 'corrected', 2, ['n1']),
        ('n3', 'secondary_need', 'Email the invoice.', 'requested', 2, []),
        ('n4', 'reported_outcome', 'Staff reports inspection pending and no shipment.', 'pending', 3, ['n1']),
        ('n5', 'late_need', 'Cancel the extra cap.', 'requested', 4, []),
    ]
    return {'id': identifier, 'summary': 'Replacement with separate invoice and cancellation needs.',
            'needs': [{'id': i, 'kind': k, 'summary': summary, 'status': status,
                       'related_ids': related, 'evidence': [cite(data, event)]}
                      for i, k, summary, status, event, related in cards],
            'unknowns': ['Replacement completion is not established.']}


def insufficient():
    return {'id': 'p1', 'speaker': 'unknown', 'evidence_status': 'insufficient_evidence',
            'actions': [], 'sequences': [], 'emotional_expression': {'status': 'insufficient_evidence', 'observations': []},
            'emotional_changes': [], 'unknowns': ['Attributed notes do not supply direct customer speech.']}


def persona(data):
    return {'id': 'p1', 'speaker': 'buyer', 'evidence_status': 'observed',
            'actions': [{'id': f'a{i}', 'function': 'request_action', 'summary': f'Request in event {i}.',
                         'evidence': [cite(data, i)]} for i in (0, 2, 4)],
            'sequences': [{'id': 'q1', 'kind': 'topic_management',
                           'summary': 'After inspection discussion, buyer introduces a separate cap cancellation.',
                           'evidence': [cite(data, 3), cite(data, 4)]}],
            'emotional_expression': {'status': 'observed', 'observations': [
                {'id': 'm1', 'labels': ['fear'], 'summary': 'Buyer states worry now; intensity unknown.',
                 'mode': 'explicit_self_report', 'evidence': [cite(data, 0, 'I feel worried.')]},
                {'id': 'm2', 'labels': ['relief'], 'summary': 'Buyer states relief now; cause unknown.',
                 'mode': 'explicit_self_report', 'evidence': [cite(data, 4, 'I feel relieved now.')]},
            ]},
            'emotional_changes': [{'id': 'c1', 'from_id': 'm1', 'to_id': 'm2',
                                   'summary': 'Buyer reports worry earlier and relief later; cause unknown.',
                                   'evidence': [cite(data, 0, 'I feel worried.'), cite(data, 4, 'I feel relieved now.')]}],
            'unknowns': ['Enduring traits and the cause of relief are unknown.']}


def response(data, note_only=False):
    return {'scenarios': [scenario(data)], 'personas': [insufficient() if note_only else persona(data)],
            'capture_status': 'complete', 'read_receipt': {'last_event_id': data['events'][-1]['event_id'], 'complete': True}}


class ExtractionTests(unittest.TestCase):
    def setUp(self):
        self.source = source()
        self.value = response(self.source)

    def parse(self, value=None):
        return extraction.parse_response(self.source, json.dumps(value or self.value))

    def test_multi_need_late_correction_pending_and_single_mock_call(self):
        calls = []
        def mock_complete(messages, operation, request_key):
            calls.append((messages, operation, request_key))
            return json.dumps(self.value)
        result = extraction.extract(self.source, mock_complete)
        self.assertEqual(1, len(calls))
        self.assertEqual('extraction', calls[0][1])
        self.assertIn(self.source['events'][-1]['text'], calls[0][0][-1]['content'])
        self.assertEqual(5, len(result['components'][0]['payload']['needs']))
        self.assertEqual({'valid'}, {row['validation_status'] for row in result['components']})
        self.assertEqual([], result['issues'])

    def test_shared_prompt_preserves_actor_and_offer_acceptance_boundaries(self):
        # This checks instructions only; no fabricated model response proves semantic success.
        system = extraction.build_messages(self.source)[0]['content']
        for rule in ('who offers, requests, accepts or declines',
                     'An offer does not establish that its recipient requested or accepted it',
                     'Explicit nonacceptance',
                     'retain the contradiction as unresolved',
                     'both scenario needs and persona actions'):
            self.assertIn(rule, system)
        self.assertEqual('engine-extraction-prompt-v2', extraction.versions()['prompt'])

    def test_standard_output_style_preserves_frozen_v2_message_bytes_and_key(self):
        default = extraction.build_messages(self.source)
        explicit = extraction.build_messages(self.source, output_style='standard')
        self.assertEqual(default, explicit)
        self.assertEqual('720036dfceb46a191e3d56653068f25d3a325954933363d4c5bdc719b525ff35',
                         hashlib.sha256(contracts.canonical_json(default).encode()).hexdigest())
        calls = []
        def mock_complete(messages, operation, request_key):
            calls.append(request_key)
            return json.dumps(self.value)
        result = extraction.extract(self.source, mock_complete, output_style='standard')
        self.assertEqual(['extraction-e2d530d2432ca60966150b34b5c71b8a19f9c7489bb349bb1f0bd375ddc05772'], calls)
        self.assertEqual(contracts.VERSIONS, result['versions'])
        self.assertEqual('standard', result['output_style'])

    def test_compact_style_changes_key_and_instructions_without_omitting_source(self):
        standard = extraction.build_messages(self.source)
        compact = extraction.build_messages(self.source, output_style='compact')
        self.assertEqual(standard[1], compact[1])
        self.assertTrue(compact[0]['content'].startswith(standard[0]['content']))
        self.assertIn('minified JSON', compact[0]['content'])
        self.assertIn('Keep EVERY meaningful', compact[0]['content'])
        for event in self.source['events']:
            self.assertIn(event['text'], compact[1]['content'])
        keys = []
        def mock_complete(messages, operation, request_key):
            keys.append(request_key)
            return json.dumps(self.value)
        extraction.extract(self.source, mock_complete)
        result = extraction.extract(self.source, mock_complete, output_style='compact')
        self.assertNotEqual(keys[0], keys[1])
        self.assertEqual('compact', result['output_style'])
        self.assertEqual('compact', result['versions']['output_style'])
        self.assertEqual('engine-extraction-prompt-v2', result['versions']['prompt'])

    def test_whole_input_or_explicit_limit_failure_before_callback(self):
        def forbidden(*_):
            self.fail('Input failure must make zero model calls')
        with self.assertRaisesRegex(ValueError, 'input_limit'):
            extraction.extract(self.source, forbidden, max_input_chars=10)
        self.source['events'][0]['text'] += 'x' * 100000
        with self.assertRaisesRegex(ValueError, 'input_limit'):
            extraction.extract(self.source, forbidden)

    def test_redacted_original_and_raw_metadata_never_enter_messages(self):
        self.source['events'][0].update(original_text='ORIGINAL-SECRET', text='[redacted]', redaction_status='redacted')
        self.source['metadata']['raw_record'] = {'body': 'METADATA-SECRET'}
        packet = json.dumps(extraction.build_messages(self.source))
        self.assertNotIn('ORIGINAL-SECRET', packet)
        self.assertNotIn('METADATA-SECRET', packet)
        self.assertIn('[redacted]', packet)
        self.assertIn(self.source['events'][-1]['text'], packet)

    def test_redaction_review_required_before_callback(self):
        self.source['events'][0]['redaction_status'] = 'not_reviewed'
        with self.assertRaisesRegex(ValueError, 'redaction_not_reviewed'):
            extraction.extract(self.source, lambda *_: self.fail('must not call'))

    def test_no_silent_inherited_scenario_or_action_caps(self):
        self.value['scenarios'] = [scenario(self.source, f's{i}') for i in range(5)]
        actions = self.value['personas'][0]['actions']
        actions.extend([{**deepcopy(actions[0]), 'id': f'extra{i}'} for i in range(5)])
        result = self.parse()
        self.assertEqual(6, len(result['components']))
        self.assertEqual({'valid'}, {row['validation_status'] for row in result['components']})

    def test_notes_keep_report_citations_and_insufficient_persona(self):
        data = source(note_only=True)
        result = extraction.parse_response(data, json.dumps(response(data, note_only=True)))
        self.assertEqual({'valid'}, {row['validation_status'] for row in result['components']})
        evidence = result['components'][0]['payload']['needs'][0]['evidence'][0]
        self.assertEqual('agent', evidence['role'])
        self.assertEqual('attributed_note', evidence['authority'])

    def test_quoted_note_cannot_supply_persona_direct_speech(self):
        data = source(note_only=True)
        value = response(data)
        value['personas'][0]['speaker'] = 'staff'
        result = extraction.parse_response(data, json.dumps(value))
        self.assertEqual('valid', result['components'][0]['validation_status'])
        self.assertEqual('quarantined', result['components'][1]['validation_status'])
        self.assertTrue(any('direct' in error for error in result['components'][1]['issues']))

    def test_persona_attribution_tamper_quarantines_only_persona_and_retains_raw(self):
        self.value['personas'][0]['actions'][0]['evidence'][0]['speaker'] = 'other'
        raw = json.dumps(self.value)
        result = extraction.parse_response(self.source, raw)
        self.assertEqual('valid', result['components'][0]['validation_status'])
        self.assertEqual('quarantined', result['components'][1]['validation_status'])
        self.assertEqual(raw, result['raw_response'])

    def test_quote_and_related_card_validation(self):
        for mutation in ('quote', 'relation'):
            value = deepcopy(self.value)
            card = value['scenarios'][0]['needs'][0]
            if mutation == 'quote':
                card['evidence'][0]['quote'] = 'invented'
            else:
                card['related_ids'] = ['missing']
            result = self.parse(value)
            self.assertEqual('quarantined', result['components'][0]['validation_status'])
            self.assertEqual('valid', result['components'][1]['validation_status'])

    def test_sequence_requires_two_distinct_ordered_events(self):
        for refs in ([cite(self.source, 4), cite(self.source, 4)],
                     [cite(self.source, 4), cite(self.source, 3)]):
            value = deepcopy(self.value)
            value['personas'][0]['sequences'][0]['evidence'] = refs
            self.assertEqual('quarantined', self.parse(value)['components'][1]['validation_status'])

    def test_sequence_allows_other_designated_participant_as_context(self):
        data = source()
        data['metadata']['customer_side_speaker_ids'] = ['buyer', 'staff']
        for event in data['events']:
            event['role'] = 'negotiation_peer'
        value = response(data)
        # Staff's preceding statement is context; buyer's own reply is still required.
        result = extraction.parse_response(data, json.dumps(value))
        self.assertEqual('valid', result['components'][1]['validation_status'])
        self.assertEqual([], result['components'][1]['issues'])

    def test_sequence_context_only_cannot_supply_target_persona(self):
        data = source()
        data['metadata']['customer_side_speaker_ids'] = ['buyer', 'staff']
        for event in data['events']:
            event['role'] = 'negotiation_peer'
        value = response(data)
        value['personas'][0]['sequences'][0]['evidence'] = [cite(data, 1), cite(data, 3)]
        result = extraction.parse_response(data, json.dumps(value))
        component = result['components'][1]
        self.assertEqual('quarantined', component['validation_status'])
        self.assertTrue(any('direct source-designated speaker evidence required' in error
                            for error in component['issues']))

    def test_emotion_changes_require_ordered_supported_endpoints(self):
        for left, right in [('m2', 'm1'), ('m1', 'm1'), ('missing', 'm2')]:
            value = deepcopy(self.value)
            value['personas'][0]['emotional_changes'][0].update(from_id=left, to_id=right)
            self.assertEqual('quarantined', self.parse(value)['components'][1]['validation_status'])
        self.value['personas'][0]['emotional_changes'][0]['evidence'] = [cite(self.source, 0, 'I feel worried.')]
        self.assertEqual('quarantined', self.parse()['components'][1]['validation_status'])

    def test_unknown_expression_not_forced_neutral(self):
        p = self.value['personas'][0]
        p['emotional_expression'] = {'status': 'unknown', 'observations': []}
        p['emotional_changes'] = []
        self.assertEqual('valid', self.parse()['components'][1]['validation_status'])
        p['emotional_expression'] = {'status': 'neutral_assessed', 'observations': []}
        self.assertEqual('quarantined', self.parse()['components'][1]['validation_status'])

    def test_malformed_prose_extra_fences_duplicate_nonfinite_json_retained(self):
        for raw in ('{"scenarios":', 'prose\n```json\n{}\n```',
                    '```json\n{}\n```\n```json\n{}\n```',
                    '{"a":1,"a":2}', '{"a":NaN}', '{"a":1e999}'):
            result = extraction.parse_response(self.source, raw)
            self.assertEqual([], result['components'])
            self.assertEqual(raw, result['raw_response'])
            self.assertEqual('invalid_json', result['issues'][0]['code'])

    def test_exact_single_outer_fence_is_normalized_and_raw_retained(self):
        for fence, normalization in [('json', 'removed_single_outer_json_fence'),
                                      ('', 'removed_single_outer_code_fence')]:
            raw = '```' + fence + '\n' + json.dumps(self.value) + '\n```'
            result = extraction.parse_response(self.source, raw)
            self.assertEqual(raw, result['raw_response'])
            self.assertEqual([normalization], result['format_normalizations'])
            self.assertEqual({'valid'}, {row['validation_status'] for row in result['components']})

    def test_output_limit_and_missing_late_receipt_are_visible(self):
        for key in ('output', 'last_event'):
            value = deepcopy(self.value)
            if key == 'output':
                value['capture_status'] = 'output_limit'
                value['read_receipt']['complete'] = False
            else:
                value['read_receipt']['last_event_id'] = 'e0'
            result = self.parse(value)
            self.assertTrue(result['issues'])
            self.assertEqual({'quarantined'}, {row['validation_status'] for row in result['components']})

    def test_replay_is_deterministic_stable_ids_bound_to_source_and_local_id(self):
        raw = json.dumps(self.value)
        first = extraction.reprocess(self.source, raw)
        self.assertEqual(first, extraction.reprocess(self.source, raw))
        second_scenario = scenario(self.source, 's2')
        value = deepcopy(self.value)
        value['scenarios'].insert(0, second_scenario)
        reordered = self.parse(value)
        self.assertEqual(first['components'][0]['object_id'], reordered['components'][1]['object_id'])
        changed = deepcopy(self.source)
        changed['events'][0]['text'] += ' Additional detail.'
        replay = extraction.reprocess(changed, raw)
        self.assertNotEqual(first['components'][0]['object_id'], replay['components'][0]['object_id'])

    def test_provider_error_is_not_retried(self):
        calls = []
        def broken(*args):
            calls.append(args)
            raise RuntimeError('synthetic provider failure')
        with self.assertRaisesRegex(RuntimeError, 'synthetic'):
            extraction.extract(self.source, broken)
        self.assertEqual(1, len(calls))

    def test_clipped_input_is_rejected_not_reinterpreted(self):
        self.source['metadata']['omitted_event_count'] = 1
        with self.assertRaisesRegex(ValueError, 'Omitted or clipped'):
            extraction.build_messages(self.source)


if __name__ == '__main__':
    unittest.main()
