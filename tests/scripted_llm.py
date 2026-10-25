"""Deterministic test-only model. It is not an automatic code repair algorithm."""
import json

TASKS = [
    ("java-null-greeting", 'Fix the NullPointerException in GreetingService.greet: name=null must return "Hello, guest!". Preserve normal trimmed-name behavior and run Maven tests.'),
    ("java-null-profile", 'Fix the NullPointerException in ProfileFormatter.format: a null profile or null displayName must return "User: guest". Preserve normal trimmed-name behavior and run Maven tests.'),
]


class ScriptedLLM:
    def __init__(self):
        self.round = 0
        self.received_experience_ids = []

    def complete(self, messages, *, tools=None):
        if tools is None:
            return {"role": "assistant", "content": json.dumps({
                "task_summary": "Repair Java NullPointerException with a default value",
                "error_type": "NullPointerException", "root_cause": "Nullable values are dereferenced before checking.",
                "solution": "Check the receiver and nullable field before trimming; preserve non-null behavior.",
                "verification": "Use the unchanged Maven tests.", "tags": ["java", "null-safety"],
            })}
        task = json.loads(messages[1]["content"])
        self.received_experience_ids = [item["id"] for item in task["reference_experiences"]]
        profile = "ProfileFormatter" in task["task"]
        name = "ProfileFormatter" if profile else "GreetingService"
        path = f"src/main/java/dev/selfevolving/benchmark/{name}.java"
        self.round += 1
        def call(tool, args, suffix=""):
            return {"id": f"scripted_{self.round}{suffix}", "type": "function", "function": {"name": tool, "arguments": json.dumps(args)}}
        if self.round == 1:
            actions = [call("read_file", {"path": path}, "_read"), call("run_tests", {}, "_baseline")]
        elif self.round == 2:
            prior = [json.loads(m["content"]) for m in messages if m["role"] == "tool"]
            original = next(r["output"] for r in prior if r["tool_name"] == "read_file")
            if profile:
                fixed = original.replace('return "User: " + profile.displayName().trim();',
                    'return "User: " + (profile == null || profile.displayName() == null ? "guest" : profile.displayName().trim());')
            else:
                fixed = original.replace('name.trim()', '(name == null ? "guest" : name.trim())')
            actions = [call("write_file", {"path": path, "content": fixed})]
        elif self.round == 3:
            actions = [call("run_tests", {})]
        else:
            return {"role": "assistant", "content": "Added null handling and preserved normal behavior; the tool reported passing tests."}
        return {"role": "assistant", "content": None, "tool_calls": actions}
