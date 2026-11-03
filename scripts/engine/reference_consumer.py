"""Local SQLite reference consumer for versioned engine exports.

This adapter stores and resolves released content; it does not author content,
interpret source semantics, approve guidance, or qualify a client platform.
Run from the repository or an installed source release with Python 3.11+.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

# Support both `python scripts/engine/reference_consumer.py` and module imports.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from snowflake_engine.contracts import canonical_json
from snowflake_engine.export import validate_export


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS releases (
 release_id TEXT PRIMARY KEY,
 schema_version TEXT NOT NULL,
 input_sha256 TEXT NOT NULL,
 raw_bytes BLOB NOT NULL,
 canonical_bytes BLOB NOT NULL,
 header_json TEXT NOT NULL,
 collections_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS release_rows (
 release_id TEXT NOT NULL REFERENCES releases(release_id),
 collection TEXT NOT NULL,
 ordinal INTEGER NOT NULL,
 entity_id TEXT NOT NULL,
 source_id TEXT,
 row_json TEXT NOT NULL,
 PRIMARY KEY (release_id, collection, ordinal),
 UNIQUE (release_id, collection, entity_id)
);
CREATE TABLE IF NOT EXISTS assignment_links (
 release_id TEXT NOT NULL,
 assignment_collection TEXT NOT NULL DEFAULT 'practice.assignments'
   CHECK (assignment_collection = 'practice.assignments'),
 assignment_id TEXT NOT NULL,
 role TEXT NOT NULL,
 ordinal INTEGER NOT NULL,
 target_collection TEXT NOT NULL CHECK (target_collection IN ('scenarios', 'personas')),
 object_id TEXT NOT NULL,
 source_collection TEXT NOT NULL DEFAULT 'sources' CHECK (source_collection = 'sources'),
 source_id TEXT NOT NULL,
 PRIMARY KEY (release_id, assignment_id, role, ordinal),
 FOREIGN KEY (release_id, assignment_collection, assignment_id)
   REFERENCES release_rows(release_id, collection, entity_id),
 FOREIGN KEY (release_id, target_collection, object_id)
   REFERENCES release_rows(release_id, collection, entity_id),
 FOREIGN KEY (release_id, source_collection, source_id)
   REFERENCES release_rows(release_id, collection, entity_id)
);
CREATE TABLE IF NOT EXISTS ingest_attempts (
 attempt_id INTEGER PRIMARY KEY,
 input_sha256 TEXT NOT NULL,
 raw_bytes BLOB NOT NULL,
 status TEXT NOT NULL CHECK (status IN ('accepted', 'already_present', 'rejected')),
 release_id TEXT,
 errors_json TEXT NOT NULL
);
"""

IDENTITY_FIELDS = {
    "sources": "source_id", "scenarios": "object_id", "personas": "object_id",
    "practice.assignments": "assignment_id", "practice.practice_groups": "group_id",
}


class IngestRejected(ValueError):
    """A retained ingest attempt that did not change accepted releases."""

    def __init__(self, errors, attempt_id):
        self.errors = errors
        self.attempt_id = attempt_id
        super().__init__("; ".join(errors))


def _canonical_bytes(value):
    return canonical_json(value).encode("utf-8")


def _strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError(f"Non-finite JSON number: {value}")


class ReferenceConsumer:
    """One immutable, multi-release local store. The caller owns its lifecycle."""

    def __init__(self, path):
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(SCHEMA)
        # SQLite prevents accidental update/delete through this adapter or SQL.
        # A database owner can still replace the file; this is not WORM storage.
        for table in ("releases", "release_rows", "assignment_links", "ingest_attempts"):
            for operation in ("UPDATE", "DELETE"):
                self.connection.execute(f"""CREATE TRIGGER IF NOT EXISTS immutable_{table}_{operation}
                    BEFORE {operation} ON {table}
                    BEGIN SELECT RAISE(ABORT, 'consumer records are immutable'); END""")
        for table in ("release_rows", "assignment_links"):
            self.connection.execute(f"""CREATE TRIGGER IF NOT EXISTS immutable_insert_{table}
                BEFORE INSERT ON {table}
                WHEN EXISTS (SELECT 1 FROM ingest_attempts a WHERE a.release_id=NEW.release_id AND a.status='accepted')
                BEGIN SELECT RAISE(ABORT, 'consumer records are immutable'); END""")
        self.connection.commit()

    def close(self):
        self.connection.close()

    def _attempt(self, raw, status, release_id, errors):
        with self.connection:
            return self.connection.execute(
                "INSERT INTO ingest_attempts(input_sha256,raw_bytes,status,release_id,errors_json) VALUES(?,?,?,?,?)",
                (hashlib.sha256(raw).hexdigest(), raw, status, release_id, canonical_json(errors)),
            ).lastrowid

    @staticmethod
    def _split(document):
        header = deepcopy(document)
        collections = {}
        for name in list(header):
            if isinstance(header[name], list):
                collections[name] = header.pop(name)
        practice = header.get("practice")
        if isinstance(practice, dict):
            for name in list(practice):
                if isinstance(practice[name], list):
                    collections["practice." + name] = practice.pop(name)
        return header, collections

    def ingest(self, raw):
        """Validate once, atomically ingest rows/links, and retain every attempt.

        Rejected input bytes and the rejection remain in ingest_attempts. A
        rejected input never replaces, marks current, or edits an old release.
        """
        if not isinstance(raw, bytes):
            raise TypeError("ingest requires the original input bytes")
        release_id = None
        try:
            document = json.loads(raw, object_pairs_hook=_strict_object,
                                  parse_constant=_reject_constant)
            if not isinstance(document, dict) or document.get("schema_version") not in {
                "engine-export-v2", "engine-export-v3"
            }:
                raise ValueError("Reference consumer requires engine-export-v2 or engine-export-v3")
            errors = validate_export(document)
            if errors:
                raise ValueError("Engine export rejected: " + "; ".join(errors))
            release_id = document["release"]["release_id"]
            canonical = _canonical_bytes(document)
            existing = self.connection.execute(
                "SELECT canonical_bytes FROM releases WHERE release_id=?", (release_id,)
            ).fetchone()
            if existing:
                if bytes(existing[0]) != canonical:
                    raise ValueError("Release identity is already stored with different canonical bytes")
                # Exercise the stored rows even on an idempotent ingest.
                if self.export(release_id) != canonical:
                    raise ValueError("Stored release reconstruction differs")
                attempt = self._attempt(raw, "already_present", release_id, [])
                return {"status": "already_present", "attempt_id": attempt, **self.summary(release_id)}
            header, collections = self._split(document)
            with self.connection:
                self.connection.execute("INSERT INTO releases VALUES(?,?,?,?,?,?,?)", (
                    release_id, document["schema_version"], hashlib.sha256(raw).hexdigest(),
                    raw, canonical, canonical_json(header), canonical_json(list(collections)),
                ))
                for name, rows in collections.items():
                    for index, row in enumerate(rows):
                        field = IDENTITY_FIELDS.get(name)
                        entity_id = row[field] if field else str(index)
                        self.connection.execute("INSERT INTO release_rows VALUES(?,?,?,?,?,?)", (
                            release_id, name, index, entity_id,
                            row.get("source_id") if isinstance(row, dict) else None, canonical_json(row),
                        ))
                for assignment in document.get("practice", {}).get("assignments", []):
                    for role in ("situation", "variant"):
                        endpoint = assignment.get(role)
                        if endpoint is not None:
                            self._link(release_id, assignment["assignment_id"], role, 0,
                                       "scenarios", endpoint)
                    for index, endpoint in enumerate(assignment.get("persona_refs", [])):
                        self._link(release_id, assignment["assignment_id"], "persona", index,
                                   "personas", endpoint)
                # A real read and reconstruction is part of the transaction.
                if self.export(release_id) != canonical:
                    raise ValueError("Stored rows do not reconstruct canonical input")
                for assignment_id in self.assignments(release_id):
                    self.resolve_assignment(release_id, assignment_id)
                attempt = self.connection.execute(
                    "INSERT INTO ingest_attempts(input_sha256,raw_bytes,status,release_id,errors_json) VALUES(?,?,?,?,?)",
                    (hashlib.sha256(raw).hexdigest(), raw, "accepted", release_id, "[]"),
                ).lastrowid
            return {"status": "accepted", "attempt_id": attempt, **self.summary(release_id)}
        except Exception as error:
            # Retain unexpected storage/readback failures too; never commit a partial ingest.
            self.connection.rollback()
            errors = [str(error)]
            attempt = self._attempt(raw, "rejected", release_id, errors)
            raise IngestRejected(errors, attempt) from error

    def _link(self, release_id, assignment_id, role, ordinal, collection, endpoint):
        stored = self.connection.execute(
            "SELECT source_id FROM release_rows WHERE release_id=? AND collection=? AND entity_id=?",
            (release_id, collection, endpoint["object_id"]),
        ).fetchone()
        if stored is None or stored[0] != endpoint["source_id"]:
            raise ValueError(f"{role} endpoint does not resolve to the stated source")
        self.connection.execute("""INSERT INTO assignment_links
            (release_id,assignment_id,role,ordinal,target_collection,object_id,source_id)
            VALUES(?,?,?,?,?,?,?)""", (
            release_id, assignment_id, role, ordinal, collection,
            endpoint["object_id"], endpoint["source_id"],
        ))

    def _release(self, release_id):
        row = self.connection.execute("SELECT * FROM releases WHERE release_id=?", (release_id,)).fetchone()
        if row is None:
            raise KeyError(f"Unknown release: {release_id}")
        return row

    def export(self, release_id, original_bytes=False):
        """Reconstruct the document from stored rows; preserve every JSON value.

        Default output is engine canonical JSON bytes. original_bytes=True
        additionally returns the first accepted input's exact whitespace/bytes,
        after checking the reconstructed content against the canonical snapshot.
        """
        release = self._release(release_id)
        document = json.loads(release["header_json"])
        for name in json.loads(release["collections_json"]):
            rows = self.connection.execute("""SELECT row_json FROM release_rows
                WHERE release_id=? AND collection=? ORDER BY ordinal""", (release_id, name))
            values = [json.loads(row[0]) for row in rows]
            if name.startswith("practice."):
                document["practice"][name.removeprefix("practice.")] = values
            else:
                document[name] = values
        result = _canonical_bytes(document)
        if result != bytes(release["canonical_bytes"]):
            raise ValueError("Stored rows differ from immutable canonical release")
        return bytes(release["raw_bytes"]) if original_bytes else result

    def assignments(self, release_id):
        self._release(release_id)
        return [row[0] for row in self.connection.execute("""SELECT entity_id FROM release_rows
            WHERE release_id=? AND collection='practice.assignments' ORDER BY ordinal""", (release_id,))]

    def resolve_assignment(self, release_id, assignment_id):
        """Read actual stored joins with complete facts, conditions and states.

        The returned card fields are unchanged release values, not client
        guidance or truth inferred from a historical conversation.
        """
        row = self.connection.execute("""SELECT row_json FROM release_rows WHERE
            release_id=? AND collection='practice.assignments' AND entity_id=?""",
            (release_id, assignment_id)).fetchone()
        if row is None:
            raise KeyError(f"Unknown assignment in release: {assignment_id}")
        assignment = json.loads(row[0])
        joins = self.connection.execute("""SELECT l.role,l.ordinal,o.row_json object_json,s.row_json source_json
            FROM assignment_links l
            JOIN release_rows o ON o.release_id=l.release_id AND o.collection=l.target_collection AND o.entity_id=l.object_id
            JOIN release_rows s ON s.release_id=l.release_id AND s.collection=l.source_collection AND s.entity_id=l.source_id
            WHERE l.release_id=? AND l.assignment_id=? ORDER BY l.role,l.ordinal""",
            (release_id, assignment_id)).fetchall()
        resolved = {"situation": None, "variant": None, "personas": []}
        for join in joins:
            value = {"card": json.loads(join["object_json"]), "source": json.loads(join["source_json"])}
            if join["role"] == "persona":
                value["reference"] = assignment["persona_refs"][join["ordinal"]]
                resolved["personas"].append(value)
            else:
                endpoint = assignment[join["role"]]
                value["assignment_view"] = endpoint
                value["focus_need"] = next(need for need in value["card"]["needs"]
                                           if need["id"] == endpoint["focus_need_id"])
                resolved[join["role"]] = value
        expected = sum(assignment.get(role) is not None for role in ("situation", "variant"))
        expected += len(assignment.get("persona_refs", []))
        if len(joins) != expected:
            raise ValueError("Assignment links are incomplete")
        header = json.loads(self._release(release_id)["header_json"])
        collections = {}
        for name in ("guidance", "issues", "quarantined_components", "source_coverage"):
            collections[name] = [json.loads(row[0]) for row in self.connection.execute(
                "SELECT row_json FROM release_rows WHERE release_id=? AND collection=? ORDER BY ordinal",
                (release_id, name))]
        return {"release_id": release_id, "assignment": assignment, "resolved": resolved,
                "release_state": header["release"], "version_set": header["manifest"]["version_set"],
                "run_status": header["run_status"], **collections}

    def summary(self, release_id):
        release = self._release(release_id)
        counts = {name: 0 for name in json.loads(release["collections_json"])}
        counts.update({row[0]: row[1] for row in self.connection.execute("""SELECT collection,COUNT(*)
            FROM release_rows WHERE release_id=? GROUP BY collection""", (release_id,))})
        joins = {row[0]: row[1] for row in self.connection.execute("""SELECT role,COUNT(*)
            FROM assignment_links WHERE release_id=? GROUP BY role""", (release_id,))}
        return {"release_id": release_id, "schema_version": release["schema_version"],
                "input_sha256": release["input_sha256"],
                "canonical_sha256": hashlib.sha256(self.export(release_id)).hexdigest(),
                "row_counts": counts, "resolved_join_counts": joins,
                "foreign_key_errors": [list(row) for row in self.connection.execute("PRAGMA foreign_key_check")]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", required=True, type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    ingest = commands.add_parser("ingest")
    ingest.add_argument("input", type=Path)
    for name in ("inspect", "resolve", "export"):
        command = commands.add_parser(name)
        command.add_argument("release_id")
        if name == "resolve":
            command.add_argument("assignment_id")
        if name == "export":
            command.add_argument("output", type=Path)
            command.add_argument("--original-bytes", action="store_true")
    args = parser.parse_args(argv)
    consumer = ReferenceConsumer(args.store)
    try:
        if args.command == "ingest":
            result = consumer.ingest(args.input.read_bytes())
        elif args.command == "inspect":
            result = consumer.summary(args.release_id)
        elif args.command == "resolve":
            result = consumer.resolve_assignment(args.release_id, args.assignment_id)
        else:
            data = consumer.export(args.release_id, original_bytes=args.original_bytes)
            # Refuse to overwrite the store, an input artifact or prior output.
            with args.output.open("xb") as output:
                output.write(data)
            result = {"output": str(args.output), "sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data)}
        print(json.dumps(result, indent=2, sort_keys=True))
    except (IngestRejected, KeyError, ValueError, OSError) as error:
        print(json.dumps({"status": "rejected", "error": str(error),
                          "attempt_id": getattr(error, "attempt_id", None)}), file=sys.stderr)
        return 1
    finally:
        consumer.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
