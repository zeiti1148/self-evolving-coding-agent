import importlib.util
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from backend.shared import ApplicationError


def budget_type():
    assert importlib.util.find_spec("backend.agent.budget"), "shared runtime budget required"
    from backend.agent.budget import RunBudget
    return RunBudget


def test_two_workers_compete_for_last_decision_without_overshoot():
    budget = budget_type()(max_decisions=1, deadline=time.monotonic() + 10)
    def attempt(_):
        try:
            return budget.reserve_decision()
        except ApplicationError:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt, range(2)), key=lambda x: x or 0) == [None, 1]


def test_expired_budget_permits_no_new_decisions():
    budget = budget_type()(max_decisions=10, deadline=time.monotonic() - 1)
    with pytest.raises(ApplicationError, match="时限"):
        budget.reserve_decision()
    with pytest.raises(ApplicationError):
        budget.remaining_seconds()
