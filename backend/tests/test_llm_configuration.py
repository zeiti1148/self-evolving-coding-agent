"""Project credential isolation and auditable routing, without external requests."""
import json
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from backend.config import Settings
from backend.llm_configuration import api_identity, resolve_profile


@pytest.fixture(autouse=True)
def clear_profile_selectors(monkeypatch):
    monkeypatch.delenv("LLM_CONFIG_FILE", raising=False)
    monkeypatch.delenv("LLM_CONFIG_SOURCE", raising=False)


def write_profile(path, *, key="project-key", model="project-model", base="https://project.invalid/v1"):
    path.write_text(f"LLM_API_BASE_URL={base}\nLLM_API_KEY={key}\nLLM_MODEL={model}\n", encoding="utf-8")


def test_project_profile_cannot_be_overridden_by_another_projects_environment(tmp_path, monkeypatch):
    write_profile(tmp_path / ".env")
    for field, value in [("LLM_API_BASE_URL", "https://other.invalid"), ("LLM_API_KEY", "other-key"), ("LLM_MODEL", "other-model")]:
        monkeypatch.setenv(field, value)
    profile, source, path = resolve_profile(tmp_path)
    assert profile == {"LLM_API_BASE_URL": "https://project.invalid/v1", "LLM_API_KEY": "project-key", "LLM_MODEL": "project-model"}
    assert source == "project_file" and path == str((tmp_path / ".env").resolve())


def test_incomplete_file_never_borrows_a_key_from_environment(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("LLM_MODEL=local-model\n", encoding="utf-8")
    monkeypatch.setenv("LLM_API_KEY", "other-project-key")
    profile, _, _ = resolve_profile(tmp_path)
    assert profile["LLM_API_KEY"] == "" and profile["LLM_MODEL"] == "local-model"


def test_no_file_does_not_implicitly_use_shared_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "other-key")
    assert not any(resolve_profile(tmp_path)[0].values())


def test_environment_only_deployment_requires_explicit_source(tmp_path, monkeypatch):
    write_profile(tmp_path / ".env")
    monkeypatch.setenv("LLM_CONFIG_SOURCE", "environment")
    monkeypatch.setenv("LLM_API_KEY", "deployment-key")
    monkeypatch.delenv("LLM_MODEL", raising=False)
    profile, source, path = resolve_profile(tmp_path)
    assert source == "environment" and path is None
    assert profile["LLM_API_KEY"] == "deployment-key" and profile["LLM_MODEL"] == ""


def test_explicit_file_wins_over_shared_profile_selector(tmp_path, monkeypatch):
    write_profile(tmp_path / "correct.env")
    monkeypatch.setenv("LLM_CONFIG_SOURCE", "environment")
    monkeypatch.setenv("LLM_CONFIG_FILE", "unrelated.env")
    assert resolve_profile(tmp_path, tmp_path / "correct.env")[0]["LLM_API_KEY"] == "project-key"


def test_missing_explicit_file_fails_without_fallback(tmp_path):
    write_profile(tmp_path / ".env")
    with pytest.raises(ValueError, match="does not exist"):
        resolve_profile(tmp_path, tmp_path / "missing.env")


def test_interpolation_cannot_import_another_key(tmp_path, monkeypatch):
    monkeypatch.setenv("ANOTHER_KEY", "private")
    write_profile(tmp_path / ".env", key="${ANOTHER_KEY}")
    with pytest.raises(ValueError, match="interpolation"):
        resolve_profile(tmp_path)


def test_reading_two_roots_does_not_pollute_credentials(tmp_path, monkeypatch):
    import backend.config as config
    for key in ("LLM_API_KEY", "LLM_MODEL", "LLM_API_BASE_URL"):
        monkeypatch.delenv(key, raising=False)
    import os
    for name in ("first", "second"):
        root = tmp_path / name
        root.mkdir()
        write_profile(root / ".env", key=f"{name}-key")
        monkeypatch.setattr(config, "ROOT", root)
        assert Settings.from_env().llm_api_key == f"{name}-key"
        assert "LLM_API_KEY" not in os.environ


def test_identity_redacts_key_and_sensitive_url_parts():
    settings = replace(Settings(), llm_base_url="https://user:url-password@api.invalid/private-path?key=query-secret#fragment-secret",
                       llm_api_key="actual-private-key", llm_model="model")
    identity = api_identity(settings)
    assert identity["origin"] == "https://api.invalid" and identity["key_present"]
    serialized = json.dumps(identity)
    for secret in ("url-password", "private-path", "query-secret", "fragment-secret", "actual-private-key"):
        assert secret not in serialized
    assert api_identity(settings) == identity


@pytest.mark.parametrize("field,value", [("llm_base_url", "https://different.invalid"), ("llm_api_key", "different-key"), ("llm_model", "different-model")])
def test_identity_changes_if_endpoint_model_or_credential_changes(field, value):
    settings = replace(Settings(), llm_base_url="https://api.invalid", llm_api_key="key", llm_model="model")
    assert api_identity(settings)["identity"] != api_identity(replace(settings, **{field: value}))["identity"]


def test_request_audit_records_actual_configuration_without_key():
    from backend.agent.llm import ChatClient
    settings = replace(Settings(), llm_base_url="https://api.invalid/v1", llm_api_key="test-private-key", llm_model="model")
    def respond(request):
        assert str(request.url) == "https://api.invalid/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer test-private-key"
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "done"}}]})
    client = ChatClient(settings, transport=httpx.MockTransport(respond))
    try:
        client.complete([{"role": "user", "content": "hello"}])
        assert client.audit[0]["api_configuration"] == api_identity(settings)
        assert settings.llm_api_key not in json.dumps(client.audit)
    finally:
        client.close()


def test_live_experiment_requires_explicit_identity_before_work(monkeypatch, tmp_path):
    from scripts import knowledge_live_experiment as experiment
    monkeypatch.setattr("sys.argv", ["experiment", "--live", "--output", str(tmp_path / "out")])
    with pytest.raises(SystemExit) as result:
        experiment.main()
    assert result.value.code == 2 and not (tmp_path / "out").exists()


def test_inspection_is_offline_and_changed_profile_cannot_launch(monkeypatch, tmp_path, capsys):
    from scripts import knowledge_live_experiment as experiment
    profile = tmp_path / "approved.env"
    write_profile(profile)
    def forbid(*args, **kwargs):
        pytest.fail("No HTTP or model client may be created during preflight")
    monkeypatch.setattr(experiment, "ChatClient", forbid)
    monkeypatch.setattr(httpx, "Client", forbid)
    monkeypatch.setattr("sys.argv", ["experiment", "--inspect-api", "--llm-env-file", str(profile)])
    experiment.main()
    inspected = json.loads(capsys.readouterr().out)
    assert inspected["source_file"] == str(profile) and "project-key" not in json.dumps(inspected)
    write_profile(profile, key="changed-key")
    monkeypatch.setattr("sys.argv", ["experiment", "--live", "--llm-env-file", str(profile),
                                     "--expect-api-identity", inspected["identity"], "--output", str(tmp_path / "out")])
    with pytest.raises(SystemExit) as result:
        experiment.main()
    assert result.value.code == 2 and not (tmp_path / "out").exists()
