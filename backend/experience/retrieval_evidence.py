"""Bounded lexical retrieval evidence; semantic applicability stays explicit."""
import json
import re
from dataclasses import dataclass


ERROR_CLASS = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*(?:Exception|Error)\b")


def error_types_in(text: str, language: str = "java") -> list[str]:
    values = list(dict.fromkeys(ERROR_CLASS.findall(text)))
    if language == "java" and "空指针" in text and "NullPointerException" not in values:
        values.append("NullPointerException")
    return values[:20]


def normalize_error_type(value):
    """Keep an exact exception class; leave non-strings to model validation."""
    if isinstance(value, str):
        match = ERROR_CLASS.search(value)
        return match.group() if match else value.strip()
    return value


@dataclass(frozen=True)
class RetrievalEvidence:
    query: str | None
    error_types: tuple[str, ...]
    reason: str


def adaptive_retrieval_evidence(task_description, results, language):
    """Use the current test state, including success, instead of old failures."""
    for value in reversed(results):
        result = value.model_dump(mode="json") if hasattr(value, "model_dump") else value
        if result.get("tool_name") != "run_tests":
            continue
        if result.get("success") is True:
            return RetrievalEvidence(None, (), "tests_passed")
        if result.get("error_code") != "TEST_FAILED":
            return RetrievalEvidence(None, (), "test_execution_error")
        metadata = result.get("metadata", {})
        errors = [value for value in metadata.get("error_types", []) if isinstance(value, str)]
        errors.extend(failure["error_type"] for failure in metadata.get("test_failures", [])
                      if isinstance(failure, dict) and isinstance(failure.get("error_type"), str))
        errors.append(str(result.get("output", ""))[:50_000])
        return RetrievalEvidence(build_retrieval_query(task_description, [result]),
            tuple(error_types_in("\n".join(errors), language)), "test_failure")
    errors = tuple(error_types_in(task_description, language))
    return RetrievalEvidence(task_description[:2000] if errors else None, errors,
                             "task_error" if errors else "no_failure_evidence")


def build_retrieval_query(task_description, results):
    parts = [task_description[:2000]]
    for value in reversed(results):
        result = value.model_dump(mode="json") if hasattr(value, "model_dump") else value
        if result.get("tool_name") != "run_tests" or result.get("success"):
            continue
        metadata = result.get("metadata", {})
        for failure in metadata.get("test_failures", [])[:12]:
            if isinstance(failure, dict):
                parts.extend(str(failure[key])[:180] for key in ("test", "error_type", "message", "source_file") if key in failure)
        # Keep metadata ahead of the raw output so long stack traces cannot crowd it out.
        parts.append(str(result.get("output", ""))[:2000])
        break
    return "\n".join(parts)[:6000]


def terms(text):
    # Identifiers may be snake_case, camelCase or dotted names; Chinese does not
    # normally separate words with spaces. No model service or tokenizer needed.
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text[:100000]).casefold()
    words = set(re.findall(r"[a-z][a-z0-9]*", text))
    for segment in re.findall(r"[\u3400-\u9fff]+", text):
        words.update(segment[index:index + 2] for index in range(len(segment) - 1))
    return words


def relevance(entry, query_terms):
    rule = " ".join([entry.task_summary, entry.root_cause, entry.solution, *entry.tags,
                     *(f"{issue.root_cause} {issue.solution} {' '.join(issue.files)}" for issue in entry.issues)])
    return len(query_terms & terms(rule)) + 2 * len(query_terms & terms(" ".join(entry.applicability)))


def rule_identity(entry):
    # Exact semantic-field equality only; different prerequisites and source
    # repository scopes must never be collapsed as approximate duplicates.
    return json.dumps({key: value for key, value in entry.model_dump(mode="json").items() if key in {
        "language", "scope", "repository_id", "task_summary", "error_type", "root_cause", "solution",
        "applicability", "required_capabilities", "issues", "constraints", "compatibility_key"}},
        sort_keys=True, ensure_ascii=False)
