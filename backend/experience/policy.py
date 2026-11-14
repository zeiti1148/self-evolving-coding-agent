"""The single model-use policy; management listing is deliberately separate."""
from dataclasses import dataclass, field

from backend.shared import Experience
from .retrieval import relevance, rule_identity, terms


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
    query_terms = terms(query) if query.strip() else set()
    ranked = []
    for entry in entries:
        if not is_eligible(entry, context):
            continue
        actual = {" ".join(value.casefold().split()) for value in
                  [*(issue.error_type for issue in entry.issues), *([entry.error_type] if entry.error_type else [])]}
        overlap = len(actual & wanted)
        if wanted and not overlap:
            continue
        score = relevance(entry, query_terms) if query_terms else 0
        ranked.append(((entry.scope == "project", overlap, score, entry.created_at, entry.id), entry))
    ranked.sort(key=lambda item: item[0], reverse=True)
    if not query.strip():
        return [entry for _, entry in ranked[:limit]]
    selected, seen = [], set()
    for _, entry in ranked:
        identity = rule_identity(entry)
        if identity not in seen:
            selected.append(entry)
            seen.add(identity)
        if len(selected) >= limit:
            break
    return selected


def validate_reference_options(experience_format, retrieval_strategy):
    if experience_format not in {"full", "compact"} or retrieval_strategy not in {"baseline", "contextual", "adaptive"}:
        raise ValueError("Invalid experience representation or retrieval strategy.")


def to_model_reference(entry: Experience, representation="full") -> dict:
    if representation not in {"full", "compact"}:
        raise ValueError("Experience representation must be full or compact.")
    payload = _full_reference(entry)
    if representation == "compact":
        # Remove supporting metadata, never truncate a rule or its prerequisites.
        fields = {"id", "schema_version", "scope", "language", "repository_id", "reuse_status", "task_summary",
                  "error_type", "root_cause", "solution", "applicability", "required_capabilities", "issues"}
        return {key: value for key, value in payload.items() if key in fields}
    return payload


def _full_reference(entry: Experience) -> dict:
    if entry.scope == "project":
        return entry.model_dump(mode="json")
    # Never include source files, task descriptions, raw verification strings or provenance in cross-repository input.
    return {"id": entry.id, "schema_version": entry.schema_version, "scope": "global", "language": entry.language,
            "reuse_status": entry.reuse_status, "task_summary": entry.task_summary, "error_type": entry.error_type,
            "root_cause": entry.root_cause, "solution": entry.solution, "tags": entry.tags,
            "applicability": entry.applicability, "required_capabilities": entry.required_capabilities,
            "issues": [issue.model_dump(exclude={"files"}) for issue in entry.issues],
            "evidence_summary": [e.model_dump(exclude={"source_task_id", "tool_call_id"}) for e in entry.evidence]}
