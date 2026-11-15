"""Compatibility index for historical management filters; jobs.py owns maintenance."""
import hashlib
import json


def index_experience(connection, record):
    eligible = (record.schema_version == 3 and record.scope == "project" and record.reuse_status == "active"
                and record.evidence and record.issues and record.applicability)
    partition = ""
    if eligible:
        identity = [record.language, record.repository_id, sorted(record.applicability), sorted(record.required_capabilities)]
        partition = hashlib.sha256(json.dumps(identity, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    # Ineligible rows also get a marker so legacy backfill runs only once per row.
    connection.execute("INSERT INTO experience_consolidation_index VALUES (?, ?, ?, ?)",
                       (record.id, partition, record.language, record.repository_id or ""))

