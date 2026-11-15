"""Read-only reference preflight and request evidence for V3 experiments."""
import hashlib
import json
import sqlite3
from contextlib import closing
from threading import Lock

from backend.experience.policy import RetrievalContext, is_eligible, to_model_reference
from backend.experience.store import COLUMNS, decode_row, unavailable_source_query
from backend.projects import source_revision
from backend.tools.workspace import inventory


def read_references(path):
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        entries = {entry.id: entry for entry in (decode_row(row) for row in db.execute(f"SELECT {COLUMNS} FROM experiences"))}
        excluded = {row[0] for row in db.execute(unavailable_source_query(db))}
    return entries, excluded


def checked_references(path, project):
    """Read the same verified projections as production without migrating a snapshot."""
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        projected = db.execute("SELECT 1 FROM sqlite_master WHERE name='knowledge_rules'").fetchone()
    if projected:
        from backend.experience.knowledge import digest
        from backend.experience.retrieval import retrieve
        revision = source_revision(project.source)
        return retrieve(path, language=project.language, repository_id=project.repository_id,
                        error_types=[], capabilities=project.capabilities,
                        compatibility_key=project.experience_contract or revision,
                        source_revision=revision, runner_hash=digest(project.runner_config.model_dump(mode="json")),
                        limit=10000, character_budget=50_000_000)
    entries, excluded = read_references(path)
    context = RetrievalContext(project.language, project.repository_id, frozenset(project.capabilities))
    return [to_model_reference(entry) for entry in entries.values()
            if entry.id not in excluded and is_eligible(entry, context)]


def inspect_references(path, projects, *, evaluation_projects=None):
    entries, excluded = read_references(path)
    # A subset run still belongs to the full evaluation suite. An unselected
    # target must never become a training source merely by changing --cases.
    evaluation_projects = list(projects if evaluation_projects is None else evaluation_projects)
    evaluation_repositories = {project.repository_id for project in evaluation_projects}
    evaluation_trees = {hashlib.sha256(json.dumps(inventory(project.source), sort_keys=True).encode()).hexdigest()
                        for project in evaluation_projects}
    validated = set()

    def visit(identifier, ancestors):
        if identifier in ancestors or identifier not in entries:
            raise ValueError("Reference provenance is missing or cyclic.")
        if identifier in validated:
            return
        entry = entries[identifier]
        revision = (entry.source_revision or "").split("tree:")[-1]
        if entry.repository_id in evaluation_repositories or revision in evaluation_trees:
            raise ValueError("Reference provenance contains an evaluation source; use independent training data.")
        sources = [*entry.derived_from, *entry.merged_from, *([entry.supersedes_id] if entry.supersedes_id else [])]
        if entry.scope == "global" and not sources:
            raise ValueError("Published references need traceable independent training sources.")
        if not sources and (not entry.repository_id or not entry.evidence):
            raise ValueError("Reference origin lacks repository identity or verification evidence.")
        for source in sources:
            visit(source, ancestors | {identifier})
        validated.add(identifier)

    allowed = {}
    for project in projects:
        context = RetrievalContext(project.language, project.repository_id, frozenset(project.capabilities))
        selected = [entry for entry in entries.values() if entry.id not in excluded and is_eligible(entry, context)]
        if not selected:
            raise ValueError(f"No eligible published training references for {project.id}.")
        for entry in selected:
            visit(entry.id, frozenset())
        checked_ids = {reference["id"] for reference in checked_references(path, project)}
        selected = [entry for entry in selected if entry.id in checked_ids]
        if not selected:
            raise ValueError(f"No verified reusable training references for {project.id}; finish review before freezing.")
        allowed[project.id] = sorted(entry.id for entry in selected)
    return allowed


class RequestRecorder:
    """Inspect actual serialized requests without retaining prompts or credentials."""
    def __init__(self, factory, references, condition):
        self.factory, self.references, self.condition = factory, references, condition
        self.events, self.lock = [], Lock()

    def __call__(self):
        owner, delegate = self, self.factory()

        class Client:
            @property
            def audit(self):
                return getattr(delegate, "audit", [])

            def set_deadline(self, deadline):
                if hasattr(delegate, "set_deadline"):
                    delegate.set_deadline(deadline)

            def close(self):
                if hasattr(delegate, "close"):
                    delegate.close()

            def complete(self, messages, *, tools=None):
                payload = json.loads(messages[1]["content"])
                references = payload.get("reference_experiences", [])
                valid = isinstance(references, list)
                if valid:
                    valid = all(isinstance(entry, dict)
                                and entry.get("rule_id", entry.get("id")) in owner.references
                                and entry == owner.references[entry.get("rule_id", entry.get("id"))]
                                for entry in references)
                if owner.condition == "none":
                    valid = valid and not references
                encoded = [json.dumps(entry, sort_keys=True, ensure_ascii=False) for entry in references] if isinstance(references, list) else []
                event = dict(role=payload.get("agent_role", "single" if tools else "extractor"),
                    experience_ids=[entry.get("id") for entry in references if isinstance(entry, dict)] if isinstance(references, list) else [],
                    rule_ids=[entry.get("rule_id") for entry in references if isinstance(entry, dict)] if isinstance(references, list) else [],
                    content_sha256=[hashlib.sha256(value.encode()).hexdigest() for value in encoded],
                    reference_bytes=sum(len(value.encode()) for value in encoded), payload_valid=valid, success=False)
                with owner.lock:
                    owner.events.append(event)
                if not valid:
                    raise ValueError("Experiment reference payload violates frozen reference controls.")
                try:
                    result = delegate.complete(messages, tools=tools)
                    event["success"] = True
                    return result
                except Exception as exc:
                    event["error_type"] = type(exc).__name__
                    raise
        return Client()
