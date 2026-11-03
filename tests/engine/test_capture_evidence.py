"""Deterministic synthetic responses only; these tests do not assess model quality."""
from copy import deepcopy
import json
import unittest

from snowflake_engine import capture_contract as contract
from snowflake_engine.capture_evidence import (CaptureError, capture_source, current_projection,
                                               PROFILE_VERSION, section_plan)


def source(count=1):
    return {'source_id': 'synthetic-capture-01', 'client_id': 'fictional', 'source_type': 'typed_chat',
            'metadata': {'customer_side_speaker_ids': ['buyer'], 'private': 'never-prompt-metadata'},
            'guidance': [{'text': 'never-prompt-guidance'}],
            'events': [{'event_id': 'turn-' + str(i), 'ordinal': i, 'timestamp': None,
                        'timing_status': 'unknown', 'kind': 'message', 'role': 'customer',
                        'speaker': 'buyer', 'authority': 'participant_message',
                        'text': 'Please inspect the blue lens. ' + ('Tail remains exact. ' * 30 if i == count - 1 else ''),
                        'original_text': 'never-prompt-original', 'redaction_status': 'redacted'}
                       for i in range(count)]}


def claim(event='turn-0', action='requests inspection', obj='blue lens'):
    return {'primary_event_id': event, 'kind': 'issue', 'actor': 'buyer', 'recipient': 'unknown',
            'action': action, 'object': obj, 'conditions': [], 'time': None,
            'certainty': 'asserted', 'polarity': 'positive', 'status': 'requested',
            'functions': ['request'], 'evidence_span_ids': [event + 's01']}


def coverage(primary):
    return [{'event_id': eid, 'disposition': 'claim', 'reason': 'source-backed synthetic request'}
            for eid in primary]


def inputs(messages):
    text = messages[1]['content']
    context, tail = text.split('SOURCE\n', 1)[1].split('\nPRIMARY_EVENTS\n', 1)
    primary, payload = tail.split('\n', 1)
    return json.loads(context), json.loads(primary), json.loads(payload)


def review(primary, candidates, verdict='accept', repair=False):
    return {'source_pass': {'read_event_ids': primary, 'material_findings': []},
            'decisions': [{'claim_id': c['claim']['claim_id'], 'verdict': verdict,
                           'reason': 'supported synthetic request' if verdict == 'accept' else 'targeted correction needed',
                           'evidence_span_ids': c['claim']['evidence_span_ids'], 'counter_span_ids': [],
                           'request_repair': repair} for c in candidates], 'omissions': []}


class Scripted:
    def __init__(self, mutate=None):
        self.calls, self.mutate = [], mutate
    def __call__(self, messages, stage, request_key, *, max_output_tokens):
        context, primary, payload = inputs(messages)
        self.calls.append((stage, request_key, max_output_tokens, deepcopy(messages)))
        if stage == 'worker':
            response = {'claims': [claim(primary[0])], 'coverage': coverage(primary)}
        elif stage in {'review', 'repair_verify', 'reconcile_verify'}:
            response = review(primary, payload['CANDIDATES'])
        else:
            response = {'changes': [], 'coverage': coverage(primary), 'unresolved': []}
        if self.mutate:
            replacement = self.mutate(stage, primary, payload, response)
            response = replacement if replacement is not None else response
        return response if isinstance(response, str) else json.dumps(response, ensure_ascii=False)


class CaptureEvidenceTest(unittest.TestCase):
    def test_complete_sections_context_no_private_fields_and_no_clipping(self):
        data, complete = source(26), Scripted()
        result = capture_source(data, complete, settings={}, versions={'engine': 'test'})
        self.assertEqual([x[0] for x in complete.calls], ['worker', 'review', 'worker', 'review', 'reconcile'])
        for stage, key, cap, messages in complete.calls:
            context, primary, _ = inputs(messages)
            self.assertEqual(len(context['events']), 26)
            reconstructed = ''.join(p[1] for p in context['events'][-1]['parts'])
            self.assertEqual(reconstructed, data['events'][-1]['text'])
            wire = json.dumps(messages)
            for secret in ('never-prompt-original', 'never-prompt-guidance', 'never-prompt-metadata', 'original_text'):
                self.assertNotIn(secret, wire)
            self.assertEqual(cap, 8192 if stage == 'worker' else 16384)
        self.assertEqual([len(s['primary_event_ids']) for s in section_plan(data)], [25, 1])
        for bad in ([{'section_id': 'x', 'primary_event_ids': ['turn-0']}],
                    [{'section_id': 'x', 'primary_event_ids': ['turn-1', 'turn-0']}], []):
            with self.assertRaises(CaptureError):
                capture_source(data, Scripted(), settings={'section_plan': bad}, versions={})
        large = source()
        large['events'][0]['text'] = 'unclipped long source ' * 6000
        sink = Scripted()
        with self.assertRaisesRegex(CaptureError, 'no source/claim clipping'):
            capture_source(large, sink, settings={}, versions={})
        self.assertFalse(sink.calls)
        self.assertEqual(result['status'], 'completed')
        override = source()
        override['metadata'].pop('customer_side_speaker_ids')
        def communication(stage, primary, payload, response):
            if stage == 'worker':
                response['claims'][0].update(kind='communication', functions=['observed_communication'])
            return response
        effective = capture_source(override, Scripted(communication),
                                   settings={'customer_side_speaker_ids': ['buyer']}, versions={})
        self.assertEqual(effective['accepted'][0]['claim']['kind'], 'communication')
        for row in effective['records'] + effective['checkpoints']:
            self.assertEqual(row['customer_side_speaker_ids'], ['buyer'])

    def test_global_source_and_wire_guards_stop_before_further_calls(self):
        for change in ('focal', 'schema'):
            data, complete = source(), Scripted()
            if change == 'focal':
                data['metadata'].pop('customer_side_speaker_ids')
            else:
                data['events'][0]['ordinal'] = 'zero'
            with self.assertRaises(ValueError):
                capture_source(data, complete, settings={}, versions={})
            self.assertEqual(complete.calls, [])
        for broken in ('read', 'anchor', 'body', 'order'):
            records, checkpoints = [], []
            def mutate(stage, primary, payload, response):
                if stage != 'review':
                    return response
                if broken == 'read':
                    response['source_pass']['read_event_ids'] = []
                elif broken == 'anchor':
                    response['source_pass']['material_findings'] = [{'evidence_span_ids': ['invented'], 'meaning': 'x' * 161}]
                elif broken == 'body':
                    return '{"source_pass":{},"source_pass":{}}'
                else:
                    return {'decisions': response['decisions'], 'source_pass': response['source_pass'], 'omissions': []}
                return response
            complete = Scripted(mutate)
            with self.assertRaises(CaptureError):
                capture_source(source(), complete, settings={}, versions={}, on_record=records.append,
                               on_checkpoint=checkpoints.append)
            self.assertEqual([x[0] for x in complete.calls], ['worker', 'review'])
            self.assertFalse([r for r in records if r['record_kind'] == 'accepted'])
            self.assertEqual(checkpoints[-1]['status'], 'failed')
        for raw in ('{"claims":[],"coverage":[],"claims":[]}',
                    '{"claims":[],"coverage":[],"other":1e999}',
                    'outside {}\n```json\n{"claims":[],"coverage":[]}\n```'):
            with self.assertRaises(ValueError):
                contract.normalize_message(raw, 'worker')
        normalized = contract.normalize_message('Here it is\n```json\n{"claims":[],"coverage":[]}\n```\nDone', 'worker')
        self.assertIn('removed_external_prose_around_single_json_fence_v5', normalized['normalizations'])

    def test_valid_neighbors_and_all_invalid_individual_abstentions_survive(self):
        def mutate(stage, primary, payload, response):
            if stage == 'worker':
                response['claims'].append(dict(claim(), action='x' * 601))
            if stage == 'review':
                response['decisions'][1]['reason'] = 'x' * 241
                response['source_pass']['material_findings'] = [
                    {'evidence_span_ids': ['turn-0s01'], 'meaning': 'x' * 161}]
            return response
        result = capture_source(source(), Scripted(mutate), settings={}, versions={})
        self.assertEqual(len(result['accepted']), 1)
        self.assertEqual(len(result['disputed']), 1)
        self.assertTrue(result['disputed'][0]['invalid_record'])
        def invalid(stage, primary, payload, response):
            if stage == 'review':
                response['decisions'][0]['reason'] = 'x' * 241
            return response
        complete = Scripted(invalid)
        result = capture_source(source(), complete, settings={}, versions={})
        self.assertEqual(len(result['accepted']), 0)
        self.assertEqual(result['disputed'][0]['decision']['verdict'], 'unresolved')
        self.assertEqual([x[0] for x in complete.calls], ['worker', 'review', 'reconcile'])
        def omit(stage, primary, payload, response):
            if stage == 'review':
                response['omissions'] = [{'claim': claim(action='requests a lens check'), 'reason': 'additional proposal'}]
            return response
        result = capture_source(source(), Scripted(omit), settings={}, versions={'test': 'omission-v1'})
        self.assertEqual(len(result['omissions']), 1)
        self.assertEqual(result['omissions'][0]['disposition'], 'proposal_requires_separate_verification')
        self.assertEqual(result['omissions'][0]['versions'], {'test': 'omission-v1'})

    def test_one_repair_separate_verification_and_reconciliation_history(self):
        prior = {}
        def mutate(stage, primary, payload, response):
            if stage == 'review':
                prior['old'] = payload['CANDIDATES'][0]['claim']['claim_id']
                return review(primary, payload['CANDIDATES'], 'reject', True)
            if stage == 'repair':
                response['changes'] = [{'supersedes': prior['old'], 'claim': claim(action='requests lens inspection')}]
            if stage == 'repair_verify':
                prior['repaired'] = payload['CANDIDATES'][0]['claim']['claim_id']
                # Even a later repair request cannot open a second repair slot.
                response['decisions'][0]['request_repair'] = True
            if stage == 'reconcile':
                response['changes'] = [{'supersedes': prior['repaired'], 'claim': claim(action='requests inspection of blue lens')}]
            return response
        complete = Scripted(mutate)
        result = capture_source(source(), complete, settings={}, versions={'contract': 'test-v1'})
        self.assertEqual([x[0] for x in complete.calls], ['worker', 'review', 'repair', 'repair_verify', 'reconcile', 'reconcile_verify'])
        self.assertEqual(len(result['accepted']), 1)
        self.assertEqual(result['accepted'][0]['stage'], 'reconcile_verify')
        self.assertEqual(set(result['superseded_claim_ids']), {prior['old'], prior['repaired']})
        self.assertEqual(len(result['disputed']), 1)
        self.assertEqual(len([r for r in result['records'] if r['record_kind'] == 'accepted']), 2)
        self.assertTrue(result['accepted'][0]['requires_client_guidance'])
        self.assertEqual(result['accepted'][0]['guidance_status'], 'not_generated')

    def test_deterministic_requests_callbacks_lineage_and_immutable_record_hashes(self):
        data, records, checkpoints = source(), [], []
        first, second = Scripted(), Scripted()
        versions = {'engine': '0.4.0', 'storage': 'test-v4'}
        a = capture_source(data, first, settings={}, versions=versions, on_record=records.append,
                           on_checkpoint=checkpoints.append)
        b = capture_source(data, second, settings={}, versions=versions)
        self.assertEqual(a, b)
        self.assertEqual(records, a['records'])
        self.assertEqual(checkpoints, a['checkpoints'])
        self.assertEqual([c[1] for c in first.calls], [c[1] for c in second.calls])
        self.assertEqual(len({c[1] for c in first.calls}), len(first.calls))
        self.assertEqual([c['sequence'] for c in checkpoints], list(range(1, len(checkpoints) + 1)))
        for row in records:
            self.assertEqual(row['record_id'], 'item-' + contract.digest({k: v for k, v in row.items() if k != 'record_id'}))
            self.assertEqual(row['versions'], versions)
            self.assertEqual(row['profile_version'], PROFILE_VERSION)
        for cp in checkpoints:
            self.assertEqual(cp['checkpoint_id'], 'checkpoint-' + contract.digest({k: v for k, v in cp.items() if k != 'checkpoint_id'}))
        self.assertEqual(checkpoints[-1]['current_projection']['accepted_record_ids'], [r['record_id'] for r in a['accepted']])
        other = Scripted()
        changed = deepcopy(data)
        changed['metadata']['private'] += '-changed'
        capture_source(changed, other, settings={}, versions=versions)
        self.assertNotEqual(first.calls[0][1], other.calls[0][1])
        self.assertEqual(current_projection(records)['accepted'], a['accepted'])
        with self.assertRaisesRegex(CaptureError, 'source hash'):
            capture_source(data, Scripted(), settings={'source_hash': '0' * 64}, versions=versions)


if __name__ == '__main__':
    unittest.main()
