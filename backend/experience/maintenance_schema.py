"""Additive maintenance schema. Original experience payloads are never rewritten."""
import sqlite3
import json


SCHEMA = """
CREATE TABLE IF NOT EXISTS knowledge_rules (
 id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL UNIQUE, language TEXT NOT NULL,
 repository_id TEXT NOT NULL, scope TEXT NOT NULL, compatibility_key TEXT NOT NULL,
 data TEXT NOT NULL, created_at TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('pending','ready','needs_review','failed')),
 version INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS knowledge_scope_idx ON knowledge_rules(language, repository_id, scope, compatibility_key);
CREATE TABLE IF NOT EXISTS knowledge_sources (
 rule_id TEXT NOT NULL REFERENCES knowledge_rules(id), experience_id TEXT NOT NULL REFERENCES experiences(id),
 ordinal INTEGER NOT NULL, PRIMARY KEY(rule_id, experience_id, ordinal)
);
CREATE INDEX IF NOT EXISTS knowledge_source_idx ON knowledge_sources(experience_id);
CREATE TABLE IF NOT EXISTS knowledge_targets (
 rule_id TEXT NOT NULL REFERENCES knowledge_rules(id), file TEXT NOT NULL, symbol TEXT NOT NULL,
 PRIMARY KEY(rule_id,file,symbol)
);
CREATE INDEX IF NOT EXISTS knowledge_target_idx ON knowledge_targets(file,symbol,rule_id);
CREATE TABLE IF NOT EXISTS knowledge_evidence (
 id TEXT PRIMARY KEY, data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS knowledge_rule_evidence (
 rule_id TEXT NOT NULL REFERENCES knowledge_rules(id), evidence_id TEXT NOT NULL REFERENCES knowledge_evidence(id),
 PRIMARY KEY(rule_id, evidence_id)
);
CREATE TABLE IF NOT EXISTS knowledge_dependencies (
 parent_id TEXT NOT NULL REFERENCES knowledge_rules(id), child_id TEXT NOT NULL REFERENCES knowledge_rules(id),
 PRIMARY KEY(parent_id, child_id)
);
CREATE TABLE IF NOT EXISTS knowledge_prerequisites (
 parent_id TEXT NOT NULL REFERENCES knowledge_rules(id), child_id TEXT NOT NULL REFERENCES knowledge_rules(id),
 PRIMARY KEY(parent_id, child_id)
);
CREATE TABLE IF NOT EXISTS knowledge_jobs (
 id TEXT PRIMARY KEY, rule_id TEXT NOT NULL UNIQUE REFERENCES knowledge_rules(id),
 state TEXT NOT NULL CHECK(state IN ('pending','running','succeeded','failed')),
 cursor TEXT NOT NULL DEFAULT '', attempts INTEGER NOT NULL DEFAULT 0,
 available_at REAL NOT NULL DEFAULT 0, lease_until REAL, lease_token TEXT,
 examined INTEGER NOT NULL DEFAULT 0, error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS knowledge_jobs_queue_idx ON knowledge_jobs(state, available_at, lease_until);
CREATE TABLE IF NOT EXISTS knowledge_relations (
 id TEXT PRIMARY KEY, left_rule TEXT NOT NULL REFERENCES knowledge_rules(id),
 right_rule TEXT NOT NULL REFERENCES knowledge_rules(id), kind TEXT NOT NULL,
 details TEXT NOT NULL, model_version TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','resolved')),
 version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, resolved_at TEXT,
 resolution TEXT, preferred_source_id TEXT, note TEXT,
 UNIQUE(left_rule, right_rule), CHECK(left_rule < right_rule)
);
CREATE INDEX IF NOT EXISTS knowledge_relation_left_idx ON knowledge_relations(left_rule, kind, status);
CREATE INDEX IF NOT EXISTS knowledge_relation_right_idx ON knowledge_relations(right_rule, kind, status);
CREATE TABLE IF NOT EXISTS knowledge_pauses (
 rule_id TEXT NOT NULL REFERENCES knowledge_rules(id), review_id TEXT NOT NULL REFERENCES knowledge_relations(id),
 PRIMARY KEY(rule_id, review_id)
);
CREATE TABLE IF NOT EXISTS knowledge_decisions (
 id INTEGER PRIMARY KEY AUTOINCREMENT, review_id TEXT NOT NULL REFERENCES knowledge_relations(id),
 version INTEGER NOT NULL, action TEXT NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(review_id,version)
);
CREATE TABLE IF NOT EXISTS knowledge_embeddings (
 rule_id TEXT NOT NULL REFERENCES knowledge_rules(id), model_version TEXT NOT NULL, vector BLOB NOT NULL,
 PRIMARY KEY(rule_id, model_version)
);
CREATE TABLE IF NOT EXISTS knowledge_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS legacy_review_decisions (
 id INTEGER PRIMARY KEY AUTOINCREMENT, review_id TEXT NOT NULL REFERENCES experience_conflicts(id),
 version INTEGER NOT NULL, action TEXT NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(review_id,version)
);
"""


def migrate(connection):
    # executescript implicitly commits; individual statements preserve the caller's migration transaction.
    for statement in SCHEMA.split(';'):
        if statement.strip():
            connection.execute(statement)
    from .automation import SCHEMA as AUTOMATION_SCHEMA
    for statement in AUTOMATION_SCHEMA.split(';'):
        if statement.strip():
            connection.execute(statement)
    from .history import SCHEMA as HISTORY_SCHEMA
    for statement in HISTORY_SCHEMA.split(';'):
        if statement.strip():
            connection.execute(statement)
    from .automatic_resolution import SCHEMA as AUTOMATIC_REVIEW_SCHEMA
    connection.execute(AUTOMATIC_REVIEW_SCHEMA)
    from .recovery import SCHEMA as RECOVERY_SCHEMA
    for statement in RECOVERY_SCHEMA.split(';'):
        if statement.strip():
            connection.execute(statement)
    try:
        connection.execute("CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(rule_id UNINDEXED, text, tokenize='trigram')")
    except sqlite3.OperationalError:
        connection.execute("INSERT OR REPLACE INTO knowledge_meta VALUES ('fts5','unavailable')")
    # Additive index upgrades must include projections created by an older server.
    for row in connection.execute('SELECT id,data FROM knowledge_rules WHERE id NOT IN (SELECT rule_id FROM knowledge_targets)').fetchall():
        issue = json.loads(row[1])['issue']
        for file in issue['files'] or ['']:
            symbols = [v for v in issue.get('symbols', []) if v.startswith(file + '#')] or ['']
            connection.executemany('INSERT OR IGNORE INTO knowledge_targets VALUES(?,?,?)', [(row[0],file,v) for v in symbols])
    if not connection.execute("SELECT 1 FROM knowledge_meta WHERE key='prerequisite_schema'").fetchone():
        grouped={}
        for row in connection.execute('SELECT s.experience_id,s.rule_id,s.ordinal,e.details FROM knowledge_sources s '
                                      'JOIN experiences e ON e.id=s.experience_id'):
            item=grouped.setdefault(row[0],{'data':json.loads(row[3]),'rules':{}})
            item['rules'][row[2]]=row[1]
        for item in grouped.values():
            data,rules=item['data'],item['rules']
            edges=set()
            for ordinal,issue in enumerate(data.get('issues',[])):
                for parent in issue.get('depends_on',[]):
                    if parent in rules and ordinal in rules: edges.add((rules[parent],rules[ordinal]))
            if data.get('schema_version',1)<4:
                edges.update((a,b) for a in rules.values() for b in rules.values() if a!=b)
            connection.executemany('INSERT OR IGNORE INTO knowledge_prerequisites VALUES(?,?)',edges)
            for source in [*data.get('merged_from',[]),*data.get('derived_from',[])]:
                for parent in grouped.get(source,{}).get('rules',{}).values():
                    connection.executemany('INSERT OR IGNORE INTO knowledge_dependencies VALUES(?,?)',
                                           [(parent,child) for child in rules.values() if parent!=child])
        connection.execute("INSERT INTO knowledge_meta VALUES ('prerequisite_schema','1')")
    connection.execute("INSERT OR REPLACE INTO knowledge_meta VALUES ('schema_version','3')")


def wal_supported(version=None):
    major, minor, patch = version or sqlite3.sqlite_version_info
    return (major, minor, patch) >= (3, 51, 3) or (major, minor) == (3, 50) and patch >= 7 or (major, minor) == (3, 44) and patch >= 6
