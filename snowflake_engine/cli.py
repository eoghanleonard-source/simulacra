"""Offline-reviewable installation and explicit native operation entrypoint.

All commands default to plans. Only --execute connects to Snowflake.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import sys
import zipfile

from . import __version__
from .schema import (SCHEMA_VERSION, TABLE_SPECS, VIEW_SPECS, VIEW_JOINS, ensure_schema_version, ensure_readable_schema_version,
                     manifest_hash, qualified_name, schema_ddl, validate_identifier)

DEFAULT_LIMITS = {"max_sources": 6, "max_events": 60, "max_generation_calls": 12,
                  "max_output_tokens": 4096, "max_embedding_inputs": 128,
                  "max_input_chars": 60000, "max_run_seconds": 900,
                  "max_sql_statements": 500, "statement_timeout_seconds": 120,
                  "concurrency": 1}
HARD_LIMITS = {"max_sources": 100000, "max_events": 2000000, "max_generation_calls": 100000,
               "max_output_tokens": 16384, "max_embedding_inputs": 1000000,
               "max_input_chars": 20000000, "max_run_seconds": 86400,
               "max_sql_statements": 1000000, "statement_timeout_seconds": 3600,
               "concurrency": 1}
NATIVE_CALL_CLOSEOUT_BUFFER_SECONDS = 60
PACKAGE_DEPENDENCIES = ["snowflake-snowpark-python==1.26.0"]
REQUIRED_MODULES = {"__init__.py", "schema.py", "native.py", "mapping.py", "contracts.py",
                    "extraction.py", "evidence_assertions.py", "aggregation.py", "export.py", "versioning.py", "practice.py", "reprocessing.py", "source_revision.py", "practice_objectives.py", "capture_evidence.py", "capture_contract.py", "capture_framing.py", "capture_prompts.py", "capture_storage.py"}


def read_json(path):
    return json.loads(Path(path).read_text())


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False, default=str) + "\n")


def validate_config(config):
    for key in ("database", "schema", "warehouse", "role"):
        validate_identifier(config[key])
    if not isinstance(config.get("client_id"), str) or not config["client_id"].strip():
        raise ValueError("client_id is required")
    if "procedure" in config:
        validate_identifier(config["procedure"])
    unknown = set(config.get("limits", {})) - set(DEFAULT_LIMITS) - {"max_generation_ai_credits"}
    if unknown:
        raise ValueError("Unknown limit keys: " + ", ".join(sorted(unknown)))
    limits = {**DEFAULT_LIMITS, **config.get("limits", {})}
    for key, maximum in HARD_LIMITS.items():
        if type(limits[key]) is not int or not 1 <= limits[key] <= maximum:
            raise ValueError(f"limits.{key} must be an integer from 1 to {maximum}")
    if "max_generation_ai_credits" in limits:
        ceiling = limits["max_generation_ai_credits"]
        if type(ceiling) not in (int, float) or not math.isfinite(ceiling) or not 0 < ceiling <= 16.0:
            raise ValueError("max_generation_ai_credits must be a positive number at most 16.0")
    if "source_schema" in config:
        validate_identifier(config["source_schema"])
    if limits["max_sql_statements"] < 40:
        raise ValueError("limits.max_sql_statements must be at least 40 for safe run closeout")
    if limits["concurrency"] != 1:
        raise ValueError("Only single-writer execution is supported")
    config = dict(config)
    config["limits"] = limits
    config.setdefault("defer_practice", False)
    if type(config["defer_practice"]) is not bool:
        raise ValueError("defer_practice must be an explicit boolean")
    config.setdefault("persist_request_messages", False)
    if type(config["persist_request_messages"]) is not bool:
        raise ValueError("persist_request_messages must be an explicit boolean")
    if config.get("extraction_mode") in ("split_v3", "evidence_v1") and limits["max_output_tokens"] != 6144:
        raise ValueError("split_v3/evidence_v1 contract requires exact max_output_tokens=6144")
    if config.get("extraction_mode") == "evidence_v1" and config.get("model") != "claude-sonnet-4-6":
        raise ValueError("evidence_v1 baseline pins claude-sonnet-4-6")
    from .native import validate_processing_modes, validate_retained_capture_manifest, validate_capture_config, capture_install_config, validate_recorded_run_config
    validate_processing_modes(config)
    validate_retained_capture_manifest(config)
    validate_capture_config(config)
    validate_recorded_run_config(config)
    capture_capability = capture_install_config(config)
    if (capture_capability.get("capture_enabled") is True or config.get("operation") == "capture_evidence") and limits["max_output_tokens"] != 16384:
        raise ValueError("Capture profile requires max_output_tokens=16384")
    return config


def package_engine(package_dir=None):
    """Deterministic pure-Python archive; no training_sim, secrets or bytecode."""
    package_dir = Path(package_dir or Path(__file__).parent)
    modules = {p.name for p in package_dir.glob("*.py")}
    missing = REQUIRED_MODULES - modules
    if missing:
        raise ValueError("Incomplete native package: " + ", ".join(sorted(missing)))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(package_dir.glob("*.py")):
            source = path.read_bytes()
            compile(source, str(path), "exec")
            info = zipfile.ZipInfo("snowflake_engine/" + path.name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, source)
    return buffer.getvalue()


def install_plan(config, archive_bytes, archive_path):
    config = validate_config(config)
    digest = hashlib.sha256(archive_bytes).hexdigest()
    procedure = "RUN_ENGINE_V2_" + digest[:12].upper()
    stage = qualified_name(config, "ENGINE_PACKAGES")
    package_name = f"snowflake_engine_{digest}.zip"
    import_path = f"@{stage}/{package_name}"
    statements = schema_ddl(config)
    statements.append(f"CREATE STAGE IF NOT EXISTS {stage}")
    statements.append(f"CREATE PROCEDURE IF NOT EXISTS {qualified_name(config, procedure)}(CONFIG_JSON VARCHAR) RETURNS VARIANT LANGUAGE PYTHON RUNTIME_VERSION='3.11' PACKAGES=('{PACKAGE_DEPENDENCIES[0]}') IMPORTS=('{import_path}') HANDLER='snowflake_engine.native.run' EXECUTE AS CALLER")
    return {"schema_version": SCHEMA_VERSION, "code_version": __version__, "manifest_hash": manifest_hash(),
            "archive_sha256": digest, "archive_path": str(Path(archive_path).resolve()),
            "stage": stage, "stage_file": package_name, "procedure": procedure,
            "handler": "snowflake_engine.native.run", "runtime": "3.11",
            "packages": PACKAGE_DEPENDENCIES, "statements": statements,
            "statement_count": len(statements), "maximum_control_statements": len(statements) + 9,
            "execution_order": ["Set session statement timeout and query tag (2 statements)", "Inspect existing objects (1 statement)", "Check metadata and immutable budget if schema populated (2 statements)", "Create schema/metadata and record new version marker (marker only for fresh schema)", "Create remaining tables/views/stage", "PUT archive without overwrite (1 statement)", "Create content-addressed procedure", "Seed singleton lease if missing (1 statement)", "Check singleton lease count (1 statement)", "Check schema version/manifest (1 statement)"], "executed": False,
            "preconditions": ["Existing configured database, warehouse and role", "Storage v4 requires a fresh dedicated engine schema; preserve older schemas and their procedures", "dbt source relations use the same database and explicit source_schema config", "Dedicated empty schema or matching engine schema and manifest", "Python 3.11 and pinned Snowpark package available in this Snowflake account", "No simultaneous installer"],
            "changes": ["Create missing engine objects", "Upload content-addressed package without overwrite", "Create new content-addressed procedure", "Preserve old tables, views, procedures and packages"],
            "config": {**config, "procedure": procedure, "package_sha256": digest}}


def credential_values(env_file=None):
    """Explicit opt-in .env reuse; never emit credentials in plans or logs."""
    values = dict(os.environ)
    if env_file:
        lines = iter(Path(env_file).read_text().splitlines())
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key, value = key.strip(), value.strip()
            if key.startswith("export "):
                key = key[7:].strip()
            if not key.startswith("SNOWFLAKE_"):
                continue
            if value.startswith(("'", '"')):
                quote = value[0]
                while len(value) == 1 or not value.endswith(quote):
                    try:
                        value += "\n" + next(lines)
                    except StopIteration:
                        raise ValueError("Unterminated quoted Snowflake credential in supplied env file") from None
                value = value[1:-1]
            values.setdefault(key, value)
    allowed = ("account", "user", "password", "authenticator", "private_key_file", "private_key", "private_key_passphrase", "token")
    return {key: values["SNOWFLAKE_" + key.upper()] for key in allowed if values.get("SNOWFLAKE_" + key.upper())}


class _Row(tuple):
    def __new__(cls, values, columns):
        obj = super().__new__(cls, values)
        obj.columns = columns
        return obj

    def as_dict(self):
        return dict(zip(self.columns, self))


class _Query:
    def __init__(self, session, sql, params):
        self.session, self.sql, self.params = session, sql, params

    def collect(self):
        with self.session.connection.cursor() as cursor:
            cursor.execute(self.sql, self.params)
            self.session.query_ids.append(cursor.sfqid)
            columns = [field[0] for field in (cursor.description or [])]
            return [_Row(row, columns) for row in cursor.fetchall()]


class _Files:
    def __init__(self, session):
        self.session = session

    def put(self, path, stage, auto_compress=False, overwrite=False):
        # PUT does not accept value binds. The local file URI is quoted as a SQL literal;
        # the stage comes from the validated identifier helper.
        uri = Path(path).resolve().as_uri().replace("'", "''")
        self.session.sql(f"PUT '{uri}' {stage} AUTO_COMPRESS=FALSE OVERWRITE=FALSE").collect()


class ConnectorSession:
    """Small Snowpark-shaped control interface; processing remains in the procedure."""
    def __init__(self, connection):
        self.connection = connection
        self.query_ids = []
        self.file = _Files(self)

    def sql(self, query, params=None):
        return _Query(self, query, params)

    def close(self):
        self.connection.close()


def private_key_der(material, passphrase=None):
    """Accept explicit PEM or base64 DER key material without exposing it on failure."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    try:
        material = material.strip().replace("\\n", "\n")
        password = passphrase.encode() if passphrase else None
        if material.startswith("-----BEGIN"):
            key = serialization.load_pem_private_key(material.encode(), password=password)
        else:
            key = serialization.load_der_private_key(base64.b64decode("".join(material.split()), validate=True), password=password)
        if not isinstance(key, rsa.RSAPrivateKey) or key.key_size < 2048:
            raise ValueError("Unsupported key")
        return key.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    except (ValueError, TypeError):
        raise ValueError("Snowflake private key must be a valid RSA key of at least 2048 bits in PEM or base64 DER format") from None


def connect(config, env_file=None):
    import snowflake.connector
    credentials = credential_values(env_file)
    if not credentials.get("account") or not credentials.get("user"):
        raise ValueError("Set SNOWFLAKE_ACCOUNT and SNOWFLAKE_USER or explicitly supply --env-file")
    inline = credentials.pop("private_key", None)
    passphrase = credentials.pop("private_key_passphrase", None)
    if inline:
        credentials["private_key"] = private_key_der(inline, passphrase)
    elif passphrase:
        credentials["private_key_file_pwd"] = passphrase
    return ConnectorSession(snowflake.connector.connect(**credentials, **{key: config[key] for key in ("database", "schema", "warehouse", "role")}, paramstyle="qmark", login_timeout=30))


def _prepare_session(session, config):
    # Caller-owned session limits; never ALTER WAREHOUSE or grant access.
    session.sql(f"ALTER SESSION SET STATEMENT_TIMEOUT_IN_SECONDS={config['limits']['statement_timeout_seconds']}").collect()
    session.sql("ALTER SESSION SET QUERY_TAG='snowflake-engine-v2'").collect()


def _preflight_install(session, config):
    database = validate_identifier(config["database"])
    schema = validate_identifier(config["schema"])
    rows = session.sql(
        f"SELECT 'RELATION' AS KIND, TABLE_NAME AS NAME FROM {database}.INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA=? "
        f"UNION ALL SELECT 'STAGE', STAGE_NAME FROM {database}.INFORMATION_SCHEMA.STAGES WHERE STAGE_SCHEMA=? "
        f"UNION ALL SELECT 'PROCEDURE', PROCEDURE_NAME FROM {database}.INFORMATION_SCHEMA.PROCEDURES WHERE PROCEDURE_SCHEMA=? "
        f"UNION ALL SELECT 'FUNCTION', FUNCTION_NAME FROM {database}.INFORMATION_SCHEMA.FUNCTIONS WHERE FUNCTION_SCHEMA=? "
        f"UNION ALL SELECT 'SEQUENCE', SEQUENCE_NAME FROM {database}.INFORMATION_SCHEMA.SEQUENCES WHERE SEQUENCE_SCHEMA=?",
        params=[schema] * 5).collect()
    names = {row[1].upper() for row in rows if row[0] == "RELATION"}
    if rows:
        if "ENGINE_METADATA" not in names:
            raise ValueError("Target schema contains unversioned objects; use a new dedicated schema")
        ensure_schema_version(session, config)
        budget_rows = session.sql(f"SELECT BUDGET FROM {qualified_name(config, 'ENGINE_METADATA')}").collect()
        installed_budget = budget_rows[0][0] if len(budget_rows) == 1 else None
        if isinstance(installed_budget, str):
            installed_budget = json.loads(installed_budget)
        if installed_budget != config["limits"]:
            raise ValueError("Installed allowance differs from this install plan; preserve the existing profile and use a separately approved new schema for changes")
        extras = names - set(TABLE_SPECS) - set(VIEW_SPECS)
        extras |= {row[1] for row in rows if row[0] == "STAGE" and row[1] != "ENGINE_PACKAGES"}
        extras |= {row[1] for row in rows if row[0] == "PROCEDURE" and not re.fullmatch(r"RUN_ENGINE_V2_[A-F0-9]{12}", row[1])}
        extras |= {row[1] for row in rows if row[0] in ("FUNCTION", "SEQUENCE")}
        if extras:
            raise ValueError("Target schema contains unrelated objects: " + ", ".join(sorted(extras)))
        from .native import capture_install_config
        capture_rows = session.sql(f"SELECT CAPTURE_CONFIG FROM {qualified_name(config, 'ENGINE_METADATA')}").collect()
        installed_capture = capture_rows[0][0] if len(capture_rows) == 1 else None
        if isinstance(installed_capture, str):
            installed_capture = json.loads(installed_capture)
        if installed_capture != capture_install_config(config):
            raise ValueError("Installed execution capability differs from this plan; use a fresh dedicated schema")
    return bool(names)


def execute_install(session, config, plan):
    """Non-destructive install. A partial attempt remains inspectable and is never dropped."""
    _prepare_session(session, config)
    existing = _preflight_install(session, config)
    statements = plan["statements"]
    # An early version marker makes interrupted installs safely resumable; it never claims completion.
    for index, statement in enumerate(statements[:-1]):
        session.sql(statement).collect()
        if index == 1 and not existing:
            from .schema import insert_row
            from .native import capture_install_config
            insert_row(session, config, "ENGINE_METADATA", {"SCHEMA_VERSION": SCHEMA_VERSION, "MANIFEST_HASH": manifest_hash(), "BUDGET": config["limits"], "CAPTURE_CONFIG": capture_install_config(config)})
    session.file.put(str(Path(plan["archive_path"]).resolve()), "@" + plan["stage"],
                     auto_compress=False, overwrite=False)
    session.sql(statements[-1]).collect()
    locks = qualified_name(config, "LOCKS")
    session.sql(f"INSERT INTO {locks} (LOCK_KEY) SELECT ? WHERE NOT EXISTS (SELECT 1 FROM {locks} WHERE LOCK_KEY=?)", params=["engine", "engine"]).collect()
    rows = session.sql(f"SELECT COUNT(*) FROM {locks} WHERE LOCK_KEY=?", params=["engine"]).collect()
    if rows[0][0] != 1:
        raise ValueError("LOCKS must contain exactly one engine lease row; stop for operator review")
    ensure_schema_version(session, config)
    return {**plan, "executed": True, "query_ids": getattr(session, "query_ids", [])}


def _native_call(session, config, request):
    if not config.get("procedure"):
        raise ValueError("Configuration has no procedure; install and use the generated installed-config.json")
    ensure_schema_version(session, config)
    _prepare_session(session, config)
    # The outer CALL covers the whole bounded run, a final in-flight child statement,
    # and closeout. Native acquire separately restores the per-child SQL timeout.
    call_timeout = (config["limits"]["max_run_seconds"]
                    + config["limits"]["statement_timeout_seconds"]
                    + NATIVE_CALL_CLOSEOUT_BUFFER_SECONDS)
    session.sql(f"ALTER SESSION SET STATEMENT_TIMEOUT_IN_SECONDS={call_timeout}").collect()
    result = session.sql(f"CALL {qualified_name(config, config['procedure'])}(?)", params=[json.dumps(request, sort_keys=True, allow_nan=False)]).collect()
    value = result[0][0]
    return json.loads(value) if isinstance(value, str) else value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    configure = sub.add_parser("configure", help="write non-secret configuration")
    for key in ("database", "schema", "warehouse", "role", "client-id", "model", "embedding-model"):
        configure.add_argument("--" + key, required=True)
    configure.add_argument("--capture-evidence", action="store_true", help="opt in to the frozen evidence-capture profile")
    configure.add_argument("--permitted-execution-mode", choices=["live", "recorded_smoke"], help="immutable installed capture mode")
    configure.add_argument("--allow-recorded-capture", action="store_true", help="installation capability for synthetic recorded smoke only")
    configure.add_argument("--allow-recorded-run", action="store_true", help="immutable capability for ordinary predetermined fixture runs; not unattended inference")
    configure.add_argument("--permitted-run-execution-mode", choices=["recorded_smoke"], help="explicit installed ordinary fixture mode")
    configure.add_argument("--source-schema", help="explicit dbt staging schema in the configured database")
    configure.add_argument("--output", required=True)
    for command in ("install", "load", "load-table", "run", "capture-evidence", "capture-export", "inspect", "export", "cleanup-plan"):
        cmd = sub.add_parser(command)
        cmd.add_argument("--config", required=True)
        cmd.add_argument("--output", required=True)
        if command != "cleanup-plan":
            cmd.add_argument("--execute", action="store_true", help="connect and perform this operation; default emits plan")
            cmd.add_argument("--env-file", help="explicitly reuse only SNOWFLAKE_* credentials")
        if command == "install":
            cmd.add_argument("--package-dir")
        elif command in ("load", "load-table"):
            if command == "load":
                cmd.add_argument("--input", required=True)
            else:
                cmd.add_argument("--relation", required=True, help="database.schema.relation with SOURCE_ID and RAW_RECORD")
                cmd.add_argument("--source-id", action="append", default=[], help="select one exact source ID; repeat for multiple IDs")
            cmd.add_argument("--mapping", required=True)
            cmd.add_argument("--run-id", required=True)
        elif command == "run":
            cmd.add_argument("--execution-mode", choices=["live", "recorded_smoke"], help="ordinary runtime mode; default preserves live/cache-only behavior")
            cmd.add_argument("--recorded-run-responses", help="recorded-run-responses-v1 JSON bound to exact source IDs, hashes and operations")
            cmd.add_argument("--run-id", required=True)
            cmd.add_argument("--enable-model-calls", action="store_true", help="explicitly enable bounded native inference; otherwise cache-only")
            cmd.add_argument("--evidence-ordering", choices=["source_ordinal_v1"],
                             help="replay exact retained split_v3 responses with citation arrays in source order; cache-only")
            cmd.add_argument("--retained-captures", help="explicit frozen retained-capture-v1 JSON artifact to import without new extraction calls")
            cmd.add_argument("--source-revision", choices=["source_assertion_revision_v1"],
                             help="explicitly revise retained assertions with one bounded source_review_v1 call per history")
            cmd.add_argument("--defer-practice", action="store_true",
                             help="skip practice callbacks while retaining the configured final practice policy in request identities")
            cmd.add_argument("--input-measurement", choices=["message_content_chars_v1"],
                             help="R3 decoded content-character bound for the three known review/planner operations")
            cmd.add_argument("--response-normalization", choices=["unique_review_json_v1"],
                             help="R3 unique final review JSON normalization; requires the paired input measurement mode")
            cmd.add_argument("--allow-generation-operation", action="append", default=None,
                             help="explicit operation whitelist; repeat for review/planner operations; extraction is prohibited")
        elif command == "capture-evidence":
            cmd.add_argument("--run-id", required=True)
            cmd.add_argument("--input-run-id", help="exact retained load batch")
            cmd.add_argument("--execution-mode", choices=["live", "recorded_smoke"], required=True)
            cmd.add_argument("--enable-model-calls", action="store_true")
            cmd.add_argument("--recorded-responses", help="JSON source_id -> stage -> ordered raw-string list, zero-based per-stage call ordinal")
            cmd.add_argument("--capture-settings", help="JSON explicit focal-speaker IDs and optional complete section plan")
        elif command == "capture-export":
            cmd.add_argument("--run-id", required=True, help="the capture run to export; read-only")
        elif command == "inspect":
            cmd.add_argument("--view", choices=sorted(VIEW_SPECS), default="MART_RUNS")
            cmd.add_argument("--limit", type=int, default=100)
        elif command == "export":
            cmd.add_argument("--release-id", required=True)
    args = parser.parse_args(argv)
    if args.command == "configure":
        config = {key: getattr(args, key) for key in ("database", "schema", "warehouse", "role", "client_id", "model", "embedding_model")}
        if args.source_schema:
            config["source_schema"] = args.source_schema
        config["limits"] = dict(DEFAULT_LIMITS)
        config["enable_model_calls"] = False
        config["code_version"] = __version__
        config["schema_version"] = SCHEMA_VERSION
        if args.capture_evidence:
            from .native import CAPTURE_PROFILE
            config.update(capture_enabled=True, capture_profile=CAPTURE_PROFILE,
                permitted_execution_mode=args.permitted_execution_mode,
                allow_recorded_capture=args.allow_recorded_capture, persist_request_messages=True)
            config["limits"].update(max_output_tokens=16384, max_input_chars=90000)
        elif args.permitted_execution_mode or args.allow_recorded_capture:
            raise ValueError("Capture installation options require --capture-evidence")
        if args.allow_recorded_run or args.permitted_run_execution_mode:
            config.update(allow_recorded_run=args.allow_recorded_run,
                          permitted_run_execution_mode=args.permitted_run_execution_mode)
        write_json(args.output, validate_config(config))
        return 0
    config = validate_config(read_json(args.config))
    if args.command == "cleanup-plan":
        namespace = ".".join(validate_identifier(config[x]) for x in ("database", "schema"))
        write_json(args.output, {"executed": False, "schema": namespace, "objects": sorted(TABLE_SPECS) + sorted(VIEW_SPECS) + ["ENGINE_PACKAGES", config.get("procedure")], "instructions": ["Inspect the exact installation and preserve required releases and evidence.", "Obtain explicit cleanup authority for this dedicated schema.", "Run a separately reviewed DROP SCHEMA only after confirming no unrelated objects exist."], "sql_executed": []})
        return 0
    session = None
    try:
        if args.command == "install":
            archive = package_engine(args.package_dir)
            digest = hashlib.sha256(archive).hexdigest()
            outdir = Path(args.output)
            outdir.mkdir(parents=True, exist_ok=True)
            archive_path = outdir / f"snowflake_engine_{digest}.zip"
            archive_path.write_bytes(archive)
            plan = install_plan(config, archive, archive_path)
            (outdir / "install.sql").write_text("-- Run through the CLI: version and ownership guards must precede DDL.\n" + ";\n".join(plan["statements"]) + ";\n")
            write_json(outdir / "schema-manifest.json", {"schema_version": SCHEMA_VERSION, "manifest_hash": manifest_hash(), "tables": TABLE_SPECS, "views": VIEW_SPECS, "view_joins": VIEW_JOINS})
            write_json(outdir / "install-plan.json", plan)
            write_json(outdir / "installed-config.json", plan["config"])
            if args.execute:
                try:
                    session = connect(config, args.env_file)
                    plan = execute_install(session, config, plan)
                except Exception as exc:
                    write_json(outdir / "install-result.json", {"status": "failed", "exception_type": type(exc).__name__, "query_ids": getattr(session, "query_ids", []), "rollback": "Preserve objects and evidence; rerun the same manifest or review the retained partial installation"})
                    raise
                write_json(outdir / "install-plan.json", plan)
                write_json(outdir / "install-result.json", {"status": "complete", "query_ids": getattr(session, "query_ids", [])})
            return 0
        request = {**config, "operation": args.command, "code_version": __version__}
        if args.command in ("load", "load-table", "run", "capture-evidence", "capture-export"):
            request["run_id"] = args.run_id
            if args.command == "capture-evidence":
                request.update(operation="capture_evidence", execution_mode=args.execution_mode,
                               enable_model_calls=args.enable_model_calls)
                if args.input_run_id:
                    request["input_run_id"] = args.input_run_id
                if args.recorded_responses:
                    request["recorded_responses"] = read_json(args.recorded_responses)
                if args.capture_settings:
                    request["capture_settings"] = read_json(args.capture_settings)
                from .native import validate_config as validate_native_config
                request = validate_native_config(request)
            elif args.command == "capture-export":
                request.update(operation="capture_export", capture_run_id=args.run_id, enable_model_calls=False)
                from .native import validate_config as validate_native_config
                request = validate_native_config(request)
            if args.command == "run":
                if args.execution_mode:
                    request["execution_mode"] = args.execution_mode
                if args.recorded_run_responses:
                    request["recorded_run_responses"] = read_json(args.recorded_run_responses)
                request["enable_model_calls"] = args.enable_model_calls
                if args.defer_practice:
                    request["defer_practice"] = True
                if args.evidence_ordering:
                    request.update(evidence_ordering=args.evidence_ordering, reprocess_retained=True)
                if args.retained_captures:
                    capture_bytes = Path(args.retained_captures).read_bytes()
                    capture_manifest = json.loads(capture_bytes)
                    from .versioning import digest
                    request.update(retained_capture_manifest=capture_manifest,
                        retained_capture_manifest_sha256=digest(capture_manifest),
                        retained_capture_manifest_file_sha256=hashlib.sha256(capture_bytes).hexdigest(),
                        evidence_ordering="source_ordinal_v1", reprocess_retained=True)
                if args.source_revision:
                    request.update(source_revision=args.source_revision, evidence_ordering="source_ordinal_v1", reprocess_retained=True)
                    request.setdefault("allowed_generation_operations", ["source_review_v1"])
                if args.input_measurement:
                    request["input_measurement"] = args.input_measurement
                if args.response_normalization:
                    request["response_normalization"] = args.response_normalization
                if args.allow_generation_operation is not None:
                    request["allowed_generation_operations"] = args.allow_generation_operation
                from .native import validate_processing_modes, validate_retained_capture_manifest
                validate_processing_modes(request)
                validate_retained_capture_manifest(request)
                from .native import validate_recorded_run_config
                validate_recorded_run_config(request)
            if args.command == "load":
                request["raw_records"] = read_json(args.input)
                request["mapping"] = read_json(args.mapping)
                if not isinstance(request["raw_records"], list):
                    raise ValueError("Load input must be a JSON array of raw records")
            elif args.command == "load-table":
                from .native import validate_config as validate_native_config
                request.update(operation="load", source_relation=args.relation,
                               source_ids=args.source_id, mapping=read_json(args.mapping))
                request = validate_native_config(request)
                if not isinstance(request["mapping"], dict):
                    raise ValueError("Native normalization requires the mapping object")
            result = {"executed": False, "operation": args.command, "procedure": config.get("procedure"), "request": request}
            if args.execute:
                session = connect(config, args.env_file)
                result = _native_call(session, config, request)
        elif args.command == "inspect":
            if not 1 <= args.limit <= 1000:
                raise ValueError("inspect --limit must be between 1 and 1000")
            sql = f"SELECT * FROM {qualified_name(config, args.view)} WHERE CLIENT_ID=? LIMIT {args.limit}"
            result = {"executed": False, "sql": sql, "params": [config["client_id"]]}
            if args.execute:
                session = connect(config, args.env_file)
                ensure_readable_schema_version(session, config)
                _prepare_session(session, config)
                result = [r.as_dict() for r in session.sql(sql, params=[config["client_id"]]).collect()]
        else:
            sql = f"SELECT PAYLOAD FROM {qualified_name(config, 'RELEASES')} WHERE RELEASE_ID=? AND CLIENT_ID=?"
            result = {"executed": False, "sql": sql, "params": [args.release_id, config["client_id"]]}
            if args.execute:
                session = connect(config, args.env_file)
                ensure_readable_schema_version(session, config)
                _prepare_session(session, config)
                rows = session.sql(sql, params=[args.release_id, config["client_id"]]).collect()
                if len(rows) != 1:
                    raise ValueError("Expected exactly one immutable release")
                result = json.loads(rows[0][0]) if isinstance(rows[0][0], str) else rows[0][0]
                from .export import read_stored_export
                result = read_stored_export(result)
        write_json(args.output, result)
        return 0
    finally:
        if session is not None:
            session.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, KeyError, OSError, json.JSONDecodeError) as error:
        print(f"Engine command stopped: {error}", file=sys.stderr)
        raise SystemExit(2)
