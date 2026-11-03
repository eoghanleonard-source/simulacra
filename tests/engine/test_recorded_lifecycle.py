"""Actual ordinary-engine lifecycle with fictional fixtures; no hosted calls."""
import json

from scripts.demo.prove_recorded_lifecycle import prove, request


def test_recorded_ordinary_lifecycle_keeps_cache_revisions_releases_and_failures(tmp_path):
    report = prove(tmp_path)
    assert [row['new_recorded_fixture_attempts'] for row in report['runs']] == [4, 0, 2, 2]
    assert report['total_recorded_fixture_attempts'] == 8
    assert report['real_hosted_model_calls'] == report['real_hosted_embedding_calls'] == 0
    assert report['old_release_immutable']
    ledger = json.loads((tmp_path / 'ledger.json').read_text())
    assert len(ledger['RUNS']) == 4
    assert len(ledger['INPUT_RAW']) == len(ledger['INPUT_SOURCES']) == 8
    assert len(ledger['ATTEMPTS']) == 8
    assert len({row['ATTEMPT_ID'] for row in ledger['ATTEMPTS']}) == 8
    failed = json.loads((tmp_path / 'failed.json').read_text())
    assert failed['run']['status'] == 'partial'
    assert failed['run']['release_id']  # eligible unaffected source remains visible
    assert len(failed['input_issues']) == 2
    service = next(row for row in failed['input_raw'] if row['RAW_ID'] == 'fictional-service-01')
    expected = request('failed', 'fictional-lifecycle-failed')['raw_records'][0]
    assert service['PAYLOAD']['record'] == expected
    assert all(row['SOURCE_ID'] == 'fictional-sales-01' for row in ledger['COMPONENTS']
               if row['RUN_ID'] == 'fictional-lifecycle-failed')
    assert {row['STATUS'] for row in failed['attempts']} == {'succeeded'}
    # Successful fixture transport is distinct from rejected extraction JSON.
    assert all(row['PAYLOAD']['provider_model_calls'] == 0 for row in ledger['ATTEMPTS'])
