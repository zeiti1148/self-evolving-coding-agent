"""Repeated injection is measured from the audit, never inferred from model output."""
from uuid import uuid4

from backend.agent.core import Agent
from backend.experience.policy import injection_summary
from backend.shared import AgentRequest, ApplicationError, ErrorCode, ToolResult
from backend.tests.test_agent import SequenceLLM, tool_message


def reference(identifier):
    return dict(id=identifier, rule_id=identifier, task_summary='Known rule', scope='project',
                language='python', root_cause='cause', solution='solution')


class AlwaysSameRule:
    def retrieve_model_references(self, query='', **kwargs):
        return [reference('rule-a')]


def execute(workspace, call):
    return ToolResult(call_id=str(uuid4()), tool_name='run_tests', success=False,
                      error_code=ErrorCode.TEST_FAILED, output='ValueError', error_message='Test failed',
                      metadata={'error_types': ['ValueError']})


def test_summary_counts_repeated_rules_only():
    events = [{'experience_ids': ['a', 'b']}, {'experience_ids': ['a']}, {'experience_ids': ['a', 'c']}]
    value = injection_summary(events)
    assert value == dict(unique=3, total=5, repeated=2, repeated_ratio=0.4)


def test_summary_is_zero_without_injections():
    assert injection_summary([]) == dict(unique=0, total=0, repeated=0, repeated_ratio=0.0)


def test_audit_reports_repeated_injection_for_the_same_rule(tmp_path):
    llm = SequenceLLM([tool_message('run_tests'), tool_message('run_tests'),
                       ApplicationError(ErrorCode.LLM_ERROR, 'Stop after observing the payload')])
    request = AgentRequest(task_id=str(uuid4()), workspace=tmp_path, task_description='Fix ValueError in the parser',
                           repository_id='A', language='python', max_steps=3)
    agent = Agent(llm, execute, AlwaysSameRule())
    agent.run(request)
    summary = agent.last_run_audit['injection_summary']
    assert summary['unique'] == 1 and summary['total'] >= 2
    assert summary['repeated'] == summary['total'] - 1 and summary['repeated_ratio'] > 0
