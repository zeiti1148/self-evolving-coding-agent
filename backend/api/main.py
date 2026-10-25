"""Run with: python -m uvicorn backend.api.main:app --host 127.0.0.1 --port 8000"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from backend.agent.core import Agent
from backend.agent.llm import ChatClient
from backend.config import Settings
from backend.experience import ExperienceStore
from backend.shared import (AgentRequest, ApplicationError, Experience, TaskCreateRequest,
                            TaskCreateResponse, TaskStatusResponse, TaskStepsResponse)
from backend.tools import ToolService, WorkspaceManager
from .store import TaskStore


def create_app(settings: Settings | None = None, *, llm_factory=None, agent_factory=None) -> FastAPI:
    settings = settings or Settings.from_env()
    tasks = TaskStore()
    manager = WorkspaceManager(settings.workspace_root, settings.projects)
    tools = ToolService(manager, settings.maven_home)
    experiences = ExperienceStore(settings.database_path)
    executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="repair")

    @asynccontextmanager
    async def lifespan(app):
        yield
        executor.shutdown(wait=True, cancel_futures=False)

    app = FastAPI(title="Self-Evolving Coding Agent", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
                       allow_methods=["GET", "POST"], allow_headers=["Content-Type"])
    app.state.tasks = tasks
    app.state.tools = tools
    app.state.settings = settings

    def run_task(task_id: str, request: TaskCreateRequest):
        try:
            tasks.start(task_id)
            workspace = manager.create_workspace(settings.projects[request.source_project], task_id)
            agent = agent_factory(tools, experiences) if agent_factory else Agent(
                llm_factory() if llm_factory else ChatClient(settings), tools.execute_tool, experiences)
            result = agent.run(AgentRequest(task_id=task_id, workspace=workspace,
                               task_description=request.task_description, max_steps=request.max_steps),
                               on_step=lambda step: tasks.append(task_id, step))
            tasks.finish(task_id, result=result)
        except ApplicationError as exc:
            tasks.finish(task_id, error=exc.message)
        except Exception:
            tasks.finish(task_id, error="Task scheduling or execution failed.")

    @app.post("/api/tasks", status_code=202, response_model=TaskCreateResponse)
    def create_task(request: TaskCreateRequest):
        source = settings.projects.get(request.source_project)
        if source is None:
            raise HTTPException(400, "Source project is not registered or available.")
        try:
            manager.validate_source(source)
        except ApplicationError:
            raise HTTPException(400, "Source project is not registered or available.") from None
        response = tasks.create()
        try:
            executor.submit(run_task, response.task_id, request.model_copy(deep=True))
        except RuntimeError:
            tasks.finish(response.task_id, error="Task scheduler is unavailable.")
        return response

    @app.get("/api/tasks/{task_id}", response_model=TaskStatusResponse)
    def get_task(task_id: UUID):
        try:
            return tasks.get(str(task_id))
        except KeyError:
            raise HTTPException(404, "Task was not found.") from None

    @app.get("/api/tasks/{task_id}/steps", response_model=TaskStepsResponse)
    def get_steps(task_id: UUID):
        try:
            return tasks.get_steps(str(task_id))
        except KeyError:
            raise HTTPException(404, "Task was not found.") from None

    @app.get("/api/experiences", response_model=list[Experience])
    def get_experiences(limit: Annotated[int, Query(ge=1, le=2**63-1)] = 20,
                        offset: Annotated[int, Query(ge=0, le=2**63-1)] = 0):
        try:
            return experiences.list_experiences(limit, offset)
        except ApplicationError:
            raise HTTPException(500, "Experience storage is unavailable.") from None

    return app


app = create_app()
