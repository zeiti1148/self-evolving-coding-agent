import importlib.util
import json
import time
from uuid import uuid4

import pytest

from backend.shared import ApplicationError
from backend.tests.test_agent_proposals import environment


def context_and_runner(tmp_path, factory):
    assert importlib.util.find_spec("backend.agent.roles"), "independent role runner required"
    from backend.agent.context import capture_context
    from backend.agent.roles import RoleRunner
    from backend.agent.budget import RunBudget
    request, tools = environment(tmp_path)
    return capture_context(request, tools.execute_tool), RoleRunner(factory, RunBudget(5, time.monotonic()+10))


def test_roles_get_independent_clients_messages_and_closed_resources(tmp_path):
    clients = []
    class Client:
        def __init__(self):
            self.requests, self.closed = [], False
        def complete(self, messages, *, tools=None):
            assert tools is None
            self.requests.append(messages)
            return {"content": json.dumps({"issues": [], "dependencies": [], "summary": "Facts only"})}
        def close(self): self.closed = True
    def factory():
        client = Client(); clients.append(client); return client
    context, runner = context_and_runner(tmp_path, factory)
    runner.diagnose(context, str(uuid4()))
    runner.diagnose(context, str(uuid4()))
    assert len(clients) == 2 and all(client.closed for client in clients)
    assert clients[0].requests[0] is not clients[1].requests[0]
    assert all(len(client.requests[0]) == 2 for client in clients)
    assert runner.audit[0]["reported_tokens"] is None


@pytest.mark.parametrize("tool", ["write_file", "run_command", "run_tests"])
def test_role_cannot_execute_tools_even_if_model_requests_them(tmp_path, tool):
    class Client:
        closed = False
        def complete(self, messages, **kwargs):
            return {"content": None, "tool_calls": [{"function": {"name": tool, "arguments": "{}"}}]}
        def close(self): self.closed = True
    client = Client()
    context, runner = context_and_runner(tmp_path, lambda: client)
    with pytest.raises(ApplicationError):
        runner.diagnose(context, str(uuid4()))
    assert client.closed
