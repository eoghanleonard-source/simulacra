"""Finite deterministic framing cases; no inference or semantic-quality claims."""
import json
import unittest

from snowflake_engine import capture_contract as contract
from snowflake_engine.capture_evidence import CaptureError, capture_source
from snowflake_engine.capture_framing import FRAMING_VERSION, normalize_message


BODY = '{"changes":[],"coverage":[],"unresolved":[]}'
CITATION = '`{"action":"quit","data":null}`'


def fenced(body=BODY):
    return '```json\n' + body + '\n```'


class FramingTests(unittest.TestCase):
    def test_baseline_valid_stages_unchanged(self):
        for stage, payload in [('worker', {'claims': [], 'coverage': []}),
                               ('review', {'source_pass': {}, 'decisions': [], 'omissions': []}),
                               ('reconcile', {'changes': [], 'coverage': [], 'unresolved': []})]:
            with self.subTest(stage=stage):
                raw = json.dumps(payload)
                self.assertEqual(normalize_message(raw, stage), contract.normalize_message(raw, stage))

    def test_exact_fenced_body_and_source_citations(self):
        raw = ('Quoted `[DATASET INTERFACE ACTION; NOT SPOKEN DIALOGUE]` and '
               + CITATION + ', `event:7s01`.\n' + fenced())
        with self.assertRaisesRegex(ValueError, 'Ambiguous additional JSON'):
            contract.normalize_message(raw, 'reconcile')
        result = normalize_message(raw, 'reconcile')
        self.assertEqual(result['payload'], json.loads(BODY))
        self.assertFalse(result['framing_provenance']['body_bytes_changed'])
        self.assertEqual(result['framing_provenance']['new_provider_calls'], 0)

    def test_ambiguous_or_absent_control_rejected(self):
        cases = [CITATION, fenced() + '\n' + fenced(), fenced() + '\n' + BODY,
                 '`' + BODY + '`\n' + fenced(),
                 CITATION + '\n' + fenced('{"action":"quit","data":null}'),
                 '`{"action":"quit","data":{"changes":[]}}`\n' + fenced(),
                 '`{"action":"quit","data":null,"coverage":[]}`\n' + fenced(),
                 '`{"action":"unknown","data":null}`\n' + fenced(),
                 '[DATASET INTERFACE ACTION; NOT SPOKEN DIALOGUE]\n' + fenced(),
                 '`{"action":"quit","data":null}\n' + fenced(),
                 CITATION + '\n' + fenced(BODY + '\n```json\n{}\n```')]
        for raw in cases:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                normalize_message(raw, 'reconcile')

    def test_recognised_dataset_actions_are_citations_only(self):
        actions = [{'action': 'offer', 'data': {'price': 80.0, 'sides': ''}}] + [
            {'action': action, 'data': None} for action in ('accept', 'reject', 'quit')]
        for action in actions:
            citation = '`' + json.dumps(action) + '`'
            with self.subTest(action=action):
                self.assertEqual(normalize_message(citation + '\n' + fenced(), 'reconcile')['payload'], json.loads(BODY))
                with self.assertRaises(ValueError):
                    normalize_message(citation, 'reconcile')
        for data in ({'price': True, 'sides': ''}, {'price': float('inf'), 'sides': ''},
                     {'price': 1e999, 'sides': ''}, {'price': 80, 'sides': {'changes': []}},
                     {'price': 80, 'sides': '{"changes":[]}'}, {'price': 80, 'sides': '', 'changes': []}):
            with self.subTest(data=data), self.assertRaises(ValueError):
                normalize_message('`' + json.dumps({'action': 'offer', 'data': data}) + '`\n' + fenced(), 'reconcile')

    def test_malformed_duplicate_nonfinite_rejected(self):
        cases = ['{"changes":[],"changes":[],"coverage":[],"unresolved":[]}',
                 '{"changes":[],"coverage":[],"unresolved":[NaN]}',
                 '{"changes":[],"coverage":[],"unresolved":[',
                 '{"changes":[],"coverage":[],"unresolved":[Infinity]}']
        for body in cases:
            with self.subTest(body=body), self.assertRaises(ValueError):
                normalize_message(CITATION + '\n' + fenced(body), 'reconcile')
        with self.assertRaises(ValueError):
            normalize_message('`{"action":"quit","action":"quit","data":null}`\n' + fenced(), 'reconcile')

    def test_default_unchanged_and_opt_in_recorded(self):
        source = {'source_id': 'framing-test', 'client_id': 'test', 'source_type': 'typed_chat', 'guidance': [],
            'metadata': {'customer_side_speaker_ids': ['buyer']}, 'events': [{
                'event_id': 'event-0', 'ordinal': 0, 'timestamp': None, 'timing_status': 'unknown',
                'kind': 'message', 'role': 'customer', 'speaker': 'buyer', 'authority': 'participant_message',
                'text': 'Please inspect.', 'original_text': 'Please inspect.', 'redaction_status': 'redacted'}]}
        claim = {'primary_event_id': 'event-0', 'kind': 'issue', 'actor': 'buyer', 'recipient': 'unknown',
            'action': 'requests inspection', 'object': 'item', 'conditions': [], 'time': None,
            'certainty': 'asserted', 'polarity': 'positive', 'status': 'requested',
            'functions': ['request'], 'evidence_span_ids': ['event-0s01']}
        coverage = [{'event_id': 'event-0', 'disposition': 'claim', 'reason': 'explicit request'}]
        calls = []

        def complete(messages, stage, key, **kwargs):
            calls.append(stage)
            if stage == 'worker':
                payload = {'claims': [claim], 'coverage': coverage}
            elif stage == 'review':
                payload = {'source_pass': {'read_event_ids': ['event-0'], 'material_findings': []},
                    'decisions': [{'claim_id': 'cl-' + contract.digest({'source': source['source_id'], 'claim': claim})[:24],
                        'verdict': 'accept', 'reason': 'explicit request', 'evidence_span_ids': ['event-0s01'],
                        'counter_span_ids': [], 'request_repair': False}], 'omissions': []}
            elif stage == 'reconcile':
                return CITATION + '\n' + fenced(json.dumps({'changes': [], 'coverage': coverage, 'unresolved': []}))
            else:
                self.fail('Unexpected stage ' + stage)
            return json.dumps(payload)

        original = contract.normalize_message
        with self.assertRaisesRegex(CaptureError, 'Ambiguous additional JSON'):
            capture_source(source, complete, settings={}, versions={})
        result = capture_source(source, complete, settings={'response_framing': FRAMING_VERSION}, versions={})
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(len(result['accepted']), 1)
        responses = [row for row in result['records'] if row['record_kind'] == 'stage_response']
        self.assertEqual(len(responses), 3)
        for row in responses:
            self.assertIn('response_framing:' + FRAMING_VERSION, row['normalizations'])
            self.assertFalse(row['body_repair'])
        self.assertIs(contract.normalize_message, original)
        with self.assertRaisesRegex(CaptureError, 'Unknown capture response framing'):
            capture_source(source, complete, settings={'response_framing': 'unknown'}, versions={})


if __name__ == '__main__':
    unittest.main()
