"""Adaptive evidence and compact prompts must retain the checked-rule safeguards."""
import json
from uuid import uuid4

import pytest

from backend.agent.core import Agent
from backend.experience.database import connect
from backend.experience.library import library
from backend.experience.policy import RetrievalContext, format_model_reference, rank_references
from backend.experience.recovery import active_grants, enqueue
from backend.shared import AgentRequest, ApplicationError, ErrorCode, ToolResult
from backend.tests.test_agent import SequenceLLM, tool_message
from backend.tests.test_experience_policy import seed


def test_compact_preserves_new_and_existing_reuse_boundaries():
    payload = dict(id="source", rule_id="rule", solution="Only use in the checked context",
        constraints=["Never discard the base-class execution order"], applicability=["Keep the target contract"],
        required_capabilities=["mro"], compatibility_key="release-1", source_revision="revision-1",
        reuse_boundary=dict(repository_id="A", source_revision="revision-1", runner_hash="hash",
                            recovery_id="recovery", mode="verified_context_only"),
        prerequisites=["parent"], evidence_summary={"verified_runs": 2}, source_ids=["source"])
    compact = format_model_reference(payload, "compact")
    assert compact == {key: value for key, value in payload.items() if key not in {"evidence_summary", "source_ids"}}


@pytest.mark.parametrize("boundary", ["constraints", "compatibility_key"])
def test_contextual_dedup_keeps_different_business_constraints(tmp_path, boundary):
    from backend.experience import ExperienceStore
    entry = seed(ExperienceStore(tmp_path / "rules.db"))
    value = ["The empty string must raise ValueError"] if boundary == "constraints" else "another-contract"
    other = entry.model_copy(update={"id": str(uuid4()), boundary: value})
    assert len(rank_references([entry, other], RetrievalContext("python", "A"),
                              query="input", error_types=[], limit=5)) == 2


def test_uploaded_adaptive_agent_retains_recovery_scope_and_withdraws_after_pass(tmp_path):
    # The recovery service actually runs pytest twice; only the subsequent model
    # conversation is scripted, making this a deterministic integration test.
    from backend.tests.test_experience_recovery import prepared
    store, _, _, _, service = prepared(tmp_path)
    card = next(item for item in library(store.database_path)["items"] if item["state"] == "paused")
    enqueue(store.database_path, card["id"])
    assert service.run_once()
    with connect(store.database_path) as db:
        grant = active_grants(db)[card["id"]]

    observed = []

    class CheckedStore:
        def retrieve_model_references(self, query="", **context):
            observed.append(context)
            return store.retrieve_model_references(query, **context)

        def retrieve_experiences(self, *args, **kwargs):
            pytest.fail("A checked-rule store must never fall back to source-row retrieval")

    def run(runner_hash):
        llm = SequenceLLM([tool_message("run_tests"), ApplicationError(ErrorCode.LLM_ERROR, "inspection complete")])
        calls = 0

        def execute(workspace, call):
            nonlocal calls
            calls += 1
            passed = calls > 1
            return ToolResult(call_id=call.id, tool_name="run_tests", success=passed,
                error_code=None if passed else ErrorCode.TEST_FAILED, output="" if passed else "AssertionError",
                error_message=None if passed else "contract failed",
                metadata=dict(runner="pytest", tests=1, failures=0 if passed else 1, errors=0, skipped=0,
                              error_types=[] if passed else ["AssertionError"]))

        request = AgentRequest(task_id=str(uuid4()), workspace=tmp_path, task_description="Repair uploaded conversion",
            language="python", repository_id=grant["repository_id"], source_revision=grant["source_revision"],
            experience_contract=grant["compatibility_key"], runner_hash=runner_hash, max_steps=3)
        agent = Agent(llm, execute, CheckedStore(), verify_before=True,
                      retrieval_strategy="adaptive", experience_format="compact")
        agent.run(request)
        references = [json.loads(messages[1]["content"])["reference_experiences"] for messages in llm.requests]
        return references, agent.last_run_audit

    references, audit = run(grant["runner_hash"])
    assert len(references) == 2 and references[1] == []
    recovered = next(entry for entry in references[0] if entry["rule_id"] == card["id"])
    assert recovered["reuse_boundary"]["runner_hash"] == grant["runner_hash"]
    assert recovered["reuse_boundary"]["source_revision"] == grant["source_revision"]
    assert recovered["reuse_boundary"]["mode"] == "verified_context_only"
    assert recovered["compatibility_key"] == grant["compatibility_key"]
    assert "constraints" in recovered and "evidence_summary" not in recovered
    assert {event.get("reason") for event in audit["retrieval_events"]} >= {"no_failure_evidence", "test_failure", "tests_passed"}
    assert all(item["compatibility_key"] == grant["compatibility_key"] for item in observed)
    assert all(item["source_revision"] == grant["source_revision"] for item in observed)
    wrong_context, _ = run("0" * 64)
    assert all(not any(entry["rule_id"] == card["id"] for entry in entries) for entries in wrong_context)


@pytest.mark.parametrize("representation", ["full", "compact"])
def test_multi_agent_uses_checked_rules_with_adaptive_evidence(tmp_path, representation):
    from backend.agent.orchestrator import MultiAgentCoordinator
    from backend.config import Settings
    from backend.experience import ExperienceStore
    from backend.tools import ToolService, WorkspaceManager
    from tests.scripted_multi_agent import ScriptedRoleFactory

    settings = Settings.from_env()
    project = settings.project("python-checkout-complex")
    manager = WorkspaceManager(tmp_path / "work", {project.id: project.source})
    workspace = manager.create_workspace(project.source, str(uuid4()))
    store = ExperienceStore(tmp_path / "references.db")
    published = seed(store, scope="global", repository_id=None)
    observed = []

    class CheckedStore:
        def retrieve_model_references(self, query="", **context):
            observed.append(context)
            return store.retrieve_model_references(query, **context)

        def __getattr__(self, name):
            return getattr(store, name)

    factory = ScriptedRoleFactory()
    tools = ToolService(manager, settings.maven_home)
    coordinator = MultiAgentCoordinator(factory, tools.execute_tool, CheckedStore(),
                                       retrieval_strategy="adaptive", experience_format=representation)
    request = AgentRequest(task_id=str(uuid4()), workspace=workspace, task_description=project.task_example,
        language=project.language, repository_id=project.repository_id, capabilities=project.capabilities,
        source_revision="revision-a", experience_contract="contract-a", runner_hash="a" * 64,
        execution_mode="multi", max_steps=12)
    result = coordinator.run(request)
    assert result.repair_succeeded and result.tests_passed
    assert observed and all(item["source_revision"] == "revision-a" and item["compatibility_key"] == "contract-a"
                            and item["runner_hash"] == "a" * 64 for item in observed)
    references = [entry for messages in factory.requests
                  for entry in json.loads(messages[1]["content"]).get("reference_experiences", [])]
    assert references and all(entry["id"] == published.id and "rule_id" in entry for entry in references)
    assert all("constraints" in entry for entry in references)
    assert all(("evidence_summary" in entry) == (representation == "full") for entry in references)
    assert all(event.get("reason") == "test_failure" for event in coordinator.last_run_audit["retrieval_events"])
