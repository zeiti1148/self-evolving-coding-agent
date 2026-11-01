from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from backend.shared import models as m


def uid():
    return str(uuid4())


def experience(**overrides):
    return dict(task_summary="Normalize missing input", root_cause="Missing input is dereferenced",
                solution="Validate missing input before normalization", verification="Final tests passed",
                **overrides)


def result(**overrides):
    call_id = uid()
    return dict(task_id=uid(), status="succeeded", summary="Fixed", repair_succeeded=True,
                tests_passed=True, experience_saved=True,
                steps=[dict(id=call_id, name="run_tests")],
                tool_results=[dict(call_id=call_id, tool_name="run_tests", success=True)], **overrides)


def test_old_result_expands_single_id():
    identifier = uid()
    value = m.AgentResult(**result(experience_id=identifier))
    assert value.experience_ids == [identifier]
    assert m.AgentResult.model_validate_json(value.model_dump_json()) == value


def test_array_result_populates_legacy_id():
    identifiers = [uid(), uid()]
    value = m.AgentResult(**result(experience_ids=identifiers))
    assert value.experience_id == identifiers[0]


@pytest.mark.parametrize("ids", [[], ["bad"], None])
def test_result_rejects_mismatched_ids(ids):
    with pytest.raises(ValidationError):
        m.AgentResult(**result(experience_id=uid(), experience_ids=ids))


def test_result_rejects_duplicate_ids():
    identifier = uid()
    with pytest.raises(ValidationError):
        m.AgentResult(**result(experience_ids=[identifier, identifier]))


def test_unsaved_result_cannot_carry_array():
    with pytest.raises(ValidationError):
        m.AgentResult(task_id=uid(), status="stopped", summary="Budget exhausted", repair_succeeded=False,
                      experience_ids=[uid()])


def test_legacy_global_is_unreviewed():
    assert m.ExperienceCreate(**experience()).reuse_status == "legacy_unreviewed"
    assert m.ExperienceCreate(**experience(schema_version=3)).reuse_status == "candidate"


def test_v3_project_requires_repository_and_verified_evidence():
    with pytest.raises(ValidationError):
        m.ExperienceCreate(**experience(schema_version=3, scope="project"))
    with pytest.raises(ValidationError):
        m.ExperienceCreate(**experience(schema_version=3, scope="project", repository_id="A"))
    task_id = uid()
    evidence = dict(source_task_id=task_id, tool_call_id=uid(), runner="maven", tests=18)
    value = m.ExperienceCreate(**experience(schema_version=3, scope="project", repository_id="A",
                                           source_task_id=task_id, evidence=[evidence]))
    assert value.reuse_status == "active"
    assert value.evidence[0].tests == 18


@pytest.mark.parametrize("path", ["../x.py", "C:/x.py", "a\\b.py", "NUL.py", "a.py."])
def test_proposal_rejects_unsafe_paths(path):
    cls = getattr(m, "FileChange", None)
    assert cls is not None, "FileChange contract is required"
    with pytest.raises(ValidationError):
        cls(path=path, before_sha256="a" * 64, content="text")


def test_proposal_limits_and_preserves_content_whitespace():
    cls = getattr(m, "PatchProposal", None)
    assert cls is not None, "PatchProposal contract is required"
    change = dict(path="app/a.py", before_sha256="a" * 64, content="  value\n")
    base = dict(proposal_id=uid(), agent_id=uid(), base_revision="b" * 64)
    assert cls(**base, changes=[change]).changes[0].content == "  value\n"
    for changes in ([], [change] * 21, [dict(change, content="界" * 70000)]):
        with pytest.raises(ValidationError):
            cls(**base, changes=changes)


def test_event_json_roundtrip_and_sequence():
    cls = getattr(m, "AgentEvent", None)
    assert cls is not None, "AgentEvent contract is required"
    data = dict(event_id=uid(), task_id=uid(), agent_id=uid(), role="diagnosis", phase="diagnose",
                status="started", sequence=1, snapshot_revision="a" * 64, summary="Reading source",
                created_at=datetime.now(timezone.utc), usage={"reported_tokens": None, "elapsed_ms": 0})
    value = cls(**data)
    assert cls.model_validate_json(value.model_dump_json()) == value
    for bad in (0, True, 1.5):
        with pytest.raises(ValidationError):
            cls(**dict(data, sequence=bad))


def test_diagnosis_rejects_cycles_and_invalid_references():
    cls = getattr(m, "DiagnosisReport", None)
    assert cls is not None, "DiagnosisReport contract is required"
    issues = [dict(error_type="ValueError", root_cause="Input invalid", solution="Check input")] * 2
    assert cls(issues=issues, dependencies=[(0, 1)], summary="Two conditions").dependencies == [(0, 1)]
    for edges in ([(0, 2)], [(0, 1), (1, 0)], [(0, 0)]):
        with pytest.raises(ValidationError):
            cls(issues=issues, dependencies=edges, summary="Invalid dependencies")


def test_review_rejection_requires_reason_and_promotion_excludes_authority_fields():
    review = getattr(m, "ReviewDecision", None)
    draft = getattr(m, "PromotionDraft", None)
    assert review is not None and draft is not None
    with pytest.raises(ValidationError):
        review(accepted=False, reasons=[])
    with pytest.raises(ValidationError):
        draft(**experience(scope="global", evidence=[]))


def test_default_mode_and_capabilities(tmp_path):
    assert m.TaskCreateRequest(task_description="Fix", source_project="A").execution_mode == "single"
    request = m.AgentRequest(task_id=uid(), task_description="Fix", workspace=tmp_path,
                             execution_mode="multi", capabilities=["normalize", "normalize"])
    assert request.capabilities == ["normalize"]
