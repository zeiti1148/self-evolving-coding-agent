"""Public synchronous Agent contract."""
from collections.abc import Callable

from backend.config import Settings
from backend.experience import ExperienceStore
from backend.shared import AgentRequest, AgentResult, TaskStep
from backend.tools import execute_tool
from .core import Agent
from .llm import ChatClient

StepCallback = Callable[[TaskStep], None]


def run_agent(request: AgentRequest, *, on_step: StepCallback | None = None) -> AgentResult:
    settings = Settings.from_env()
    return Agent(ChatClient(settings), execute_tool, ExperienceStore(settings.database_path),
                 use_experience=settings.use_experience, timeout_seconds=settings.task_timeout).run(request, on_step=on_step)
