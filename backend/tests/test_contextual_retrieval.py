import importlib.util
import json
from datetime import timedelta
from uuid import uuid4

import pytest

from backend.agent.core import Agent
from backend.experience import ExperienceStore
from backend.experience.policy import RetrievalContext, rank_references
from backend.shared import AgentRequest, ApplicationError, ErrorCode
from backend.tests.test_agent import SequenceLLM
from backend.tests.test_experience_policy import seed


@pytest.mark.parametrize("query,summary", [
    ("修复数量格式错误后再检查业务边界", "数量格式验证"),
    ("parseQuantity", "parse_quantity validation"),
])
def test_contextual_ranking_prefers_matching_rule_over_newest_record(tmp_path, query, summary):
    entry = seed(ExperienceStore(tmp_path / "refs.db"))
    relevant = entry.model_copy(update={"id": str(uuid4()), "task_summary": summary})
    unrelated = entry.model_copy(update={"id": str(uuid4()), "task_summary": "Calendar dates",
        "created_at": entry.created_at + timedelta(seconds=1)})
    context = RetrievalContext("python", "A")
    assert rank_references([relevant, unrelated], context, query=query, error_types=["ValueError"], limit=1) == [relevant]
    assert rank_references([relevant, unrelated], context, query="", error_types=["ValueError"], limit=1) == [unrelated]


def test_contextual_deduplication_preserves_distinct_applicability(tmp_path):
    entry = seed(ExperienceStore(tmp_path / "refs.db"))
    duplicate = entry.model_copy(update={"id": str(uuid4()), "created_at": entry.created_at + timedelta(seconds=1)})
    distinct = entry.model_copy(update={"id": str(uuid4()), "applicability": ["Only Quantity >= 1 is valid"]})
    context = RetrievalContext("python", "A")
    result = rank_references([entry, duplicate, distinct], context, query="Unchecked input", error_types=[], limit=5)
    assert len(result) == 2 and distinct in result
    assert len(rank_references([entry, duplicate, distinct], context, query="", error_types=[], limit=5)) == 3


def test_retrieval_query_uses_latest_failure_evidence_and_is_bounded():
    assert importlib.util.find_spec("backend.experience.retrieval") is not None
    from backend.experience.retrieval import build_retrieval_query
    results = [dict(tool_name="run_tests", success=False, output="obsoleteFailure", metadata={}),
               dict(tool_name="read_file", success=True, output="irrelevantSource", metadata={}),
               dict(tool_name="run_tests", success=False, output="parseQuantity ValueError " + "x" * 10000,
                    metadata={"test_failures": [{"test": "test_quantity_range", "error_type": "ValueError"}]})]
    query = build_retrieval_query("修复数量处理", results)
    assert "修复数量处理" in query and "test_quantity_range" in query and "parseQuantity" in query
    assert "obsoleteFailure" not in query and "irrelevantSource" not in query
    assert len(query) <= 6000


@pytest.mark.parametrize("strategy", ["baseline", "contextual"])
def test_single_runtime_supplies_task_context_only_for_contextual_strategy(tmp_path, strategy):
    store = ExperienceStore(tmp_path / "refs.db")
    relevant = seed(store)
    queries = []

    class ObservedStore:
        def retrieve_experiences(self, query="", **kwargs):
            queries.append(query)
            return [relevant]

    llm = SequenceLLM([ApplicationError(ErrorCode.LLM_ERROR, "Stop after observing request")])
    request = AgentRequest(task_id=str(uuid4()), workspace=tmp_path, task_description="修复 parse_quantity 数量格式",
                           repository_id="A", language="python")
    Agent(llm, lambda *_: None, ObservedStore(), retrieval_strategy=strategy).run(request)
    assert queries and all(("parse_quantity" in query) if strategy == "contextual" else query == "" for query in queries)
    assert relevant.id in json.dumps(llm.requests)
