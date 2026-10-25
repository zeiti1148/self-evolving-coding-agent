"""Server-owned configuration. Importing this module performs no I/O."""
from __future__ import annotations

import os
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
    projects: dict[str, Path] = field(default_factory=lambda: {
        "java-null-greeting": ROOT / "benchmarks/java",
        "java-null-profile": ROOT / "benchmarks/java-profile",
    })

    @classmethod
    def from_env(cls) -> Settings:
        load_dotenv(ROOT / ".env", override=False)
        from backend.experience.database import configured_database_path

        def path(name: str, default: Path) -> Path:
            value = Path(os.environ.get(name) or default)
            return (ROOT / value).resolve() if not value.is_absolute() else value.resolve()

        return cls(
            workspace_root=path("WORKSPACE_ROOT", ROOT / "runtime/workspaces"),
            database_path=configured_database_path(),
            maven_home=path("MAVEN_HOME", ROOT / ".cache/maven/apache-maven-3.9.11"),
            llm_base_url=os.environ.get("LLM_API_BASE_URL", "").rstrip("/"),
            llm_api_key=os.environ.get("LLM_API_KEY", ""),
            llm_model=os.environ.get("LLM_MODEL", ""),
            llm_timeout=float(os.environ.get("LLM_TIMEOUT_SECONDS", "60")),
        )
