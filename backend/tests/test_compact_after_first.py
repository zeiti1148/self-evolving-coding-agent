"""Compact representation after the first decision saves repeat-injection tokens.

Contextual retrieval re-sends the same rules every round, so the only per-round
saving available is dropping supporting metadata. Conditions, constraints and
reuse boundaries must survive, which is what the compact formatter keeps.
"""
from uuid import uuid4
from backend.agent.core import Agent
from backend.shared import AgentRequest, ApplicationError, ErrorCode, ToolResult
from backend.tests.test_agent import SequenceLLM, tool_message


def reference(identifier):
    return dict(id=identifier, rule_id=identifier, schema_version=4, language='python', scope='project',
                reuse_status='active', task_summary='Known rule', root_cause='cause', solution='solution',
                applicability=['only when input may be missing'], constraints=['keep the default text'],
                created_at='2026-11-21T00:00:00Z', tags=['tag'], verification='original tests passed',
                evidence=[{'tests': 2}], issues=[{'error_type': 'ValueError', 'root_cause': 'cause',
                                                  'solution': 'solution', 'files': ['app.py']}])


class SameRuleEveryRound:
    def retrieve_model_references(self, query='', **kwargs):
        return [reference('rule-a')]


def execute(workspace, call):
    return ToolResult(call_id=str(uuid4()), tool_name='run_tests', success=False,
                      error_code=ErrorCode.TEST_FAILED, output='ValueError', error_message='Test failed',
                      metadata={'error_types': ['ValueError']})


def run(tmp_path, **options):
    llm = SequenceLLM([tool_message('read_file'), tool_message('read_file'),
                       ApplicationError(ErrorCode.LLM_ERROR, 'stop')])
    request = AgentRequest(task_id=str(uuid4()), workspace=tmp_path,
                           task_description='Repair the parser', repository_id='A', language='python',
                           max_steps=4)
    agent = Agent(llm, execute, SameRuleEveryRound(), **options)
    agent.run(request)
    return agent


def test_compact_after_first_shrinks_later_injections(tmp_path):
    agent = run(tmp_path, compact_after_first=True)
    sizes = [event['injected_characters'] for event in agent.last_run_audit['injection_events']]
    representations = [event['representation'] for event in agent.last_run_audit['injection_events']]
    assert representations[0] == 'full' and representations[1] == 'compact'
    assert sizes[1] < sizes[0]


def test_disabled_by_default_keeps_full_references(tmp_path):
    agent = run(tmp_path)
    representations = {event['representation'] for event in agent.last_run_audit['injection_events']}
    assert representations == {'full'}


def test_compact_keeps_conditions_and_constraints(tmp_path):
    run(tmp_path, compact_after_first=True)
    from backend.experience.policy import format_model_reference
    compact = format_model_reference(reference('rule-a'), 'compact')
    assert compact['applicability'] and compact['constraints'] and compact['issues']
    assert 'created_at' not in compact and 'verification' not in compact
