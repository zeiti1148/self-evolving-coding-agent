from dataclasses import replace

import pytest

from backend.config import Settings


@pytest.mark.parametrize("name", ["llm_timeout", "task_timeout"])
@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan")])
def test_time_budget_requires_positive_finite_value(name, value):
    with pytest.raises(ValueError, match=name):
        replace(Settings(), **{name: value})


def test_experience_switch_rejects_typo_and_parses_false(monkeypatch):
    monkeypatch.setenv("USE_EXPERIENCE", " FALSE ")
    assert Settings.from_env().use_experience is False
    monkeypatch.setenv("USE_EXPERIENCE", "flase")
    with pytest.raises(ValueError, match="USE_EXPERIENCE"):
        Settings.from_env()
