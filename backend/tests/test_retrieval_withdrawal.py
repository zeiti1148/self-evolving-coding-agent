"""Reference withdrawal is an eligibility result, never a side effect of a failed query."""
import json
from uuid import uuid4

from backend.agent.core import Agent
from backend.shared import AgentRequest, ApplicationError, ErrorCode, ToolResult
from backend.tests.test_agent import SequenceLLM, tool_message


def reference(identifier):
    return dict(id=identifier, rule_id=identifier, task_summary='Known rule', scope='project',
                language='python', root_cause='cause', solution='solution')


def execute(workspace, call):
    return ToolResult(call_id=str(uuid4()), tool_name="run_tests", success=False,
                      error_code=ErrorCode.TEST_FAILED, output="ValueError", error_message="Test failed",
                      metadata={"error_types": ["ValueError"]})


def run(tmp_path, store):
    llm = SequenceLLM([tool_message("run_tests"), tool_message("run_tests"),
                       ApplicationError(ErrorCode.LLM_ERROR, "Stop after observing the payload")])
    request = AgentRequest(task_id=str(uuid4()), workspace=tmp_path, task_description="Repair a parser",
                           repository_id="A", language="python", max_steps=3)
    Agent(llm, execute, store).run(request)
    return [[entry["rule_id"] for entry in json.loads(messages[1]["content"])["reference_experiences"]]
            for messages in llm.requests]


class FailingAfterFirst:
    """First retrieval succeeds, every later one raises a transport error."""

    def __init__(self):
        self.calls = 0

    def retrieve_model_references(self, query="", **kwargs):
        self.calls += 1
        if self.calls > 1:
            raise RuntimeError('retrieval transport failure')
        return [reference('rule-a')]


class EmptyAfterFirst:
    """Serves the rule through the first decision, then succeeds with no eligible rules."""

    def __init__(self):
        self.calls = 0

    def retrieve_model_references(self, query="", **kwargs):
        # Call 1 is the initial retrieval; call 2 is the first decision boundary.
        self.calls += 1
        return [reference('rule-a')] if self.calls <= 2 else []


class FailingThenEmpty:
    """Fails twice, then succeeds with no eligible rules."""

    def __init__(self):
        self.calls = 0

    def retrieve_model_references(self, query="", **kwargs):
        self.calls += 1
        if self.calls == 1:
            return [reference('rule-a')]
        if self.calls < 4:
            raise RuntimeError('retrieval transport failure')
        return []


def test_transport_failure_keeps_previously_injected_references(tmp_path):
    payloads = run(tmp_path, FailingAfterFirst())
    assert payloads[0] == ['rule-a']
    # Every later decision still carries the rule the model already saw.
    assert payloads[-1] == ['rule-a']


def test_successful_retrieval_without_rules_withdraws_them(tmp_path):
    payloads = run(tmp_path, EmptyAfterFirst())
    assert payloads[0] == ['rule-a']
    # An empty successful result means the rule is no longer eligible for this evidence.
    assert payloads[-1] == []


def test_recovery_after_failures_still_withdraws(tmp_path):
    payloads = run(tmp_path, FailingThenEmpty())
    assert payloads[0] == ['rule-a']
    # The error flag describes the latest attempt only: a later success must withdraw.
    assert payloads[-1] == []
