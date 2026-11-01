"""Test-only model responses. All tools, coordination, storage and tests remain real."""
import copy
import json
from pathlib import Path
from threading import Lock
from uuid import uuid4


class ScriptedRoleFactory:
    def __init__(self, *, partial_first=False, reject_first=False, stale_first=False, fail_role=None, invalid_first=False, repository_cases=None):
        self.partial_first, self.reject_first, self.stale_first, self.fail_role = partial_first, reject_first, stale_first, fail_role
        self.invalid_first = invalid_first
        self.requests, self.clients, self.counts, self.lock = [], [], {}, Lock()
        root = Path(__file__).resolve().parents[1]
        self.fixes = json.loads((root / "backend/tests/fixtures/experience_v3_fixes.json").read_text(encoding="utf-8"))
        catalog = json.loads((root / "benchmarks/experience-v3/catalog.json").read_text(encoding="utf-8"))
        self.repository_cases = {project["repository_id"]: project["id"] for project in catalog}
        self.repository_cases.update(repository_cases or {})

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
                identifier = factory.repository_cases[facts["repository_id"]]
                target = identifier.endswith("booking-transfer")
                fixes = factory.fixes[identifier]
                if role == "single":
                    self.decisions += 1
                    if self.decisions == 1:
                        specs = [("run_tests", {}), *[("read_file", {"path": x["path"]}) for x in fixes]]
                    elif self.decisions == 2:
                        specs = [("write_file", {"path": x["path"], "content": x["content"]}) for x in fixes]
                    else:
                        return {"role": "assistant", "content": "代码修改已完成，等待程序独立运行最终测试。"}
                    return {"role": "assistant", "content": None, "tool_calls": [
                        {"id": f"script-{uuid4()}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
                        for name, args in specs]}
                if role == "diagnosis":
                    payload = {"issues": [dict(error_type="InputError", root_cause="文本、数量和列表选择均缺少输入条件检查",
                        solution="分别按当前项目的业务要求处理文本、数量和列表选择", files=[x["path"] for x in fixes])],
                        "dependencies": [], "summary": "发现三组输入校验问题，需要分别处理并检查组合调用。"}
                elif role == "repair":
                    changes = [dict(path=x["path"], before_sha256=facts["file_hashes"][x["path"]], content=x["content"])
                               for x in fixes if facts["files"][x["path"]] != x["content"]]
                    if factory.partial_first and count == 1: changes = changes[:1]
                    if factory.stale_first and count == 1: changes[0]["before_sha256"] = "0" * 64
                    if factory.invalid_first and count == 1: changes[0]["content"] = "this is not valid source !!!\n"
                    payload = dict(proposal_id=str(uuid4()), agent_id=facts["agent_id"], base_revision=facts["snapshot_revision"], changes=changes)
                elif role == "review":
                    rejected = factory.reject_first and count == 1
                    payload = dict(accepted=not rejected, reasons=["请重新核对当前项目的默认值和异常处理要求"] if rejected else ["修改符合当前项目原有的业务要求"])
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
        ("处理缺失和空白文本", "输入缺失与去除空白后为空是两种不同的边界情况",
         "先检查输入是否缺失，再去除首尾空白；随后检查结果是否为空，并按当前项目的名称规则处理",
         [("输入值缺失", "去除首尾空白前先检查输入是否存在"), ("去除首尾空白后文本为空", "处理空白后再次检查文本是否为空")]),
        ("校验整数格式和取值范围", "成功解析为整数并不代表该数值符合业务范围",
         "先处理输入缺失或格式错误，再检查整数范围；按当前项目要求返回默认值或抛出异常",
         [("数字文本缺失或格式错误", "按业务要求处理解析失败"), ("整数超出业务范围", "解析成功后继续检查上下界")]),
        ("同时校验列表和下标", "列表缺失和下标越界需要分别检查",
         "读取列表长度前先检查列表是否存在；取值前排除负下标和达到或超过长度的下标",
         [("列表缺失", "读取长度或元素前先检查列表"), ("下标不在有效范围内", "访问元素前确认 0 <= index < size")]),
    ]
    applicability = ["仅适用于已登记的" + ("预约" if target else "结算") + "项目输入规则。",
                     "文本默认值、数量范围和选择失败时的返回值，必须符合项目原有测试验证的业务要求。"]
    return {"experiences": [dict(task_summary=title, error_type=errors[i], root_cause=cause, solution=solution,
        applicability=applicability, tags=[language, "输入校验"], issues=[dict(error_type=errors[i], root_cause=why,
        solution=how, files=[fixes[i]["path"]]) for why, how in related])
        for i, (title, cause, solution, related) in enumerate(groups)]}
