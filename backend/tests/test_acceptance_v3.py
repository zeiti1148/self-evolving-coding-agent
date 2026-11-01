import pytest

from scripts.acceptance_v3 import inspect_requests, run


def test_acceptance_audit_detects_private_cross_repository_payload():
    safe = [{"role": "user", "content": '{"reference_experiences":[{"id":"x","scope":"global","language":"python","reuse_status":"active"}]}'}]
    assert inspect_requests([safe], language="python", repository_id="B")[0]["experience_ids"] == ["x"]
    unsafe = [{"role": "user", "content": '{"reference_experiences":[{"id":"x","scope":"project","language":"python","repository_id":"A","reuse_status":"active"}]}'}]
    with pytest.raises(AssertionError):
        inspect_requests([unsafe], language="python", repository_id="B")
    private = [{"role": "user", "content": '{"reference_experiences":[{"id":"x","scope":"global","language":"python","source_task_id":"private","reuse_status":"active"}]}'}]
    with pytest.raises(AssertionError):
        inspect_requests([private], language="python", repository_id="B")


def test_live_without_configuration_is_reported_without_creating_database(tmp_path, monkeypatch):
    from backend.config import Settings
    monkeypatch.setattr(Settings, "from_env", classmethod(lambda _: Settings()))
    report = run(True, tmp_path / "live")
    assert report["status"] == "not_run" and report["passed"] is None
    assert not (tmp_path / "live/experiences.db").exists()
