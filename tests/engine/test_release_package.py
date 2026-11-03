"""Verify that source releases are bounded and work outside this checkout."""
import ast
import hashlib
import io
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import tarfile
import tomllib
import uuid
import zipfile

import pytest


ROOT = Path(__file__).resolve().parents[2]
RELEASE = runpy.run_path(str(ROOT / "scripts/engine/package_release.py"))


@pytest.fixture
def source_tree(tmp_path):
    root = tmp_path / "source"
    for name in RELEASE["RELEASE_FILES"]:
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    return root


def members(payload, archive_format):
    if archive_format == "zip":
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            return {name: archive.read(name) for name in archive.namelist()}
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
        assert all(item.isfile() for item in archive.getmembers())
        return {item.name: archive.extractfile(item).read() for item in archive.getmembers()}


@pytest.mark.parametrize("archive_format", ["zip", "tar.gz"])
def test_release_allowlist_excludes_local_secrets_corpora_and_coordination(source_tree, archive_format):
    sentinel = uuid.uuid4().hex.encode()
    for name in (
        ".env", ".aws/credentials", ".codex/config.toml", "AGENTS.md",
        "archive/legacy/root/pyproject.toml", "training_sim/__init__.py",
        "datasets/client.json", "evidence/provider-output.json", "acceptance/private.json",
        "dispatch/feature-registry.yaml", "docs/programme-scoreboard.md",
        "configs/engine/.env", "fixtures/engine/client.json", "deploy/engine/private.key",
        "snowflake_engine/secret.py", "snowflake_engine/__pycache__/native.pyc",
        "scripts/engine/fidelity_cycle.py",
    ):
        path = source_tree / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(sentinel)
    payload, manifest = RELEASE["build_release"](source_tree, archive_format)
    content = members(payload, archive_format)
    prefix = f"snowflake-engine-{manifest['engine_version']}/"
    assert set(content) == {prefix + name for name in RELEASE["RELEASE_FILES"]} | {
        prefix + RELEASE["MANIFEST_NAME"]
    }
    assert all(sentinel not in value for value in content.values())
    assert json.loads(content[prefix + RELEASE["MANIFEST_NAME"]]) == manifest
    for record in manifest["files"]:
        data = content[prefix + record["path"]]
        assert data == (source_tree / record["path"]).read_bytes()
        assert record["size_bytes"] == len(data)
        assert record["sha256"] == hashlib.sha256(data).hexdigest()


@pytest.mark.parametrize("archive_format", ["zip", "tar.gz"])
def test_release_is_deterministic_across_timestamps_permissions_and_locations(source_tree, tmp_path, archive_format):
    first, _ = RELEASE["build_release"](source_tree, archive_format)
    other = tmp_path / "different-location"
    shutil.copytree(source_tree, other)
    for name in RELEASE["RELEASE_FILES"]:
        path = other / name
        os.utime(path, (1234567890, 1234567890))
        path.chmod(0o600)
    second, _ = RELEASE["build_release"](other, archive_format)
    assert first == second


@pytest.mark.parametrize("symlink_parent", [False, True])
def test_release_refuses_symlink_sources(source_tree, tmp_path, symlink_parent):
    if symlink_parent:
        real = tmp_path / "external-configs"
        shutil.move(source_tree / "configs", real)
        (source_tree / "configs").symlink_to(real, target_is_directory=True)
    else:
        target = source_tree / "README.md"
        target.unlink()
        target.symlink_to(ROOT / "README.md")
    with pytest.raises(ValueError, match="symlink"):
        RELEASE["build_release"](source_tree)


def test_release_refuses_incomplete_tree_and_output_overwrite(source_tree, capsys):
    with pytest.raises(SystemExit) as error:
        RELEASE["main"](["--source-root", str(source_tree), "--output", str(source_tree / "README.md")])
    assert error.value.code == 2
    assert "overwrite" in capsys.readouterr().err
    (source_tree / "snowflake_engine/native.py").unlink()
    with pytest.raises(ValueError, match="Missing required release file"):
        RELEASE["build_release"](source_tree)


def test_metadata_packages_only_engine_and_matches_live_requirements():
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text())
    requirements = [line for line in (ROOT / "deploy/engine/requirements.txt").read_text().splitlines()
                    if line.strip() and not line.startswith("#")]
    assert metadata["project"]["dependencies"] == requirements
    assert metadata["tool"]["setuptools"]["packages"] == ["snowflake_engine"]
    assert metadata["project"]["scripts"] == {"snowflake-engine": "snowflake_engine.cli:main"}
    assert "pytest==8.1.1" in metadata["project"]["optional-dependencies"]["dev"]
    assert metadata["project"]["version"] == RELEASE["engine_version"](
        (ROOT / "snowflake_engine/__init__.py").read_bytes())
    for path in (ROOT / "snowflake_engine").glob("*.py"):
        for node in ast.walk(ast.parse(path.read_bytes())):
            if isinstance(node, ast.Import):
                assert all(alias.name.split(".")[0] != "training_sim" for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                assert (node.module or "").split(".")[0] != "training_sim"


@pytest.mark.parametrize("archive_format", ["zip", "tar.gz"])
def test_extracted_release_runs_offline_without_installed_dependencies(tmp_path, archive_format):
    payload, manifest = RELEASE["build_release"](ROOT, archive_format)
    extracted = tmp_path / "extracted"
    for name, data in members(payload, archive_format).items():
        target = extracted / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    root = extracted / f"snowflake-engine-{manifest['engine_version']}"
    # -S removes site-packages; the guard fails any attempted connector import
    # or socket use, even when credentials exist on the developer's machine.
    runner = (
        "import runpy,sys\n"
        "def guard(event,args):\n"
        " if event.startswith('socket.') or (event=='import' and "
        "args[0].split('.')[0] in {'snowflake','cryptography','training_sim'}):\n"
        "  raise RuntimeError('Offline release attempted a live dependency or network access')\n"
        "sys.addaudithook(guard)\n"
        "sys.argv=['snowflake_engine.cli',*sys.argv[1:]]\n"
        "runpy.run_module('snowflake_engine.cli',run_name='__main__')\n"
    )
    env = {key: value for key, value in os.environ.items()
           if key not in {"PYTHONPATH", "PYTHONHOME"} and not key.startswith("SNOWFLAKE_")}

    def command(*args):
        return subprocess.run([sys.executable, "-S", "-B", "-c", runner, *args], cwd=root,
                              env=env, capture_output=True, text=True, check=True, timeout=30)

    assert "install" in command("--help").stdout
    command("install", "--config", "configs/engine/example_config.json", "--output", "plan")
    plan = json.loads((root / "plan/install-plan.json").read_text())
    assert plan["executed"] is False
    assert plan["handler"] == "snowflake_engine.native.run"
    command("load", "--config", "plan/installed-config.json",
            "--input", "fixtures/engine/synthetic_sources.json",
            "--mapping", "configs/engine/synthetic_mapping.json",
            "--run-id", "portable-load-001", "--output", "load-plan.json")
    load = json.loads((root / "load-plan.json").read_text())
    assert load["executed"] is False
    assert load["request"]["operation"] == "load"
    assert len(load["request"]["raw_records"]) == 6
    repackaged = subprocess.run(
        [sys.executable, "-S", "-B", "scripts/engine/package_release.py", "--output", "repacked.zip"],
        cwd=root, env=env, capture_output=True, text=True, check=True, timeout=30)
    assert json.loads(repackaged.stdout)["file_count"] == len(RELEASE["RELEASE_FILES"])
