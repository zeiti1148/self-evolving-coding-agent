"""Run with: python -m uvicorn backend.api.main:app --host 127.0.0.1 --port 8000"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
import logging
from threading import Lock
from typing import Annotated, Literal
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.agent.core import Agent
from backend.agent.orchestrator import MultiAgentCoordinator
from backend.agent.llm import ChatClient
from backend.config import Settings
from backend.projects import public_project, source_revision, validate_project_language
from backend.experience import ExperienceStore
from backend.shared import (AgentRequest, ApplicationError, Experience, TaskCreateRequest,
                            TaskCreateResponse, TaskStatusResponse, TaskStepsResponse, ErrorCode)
from backend.tools import ToolService, WorkspaceManager
from .store import TaskStorageError, TaskStore
from .experience_routes import register_experience_routes, MergeRequest
from backend.shared import AgentEvent


logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None, *, llm_factory=None, agent_factory=None, multi_agent_factory=None) -> FastAPI:
    settings = settings or Settings.from_env()
    tasks = TaskStore(settings.database_path)
    manager = WorkspaceManager(settings.workspace_root, settings.projects)
    tools = ToolService(manager, settings.maven_home, python_executable=settings.python_executable)
    experiences = ExperienceStore(settings.database_path)
    executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="repair")
    recording_failures: set[str] = set()
    recording_lock = Lock()

    @asynccontextmanager
    async def lifespan(app):
        try:
            tasks.initialize()
        except TaskStorageError:
            logger.warning("Task storage unavailable at startup; task endpoints will report 503.")
        try:
            yield
        finally:
            try:
                executor.shutdown(wait=True, cancel_futures=False)
            finally:
                tasks.close()

    app = FastAPI(title="Self-Evolving Coding Agent", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
                       allow_methods=["GET", "POST"], allow_headers=["Content-Type"])
    app.state.tasks = tasks
    app.state.tools = tools
    app.state.settings = settings
    app.state.experiences = experiences

    @app.exception_handler(TaskStorageError)
    async def task_storage_error(request, exc):
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    def ensure_recorded(task_id: str | None = None):
        with recording_lock:
            failed = bool(recording_failures) if task_id is None else task_id in recording_failures
        if failed:
            raise HTTPException(503, "Task recording failed; restore storage and restart the service.")

    def fail_task(task_id: str, message: str):
        try:
            tasks.finish(task_id, error=message.strip()[:5000] or "Task execution failed.")
        except (TaskStorageError, KeyError, ValueError):
            # Do not leave a live client polling a stale 'running' snapshot forever.
            with recording_lock:
                recording_failures.add(task_id)
            logger.error("Could not persist terminal state for task=%s", task_id)

    def run_task(task_id: str, request: TaskCreateRequest):
        try:
            tasks.start(task_id)
            project = settings.project(request.source_project)
            validate_project_language(project)
            workspace = manager.create_workspace(settings.projects[request.source_project], task_id)
            revision = source_revision(project.source, manager.get(workspace).baseline)
            factory = llm_factory or (lambda: ChatClient(settings))
            if request.execution_mode == "multi":
                agent = multi_agent_factory(tools, experiences) if multi_agent_factory else MultiAgentCoordinator(
                    factory, tools.execute_tool, experiences, use_experience=settings.use_experience,
                    timeout_seconds=settings.task_timeout)
            else:
                agent = agent_factory(tools, experiences) if agent_factory else Agent(
                    factory(), tools.execute_tool, experiences, use_experience=settings.use_experience,
                    timeout_seconds=settings.task_timeout)
            callbacks = {"on_step": lambda step: tasks.append(task_id, step)}
            if request.execution_mode == "multi":
                callbacks["on_event"] = lambda event: tasks.append_agent_event(task_id, event)
            result = agent.run(AgentRequest(task_id=task_id, workspace=workspace,
                               task_description=request.task_description, max_steps=request.max_steps,
                               language=project.language, repository_id=project.repository_id,
                               source_revision=revision, capabilities=project.capabilities,
                               execution_mode=request.execution_mode), **callbacks)
            tasks.finish(task_id, result=result, audit=getattr(agent, "last_run_audit", {}))
        except ApplicationError as exc:
            fail_task(task_id, exc.message)
        except Exception:
            fail_task(task_id, "Task scheduling or execution failed.")

    @app.post("/api/tasks", status_code=202, response_model=TaskCreateResponse)
    def create_task(request: TaskCreateRequest):
        source = settings.projects.get(request.source_project)
        if source is None:
            raise HTTPException(400, "Source project is not registered or available.")
        try:
            manager.validate_source(source)
            validate_project_language(settings.project(request.source_project))
        except (ApplicationError, ValueError):
            raise HTTPException(400, "Source project is not registered or available.") from None
        response = tasks.create(request)
        try:
            executor.submit(run_task, response.task_id, request.model_copy(deep=True))
        except RuntimeError:
            fail_task(response.task_id, "Task scheduler is unavailable.")
        return response

    @app.get("/api/tasks", response_model=list[TaskStatusResponse])
    def list_tasks(limit: Annotated[int, Query(ge=1, le=100)] = 20,
                   offset: Annotated[int, Query(ge=0, le=2**63-1)] = 0):
        ensure_recorded()
        return tasks.list_tasks(limit, offset)

    @app.get("/api/tasks/{task_id}", response_model=TaskStatusResponse)
    def get_task(task_id: UUID):
        ensure_recorded(str(task_id))
        try:
            return tasks.get(str(task_id))
        except KeyError:
            raise HTTPException(404, "Task was not found.") from None

    @app.get("/api/tasks/{task_id}/steps", response_model=TaskStepsResponse)
    def get_steps(task_id: UUID):
        ensure_recorded(str(task_id))
        try:
            return tasks.get_steps(str(task_id))
        except KeyError:
            raise HTTPException(404, "Task was not found.") from None

    @app.get("/api/tasks/{task_id}/agents", response_model=list[AgentEvent])
    def get_agent_events(task_id: UUID, after_sequence: Annotated[int, Query(ge=0, le=2**63-1)] = 0,
                         limit: Annotated[int, Query(ge=1, le=100)] = 100):
        ensure_recorded(str(task_id))
        try:
            return tasks.get_agent_events(str(task_id), after_sequence=after_sequence, limit=limit)
        except KeyError:
            raise HTTPException(404, "Task was not found.") from None

    register_experience_routes(app, experiences)

    @app.get("/api/projects")
    def get_projects():
        result = []
        for identifier in settings.projects:
            project = settings.project(identifier)
            try:
                manager.validate_source(project.source)
                validate_project_language(project)
                result.append(public_project(project, source_revision(project.source)))
            except (ApplicationError, OSError, ValueError):
                continue
        return result

    return app


app = create_app()
