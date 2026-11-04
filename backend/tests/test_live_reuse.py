import json
from uuid import uuid4

import pytest

from backend.config import Settings
from backend.projects import ProjectDefinition
from scripts import verify_live_reuse as verification


def source_record():
    return {"id": str(uuid4()), "language": "java", "error_type": "NullPointerException",
        "repository_id": "private-source", "source_task_id": str(uuid4()),
        "solution": 'Source fallback is "guest".', "required_capabilities": ["text-input"],
        "issues": [{"error_type": "NullPointerException", "files": ["private/Names.java"]}]}


def test_reviewed_draft_does_not_copy_source_defaults_identifiers_or_paths():
    source = source_record()
    draft = verification.reviewed_text_draft(source)
    text = draft.model_dump_json()
    assert draft.language == "java" and draft.required_capabilities == ["text-input"]
    assert all(not issue.files for issue in draft.issues)
    assert all(value not in text for value in ("guest", "visitor", source["id"], source["source_task_id"], "private-source", "private/Names.java"))
    assert "目标" in draft.solution and "原始测试" in draft.solution


def test_default_preflight_does_not_copy_database_create_output_or_call_models(tmp_path, monkeypatch):
    source = source_record()
    target = ProjectDefinition(id="java-booking-transfer", label="Target", language="java",
        repository_id="target", source=tmp_path / "fixture", expected_tests=12, capabilities=["text-input"])
    settings = Settings(project_definitions={target.id: target}, projects={target.id: target.source})
    monkeypatch.setattr(Settings, "from_env", classmethod(lambda cls: settings))
    monkeypatch.setattr(verification, "read_source", lambda *args: {
        "text_source": source, "tests": 18, "workspace": str(tmp_path / "original-workspace"),
        "snapshot": {"experiences": [source]}})
    def forbidden(*args, **kwargs):
        pytest.fail("Preflight invoked a mutating or model operation")
    for name in ("backup", "create_app", "ChatClient"):
        monkeypatch.setattr(verification, name, forbidden)
    destination = tmp_path / "new-output"
    result = verification.run(tmp_path / "original.db", str(uuid4()), destination)
    assert result["status"] == "preflight_passed" and result["live_request_made"] is False
    assert result["full_v3_acceptance"] is False and result["source_repair_rerun"] is False
    assert result["efficiency_comparison"] is False and not destination.exists()


def test_existing_output_is_rejected_before_reading_source(tmp_path, monkeypatch):
    output = tmp_path / "existing"
    output.mkdir()
    evidence = output / "report.json"
    evidence.write_text('{"preserve":true}', encoding="utf-8")
    with pytest.raises(ValueError, match="must not exist"):
        verification.run(tmp_path / "missing.db", str(uuid4()), output, live=True)
    assert json.loads(evidence.read_text(encoding="utf-8")) == {"preserve": True}


@pytest.mark.parametrize("inside", ["workspaces", "fixture"])
def test_output_cannot_recurse_into_original_workspaces_or_fixtures(tmp_path, monkeypatch, inside):
    settings = Settings(projects={"project": tmp_path / "fixture"})
    monkeypatch.setattr(Settings, "from_env", classmethod(lambda cls: settings))
    destination = tmp_path / inside / "new-run"
    with pytest.raises(ValueError, match="outside source workspaces"):
        verification.run(tmp_path / "source.db", str(uuid4()), destination, live=True)
    assert not destination.exists()
