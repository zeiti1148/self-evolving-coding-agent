"""Server-owned configuration. Importing this module performs no I/O."""
from __future__ import annotations

import os
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv
from backend.projects import ProjectDefinition, load_catalog

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Settings:
    workspace_root: Path = ROOT / "runtime/workspaces"
    database_path: Path = ROOT / "data/app.db"
    maven_home: Path = ROOT / ".cache/maven/apache-maven-3.9.11"
    llm_base_url: str = ""
    llm_api_key: str = field(default="", repr=False)
    llm_model: str = ""
    llm_trust_env: bool = False
    llm_timeout: float = 60.0
    task_timeout: float = 600.0
    use_experience: bool = True
    python_executable: Path = field(default_factory=lambda: Path(sys.executable))
    project_definitions: dict[str, ProjectDefinition] = field(default_factory=dict)
    projects: dict[str, Path] = field(default_factory=lambda: {
        "java-null-greeting": ROOT / "benchmarks/java",
        "java-null-profile": ROOT / "benchmarks/java-profile",
        "java-null-list": ROOT / "benchmarks/java-null-list",
        "java-null-map": ROOT / "benchmarks/java-null-map",
        "java-range-end": ROOT / "benchmarks/java-range-end",
        "java-parse-count": ROOT / "benchmarks/java-parse-count",
    })

    def __post_init__(self) -> None:
        if type(self.llm_trust_env) is not bool:
            raise ValueError("llm_trust_env must be a boolean")
        for name in ("llm_timeout", "task_timeout"):
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be a positive finite number")

    def project(self, identifier: str) -> ProjectDefinition:
        if identifier not in self.projects:
            raise KeyError(identifier)
        configured = self.project_definitions.get(identifier)
        if configured is not None:
            return configured.model_copy(update={"source": self.projects[identifier]})
        source = self.projects[identifier]
        language = "python" if (source / "pyproject.toml").is_file() and not (source / "pom.xml").is_file() else "java"
        greeting = '修复 GreetingService.greet：name 为 null 时返回 "Hello, guest!"，保留正常字符串行为，并通过原始测试。'
        repository = "java-benchmarks" if identifier in Settings().projects else identifier
        return ProjectDefinition(id=identifier, label=identifier, language=language,
                                 repository_id=repository, source=source,
                                 task_example=greeting if identifier == "java-null-greeting" else "修复项目中的异常并通过全部原始测试，保持正常行为。")

    @classmethod
    def from_env(cls) -> Settings:
        load_dotenv(ROOT / ".env", override=False)
        from backend.experience.database import configured_database_path

        def path(name: str, default: Path) -> Path:
            value = Path(os.environ.get(name) or default)
            return (ROOT / value).resolve() if not value.is_absolute() else value.resolve()

        experience = os.environ.get("USE_EXPERIENCE", "true").strip().lower()
        if experience not in {"true", "false"}:
            raise ValueError("USE_EXPERIENCE must be true or false")
        trust_env = os.environ.get("LLM_TRUST_ENV", "false").strip().lower()
        if trust_env not in {"true", "false"}:
            raise ValueError("LLM_TRUST_ENV must be true or false")

        defaults = cls()
        projects = dict(defaults.projects)
        definitions = {}
        bundled = ROOT / "benchmarks/multi-issue/catalog.json"
        if bundled.is_file():
            definitions.update(load_catalog(bundled, ROOT))
        external = os.environ.get("PROJECT_REGISTRY")
        if external:
            definitions.update(load_catalog(path("PROJECT_REGISTRY", Path(external)), ROOT))
        projects.update({key: value.source for key, value in definitions.items()})
        return cls(
            workspace_root=path("WORKSPACE_ROOT", ROOT / "runtime/workspaces"),
            database_path=configured_database_path(),
            maven_home=path("MAVEN_HOME", ROOT / ".cache/maven/apache-maven-3.9.11"),
            llm_base_url=os.environ.get("LLM_API_BASE_URL", "").rstrip("/"),
            llm_api_key=os.environ.get("LLM_API_KEY", ""),
            llm_model=os.environ.get("LLM_MODEL", ""),
            llm_trust_env=trust_env == "true",
            llm_timeout=float(os.environ.get("LLM_TIMEOUT_SECONDS", "60")),
            task_timeout=float(os.environ.get("TASK_TIMEOUT_SECONDS", "600")),
            use_experience=experience == "true",
            python_executable=path("PYTHON_EXECUTABLE", Path(sys.executable)),
            project_definitions=definitions,
            projects=projects,
        )
