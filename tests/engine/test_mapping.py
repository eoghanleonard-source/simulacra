"""Complete-source mapping checks; no provider calls or external data."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from snowflake_engine.mapping import MappingError, normalize

ROOT = Path(__file__).resolve().parents[2]


def fixtures():
    mapping = json.loads((ROOT / 'configs/engine/synthetic_mapping.json').read_text())
    raw = json.loads((ROOT / 'fixtures/engine/synthetic_sources.json').read_text())
    return raw, mapping


class MappingTests(unittest.TestCase):
    def test_all_source_types_and_input_accounting(self):
        raw, mapping = fixtures()
        result = normalize(raw, mapping, 'fictional-test')
        facts = json.loads((ROOT / 'fixtures/engine/expected_facts.json').read_text())
        for key, expected in facts['expected_accounting'].items():
            self.assertEqual(result['accounting'][key], expected, key)
        self.assertEqual(len(result['accounting']['records']), len(raw))
        self.assertFalse(result['accounting']['body_clipping'])
        self.assertEqual(result['accounting']['omitted_event_count'], 0)
        self.assertEqual({s['source_type'] for s in result['sources']}, {'case', 'typed_chat', 'call_transcript'})
        by_id = {s['source_id']: s for s in result['sources']}
        for source_id, expected in facts['sources'].items():
            source = by_id[source_id]
            self.assertEqual(len(source['events']), expected['event_count'])
            events = {e['event_id']: e for e in source['events']}
            for fact in expected.get('must_preserve', []):
                self.assertTrue(set(fact['event_ids']) <= events.keys())
        self.assertTrue(any(i['raw_id'] == 'raw-orphan-note' and i['code'] == 'unlinked_record' for i in result['issues']))
        self.assertNotIn(None, by_id)

    def test_complete_text_order_attribution_and_unknown_time(self):
        raw, mapping = fixtures()
        result = normalize(raw, mapping, 'fictional-test')
        sources = {s['source_id']: s for s in result['sources']}
        support = sources['support-pending']
        self.assertEqual([e['ordinal'] for e in support['events']], list(range(8)))
        self.assertEqual([e['original_text'] for e in support['events']],
                         [raw[0]['description'] + '\ncase_status: ' + raw[0]['status'] + '\nchannel: ' + raw[0]['channel'] + '\nsubject: ' + raw[0]['subject'], raw[0]['notes'][0]['body']] + [m['body'] for m in raw[0]['messages']])
        self.assertEqual(support['events'][0]['authority'], 'case_report')
        self.assertEqual(support['events'][1]['authority'], 'attributed_note')
        self.assertEqual(support['events'][1]['speaker'], 'support-reporter')
        self.assertEqual(support['guidance'][0]['authority'], 'fictional')
        for source_id in ['support-completed', 'sales-call', 'negotiation-chat']:
            self.assertNotIn('case_id', sources[source_id]['metadata'])
            self.assertTrue(all(e['timestamp'] is None and e['timing_status'] == 'unknown' for e in sources[source_id]['events']))
        self.assertEqual(sources['support-note-only']['metadata']['customer_side_speaker_ids'], [])
        self.assertEqual(sources['negotiation-chat']['metadata']['customer_side_speaker_ids'], ['peer-a', 'peer-b'])
        self.assertEqual(sources['sales-call']['metadata']['timing'], raw[3]['timing'])

    def test_mapping_transport_key_order_preserves_sources_and_hashes(self):
        from snowflake_engine.contracts import source_hash
        raw, mapping = fixtures()
        original = normalize(raw, mapping, 'fictional-test')
        transported = normalize(json.loads(json.dumps(raw, sort_keys=True)),
                                json.loads(json.dumps(mapping, sort_keys=True)), 'fictional-test')
        self.assertEqual(original, transported)
        for local, native in zip(original['sources'], transported['sources']):
            self.assertEqual(source_hash(local), source_hash(native), local['source_id'])
        context = original['sources'][0]['events'][0]['text']
        self.assertLess(context.index('case_status:'), context.index('channel:'))
        self.assertLess(context.index('channel:'), context.index('subject:'))

    def test_explicit_case_context_fields_enter_permitted_text_after_redaction(self):
        from snowflake_engine.extraction import build_messages
        raw, mapping = fixtures()
        raw[0]['subject'] = 'Material title-only condition: demo-customer@example.invalid'
        source = normalize([raw[0]], mapping, 'test')['sources'][0]
        context = source['events'][0]
        self.assertIn(raw[0]['subject'], context['original_text'])
        self.assertIn('Material title-only condition: [EMAIL]', context['text'])
        self.assertIn('case_status: pending', context['text'])
        self.assertIn('channel: typed_chat', context['text'])
        packet = build_messages(source)[1]['content']
        self.assertIn('Material title-only condition: [EMAIL]', packet)
        self.assertNotIn('demo-customer@example.invalid', packet)
        self.assertNotIn('"subject":', packet)  # raw metadata stays outside the allowlist

    def test_deterministic_redaction_and_supplied_redaction_are_distinct(self):
        raw, mapping = fixtures()
        supplied = raw[1]['messages'][0]
        supplied['original_body'] = 'Original fictional identifier DEMO-001'
        supplied['body'] = 'Original fictional identifier [ID]'
        supplied['redaction_status'] = 'supplied_redacted'
        result = normalize(raw, mapping, 'fictional-test')
        support = result['sources'][0]['events'][2]
        self.assertIn('demo-customer@example.invalid', support['original_text'])
        self.assertIn('[EMAIL]', support['text'])
        self.assertEqual(support['redaction_status'], 'redacted')
        event = result['sources'][1]['events'][0]
        self.assertEqual(event['original_text'], supplied['original_body'])
        self.assertEqual(event['text'], supplied['body'])
        self.assertEqual(event['redaction_status'], 'supplied_redacted')
        mapping['redaction']['reviewed'] = False
        self.assertEqual(normalize(raw, mapping, 'test')['sources'][0]['events'][0]['redaction_status'], 'not_reviewed')

    def test_mapped_fields_are_client_configurable_without_prompt_changes(self):
        mapping = {'record_type_field': 'kind', 'raw_id_field': 'record', 'redaction': {'reviewed': True},
                   'types': {'call': {'source_type': 'call_transcript', 'fields': {'source_id': 'context.key'},
                                     'metadata': {'customer_side_speaker_ids': 'subjects'}, 'events': [
                       {'path': 'turns', 'fields': {'event_id': 'key', 'text': 'words', 'original_text': 'original',
                                                  'ordinal': 'order', 'timestamp': 'time', 'kind': 'type',
                                                  'role': 'who.role', 'speaker': 'who.key', 'authority': 'provenance',
                                                  'timing_status': 'time_status', 'redaction_status': 'privacy'}}]}}}
        raw = [{'kind': 'call', 'record': 'r', 'context': {'key': 's'}, 'subjects': ['p'], 'turns': [
            {'key': 'b', 'order': 1, 'words': 'second', 'original': 'second original', 'type': 'utterance',
             'who': {'role': 'buyer', 'key': 'p'}, 'provenance': 'participant_statement', 'privacy': 'supplied_redacted',
             'time': '2026-10-01T10:00:01+10:00', 'time_status': 'known'},
            {'key': 'a', 'order': 0, 'words': '', 'type': 'utterance', 'who': {}, 'provenance': 'unknown'}]}]
        copy = deepcopy(raw)
        result = normalize(raw, mapping, 'custom')
        self.assertEqual(raw, copy)
        events = result['sources'][0]['events']
        self.assertEqual([e['event_id'] for e in events], ['a', 'b'])
        self.assertEqual(events[0]['text'], '')
        self.assertEqual(events[0]['role'], 'unknown')
        self.assertEqual(events[1]['speaker'], 'p')
        self.assertEqual(events[1]['original_text'], 'second original')
        self.assertEqual(events[1]['timestamp'], '2026-10-01T10:00:01+10:00')

    def test_notes_never_become_direct_customer_speech(self):
        raw, mapping = fixtures()
        raw[2]['notes'][0]['author_role'] = 'customer'
        mapping['types']['support_case']['events'][1]['defaults']['authority'] = 'customer_statement'
        note = normalize([raw[2]], mapping, 'test')['sources'][0]['events'][1]
        self.assertEqual(note['role'], 'customer')
        self.assertEqual(note['authority'], 'attributed_note')

    def test_malformed_event_quarantines_complete_source_and_retains_raw(self):
        raw, mapping = fixtures()
        for mutation in ['text', 'time', 'ordinal', 'identity', 'role', 'redaction', 'timing']:
            row = deepcopy(raw[1])
            if mutation == 'text': row['messages'][1]['body'] = {'not': 'text'}
            if mutation == 'time': row['messages'][1]['sent_at'] = '2026-10-01T00:00:00'
            if mutation == 'ordinal': row['messages'][1]['position'] = 0
            if mutation == 'identity': row['messages'][1]['id'] = row['messages'][0]['id']
            if mutation == 'role': row['messages'][1]['role'] = {'invalid': 'role'}
            if mutation == 'redaction': row['messages'][1]['redaction_status'] = 'arbitrary'
            if mutation == 'timing':
                mapping = deepcopy(mapping)
                mapping['types']['typed_chat']['events'][0]['fields']['timing_status'] = 'time_status'
                row['messages'][1]['time_status'] = 'known'
            result = normalize([row], mapping, 'test')
            self.assertEqual(result['sources'], [], mutation)
            self.assertEqual(result['accounting']['excluded_event_count'], 3)
            self.assertTrue(any(i['code'] == 'malformed_event' for i in result['issues']))
            self.assertEqual(result['issues'][0]['raw_source'], row)

    def test_bad_rows_and_duplicate_sources_are_visibly_accounted(self):
        raw, mapping = fixtures()
        records = [raw[1], raw[1], {'record_id': 'unknown', 'record_type': 'unsupported'}, None, raw[-1]]
        result = normalize(records, mapping, 'test')
        self.assertEqual(len(result['sources']), 1)
        self.assertEqual(result['accounting']['excluded_record_count'], 4)
        self.assertEqual(len(result['accounting']['records']), 5)
        self.assertTrue({'duplicate_source_id', 'unsupported_record_type', 'malformed_record', 'unlinked_record'} <= {i['code'] for i in result['issues']})

    def test_invalid_configuration_fails_before_mapping(self):
        raw, mapping = fixtures()
        mapping['redaction']['rules'][0]['pattern'] = '['
        with self.assertRaises(MappingError): normalize(raw, mapping, 'test')
        with self.assertRaises(MappingError): normalize(raw, {}, 'test')


if __name__ == '__main__':
    unittest.main()
