"""One deadline and one decision counter shared by all runtime roles."""
import math
import time
from threading import Lock

from backend.shared import ApplicationError, ErrorCode


class RunBudget:
    def __init__(self, max_decisions: int, deadline: float):
        if type(max_decisions) is not int or not 1 <= max_decisions <= 100 or not math.isfinite(deadline):
            raise ValueError("Invalid run budget")
        self.max_decisions, self.deadline = max_decisions, deadline
        self.decisions = 0
        self.lock = Lock()

    def remaining_seconds(self):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise ApplicationError(ErrorCode.AGENT_STOPPED, "任务时限已用完。")
        return remaining

    def reserve_decision(self):
        with self.lock:
            self.remaining_seconds()
            if self.decisions >= self.max_decisions:
                raise ApplicationError(ErrorCode.AGENT_STOPPED, "所有角色共用的决策次数已用完。")
            self.decisions += 1
            return self.decisions
