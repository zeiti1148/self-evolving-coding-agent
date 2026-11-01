import importlib.util
import json
from uuid import uuid4

import pytest

from backend.agent.core import Agent
from backend.experience import ExperienceStore
from backend.experience.database import connect
from backend.experience.store import _insert, _new_experience
from backend.shared import AgentRequest, ApplicationError, ErrorCode, ExperienceCreate
from backend.tests.test_agent import SequenceLLM


def seed(store, *, scope="project", repository_id="A", language="python", status="active", capabilities=()):
    """Unit policy fixture represents an already published row; production uses publication."""
    task_id = str(uuid4())
    record = _new_experience(ExperienceCreate(
        task_summary="Normalize inputs", root_cause="Unchecked input", solution="Check boundaries",
        verification="Private source test description", schema_version=3, scope=scope,
        repository_id=repository_id, language=language, reuse_status=status, source_task_id=task_id,
        source_revision="private-revision", required_capabilities=list(capabilities),
        evidence=[dict(source_task_id=task_id, tool_call_id=str(uuid4()), runner="pytest" if language == "python" else "maven", tests=18)],
        issues=[dict(error_type="ValueError", root_cause="Unchecked input", solution="Check boundaries", files=["private/source.py"])],
        applicability=["Respect the target project's missing-value policy"]))
    with connect(store.database_path) as db:
        _insert(db, record)
    return record


@pytest.mark.parametrize("repo,language,capabilities,expected", [
    ("A", "python", ["normalize"], {"local", "global"}),
    ("B", "python", ["normalize"], {"other", "global"}),
    (None, "python", ["normalize"], {"global"}),
    ("A", "java", ["normalize"], {"java"}),
    ("A", "python", [], {"local"}),
    ("a", "python", [], set()),
])
def test_every_retrieval_obeys_repository_language_status_and_capability(tmp_path, repo, language, capabilities, expected):
    store = ExperienceStore(tmp_path / "policy.db")
    records = dict(local=seed(store), other=seed(store, repository_id="B"),
                   global_=seed(store, scope="global", repository_id=None, capabilities=["normalize"]),
                   java=seed(store, scope="global", repository_id=None, language="java"))
    records["global"] = records.pop("global_")
    seed(store, status="candidate")
    seed(store, scope="global", repository_id=None, status="legacy_unreviewed")
    found = store.retrieve_experiences(language=language, repository_id=repo, capabilities=capabilities, error_types=[], limit=5)
    assert {x.id for x in found} == {records[name].id for name in expected}


def test_filtering_precedes_limit_and_management_keeps_unreviewed_rows(tmp_path):
    store = ExperienceStore(tmp_path / "policy.db")
    local = seed(store)
    for _ in range(6):
        seed(store, repository_id="B")
    store.save_experience(ExperienceCreate(task_summary="Old global", root_cause="Cause", solution="Rule", verification="old"))
    assert store.retrieve_experiences(language="python", repository_id="A", error_types=[], limit=1) == [local]
    assert len(store.list_experiences()) == 8
    assert store.retrieve_experiences(language="java", repository_id=None, error_types=[]) == []


def test_global_payload_omits_private_provenance(tmp_path):
    assert importlib.util.find_spec("backend.experience.policy") is not None, "shared policy module required"
    from backend.experience.policy import to_model_reference
    store = ExperienceStore(tmp_path / "policy.db")
    entry = seed(store, scope="global", repository_id=None)
    payload = to_model_reference(entry)
    encoded = json.dumps(payload)
    assert payload["id"] == entry.id and payload["applicability"] == entry.applicability
    for private in ("repository_id", "source_revision", "source_task_id", "derived_from", "merged_from", "private/source.py", "Private source test description"):
        assert private not in encoded
    assert entry.source_task_id and entry.issues[0].files


def test_single_agent_request_never_contains_other_repository_or_unreviewed(tmp_path):
    store = ExperienceStore(tmp_path / "policy.db")
    private = seed(store)
    general = seed(store, scope="global", repository_id=None)
    legacy = seed(store, scope="global", repository_id=None, status="legacy_unreviewed")
    llm = SequenceLLM([ApplicationError(ErrorCode.LLM_ERROR, "End inspection")])
    request = AgentRequest(task_id=str(uuid4()), workspace=tmp_path, task_description="Fix ValueError", language="python", repository_id="B")
    Agent(llm, lambda *_: None, store).run(request)
    payload = llm.requests[0][1]["content"]
    assert general.id in payload
    assert private.id not in payload and legacy.id not in payload and "private/source.py" not in payload


def test_regular_save_cannot_publish_active_global(tmp_path):
    store = ExperienceStore(tmp_path / "policy.db")
    data = seed(store).model_dump(exclude={"id", "created_at"})
    data.update(scope="global", repository_id=None)
    with pytest.raises(ApplicationError):
        store.save_experience(ExperienceCreate(**data))


def test_frozen_experiment_uses_identical_policy_without_mutating_snapshot(tmp_path):
    from scripts.experiment import FrozenSearchStore, freeze_database, file_hash
    store = ExperienceStore(tmp_path / "training.db")
    local = seed(store)
    seed(store, repository_id="B")
    seed(store, scope="global", repository_id=None, status="legacy_unreviewed")
    frozen = tmp_path / "frozen.db"
    digest = freeze_database(store.database_path, frozen)
    isolated = FrozenSearchStore(frozen, ExperienceStore(tmp_path / "output.db"))
    method = getattr(isolated, "retrieve_experiences", None)
    assert method is not None, "experiment must not use unrestricted management search for injection"
    assert method(language="python", repository_id="A", error_types=[]) == [local]
    assert method(language="python", repository_id=None, error_types=[]) == []
    assert file_hash(frozen) == digest
