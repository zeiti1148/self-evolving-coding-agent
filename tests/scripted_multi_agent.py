"""Test-only model responses. All tools, coordination, storage and tests remain real."""
import copy
import json
from pathlib import Path
from threading import Lock
from uuid import uuid4


class ScriptedRoleFactory:
    def __init__(self, *, partial_first=False, reject_first=False, stale_first=False, fail_role=None, invalid_first=False):
        self.partial_first, self.reject_first, self.stale_first, self.fail_role = partial_first, reject_first, stale_first, fail_role
        self.invalid_first = invalid_first
        self.requests, self.clients, self.counts, self.lock = [], [], {}, Lock()
        self.fixes = json.loads((Path(__file__).resolve().parents[1] / "backend/tests/fixtures/experience_v3_fixes.json").read_text(encoding="utf-8"))

    def __call__(self):
        factory = self
        class Client:
            def __init__(self):
                self.closed = False
                self.decisions = 0
            def set_deadline(self, deadline): self.deadline = deadline
            def close(self): self.closed = True
            def complete(self, messages, *, tools=None):
                facts = json.loads(messages[1]["content"])
                role = "single" if tools else facts.get("agent_role", "extractor")
                with factory.lock:
                    factory.requests.append(copy.deepcopy(messages))
                    factory.counts[role] = factory.counts.get(role, 0) + 1
                    count = factory.counts[role]
                if factory.fail_role == role:
                    raise RuntimeError("Scripted role failure")
                target = "booking" in facts["task"]
                identifier = f'{facts["language"]}-' + ("booking-transfer" if target else "checkout-complex")
                fixes = factory.fixes[identifier]
                if role == "single":
                    self.decisions += 1
                    if self.decisions == 1:
                        specs = [("run_tests", {}), *[("read_file", {"path": x["path"]}) for x in fixes]]
                    elif self.decisions == 2:
                        specs = [("write_file", {"path": x["path"], "content": x["content"]}) for x in fixes]
                    else:
                        return {"role": "assistant", "content": "Source changes are ready for independent final tests."}
                    return {"role": "assistant", "content": None, "tool_calls": [
                        {"id": f"script-{uuid4()}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
                        for name, args in specs]}
                if role == "diagnosis":
                    payload = {"issues": [dict(error_type="InputError", root_cause="Input groups lack precondition checks",
                        solution="Respect text, number and selection policies", files=[x["path"] for x in fixes])],
                        "dependencies": [], "summary": "Three related input-validation groups require separate checks."}
                elif role == "repair":
                    changes = [dict(path=x["path"], before_sha256=facts["file_hashes"][x["path"]], content=x["content"])
                               for x in fixes if facts["files"][x["path"]] != x["content"]]
                    if factory.partial_first and count == 1: changes = changes[:1]
                    if factory.stale_first and count == 1: changes[0]["before_sha256"] = "0" * 64
                    if factory.invalid_first and count == 1: changes[0]["content"] = "this is not valid source !!!\n"
                    payload = dict(proposal_id=str(uuid4()), agent_id=facts["agent_id"], base_revision=facts["snapshot_revision"], changes=changes)
                elif role == "review":
                    rejected = factory.reject_first and count == 1
                    payload = dict(accepted=not rejected, reasons=["Recheck target-specific fallback contracts"] if rejected else ["Changes preserve the original target contract"])
                else:
                    payload = knowledge_response(facts["language"], fixes, target)
                return {"role": "assistant", "content": json.dumps(payload)}
        client = Client()
        with self.lock: self.clients.append(client)
        return client


def knowledge_response(language, fixes, target=False):
    """Three distinct knowledge groups authored as one model response, never copied from stored records."""
    errors = ("NullPointerException", "NumberFormatException", "IndexOutOfBoundsException") if language == "java" else ("AttributeError", "ValueError", "IndexError")
    groups = [
        ("Normalize missing and blank text", "Missing receivers and blank normalized values are different failure cases",
         "Check missing input before trimming, then check the trimmed result and apply the tested name policy",
         [("Missing receiver", "Check missing input before normalization"), ("Blank normalized text", "Check emptiness after normalization")]),
        ("Validate integer syntax and range", "Successful parsing does not establish that a number meets its business range",
         "Handle missing/malformed text, then validate the parsed range using the target's rejection or fallback contract",
         [("Malformed or missing numeric text", "Handle the supported parse failures"), ("Parsed number outside business bounds", "Validate bounds after parsing")]),
        ("Validate collection and index together", "An absent collection and an out-of-range index need separate guards",
         "Check the collection before its size; reject negative and upper-bound indices before selection",
         [("Missing collection", "Guard the collection before size/access"), ("Index outside half-open bounds", "Require 0 <= index < size before access")]),
    ]
    applicability = ["Use only for the registered " + ("booking" if target else "checkout") + " input contract.",
                     "Text defaults, number bounds and selection fallbacks must preserve the original tested business policy."]
    return {"experiences": [dict(task_summary=title, error_type=errors[i], root_cause=cause, solution=solution,
        applicability=applicability, tags=[language, "input-validation"], issues=[dict(error_type=errors[i], root_cause=why,
        solution=how, files=[fixes[i]["path"]]) for why, how in related])
        for i, (title, cause, solution, related) in enumerate(groups)]}
