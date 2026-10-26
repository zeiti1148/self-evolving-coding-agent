"""Run with: python -m uvicorn backend.api.main:app --host 127.0.0.1 --port 8000"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from typing import Annotated, Literal
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.agent.core import Agent
from backend.agent.llm import ChatClient
from backend.config import Settings
from backend.projects import public_project, source_revision, validate_project_language
from backend.experience import ExperienceStore
from backend.shared import (AgentRequest, ApplicationError, Experience, TaskCreateRequest,
                            TaskCreateResponse, TaskStatusResponse, TaskStepsResponse, ErrorCode)
from backend.tools import ToolService, WorkspaceManager
from .store import TaskStore


class MergeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    experience_ids: list[UUID] = Field(min_length=2, max_length=10)

    @field_validator("experience_ids")
    @classmethod
    def unique_ids(cls, values):
        if len(set(values)) != len(values):
            raise ValueError("Select distinct experiences")
        return values


def create_app(settings: Settings | None = None, *, llm_factory=None, agent_factory=None) -> FastAPI:
    settings = settings or Settings.from_env()
    tasks = TaskStore()
    manager = WorkspaceManager(settings.workspace_root, settings.projects)
    tools = ToolService(manager, settings.maven_home, python_executable=settings.python_executable)
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
    app.state.experiences = experiences

    def run_task(task_id: str, request: TaskCreateRequest):
        try:
            tasks.start(task_id)
            project = settings.project(request.source_project)
            validate_project_language(project)
            workspace = manager.create_workspace(settings.projects[request.source_project], task_id)
            revision = source_revision(project.source, manager.get(workspace).baseline)
            agent = agent_factory(tools, experiences) if agent_factory else Agent(
                llm_factory() if llm_factory else ChatClient(settings), tools.execute_tool, experiences,
                use_experience=settings.use_experience, timeout_seconds=settings.task_timeout)
            result = agent.run(AgentRequest(task_id=task_id, workspace=workspace,
                               task_description=request.task_description, max_steps=request.max_steps,
                               language=project.language, repository_id=project.repository_id,
                               source_revision=revision),
                               on_step=lambda step: tasks.append(task_id, step))
            tasks.put_audit(task_id, getattr(agent, "last_run_audit", {}))
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
            validate_project_language(settings.project(request.source_project))
        except (ApplicationError, ValueError):
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
                        offset: Annotated[int, Query(ge=0, le=2**63-1)] = 0,
                        language: Literal["java", "python"] | None = None,
                        repository_id: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
                        scope: Literal["global", "project"] | None = None):
        try:
            return experiences.list_experiences(limit, offset, language=language,
                                                repository_id=repository_id, scope=scope)
        except ApplicationError:
            raise HTTPException(500, "Experience storage is unavailable.") from None

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

    def merge_sources(body: MergeRequest) -> list[str]:
        ids = [str(value) for value in body.experience_ids]
        if any(experiences.get_experience(value) is None for value in ids):
            raise HTTPException(404, "An experience selected for merging was not found.")
        return ids

    @app.post("/api/experiences/merge-preview")
    def preview_merge(body: MergeRequest):
        try:
            return experiences.preview_merge(merge_sources(body))
        except ApplicationError as exc:
            code = 409 if exc.code == ErrorCode.EXPERIENCE_VALIDATION_ERROR else 500
            raise HTTPException(code, exc.message) from None

    @app.post("/api/experiences/merge", response_model=Experience)
    def merge_experiences(body: MergeRequest):
        try:
            return experiences.merge_experiences(merge_sources(body))
        except ApplicationError as exc:
            code = 409 if exc.code == ErrorCode.EXPERIENCE_VALIDATION_ERROR else 500
            raise HTTPException(code, exc.message) from None

    return app


app = create_app()
