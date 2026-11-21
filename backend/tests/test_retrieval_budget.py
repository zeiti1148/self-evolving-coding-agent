"""Injection budget is explicit per retrieval strategy and recorded in the audit."""
from uuid import uuid4

import pytest

from backend.agent.core import Agent
from backend.experience.policy import RETRIEVAL_BUDGETS, retrieval_budget
from backend.shared import AgentRequest, ApplicationError, ErrorCode, ToolResult
from backend.tests.test_agent import SequenceLLM, tool_message


def test_budget_table_is_explicit_per_strategy():
    assert retrieval_budget('baseline') == (5, 16000)
    assert retrieval_budget('contextual') == (5, 16000)
    assert retrieval_budget('adaptive') == (3, 8000)
    # Unknown values never widen the payload.
    assert retrieval_budget('unknown') == RETRIEVAL_BUDGETS['contextual']


class RecordingStore:
    def __init__(self):
        self.calls = []

    def retrieve_model_references(self, query='', **kwargs):
        self.calls.append(kwargs)
        return []


def execute(workspace, call):
    return ToolResult(call_id=str(uuid4()), tool_name='run_tests', success=False,
                      error_code=ErrorCode.TEST_FAILED, output='ValueError', error_message='Test failed',
                      metadata={'error_types': ['ValueError']})


@pytest.mark.parametrize('strategy,expected', [('contextual', (5, 16000)), ('adaptive', (3, 8000))])
def test_agent_passes_the_strategy_budget_to_retrieval(tmp_path, strategy, expected):
    store = RecordingStore()
    llm = SequenceLLM([tool_message('run_tests'),
                       ApplicationError(ErrorCode.LLM_ERROR, 'Stop after observing the payload')])
    # Adaptive only reaches retrieval when the task itself names a failure.
    request = AgentRequest(task_id=str(uuid4()), workspace=tmp_path, task_description='Fix ValueError in the parser',
                           repository_id='A', language='python', max_steps=3)
    agent = Agent(llm, execute, store, retrieval_strategy=strategy)
    agent.run(request)
    assert store.calls, 'retrieval must run at least once'
    assert all((call['limit'], call['character_budget']) == expected for call in store.calls)
    assert agent.last_run_audit['reference_limit'] == expected[0]
    assert agent.last_run_audit['character_budget'] == expected[1]
    assert all(event['reference_limit'] == expected[0] for event in agent.last_run_audit['injection_events'])
