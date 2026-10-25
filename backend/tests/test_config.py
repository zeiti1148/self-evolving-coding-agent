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


def test_model_environment_inheritance_is_explicit_and_validated(monkeypatch):
    assert Settings().llm_trust_env is False
    monkeypatch.setenv("LLM_TRUST_ENV", " TRUE ")
    assert Settings.from_env().llm_trust_env is True
    monkeypatch.setenv("LLM_TRUST_ENV", "false")
    assert Settings.from_env().llm_trust_env is False
    monkeypatch.setenv("LLM_TRUST_ENV", "automatic")
    with pytest.raises(ValueError, match="LLM_TRUST_ENV"):
        Settings.from_env()
    with pytest.raises(ValueError, match="llm_trust_env"):
        replace(Settings(), llm_trust_env="false")
