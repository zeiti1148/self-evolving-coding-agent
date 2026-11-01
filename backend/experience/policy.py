"""The single model-use policy; management listing is deliberately separate."""
from dataclasses import dataclass, field

from backend.shared import Experience


@dataclass(frozen=True)
class RetrievalContext:
    language: str
    repository_id: str | None
    capabilities: frozenset[str] = field(default_factory=frozenset)


def is_eligible(entry: Experience, context: RetrievalContext) -> bool:
    return (entry.language == context.language and entry.reuse_status == "active"
            and set(entry.required_capabilities).issubset(context.capabilities)
            and (entry.scope == "global" or
                 context.repository_id is not None and entry.repository_id == context.repository_id))


def rank_references(entries, context: RetrievalContext, *, query: str, error_types: list[str], limit: int):
    wanted = {" ".join(value.casefold().split()) for value in error_types}
    keywords = set(query.casefold().split())
    ranked = []
    for entry in entries:
        if not is_eligible(entry, context):
            continue
        actual = {" ".join(value.casefold().split()) for value in
                  [*(issue.error_type for issue in entry.issues), *([entry.error_type] if entry.error_type else [])]}
        overlap = len(actual & wanted)
        if wanted and not overlap:
            continue
        content = " ".join([entry.task_summary, entry.root_cause, entry.solution, *entry.applicability, *entry.tags]).casefold()
        ranked.append(((entry.scope == "project", overlap, sum(word in content for word in keywords), entry.created_at, entry.id), entry))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return [entry for _, entry in ranked[:limit]]


def to_model_reference(entry: Experience) -> dict:
    if entry.scope == "project":
        return entry.model_dump(mode="json")
    # Never include source files, task descriptions, raw verification strings or provenance in cross-repository input.
    return {"id": entry.id, "schema_version": entry.schema_version, "scope": "global", "language": entry.language,
            "reuse_status": entry.reuse_status, "task_summary": entry.task_summary, "error_type": entry.error_type,
            "root_cause": entry.root_cause, "solution": entry.solution, "tags": entry.tags,
            "applicability": entry.applicability, "required_capabilities": entry.required_capabilities,
            "issues": [issue.model_dump(exclude={"files"}) for issue in entry.issues],
            "evidence_summary": [e.model_dump(exclude={"source_task_id", "tool_call_id"}) for e in entry.evidence]}
