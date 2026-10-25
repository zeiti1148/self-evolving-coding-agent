"""Server-owned configuration. Importing this module performs no I/O."""
from __future__ import annotations

import os
import math
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Settings:
    workspace_root: Path = ROOT / "runtime/workspaces"
    database_path: Path = ROOT / "data/app.db"
    maven_home: Path = ROOT / ".cache/maven/apache-maven-3.9.11"
    llm_base_url: str = ""
    llm_api_key: str = field(default="", repr=False)
    llm_model: str = ""
    llm_timeout: float = 60.0
    task_timeout: float = 600.0
    use_experience: bool = True
    projects: dict[str, Path] = field(default_factory=lambda: {
        "java-null-greeting": ROOT / "benchmarks/java",
        "java-null-profile": ROOT / "benchmarks/java-profile",
        "java-null-list": ROOT / "benchmarks/java-null-list",
        "java-null-map": ROOT / "benchmarks/java-null-map",
        "java-range-end": ROOT / "benchmarks/java-range-end",
        "java-parse-count": ROOT / "benchmarks/java-parse-count",
    })

    def __post_init__(self) -> None:
        for name in ("llm_timeout", "task_timeout"):
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be a positive finite number")

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

        return cls(
            workspace_root=path("WORKSPACE_ROOT", ROOT / "runtime/workspaces"),
            database_path=configured_database_path(),
            maven_home=path("MAVEN_HOME", ROOT / ".cache/maven/apache-maven-3.9.11"),
            llm_base_url=os.environ.get("LLM_API_BASE_URL", "").rstrip("/"),
            llm_api_key=os.environ.get("LLM_API_KEY", ""),
            llm_model=os.environ.get("LLM_MODEL", ""),
            llm_timeout=float(os.environ.get("LLM_TIMEOUT_SECONDS", "60")),
            task_timeout=float(os.environ.get("TASK_TIMEOUT_SECONDS", "600")),
            use_experience=experience == "true",
        )
