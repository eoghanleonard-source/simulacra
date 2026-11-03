"""Offline candidate regressions: structural isolation, never semantic benchmark proof."""
from copy import deepcopy
import json
import unittest

from snowflake_engine import contracts, extraction
from tests.engine.test_extraction import source, response, cite


def assertion(actor='buyer', recipient='staff', state='requested'):
    return {'actor': actor, 'recipient': recipient, 'action': 'replace lens',
            'scope': 'damaged lens; completion unknown', 'state': state, 'certainty': 'explicit'}


def responses(data):
    old = response(data)
    for need in old['scenarios'][0]['needs']:
        need['assertion'] = assertion()
    for action in old['personas'][0]['actions']:
        action['assertion'] = assertion()
    return {kind: {collection: old[collection], 'capture_status': old['capture_status'],
                   'read_receipt': old['read_receipt']}
            for kind, collection in (('scenario', 'scenarios'), ('persona', 'personas'))}


class CaptureV3Tests(unittest.TestCase):
    def setUp(self):
        self.source = source()
        self.values = responses(self.source)

    def parse(self, kind='persona'):
        return extraction.parse_response_v3(self.source, json.dumps(self.values[kind]), kind)

    def test_two_independent_complete_source_calls_and_caller_owned_cap(self):
        calls = []
        def complete(messages, operation, key):
            kind = operation.removeprefix('extraction_v3_')
            calls.append((messages, operation, key))
            return json.dumps(self.values[kind])
        result = extraction.extract_v3(self.source, complete)
        self.assertEqual(['extraction_v3_scenario', 'extraction_v3_persona'], [c[1] for c in calls])
        self.assertNotEqual(calls[0][2], calls[1][2])
        self.assertEqual(calls[0][0][1], calls[1][0][1])
        for messages, _, _ in calls:
            for event in self.source['events']:
                self.assertIn(event['text'], messages[1]['content'])
            self.assertIn('6144', messages[0]['content'])
        self.assertEqual(6144, extraction.MAX_OUTPUT_TOKENS_V3)
        self.assertEqual(6144, result['candidate']['max_output_tokens_per_call'])
        self.assertFalse(result['candidate']['semantic_review_performed'])
        self.assertEqual({'valid'}, {c['validation_status'] for c in result['components']})

    def test_each_layer_can_truncate_without_erasing_other_layer_and_no_retry(self):
        for bad_kind in ('scenario', 'persona'):
            calls = []
            raws = {k: json.dumps(v) for k, v in self.values.items()}
            raws[bad_kind] = '{"' + bad_kind + 's":['
            def complete(messages, operation, key):
                kind = operation.removeprefix('extraction_v3_')
                calls.append(kind)
                return raws[kind]
            result = extraction.extract_v3(self.source, complete)
            self.assertEqual(['scenario', 'persona'], calls)
            self.assertEqual(raws, result['raw_responses'])
            self.assertEqual(1, len(result['components']))
            self.assertNotEqual(bad_kind, result['components'][0]['kind'])
            self.assertEqual('valid', result['components'][0]['validation_status'])
            self.assertEqual('invalid_json', result['issues'][0]['code'])
            self.assertEqual(result, {**extraction.reprocess_v3(self.source, raws), 'output_style': 'compact'})

    def test_visible_schema_is_exact_validator_contract_and_unknown_enum_fails(self):
        for kind in ('scenario', 'persona'):
            system = extraction.build_messages_v3(self.source, kind)[0]['content']
            text = system.split('\nSCHEMA:\n', 1)[1].split('\nDEFINITIONS:\n', 1)[0].split('\nReturn minified', 1)[0]
            self.assertEqual(contracts.CAPTURE_SCHEMAS_V3[kind], json.loads(text))
        p = self.values['persona']['personas'][0]
        p['emotional_expression']['observations'][0]['labels'] = ['anxious']
        result = self.parse()
        original, projection = result['components']
        self.assertEqual('quarantined', original['validation_status'])
        self.assertTrue(any('invalid canonical label' in e for e in original['issues']))
        self.assertEqual('valid', projection['validation_status'])
        self.assertEqual('unknown', projection['payload']['emotional_expression']['status'])
        self.assertEqual(['anxious'], original['payload']['emotional_expression']['observations'][0]['labels'])
        self.assertEqual(json.dumps(self.values['persona']), result['raw_response'])

    def test_bad_emotion_preserves_actions_sequences_and_explicit_partial_projection(self):
        p = self.values['persona']['personas'][0]
        p['emotional_expression']['status'] = 'calm'
        original, projection = self.parse()['components']
        self.assertEqual(p, original['payload'])
        self.assertEqual('quarantined', original['validation_status'])
        self.assertEqual('partial', original['partial_state'])
        self.assertNotEqual(original['object_id'], projection['object_id'])
        self.assertEqual(original['object_id'], projection['projection_of'])
        self.assertEqual('validated_children_only', projection['delivery_scope'])
        self.assertEqual('partial', projection['partial_state'])
        self.assertEqual(p['actions'], projection['payload']['actions'])
        self.assertEqual(p['sequences'], projection['payload']['sequences'])
        self.assertEqual([], projection['payload']['emotional_changes'])
        self.assertIn('withheld invalid children', projection['payload']['unknowns'][-1])
        children = original['child_records']
        self.assertEqual(6, len(children))  # 3 actions, 1 sequence, 2 emotion layers
        self.assertEqual(4, sum(c['validation_status'] == 'valid' for c in children))
        self.assertEqual({'total': 6, 'valid': 4, 'quarantined': 2, 'delivery_eligible': 4}, original['child_accounting'])
        self.assertEqual(2, len(projection['withheld_layers']))
        self.assertEqual([], contracts.validate_component_v3('persona', projection['payload'], self.source))

    def test_observed_neutral_mismatch_is_schema_visible_and_does_not_erase_actions(self):
        p = self.values['persona']['personas'][0]
        p['emotional_expression']['observations'][0]['labels'] = ['neutral']
        schema = contracts.PERSONA_SCHEMA_V3['properties']['emotional_expression']
        self.assertIn('oneOf', schema)
        self.assertTrue(contracts.shape_errors_v3(p['emotional_expression'], schema))
        original, projection = self.parse()['components']
        self.assertEqual('quarantined', original['validation_status'])
        self.assertEqual(p['actions'], projection['payload']['actions'])
        self.assertEqual('unknown', projection['payload']['emotional_expression']['status'])
        self.assertEqual(['neutral'], original['payload']['emotional_expression']['observations'][0]['labels'])
        p['emotional_expression']['status'] = 'neutral_assessed'
        p['emotional_expression']['observations'] = [p['emotional_expression']['observations'][0]]
        p['emotional_changes'] = []
        self.assertEqual([], contracts.shape_errors_v3(p['emotional_expression'], schema))
        self.assertEqual('valid', self.parse()['components'][0]['validation_status'])

    def test_emotion_abstention_schema_requires_empty_observations(self):
        expression = self.values['persona']['personas'][0]['emotional_expression']
        schema = contracts.EMOTION_EXPRESSION_SCHEMA_V3
        expression['status'] = 'unknown'
        self.assertTrue(contracts.shape_errors_v3(expression, schema))
        expression['observations'] = []
        self.assertEqual([], contracts.shape_errors_v3(expression, schema))

    def test_bad_action_and_sequence_are_individually_withheld(self):
        p = self.values['persona']['personas'][0]
        p['actions'][0]['function'] = 'invented'
        p['sequences'][0]['evidence'] = [cite(self.source, 4), cite(self.source, 4)]
        original, projection = self.parse()['components']
        self.assertEqual(2, len(projection['payload']['actions']))
        self.assertEqual([], projection['payload']['sequences'])
        self.assertEqual(p['emotional_expression'], projection['payload']['emotional_expression'])
        bad = [c for c in original['child_records'] if c['validation_status'] == 'quarantined']
        self.assertEqual({'persona_action', 'persona_sequence'}, {c['kind'] for c in bad})
        self.assertEqual(p['actions'][0], bad[0]['payload'])

    def test_no_false_completion_or_verification_inferred_by_validator(self):
        p = self.values['persona']['personas'][0]
        p['actions'][0]['assertion'] = assertion(state='in_progress')
        p['actions'][0]['summary'] = 'Identity verification succeeded.'
        result = self.parse()
        self.assertEqual('valid', result['components'][0]['validation_status'])
        self.assertEqual('in_progress', result['components'][0]['payload']['actions'][0]['assertion']['state'])
        self.assertFalse(result['candidate']['semantic_review_performed'])
        # The summary is deliberately false: structural validity cannot adjudicate it.
        p['actions'][0]['assertion']['state'] = 'verified_completed'
        self.assertEqual('quarantined', self.parse()['components'][0]['validation_status'])
        p['actions'][0]['assertion']['state'] = 'source_recorded_completed'
        self.assertTrue(any('cited source action record' in e for e in self.parse()['components'][0]['issues']))
        p['actions'][0]['assertion']['state'] = 'reported_completed'
        self.assertEqual('valid', self.parse()['components'][0]['validation_status'])
        self.assertEqual('reported_completed', p['actions'][0]['assertion']['state'])

    def test_source_record_completion_needs_record_not_just_speaker_claim(self):
        self.source['events'][3]['kind'] = 'action'
        n = self.values['scenario']['scenarios'][0]['needs'][3]
        n['assertion'] = assertion(actor='staff', recipient='buyer', state='source_recorded_completed')
        self.assertEqual('valid', self.parse('scenario')['components'][0]['validation_status'])
        # This still tests only record type and citation, not that the text says complete.
        self.assertIn('pending', n['evidence'][0]['quote'])

    def test_bad_coverage_or_duplicate_parent_cannot_deliver_projection(self):
        self.values['persona']['personas'][0]['emotional_expression']['status'] = 'calm'
        self.values['persona']['capture_status'] = 'output_limit'
        result = self.parse()
        self.assertEqual(1, len(result['components']))
        self.assertEqual('quarantined', result['components'][0]['validation_status'])
        self.values['persona']['capture_status'] = 'complete'
        self.values['persona']['personas'] *= 2
        result = self.parse()
        self.assertEqual(2, len(result['components']))
        self.assertEqual({'quarantined'}, {c['validation_status'] for c in result['components']})

    def test_unknown_emotion_stays_unknown_without_forced_neutral(self):
        p = self.values['persona']['personas'][0]
        p['emotional_expression'] = {'status': 'unknown', 'observations': []}
        p['emotional_changes'] = []
        components = self.parse()['components']
        self.assertEqual(1, len(components))
        self.assertEqual('valid', components[0]['validation_status'])
        self.assertEqual('unknown', components[0]['payload']['emotional_expression']['status'])

    def test_originals_not_sent_and_both_requests_preflight_before_callback(self):
        self.source['events'][0]['original_text'] = 'ORIGINAL-SECRET'
        self.source['metadata']['raw_record'] = {'body': 'METADATA-SECRET'}
        for kind in ('scenario', 'persona'):
            packet = json.dumps(extraction.build_messages_v3(self.source, kind))
            self.assertNotIn('ORIGINAL-SECRET', packet)
            self.assertNotIn('METADATA-SECRET', packet)
        scenario_size = sum(len(m['content']) for m in extraction.build_messages_v3(self.source, 'scenario'))
        with self.assertRaisesRegex(ValueError, 'input_limit'):
            extraction.extract_v3(self.source, lambda *_: self.fail('no calls before both preflight'), max_input_chars=scenario_size)
        self.source['metadata']['omitted_event_count'] = 1
        with self.assertRaisesRegex(ValueError, 'Omitted or clipped'):
            extraction.extract_v3(self.source, lambda *_: self.fail('no inference for clipped inputs'))

    def test_provider_error_is_not_retried(self):
        calls = []
        def broken(*args):
            calls.append(args)
            raise RuntimeError('synthetic provider failure')
        with self.assertRaisesRegex(RuntimeError, 'synthetic'):
            extraction.extract_v3(self.source, broken)
        self.assertEqual(1, len(calls))

    def test_candidate_keys_are_not_legacy_keys_and_layers_are_distinct(self):
        calls = []
        extraction.extract(self.source, lambda m, op, key: calls.append(key) or json.dumps(response(self.source)))
        extraction.extract_v3(self.source, lambda m, op, key: calls.append(key) or json.dumps(self.values[op.removeprefix('extraction_v3_')]))
        self.assertEqual(3, len(set(calls)))
        self.assertNotEqual(contracts.VERSIONS, extraction.versions_v3())


if __name__ == '__main__':
    unittest.main()
