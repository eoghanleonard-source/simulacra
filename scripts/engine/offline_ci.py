"""Focused, network-blocked checks and source-package provenance; never deploys."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.engine.package_release import build_release

TEST_FILES = (
    'test_mapping.py', 'test_extraction.py', 'test_aggregation.py',
    'test_capture_v3.py', 'test_evidence_assertions.py',
    'test_native_recorded_run.py', 'test_native_capture.py', 'test_native_export_roundtrip.py',
    'test_recorded_lifecycle.py', 'test_reference_consumer.py',
    'test_export.py', 'test_export_v2.py', 'test_partial_scope.py',
    'test_release_package.py',
)
EVALUATION = 'configs/baselines/repaired-baseline-release40-20261103.public.json'


def sha(value):
    return hashlib.sha256(value).hexdigest()


def git(*args):
    result = subprocess.run(['git', '-C', str(ROOT), *args], capture_output=True, text=True)
    return result.returncode, result.stdout.strip()


def main():
    os.chdir(ROOT)
    os.environ['PYTEST_DISABLE_PLUGIN_AUTOLOAD'] = '1'
    for key in list(os.environ):
        if key.startswith('SNOWFLAKE_'):
            os.environ.pop(key)

    def offline(event, args):
        if event.startswith('socket.') or (event == 'import' and args[0].split('.')[0] == 'snowflake'):
            raise RuntimeError('Offline CI prohibits sockets and Snowflake connector imports')

    sys.addaudithook(offline)
    import pytest
    status = pytest.main(['-q', *['tests/engine/' + name for name in TEST_FILES]])
    if status:
        return int(status)
    archive, manifest = build_release(ROOT)
    repeated, repeated_manifest = build_release(ROOT)
    if archive != repeated or manifest != repeated_manifest:
        raise ValueError('Source archive is not reproducible')
    _, commit = git('rev-parse', '--verify', 'HEAD')
    names = [record['path'] for record in manifest['files']]
    clean_code, _ = git('diff', '--quiet', 'HEAD', '--', *names)
    tracked_code, _ = git('ls-files', '--error-unmatch', '--', *names)
    matches_commit = bool(commit) and clean_code == tracked_code == 0
    if os.environ.get('GITHUB_ACTIONS') == 'true' and not matches_commit:
        raise ValueError('CI source archive does not match tracked files at the checked-out commit')
    output = ROOT / 'dist/offline-ci'
    output.mkdir(parents=True, exist_ok=True)
    (output / 'source.zip').write_bytes(archive)
    (output / 'source-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    evaluation = json.loads((ROOT / EVALUATION).read_text())
    attestation = {
        'kind': 'offline-source-provenance-v1',
        'commit': commit or None,
        'source_matches_commit': matches_commit,
        'source_state': 'committed' if matches_commit else 'uncommitted_local_snapshot',
        'source_package_version': manifest['engine_version'],
        'source_manifest_sha256': sha(json.dumps(manifest, sort_keys=True, separators=(',', ':')).encode()),
        'source_archive_sha256': sha(archive),
        'reproducible_archive': True,
        'evaluation_record': EVALUATION,
        'evaluation_record_sha256': sha((ROOT / EVALUATION).read_bytes()),
        'historical_candidate_archive_sha256': evaluation['candidate_archive_sha256'],
        'historical_candidate_is_this_archive': False,
        'qualification_decision': evaluation['decision'],
        'scope': 'Offline software/package checks; no new semantic evaluation or client qualification',
        'hosted_model_or_warehouse_calls': 0,
    }
    (output / 'attestation.json').write_text(json.dumps(attestation, indent=2) + '\n')
    print(json.dumps(attestation, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
