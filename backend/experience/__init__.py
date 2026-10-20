"""Experience contracts for Agent/API; importing this module performs no I/O."""

from backend.shared import Experience, ExperienceCreate

from .database import configured_database_path
from .store import ExperienceStore

__all__ = ["ExperienceStore", "save_experience", "search_experiences", "list_experiences"]


def save_experience(data: ExperienceCreate) -> Experience:
    return ExperienceStore(configured_database_path()).save_experience(data)


def list_experiences(limit: int = 20, offset: int = 0) -> list[Experience]:
    return ExperienceStore(configured_database_path()).list_experiences(limit, offset)


def search_experiences(
    query: str,
    tags: list[str] | None = None,
    error_type: str | None = None,
    limit: int = 5,
) -> list[Experience]:
    return ExperienceStore(configured_database_path()).search_experiences(query, tags, error_type, limit)
