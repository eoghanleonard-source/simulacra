"""Build a deterministic, allowlisted engine source release without live calls.

New files require a deliberate allowlist change. Original source bytes are
preserved; archive metadata is normalized and a content manifest is included.
"""
from __future__ import annotations

import argparse
import ast
import gzip
import hashlib
import io
import json
from pathlib import Path
import re
import tarfile
import zipfile


RELEASE_FILES = (
    "README.md", "pyproject.toml", "Makefile",
    "snowflake_engine/__init__.py", "snowflake_engine/aggregation.py",
    "snowflake_engine/cli.py", "snowflake_engine/contracts.py",
    "snowflake_engine/export.py", "snowflake_engine/extraction.py",
    "snowflake_engine/mapping.py", "snowflake_engine/native.py",
    "snowflake_engine/schema.py", "snowflake_engine/versioning.py", "snowflake_engine/practice.py", "snowflake_engine/reprocessing.py",
    "snowflake_engine/source_revision.py", "snowflake_engine/practice_objectives.py",
    "snowflake_engine/capture_contract.py", "snowflake_engine/capture_framing.py", "snowflake_engine/capture_prompts.py",
    "snowflake_engine/capture_evidence.py", "snowflake_engine/capture_storage.py",
    "snowflake_engine/capture_cards.py", "snowflake_engine/transcript_cards.py",
    "snowflake_engine/evidence_assertions.py",
    "scripts/engine/package_release.py", "scripts/engine/preflight.py",
    "scripts/engine/recover.py", "scripts/engine/inspect_state.py",
    "scripts/engine/verify_smoke.py",
    "scripts/engine/reference_consumer.py",
    "scripts/engine/offline_ci.py",
    "configs/baselines/repaired-baseline-release40-20261103.public.json",
    "scripts/demo/compose_supported_record.py", "scripts/demo/prove_recorded_lifecycle.py",
    "configs/engine/example_config.json", "configs/engine/synthetic_mapping.json",
    "fixtures/engine/README.md", "fixtures/engine/synthetic_sources.json",
    "fixtures/engine/expected_facts.json",
    "deploy/engine/fixtures/recorded-delivery/README.md",
    "deploy/engine/fixtures/recorded-delivery/lifecycle-fixture.json",
    "deploy/engine/fixtures/recorded-delivery/fictional-mapping.json",
    "deploy/engine/fixtures/recorded-delivery/fictional-raw-records.json",
    "deploy/engine/fixtures/recorded-delivery/baseline-recorded-responses.json",
    "deploy/engine/fixtures/recorded-delivery/recorded-smoke-config.template.json",
    "deploy/engine/README.md", "deploy/engine/requirements.txt",
    "docs/snowflake-engine.md", "docs/repository-layout.md",
    "docs/engine/data-dictionary.md", "docs/engine/validation.md",
    "docs/engine/known-limitations.md", "docs/engine/warehouse-intake.md", "docs/engine/versioned-operation.md",
    "docs/engine/native-capture-evidence.md",
    "docs/engine/portable-delivery.md",
    "docs/engine/client-tuning-handover.md",
    "docs/engine/git-source-baseline.md",
    "tests/engine/test_aggregation.py", "tests/engine/test_export.py",
    "tests/engine/test_extraction.py", "tests/engine/test_install.py",
    "tests/engine/test_mapping.py", "tests/engine/test_native.py",
    "tests/engine/test_release_package.py", "tests/engine/test_capture_v3.py",
    "tests/engine/test_export_v2.py", "tests/engine/test_versioning_v2.py", "tests/engine/test_practice.py",
    "tests/engine/test_retained_replay.py", "tests/engine/test_partial_scope.py", "tests/engine/test_retained_import.py",
    "tests/engine/test_source_revision.py", "tests/engine/test_native_source_revision.py", "tests/engine/test_practice_objectives.py",
    "tests/engine/test_capture_storage.py",
    "tests/engine/test_capture_evidence.py", "tests/engine/test_native_capture.py",
    "tests/engine/test_capture_framing.py", "tests/engine/test_capture_continuation.py", "tests/engine/test_native_recorded_run.py",
    "tests/engine/test_native_issue_linkage.py",
    "tests/engine/test_recorded_lifecycle.py", "tests/engine/test_native_export_roundtrip.py",
    "tests/engine/test_reference_consumer.py",
    "tests/engine/test_evidence_assertions.py",
)
MANIFEST_NAME = "release-manifest.json"
ZIP_TIMESTAMP = (2026, 1, 1, 0, 0, 0)


def collect_files(source_root):
    """Read only regular allowlisted files; never follow a symlink."""
    root = Path(source_root).resolve(strict=True)
    files = {}
    for name in sorted(RELEASE_FILES):
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"Unsafe release path: {name}")
        path = root
        for part in relative.parts:
            path = path / part
            if path.is_symlink():
                raise ValueError(f"Release source must not be a symlink: {name}")
        if not path.is_file():
            raise ValueError(f"Missing required release file: {name}")
        files[name] = path.read_bytes()
    return files


def engine_version(source):
    """Read the literal version without importing the engine."""
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "__version__"
            for target in node.targets
        ):
            version = ast.literal_eval(node.value)
            if isinstance(version, str) and re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
                return version
    raise ValueError("Engine must declare a literal major.minor.patch version")


def build_release(source_root, archive_format="zip"):
    """Return deterministic archive bytes and the enclosed manifest."""
    if archive_format not in {"zip", "tar.gz"}:
        raise ValueError("Release format must be zip or tar.gz")
    files = collect_files(source_root)
    version = engine_version(files["snowflake_engine/__init__.py"])
    prefix = f"snowflake-engine-{version}"
    manifest = {
        "format_version": "engine-source-release-v1", "engine_version": version,
        "files": [
            {"path": name, "size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
            for name, data in files.items()
        ],
    }
    files[MANIFEST_NAME] = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    buffer = io.BytesIO()
    if archive_format == "zip":
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, data in sorted(files.items()):
                info = zipfile.ZipInfo(f"{prefix}/{name}", date_time=ZIP_TIMESTAMP)
                info.create_system = 3
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                archive.writestr(info, data)
    else:
        with gzip.GzipFile(fileobj=buffer, filename="", mode="wb", mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w", format=tarfile.USTAR_FORMAT) as archive:
                for name, data in sorted(files.items()):
                    info = tarfile.TarInfo(f"{prefix}/{name}")
                    info.size, info.mode, info.mtime = len(data), 0o644, 0
                    info.uid = info.gid = 0
                    info.uname = info.gname = ""
                    archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue(), manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--output", type=Path, default=Path("dist/snowflake-engine.zip"))
    parser.add_argument("--format", choices=("zip", "tar.gz"), default="zip")
    args = parser.parse_args(argv)
    try:
        output = args.output.absolute()
        if any(part.is_symlink() for part in (output, *output.parents)):
            raise ValueError("Release output must not use a symlink")
        if output.resolve() in {args.source_root.resolve() / name for name in RELEASE_FILES}:
            raise ValueError("Release output must not overwrite an allowlisted source file")
        payload, manifest = build_release(args.source_root, args.format)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(payload)
    except (OSError, ValueError, SyntaxError) as error:
        parser.error(str(error))
    print(json.dumps({"archive": str(args.output), "format": args.format,
                      "engine_version": manifest["engine_version"],
                      "file_count": len(manifest["files"]), "size_bytes": len(payload),
                      "sha256": hashlib.sha256(payload).hexdigest()}, sort_keys=True))


if __name__ == "__main__":
    main()
