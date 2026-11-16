"""Read-only accounting of real upload experiments; never invokes a model or writes a database."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
from statistics import mean, median
import sys
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
TERMINAL = {"succeeded", "failed", "stopped", "cancelled", "canceled", "error", "timed_out"}
LABELS = {"before": "更新前", "after": "更新后", "contextual": "上下文检索", "adaptive": "自适应检索"}


def read(path, default=None):
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else default


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def events(items):
    for item in items:
        if not isinstance(item, dict):
            continue
        if isinstance(item.get("requests"), list):
            yield from events(item["requests"])
        else:
            yield item


def tokens(record):
    audit = record.get("audit") or {}
    if "model_requests" in audit:
        calls = list(events(audit["model_requests"]))
        values = [(call.get("usage") or {}).get("total_tokens") for call in calls]
        reported = [value for value in values if number(value)]
        return dict(total_tokens=sum(reported) if len(reported) == len(calls) else None,
                    reported_total_tokens=sum(reported), reported_requests=len(reported), total_requests=len(calls))
    value = (record.get("metrics") or {}).get("token_usage")
    if isinstance(value, dict):
        return {key: value.get(key) for key in ("total_tokens", "reported_total_tokens", "reported_requests", "total_requests")}
    return dict(total_tokens=None, reported_total_tokens=None, reported_requests=None, total_requests=None)


def identity(record):
    arm = str(record.get("arm", "unknown"))
    condition = record.get("condition") or record.get("strategy")
    if condition not in LABELS:
        condition = next((value for value in LABELS if arm.startswith(value) or value in arm.split("-")), "unknown")
    repetition = record.get("repetition")
    if repetition is None:
        match = re.search(r"(\d+)$", arm)
        repetition = int(match.group(1)) if match else 1
    return condition, repetition


def task_elapsed(record):
    metric = (record.get("metrics") or {}).get("elapsed_ms")
    if number(metric):
        return metric
    status = record.get("status") or {}
    if status.get("started_at") and status.get("finished_at"):
        try:
            return max(0, (datetime.fromisoformat(status["finished_at"]) - datetime.fromisoformat(status["started_at"])).total_seconds() * 1000)
        except (TypeError, ValueError):
            pass
    return None


def summarize_metric(rows, key):
    values = [row[key] for row in rows if number(row.get(key))]
    return dict(available=len(values), denominator=len(rows), mean=mean(values) if values else None,
                median=median(values) if values else None, minimum=min(values) if values else None,
                maximum=max(values) if values else None,
                total=sum(values) if rows and len(values) == len(rows) else None,
                reported_subtotal=sum(values) if values else None)


def summarize(rows):
    completed = [row for row in rows if row["terminal"]]
    successful = [row for row in completed if row["verified_success"]]
    verified_patches = [row for row in completed if row.get('final_tests_passed') is True]
    return dict(planned=len(rows), submitted=sum(bool(row.get("task_id")) for row in rows),
                invalid_comparisons=sum(row.get("comparison_valid") is False for row in rows),
                terminal=len(completed), unfinished=len(rows) - len(completed), passed=len(successful),
                failed=len(completed) - len(successful),
                success_rate=len(successful) / len(completed) if completed else None,
                final_test_passed=len(verified_patches),
                final_test_failed=sum(row.get('final_tests_passed') is False for row in completed),
                final_test_unconfirmed=sum(row.get('final_tests_passed') is None for row in completed),
                final_test_pass_rate=len(verified_patches)/len(completed) if completed else None,
                experience_saved=sum(row.get('experience_saved') is True for row in completed),
                elapsed_ms=summarize_metric(completed, "elapsed_ms"),
                task_tokens=summarize_metric(completed, "task_tokens"),
                background_tokens=summarize_metric(completed, "background_tokens"),
                background_elapsed_ms=summarize_metric(completed, "background_elapsed_ms"),
                reported_background_tokens=sum(row['reported_background_tokens'] for row in completed if number(row.get('reported_background_tokens')))
                    if any(number(row.get('reported_background_tokens')) for row in completed) else None,
                reported_task_tokens=sum(row["reported_task_tokens"] for row in completed if number(row["reported_task_tokens"])) if any(number(row["reported_task_tokens"]) for row in completed) else None,
                token_requests_reported=sum(row["token_requests_reported"] for row in completed if number(row["token_requests_reported"])),
                token_requests_observed=sum(row["token_requests_observed"] for row in completed if number(row["token_requests_observed"])),
                known_usage_tasks=sum(number(row["task_tokens"]) for row in completed),
                background_states=dict(Counter(row["background_state"] for row in completed)))


def rows_from(records, manifest):
    cases = {case["id"]: case for case in manifest.get("cases", [])}
    rows, seen = [], set()
    for record in records:
        key = record["key"]
        if key in seen:
            raise ValueError("Duplicate experimental record: " + key)
        seen.add(key)
        condition, repetition = identity(record)
        phase = record.get("phase", record.get("case_id", "unknown"))
        status = record.get("status") or {}
        state = status.get("status", "planned")
        result = status.get("result") or {}
        final = next((item for item in reversed(result.get("tool_results", []))
                      if item.get("metadata", {}).get("final_verification")), None)
        metadata = (final or {}).get("metadata", {})
        expected = cases.get(record.get("case_id"), {}).get("baseline", {}).get("tests")
        final_tests_passed = (bool(final.get("success") is True and number(expected) and metadata.get("tests") == expected
                                  and all(metadata.get(name) == 0 for name in ("failures", "errors", "skipped")))
                              if final else None)
        # Preserve the registered full-workflow success definition. A stopped
        # task can have a valid patch while never reaching experience extraction.
        passed = bool(state == "succeeded" and result.get("repair_succeeded") is True
                      and result.get("tests_passed") is True and final_tests_passed is True)
        metric = record.get("metrics") or {}
        background = metric.get("experience") or {}
        usage = tokens(record)
        bg_usage = background.get("token_usage") or {}
        project = record.get("project") or {}
        expected_digest = cases.get(record.get("case_id"), {}).get("source_digest")
        # An unsubmitted row has no uploaded source yet. A submitted mismatch is an invalid comparison.
        comparable = not record.get("task_id") or bool(expected_digest and project.get("source_digest") == expected_digest)
        audit = record.get("audit") or {}
        request = record.get("request") or {}
        contract = dict(task_description=request.get("task_description"), max_steps=request.get("max_steps"),
                        execution_mode=request.get("execution_mode"), runner_config=project.get("runner_config"))
        contract_hash = hashlib.sha256(json.dumps(contract,sort_keys=True,ensure_ascii=False).encode()).hexdigest() if request and project.get("runner_config") else None
        retrieval = metric.get("retrieval") or {}
        all_events = list(events(audit.get("model_requests", [])))
        injected = [event for event in all_events if event.get("success") and event.get("request_experience_ids")]
        reason_counts = Counter(event.get("reason", "not_recorded") for event in (audit.get("retrieval_events") or retrieval.get("events") or []))
        rows.append(dict(key=key, arm=record.get("arm"), condition=condition, repetition=repetition,
            phase=phase, cohort="retrieval_ablation" if condition in {"contextual", "adaptive"} else "reuse" if phase.endswith("-reuse") else "initial",
            case_id=record.get("case_id"), task_id=record.get("task_id"), task_url=metric.get("task_url"),
            status=state, terminal=state in TERMINAL, verified_success=passed and comparable, comparison_valid=comparable,
            final_tests_passed=final_tests_passed, registered_verified_success=record.get('verified_success'),
            experience_saved=result.get('experience_saved'), experience_ids=result.get('experience_ids') or ([result['experience_id']] if result.get('experience_id') else []),
            stop_reason=status.get('error_message') or result.get('error_message') or result.get('experience_save_error'),
            expected_tests=expected, final_tests=metadata.get("tests"), elapsed_ms=task_elapsed(record),
            final_failures=metadata.get('failures'),final_errors=metadata.get('errors'),final_exit_code=metadata.get('exit_code'),
            final_error_code=(final or {}).get('error_code'),
            final_diagnostic=('NameError: PLACEHOLDER' if 'NameError' in str((final or {}).get('output','')) and 'PLACEHOLDER' in str((final or {}).get('output','')) else None),
            task_tokens=usage["total_tokens"], reported_task_tokens=usage["reported_total_tokens"],
            token_requests_reported=usage["reported_requests"], token_requests_observed=usage["total_requests"],
            background_tokens=bg_usage.get("total_tokens"), background_elapsed_ms=background.get("elapsed_ms"),
            reported_background_tokens=bg_usage.get('reported_total_tokens'),
            background_metrics_correction={'initial':record['metrics_initial_snapshot'].get('experience'),'current':background}
                if record.get('metrics_initial_snapshot') else None,
            background_model_requests=background.get("model_requests"), background_state=background.get("state", "not_observed"),
            background_notice=background.get("notice"), background_observation_timeout=record.get("background_observation_timeout", False),
            original_records_before=background.get("before_count"), original_records_after=background.get("after_count"),
            generated_principles=background.get("merged_count"), discovered_conflicts=background.get("conflict_count"),
            resolved_conflicts=background.get("resolved_count"), pending_confirmations=background.get("pending_confirmation_count"),
            source_digest=project.get("source_digest"), frozen_sha256=record.get("frozen_sha256", record.get("retrieval_snapshot_sha256")),
            request_contract_sha256=contract_hash,
            strategy=audit.get("retrieval_strategy", retrieval.get("strategy")),
            injected_requests=len(injected) if "model_requests" in audit else retrieval.get("injected_requests"),
            retrieved_source_count=len({identifier for event in injected for identifier in event["request_experience_ids"]}) if "model_requests" in audit else retrieval.get("reference_count"),
            retrieval_reasons=dict(reason_counts), observed_complete=record.get("observed_complete", False)))
    return rows


def paired(rows, left="before", right="after"):
    grouped = defaultdict(dict)
    for row in rows:
        if row["condition"] in {left, right}:
            key = (row["case_id"], row["phase"], row["repetition"])
            if row["condition"] in grouped[key]:
                raise ValueError("Duplicate pairing condition: " + str(key))
            grouped[key][row["condition"]] = row
    details = []
    for key, group in grouped.items():
        a, b = group.get(left), group.get(right)
        if not a or not b or not a["terminal"] or not b["terminal"]:
            continue
        valid = bool(a["comparison_valid"] and b["comparison_valid"] and a["source_digest"] == b["source_digest"]
                     and a.get("request_contract_sha256") and a["request_contract_sha256"] == b.get("request_contract_sha256"))
        if left == "contextual":
            valid = bool(valid and a.get("frozen_sha256") and a["frozen_sha256"] == b.get("frozen_sha256"))
        details.append(dict(case_id=key[0], phase=key[1], repetition=key[2], left_key=a["key"], right_key=b["key"],
            valid=valid, left_success=a["verified_success"], right_success=b["verified_success"],
            left_final_tests_passed=a.get('final_tests_passed'), right_final_tests_passed=b.get('final_tests_passed'),
            both_successful=a["verified_success"] and b["verified_success"],
            both_final_tests_passed=a.get('final_tests_passed') is True and b.get('final_tests_passed') is True,
            elapsed_delta_ms=b["elapsed_ms"]-a["elapsed_ms"] if number(a["elapsed_ms"]) and number(b["elapsed_ms"]) else None,
            token_delta=b["task_tokens"]-a["task_tokens"] if number(a["task_tokens"]) and number(b["task_tokens"]) else None,
            left_elapsed_ms=a["elapsed_ms"], right_elapsed_ms=b["elapsed_ms"], left_tokens=a["task_tokens"], right_tokens=b["task_tokens"]))
    valid = [row for row in details if row["valid"]]
    def cost_summary(items):
        return {"pairs":len(items), **{field:{"complete_pairs":len(values), "mean_delta":mean(values) if values else None,
                    "median_delta":median(values) if values else None}
                for field in ("elapsed_delta_ms", "token_delta")
                for values in [[row[field] for row in items if isinstance(row[field],(int,float)) and not isinstance(row[field],bool)]]}}
    return dict(left=left,right=right,completed_pairs=len(details),valid_pairs=len(valid),
        wins=sum(not row["left_success"] and row["right_success"] for row in valid),
        losses=sum(row["left_success"] and not row["right_success"] for row in valid),
        all_terminal=cost_summary(valid), both_successful=cost_summary([row for row in valid if row["both_successful"]]),
        both_final_tests_passed=cost_summary([row for row in valid if row['both_final_tests_passed']]),details=details)


def snapshot_counts(snapshot):
    if snapshot is None:
        return None
    relations = snapshot.get("knowledge_relations", [])
    return dict(original_records=len(snapshot.get("experiences", [])), rules=len(snapshot.get("knowledge_rules", [])),
                shared_principles=len(snapshot.get("knowledge_principles", [])),
                relation_kinds=dict(Counter(row.get("kind", "unknown") for row in relations)),
                resolved_relations=sum(row.get("status") == "resolved" for row in relations))


def observations(directory, rows):
    output = []
    for row in rows:
        before = read(directory / (row["key"]+"-before.json"))
        after = read(directory / (row["key"]+"-after.json"))
        if before is None and after is None:
            continue
        decisions = []
        for item in (after or {}).get("knowledge_auto_reviews", []):
            data = json.loads(item["data"]) if isinstance(item.get("data"),str) else item.get("data",{})
            decisions.append(dict(review_id=item.get("review_id"),state=data.get("state",item.get("state")),
                actor=data.get("actor"),resolution=data.get("resolution"),reason=data.get("reason"),
                runs=[{name:run.get(name) for name in ("implementation_source_id","target_source_id","outcome","tests","failures","errors","skipped","failed_tests","repeat","reproduces_baseline","artifact_sha256")}
                      for run in data.get("runs",[])]))
        output.append(dict(key=row["key"], before=snapshot_counts(before), after=snapshot_counts(after),decisions=decisions,
                           complete=after is not None, background_state=row["background_state"]))
    return output


def build_report(records, protocol, manifest, observation_directory=None):
    rows = rows_from(records, manifest)
    conditions = list(dict.fromkeys(row["condition"] for row in rows))
    primary_plan_size=protocol.get('primary_tasks',sum(row['condition'] in {'before','after'} for row in rows))
    ablation_plan_size=protocol.get('retrieval_ablation_tasks',4 if protocol.get('retrieval_ablation') else sum(row['cohort']=='retrieval_ablation' for row in rows))
    planned_tasks=primary_plan_size+ablation_plan_size
    cohort_summaries = {cohort:{condition:summarize([row for row in rows if row["condition"]==condition and row["cohort"]==cohort])
                        for condition in conditions if any(row["condition"]==condition and row["cohort"]==cohort for row in rows)}
                       for cohort in ("initial","reuse","retrieval_ablation")}
    return dict(schema_version=1,generated_at=datetime.now(timezone.utc).isoformat(),
                status="complete" if rows and len(rows)>=planned_tasks and all(row["terminal"] and row["observed_complete"] for row in rows) else "in_progress",
                planned_tasks=planned_tasks, primary_plan_size=primary_plan_size, ablation_plan_size=ablation_plan_size,
                missing_planned_records=max(0,planned_tasks-len(rows)),
                protocol=protocol,independent_defect_cases=len({case["id"] for case in manifest.get("cases",[])}),
                cases=[{**{name:case.get(name) for name in ("id","label","repo","base_commit","archive_sha256","source_digest","tests_digest","baseline","test_scope","exclusions_reason")},
                        "issue_url":(case.get('issue') or {}).get('html_url'), "upstream_pr_url":(case.get('upstream_pr') or {}).get('html_url')}
                       for case in manifest.get("cases",[])],
                rows=rows,summary={condition:summarize([row for row in rows if row["condition"]==condition]) for condition in conditions},
                cohorts=cohort_summaries,paired_update=paired(rows),paired_retrieval=paired(rows,"contextual","adaptive"),
                paired_update_by_cohort={cohort:paired([row for row in rows if row['cohort']==cohort]) for cohort in ('initial','reuse')},
                observations=observations(observation_directory,rows) if observation_directory else [],
                limitations=["Only three independent defect cases; two repeated sequences and repeat repairs do not add independent projects.",
                    "Whole-update effects include prompts/tools/extraction; contextual/adaptive attribution requires the same frozen references.",
                    "Pending/running/planned tasks are unfinished, not failures or successes.",
                    "Missing token usage remains unknown; reported subtotals are lower bounds, not zero-filled totals.",
                    "Task elapsed includes repair and extraction; background elapsed includes queueing, comparison and evidence tests.",
                    "Original records are retained; shared-principle counts are not deleted-source counts."])


def captured_history(directory):
    """Keep independently recorded UI/API states tied to their own repetition."""
    history=[]
    for filename,arm,stage in [('after2-merge-before-conflict.json','after2','before_conflict'),
            ('after2-principle-after-conflict.json','after2','after_conflict'),
            ('after1-conflict-resolved.json','after1','automatic_conflict'),
            ('after2-conflict-confirmation.json','after2','insufficient_evidence')]:
        document=read(directory/filename)
        if document is None:continue
        history.append(dict(filename=filename,sha256=hashlib.sha256((directory/filename).read_bytes()).hexdigest(),
            arm=arm,stage=stage,changes=[dict(id=change.get('id'),kind=change.get('kind'),state=change.get('state'),
                reason=change.get('reason'),source_ids=[source.get('id') for source in change.get('before',[])],
                title=(change.get('after') or change.get('proposal') or {}).get('title'))
                for item in document.get('items',[]) for change in item.get('changes',[])]))
    return history


def captured_screenshots(source,destination):
    outputs=[]
    for filename,caption in [('after2-merge-before-conflict.png','第 2 轮：pytest 与 Sphinx 独立经验自动归纳共同原则；冲突发生前'),
        ('after2-merge-after-conflict.png','同一第 2 轮：后续冲突使上述共同原则 invalidated，暂停复用'),
        ('after1-conflict-matrix.png','第 1 轮：双方各自通过，四次交叉测试复现原始失败，系统自动隔离'),
        ('after2-conflict-confirmation-reason.png','第 2 轮：函数接口无法可靠移植，交叉测试未运行，交用户确认')]:
        path=source/filename
        if path.is_file():
            destination.mkdir(parents=True,exist_ok=True);target=destination/filename;shutil.copy2(path,target)
            outputs.append(dict(path=str(target),caption=caption,sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    return outputs


def supplemental_safety(directory,manifest,*,verify_workspaces=False):
    """A separately seeded follow-up is never an additional primary observation."""
    records=read(directory/'safety-records.json',[])
    rows=rows_from(records,manifest)
    for row in rows:
        row['condition']='safety_patch';row['cohort']='supplemental_safety'
        row['involved_review_relations']=row.pop('discovered_conflicts')
        row['relation_count_scope']='Includes historical unknown/review relationships involving this source; not newly discovered conflicts.'
    complete=bool(rows and all(row['terminal'] and row['observed_complete'] for row in rows))
    provenance=[dict(key=record['key'],seed_backup_sha256=record.get('seed_backup_sha256'),core=record.get('core'),
        case_source_digest=record.get('case_source_digest'),case_archive_sha256=record.get('case_archive_sha256'),
        limitation=record.get('limitation')) for record in records]
    return dict(state='已完成，独立补充记录' if complete else '运行中，独立补充记录' if any(row.get('task_id') for row in rows) else '尚未执行',
        excluded_from_primary=True,registered_tasks=1,submitted=sum(bool(row.get('task_id')) for row in rows),
        observed_complete=sum(bool(row.get('observed_complete')) for row in rows),rows=rows,summary=summarize(rows),
        provenance=provenance,snapshot_origin=read(directory/'runs'/'safety1'/'snapshot-origin.json'),
        request_safety_audit=read(directory/'safety-verification-audit.json'),
        test_integrity=verify_test_integrity(directory,manifest,rows,ready=complete and verify_workspaces))


def main_comparison_link(directory,rows):
    path=directory/'main-comparison-url.txt'
    if not path.is_file():return None
    url=path.read_text(encoding='utf-8-sig').strip()
    parts=urlsplit(url);route,_,query=parts.fragment.partition('?')
    selected=parse_qs(query).get('compare',[''])[0].split(',')
    expected={row['task_id'] for row in rows if row['cohort']=='initial' and row['condition'] in {'before','after'}}
    container=route.rstrip('/').split('/')[-1]
    if parts.scheme!='http' or parts.netloc!='127.0.0.1:5173' or len(selected)!=12 or set(selected)!=expected or container in selected:
        raise ValueError('Normal comparison URL must select exactly the original 12 initial tasks, excluding its container task.')
    return dict(url=url,selected_task_ids=selected,container_task_id=container,container_counted=False)


def refresh_supplement(report,directory,manifest,*,verify_workspaces=False):
    if report.get('status')!='complete' or len(report.get('rows',[]))!=20:
        raise ValueError('Supplement-only update requires the sealed original 20-task report.')
    # No rebuilding primary rows, pairing, charts, provenance or original hashes.
    report['supplemental_safety']=supplemental_safety(directory,manifest,verify_workspaces=verify_workspaces)
    report['main_comparison']=main_comparison_link(directory,report['rows'])
    report['supplemental_updated_at']=datetime.now(timezone.utc).isoformat()
    return report


def verify_test_integrity(directory, manifest, rows, *, ready=False):
    """Final, read-only byte checks with exactly the package manifest's inventory rules.

    This is deliberately gated before any source/workspace read so in-progress
    model/test timings cannot be disturbed by a concurrent filesystem scan.
    """
    if not ready or any(not row['terminal'] or not row['observed_complete'] for row in rows):
        return dict(state='deferred', reason='全部计划运行与后台观测结束后才读取工作区；当前未扫描。', checked=0, unchanged=0, records=[])
    if str(ROOT) not in sys.path:
        sys.path.insert(0,str(ROOT))
    from backend.tools.workspace import inventory, ignored_path, reject_links
    cases={case['id']:case for case in manifest.get('cases',[])}
    reference_tests={}
    reference_fixtures={}
    checks=[]
    run_root=(directory/'runs').resolve()
    digest=lambda values:hashlib.sha256(json.dumps(values,sort_keys=True).encode()).hexdigest()

    def fixture_inventory(root):
        # inventory's directory pruning uses a bare directory name. Its manifest
        # can omit a test fixture called `target`; also check all fixture bytes
        # with the same exclusions evaluated against the full relative path.
        values={}
        for name in ('tests','testing'):
            fixture_root=root/name
            reject_links(fixture_root)
            for current,dirs,files in os.walk(fixture_root,followlinks=False):
                parent=Path(current)
                for child in dirs+files:
                    reject_links(parent/child)
                dirs[:]=[child for child in dirs if not ignored_path((parent/child).relative_to(root))]
                for child in files:
                    path=parent/child
                    relative=path.relative_to(root)
                    if not ignored_path(relative):
                        values[relative.as_posix()]=hashlib.sha256(path.read_bytes()).hexdigest()
        return values

    for row in rows:
        if not row.get('task_id'):
            continue
        case=cases[row['case_id']]
        if row['case_id'] not in reference_tests:
            reference_tests[row['case_id']]={path:value for path,value in inventory(Path(case['source'])).items()
                                            if path.startswith(('tests/','testing/'))}
            reference_fixtures[row['case_id']]=fixture_inventory(Path(case['source']))
        expected=reference_tests[row['case_id']]
        expected_fixtures=reference_fixtures[row['case_id']]
        source_valid=digest(expected)==case.get('tests_digest') and len(expected)==case.get('test_files')
        workspace=(run_root/row['arm']/'workspaces'/row['task_id']).resolve()
        if not workspace.is_relative_to(run_root):
            raise ValueError('Experimental workspace is outside the registered run root.')
        if not workspace.is_dir():
            checks.append(dict(key=row['key'],task_id=row['task_id'],state='missing_workspace',unchanged=None,
                               source_manifest_matches=source_valid,reason='任务没有可核对的工作副本。'))
            continue
        actual={path:value for path,value in inventory(workspace).items() if path.startswith(('tests/','testing/'))}
        actual_fixtures=fixture_inventory(workspace)
        actual_digest=digest(actual)
        changed=sorted(path for path in expected.keys() & actual.keys() if expected[path]!=actual[path])
        added=sorted(actual.keys()-expected.keys());removed=sorted(expected.keys()-actual.keys())
        fixture_changed=sorted(path for path in expected_fixtures.keys() & actual_fixtures.keys() if expected_fixtures[path]!=actual_fixtures[path])
        fixture_added=sorted(actual_fixtures.keys()-expected_fixtures.keys())
        fixture_removed=sorted(expected_fixtures.keys()-actual_fixtures.keys())
        unchanged=bool(source_valid and actual_digest==case.get('tests_digest') and len(actual)==case.get('test_files')
                       and expected_fixtures==actual_fixtures)
        checks.append(dict(key=row['key'],task_id=row['task_id'],state='verified' if unchanged else 'mismatch',unchanged=unchanged,
                           source_manifest_matches=source_valid,expected_digest=case.get('tests_digest'),actual_digest=actual_digest,
                           expected_test_files=case.get('test_files'),actual_test_files=len(actual),changed=changed,added=added,removed=removed,
                           full_fixture_expected_files=len(expected_fixtures),full_fixture_actual_files=len(actual_fixtures),
                           full_fixture_expected_digest=digest(expected_fixtures),full_fixture_actual_digest=digest(actual_fixtures),
                           full_fixture_changed=fixture_changed,full_fixture_added=fixture_added,full_fixture_removed=fixture_removed))
    return dict(state='verified' if checks and all(item['unchanged'] is True for item in checks) else 'incomplete_or_mismatch',
                checked=len(checks),unchanged=sum(item['unchanged'] is True for item in checks),records=checks,
                method='匹配上传manifest的inventory摘要；另以完整相对路径遍历tests/与testing/，补核target等fixture子目录，忽略缓存。')


def fmt(value, digits=1):
    return "—" if value is None else f"{value:,.{digits}f}" if isinstance(value,float) else f"{value:,}" if isinstance(value,int) else str(value)


def write_markdown(report, output, images):
    lines=["# 真实工程更新前后对比实验", "", f"状态：**{'已完成' if report['status']=='complete' else '进行中，尚不可下最终结论'}**。统计生成时间：{report['generated_at']}。", "",
        "通过正常 ZIP 上传、运行环境配置和修复任务 API 执行真实模型调用。没有插入模拟经验或预设冲突；失败、停止和未完成任务全部保留。", "",
        "## 对照范围", "", f"更新前 `{report['protocol'].get('before_revision','未知')[:7]}`，更新后 `{report['protocol'].get('after_revision','未知')[:7]}`。",
        "仅 **3 个独立工程缺陷**。每版本两条独立经验累积序列，后续再次修复同一缺陷属于复用试验，不能当成新工程样本。", "",
        f"计划为主实验 {report['primary_plan_size']} 次，加同冻结库检索消融 {report['ablation_plan_size']} 次，共 {report['planned_tasks']} 次；当前已有 {len(report['rows'])} 条计划/执行记录，尚未登记的计划不计成成功或失败。", "",
        "原版本没有 ZIP 导入，使用共同的上传、物理 ToolService、运行环境及观测适配层；旧版核心仍向模型提供 5 个工具，新版核心提供 7 个工具。旧版修复、检索和经验处理逻辑保留。当前版通过普通流程自动处理经验。两组使用同一来源归档、公开回归测试、模型与决策预算。工具菜单、提示词和提取变化属于整体更新效应，不能单独归因于检索。", "",
        "每条序列的第一次 pytest 修复从空经验库开始，两版本均没有历史参考经验；这轮出现的 token 差异不能解释为自适应检索节省。后续 Sphinx 和 pytest 回归任务按序累积真实经验。第四阶段再次修复 pytest 位于冲突处理阶段之后，单列为冲突后复修，不与前三个首次任务混为独立样本。", "",
        "| 真实缺陷 | 原始测试 | 原始失败 | 测试范围 |", "|---|---:|---:|---|"]
    for case in report["cases"]:
        baseline=case.get("baseline") or {}
        name=f"[{case['id']}]({case['issue_url']})" if case.get('issue_url') else case['id']
        lines.append(f"| {name} | {fmt(baseline.get('tests'))} | {fmt(baseline.get('failures'))} | 完整登记上游模块；非全仓库测试 |")
    a=report['cohorts']['initial'].get('before');b=report['cohorts']['initial'].get('after')
    if a and b and a['terminal'] and b['terminal']:
        findings=['## 实际结果概览','',
            f"前三个真实缺陷、每版两轮：最终测试验收 **{a['final_test_passed']}/{a['terminal']} → {b['final_test_passed']}/{b['terminal']}**，完整流程成功 **{a['passed']}/{a['terminal']} → {b['passed']}/{b['terminal']}**，已保存经验 **{a['experience_saved']} → {b['experience_saved']}**。",
            f"任务耗时均值 **{fmt(a['elapsed_ms']['mean']/1000 if a['elapsed_ms']['mean'] is not None else None)}s → {fmt(b['elapsed_ms']['mean']/1000 if b['elapsed_ms']['mean'] is not None else None)}s**；任务 token 总计 **{fmt(a['task_tokens']['total'])} → {fmt(b['task_tokens']['total'])}**。包含全部终止任务，后台用量另列。",
            '整体更新成本变化不能直接归因于自适应检索；下文同时列出双方验收通过的配对，以排除提前失败造成的低成本。','']
        retrieval=report['paired_retrieval']['both_final_tests_passed']
        if retrieval['pairs']:
            delta=retrieval['elapsed_delta_ms']['mean_delta'];token_delta=retrieval['token_delta']['mean_delta']
            findings += [f"同冻结库检索消融有 **{retrieval['pairs']} 对双方验收通过**的运行：自适应相对上下文检索平均耗时差 **{fmt(delta/1000 if delta is not None else None)}s**、token 差 **{fmt(token_delta)}**。正值表示自适应更慢、更贵；本次实验没有证明自适应节省成本。",'']
        lines=lines[:6]+findings+lines[6:]
    if report.get('main_comparison'):
        lines[6:6]=[f"[在正常项目任务页查看 12 次首次修复对比]({report['main_comparison']['url']})。入口为主应用 `5173` 的普通任务页；承载页面的已有任务仅作导航容器，不计入选中的 12 次实验或统计分母。",'']
    lines += ["", "测试集合中的既有无关预期失败和可选 Cython 用例按清单明确排除，两组相同。未向模型提供上游生产修复补丁。", "", "## 修复结果", "",
        "分开统计两种结果：**最终测试验收通过**要求程序独立测试数量匹配且无失败、异常、跳过，与任务是否耗尽决策预算无关；**完整流程成功**保持预注册条件，要求任务 succeeded 且修复/测试标记通过。任务 stopped 但最终测试全通过，记作补丁验收通过、流程未完成。经验是否保存另列。两个通过率的分母均为已终止任务；缺少最终证据与未完成任务不会称为测试失败。", "",
        "| 阶段 / 版本 | 计划 / 提交 / 终止 | 最终验收通过 / 终止 | 验收通过率 | 完整流程成功 / 终止 | 流程成功率 | 未完成 | 任务耗时均值 / 中位数(s) | 平均 token / 用量完整任务 |", "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    phase_names={"initial":"首次三案例","reuse":"冲突后复修","retrieval_ablation":"同冻结库检索消融"}
    for cohort,conditions in report["cohorts"].items():
        for condition,summary in conditions.items():
            duration=summary['elapsed_ms'];usage=summary['task_tokens'];rate=summary['success_rate']
            test_rate=summary['final_test_pass_rate']
            lines.append(f"| {phase_names[cohort]} / {LABELS.get(condition,condition)} | {summary['planned']} / {summary['submitted']} / {summary['terminal']} | {summary['final_test_passed']} / {summary['terminal']} | {'—' if test_rate is None else f'{test_rate:.1%}'} | {summary['passed']} / {summary['terminal']} | {'—' if rate is None else f'{rate:.1%}'} | {summary['unfinished']} | {fmt(duration['mean']/1000 if duration['mean'] is not None else None)} / {fmt(duration['median']/1000 if duration['median'] is not None else None)} | {fmt(usage['mean'])} / {usage['available']} of {usage['denominator']} |")
    lines += ["", "均值和中位数包含失败/停止的终止任务；token 平均值仅基于用量完整任务，覆盖不足时不能代表整组真实成本。任务耗时含经验提取，不含后续后台比较。", "", "## 同任务配对", "",
        "差值为后者减前者。更新对照按缺陷、阶段、重复序号配对；检索消融还要求冻结经验库指纹完全相同。三种成本口径并列：全部已终止任务、双方最终测试验收通过、双方完整流程成功。验收通过子集包含预算耗尽但补丁有效的任务，排除一方最终未修好的配对；新增口径不改变预注册完整流程条件。", "",
        "| 对照 / 计入条件 | 有效配对 | 耗时均值差(s) / 完整配对 | token均值差 / 完整配对 |", "|---|---:|---:|---:|"]
    pair_sections=[('主实验全部',report['paired_update']),('首次三案例',report['paired_update_by_cohort']['initial']),
                   ('冲突后复修',report['paired_update_by_cohort']['reuse']),('同冻结库检索消融',report['paired_retrieval'])]
    for section,pair in pair_sections:
        for scope,label in (("all_terminal","全部已终止"),("both_final_tests_passed","双方最终验收通过"),("both_successful","双方完整流程成功")):
            stats=pair[scope];elapsed=stats['elapsed_delta_ms'];token=stats['token_delta'];delta=elapsed['mean_delta']
            lines.append(f"| {section}：{LABELS.get(pair['left'],pair['left'])} → {LABELS.get(pair['right'],pair['right'])} / {label} | {stats['pairs']} | {fmt(delta/1000 if delta is not None else None)} / {elapsed['complete_pairs']} | {fmt(token['mean_delta'])} / {token['complete_pairs']} |")
    provenance=report.get('model_provenance_audit')
    if provenance:
        evidence=provenance['configuration_evidence']
        lines += ['', '## 模型来源与已经发生的用量', '',
            f"本次唯一配置文件：`{provenance['configuration_source']}`；服务源站 `{provenance['expected_origin']}`，请求模型 `{provenance['expected_model']}`。已封存 {provenance['request_count']} 个实际响应，{provenance['usage_reported_requests']} 个均上报 usage；配置不符数量 {len(provenance['mismatches'])}。",
            f"证据层级区分：{evidence['request_configuration']} 个请求有逐请求 api_configuration；{evidence['legacy_task_configuration']} 个旧版请求仅有旧 worker 的任务级项目配置、未改动内核，以及逐请求 requested/returned model 与 direct transport。旧版缺少逐请求配置字段，不补造该字段，也不因此误判 API 不符。",
            f"当前已经发生并上报的 token 为 **{fmt(provenance['reported_total_tokens'])}**，包含任务 {fmt(sum(row['reported_task_tokens'] for row in report['rows'] if number(row['reported_task_tokens'])))} 和 {provenance['background_requests']} 次后台调用。等待用户确认后可能发生的未来用量尚不存在，不计入这个封存小计；等待状态的完整生命周期成本仍保持未知。",'']
    lines += ["", "## 后台经验处理用量", "", "后台归纳、独立复核和冲突发现的模型用量单列。交叉运行真实测试的时间不等于额外模型 token。未记录不填成零。token 为服务返回的用量，不等于货币账单；缓存命中等计费因素未据此估算。", "",
        "| 版本 | 后台 token 总计 | 已上报 token 小计（下界） | 用量完整任务 / 终止任务 | 后台耗时均值(s) |", "|---|---:|---:|---:|---:|"]
    for condition,summary in report['summary'].items():
        token=summary['background_tokens'];duration=summary['background_elapsed_ms']['mean']
        lines.append(f"| {LABELS.get(condition,condition)} | {fmt(token['total'])} | {fmt(summary['reported_background_tokens'])} | {token['available']} / {token['denominator']} | {fmt(duration/1000 if duration is not None else None)} |")
    corrected=[row['key'] for row in report['rows'] if row.get('background_metrics_correction')]
    if corrected:
        lines += ['',f"指标修正保留原快照：{', '.join(corrected)}。任务结果与数据库共同确认未生成经验、未启动后台工作，因此后台为实际不适用的 0；不是把未知用量补零。原 metrics_initial_snapshot 与当前读数均保留。"]
    lines += ['', '## 自适应检索的独立对照', '',
              '各对使用同一真实首次修复生成的冻结经验库，库指纹一致；全部任务仍走普通上传/修复 API。每个条件只有两次运行。', '',
              '| 条件 / 轮次 | 最终验收 | 耗时(s) | 任务 token | 实际携带经验的模型请求 | 撤下经验事件 tests_passed |',
              '|---|---|---:|---:|---:|---:|']
    for row in report['rows']:
        if row['cohort']=='retrieval_ablation':
            lines.append(f"| {LABELS[row['condition']]} / {row['repetition']} | {'通过' if row['final_tests_passed'] else '未确认或未通过'} | {fmt(row['elapsed_ms']/1000 if row['elapsed_ms'] is not None else None)} | {fmt(row['task_tokens'])} | {fmt(row['injected_requests'])} | {row['retrieval_reasons'].get('tests_passed',0)} |")
    lines += ['', '自适应确实会按测试阶段撤下经验；请求总数与修复轨迹也随模型决策变化，撤下机制生效不等于总 token 必然下降。本次两个配对均观察到自适应更慢、更贵，不能宣传为性能提升。'] if report['paired_retrieval']['valid_pairs'] else []
    lines += ["", "## 经验处理前后", "", "下表来自实际任务前后快照。原始经验仍保留，共同原则增加不意味着删除来源。规则数是存储的规则记录数，不等于全部已获准复用。", "",
        "| 运行 | 原始记录 | 规则记录 | 共同原则 | 冲突关系 | 后台状态 |", "|---|---:|---:|---:|---:|---|"]
    for item in report['observations']:
        a,b=item['before'],item['after']
        def transition(key):return f"{fmt(a.get(key) if a else None)} → {fmt(b.get(key) if b else None)}"
        ca=a['relation_kinds'].get('conflict',0) if a is not None else None
        cb=b['relation_kinds'].get('conflict',0) if b is not None else None
        lines.append(f"| {item['key']} | {transition('original_records')} | {transition('rules')} | {transition('shared_principles')} | {fmt(ca)} → {fmt(cb)} | {item['background_state']} |")
    decisions={}
    for item in report['observations']:
        for decision in item['decisions']:
            decisions[(item['key'].split('-')[0],decision['review_id'])]=decision
    for (_,review_id),decision in decisions.items():
        lines += ["", f"- 冲突 `{review_id}`：执行者 `{decision['actor']}`，状态 `{decision['state']}`，处理 `{decision['resolution']}`。{decision['reason']}"]
        if decision['runs']:
            lines += ["", "  | 实现来源 → 测试来源 | 轮次 | 结果 | 失败 / 测试数 | 原始失败重现 |", "  |---|---:|---|---:|---|"]
            for run in decision['runs']:
                lines.append(f"  | `{str(run['implementation_source_id'])[:8]}` → `{str(run['target_source_id'])[:8]}` | {fmt(run['repeat'])} | {run['outcome']} | {fmt(run['failures'])} / {fmt(run['tests'])} | {run['reproduces_baseline']} |")
    if report.get('captured_history'):
        lines += ['', '## 同轮次经验生命周期', '',
            '第 2 轮的 pytest 标记继承和 Sphinx 实例属性文档来自两个独立修复任务。系统将“继承链元数据需要逐类读取自身定义再按业务规则合并”归纳为共同原则，保留原始两条经验及各自参数。随后 pytest 版本冲突使该原则失效并暂停复用。第 1 轮自动隔离矩阵属于另一条独立序列，不能拿来充当第 2 轮已完成处理的证据。', '',
            '| 序列 / 阶段 | 记录 | 前后状态 | 原因 |','|---|---|---|---|']
        for item in report['captured_history']:
            for change in item['changes']:
                lines.append(f"| {item['arm']} / {item['stage']} | {change['id']} | {change['state']} | {change['reason']} |")
        lines += ['', '第 1 轮：原上下文分别 275/275、277/277 通过；两向交叉各重复两次，分别重现 1/275 与 2/277 失败，系统自动 keep_separate。第 2 轮：函数接口或装饰器差异使可靠移植不成立，交叉执行次数为 0，保留双方并等待用户确认；没有人工替系统挑选胜者。']
    lines += ["", "## 逐任务结果", "", "| 运行 | 任务状态 | 最终测试验收 | 完整流程 / 经验保存 | 耗时(s) | 任务token | 注入请求 | 后台token |", "|---|---|---|---|---:|---:|---:|---:|"]
    for row in report['rows']:
        name=f"[{row['key']}]({row['task_url']})" if row['task_url'] else row['key']
        verdict='通过' if row['final_tests_passed'] is True else '测试无法启动' if row.get('final_tests')==0 and row.get('final_error_code')=='COMMAND_FAILED' else '未通过' if row['final_tests_passed'] is False else '缺少最终证据' if row['terminal'] else '未完成'
        saved='已保存' if row['experience_saved'] is True else '未保存' if row['terminal'] and not row['experience_ids'] else '未确认'
        lines.append(f"| {name} | {row['status']} | {verdict} | {'成功' if row['verified_success'] else '未完成'} / {saved} | {fmt(row['elapsed_ms']/1000 if row['elapsed_ms'] is not None else None)} | {fmt(row['task_tokens'])} | {fmt(row['injected_requests'])} | {fmt(row['background_tokens'])} |")
    for row in report['rows']:
        if row['terminal'] and not row['verified_success']:
            lines += ['',f"- `{row['key']}`：{row['stop_reason'] or '未满足完整流程成功条件。'}"+
                      ('独立最终测试实际通过，不能称为测试失败。' if row['final_tests_passed'] is True else '')+
                      ('没有生成经验记录。' if not row['experience_ids'] else '')]
            if row.get('final_diagnostic'):
                lines += [f"  模型生成的生产补丁含 `PLACEHOLDER`，最终测试启动即 `NameError`，测试数为 0、退出码为 1。不是 277 项测试全部失败；这是生成了无效补丁。"]
    integrity=report.get('test_integrity') or {'state':'deferred','checked':0,'unchanged':0,'records':[]}
    lines += ['', '## 测试与 fixture 完整性', '',
              f"最终只读字节核查状态：`{integrity['state']}`；核对 {integrity['checked']} 个任务工作副本，{integrity['unchanged']} 个与上传清单的测试摘要及文件数完全一致。",
              '先按原始 manifest 的 inventory 过滤匹配摘要及文件数，再独立遍历 tests/、testing/ 下测试和 fixture，补核原摘要目录剪枝可能漏掉的 target 等测试资源。缓存按原规则排除。运行中的任务不扫描；最终阶段才读取工作区，避免影响正式计时。']
    for item in integrity['records']:
        if item['unchanged'] is not True:
            lines += ['', f"- `{item['key']}`：{item['state']}；变更 {item.get('full_fixture_changed',item.get('changed',[]))}，新增 {item.get('full_fixture_added',item.get('added',[]))}，缺失 {item.get('full_fixture_removed',item.get('removed',[]))}。"]
    if images:
        lines += ["", "## 实际观测图", ""]
        for path in images:
            lines.append(f"![{path.stem}]({path.relative_to(output.parent).as_posix()})")
    if report.get('screenshots'):
        lines += ['', '## 普通经验记录页的真实前后态', '']
        for screenshot in report['screenshots']:
            path=Path(screenshot['path'])
            lines += [screenshot['caption'],'',f"![{screenshot['caption']}]({path.relative_to(output.parent).as_posix()})",'']
    lines += ['', '## 发现的不足与补充验证', '',
        '本次冻结版本存在规则过滤后的共享约束泄漏：暂停的 MRO 规则 ID 未直接注入，但同一来源的可用列表规则仍携带完整 source.constraints，把已暂停的 [C,A,B] 顺序带回模型上下文。历史请求审计发现 5 次携带，单项与列表双哈希核对当时实际渲染的参考内容。因此“规则 ID 已隔离”不能等同于“冲突语义已彻底隔离”。该证据证明引用进入真实请求，不能证明模型内部采纳或对修复结果的因果影响。',
        '20 次原实验完整保留，期间没有更换核心修复上述问题。补丁和第 21 次真实安全回归单列为后续补充；当前补充状态：'+report.get('supplemental_safety',{}).get('state','尚未执行')+'。补充未运行不会记成失败或成功，也不会混入原 20 次统计。',
        '补充回归从当时真实 after1 数据库备份启动，不手改经验或暂停记录。该库后来新增 unknown 关系已阻断先前来源，因此补充运行仅能证明当前流程是否注入错误约束及修复是否通过，不能冒充重现旧前态或证明受控的补丁效果。','']
    if report.get('shared_constraint_leak_audit'):
        lines += ['已保存独立约束泄漏审计；详见统计 JSON 的 shared_constraint_leak_audit，保留历史任务/请求与双哈希证据。','']
    supplement=report.get('supplemental_safety') or {}
    if supplement.get('rows'):
        lines += ['## 附录：第 21 次补充安全回归','',
            '这是使用修复后实现与后来真实演化经验库的一次补充运行。它没有对原 20 次实验重跑或替换结果，不进入上面的成功率、均值、配对和图。原始历史泄漏证据仍保留。','',
            '| 任务 | 状态 | 最终测试验收 | 完整流程 | 经验保存 | 耗时(s) | 任务 token | 携带经验的成功请求 | 后台状态 |',
            '|---|---|---|---|---|---:|---:|---:|---|']
        for row in supplement['rows']:
            name=f"[{row['task_id']}]({row['task_url']})" if row.get('task_id') and row.get('task_url') else row['key']
            verdict=f"通过（{row['final_tests']} 项）" if row['final_tests_passed'] is True else '未通过' if row['final_tests_passed'] is False else '尚无最终证据'
            lines.append(f"| {name} | {row['status']} | {verdict} | {'成功' if row['verified_success'] else '未完成'} | {'已保存' if row['experience_saved'] is True else '未确认或未保存'} | {fmt(row['elapsed_ms']/1000 if row['elapsed_ms'] is not None else None)} | {fmt(row['task_tokens'])} | {fmt(row['injected_requests'])} | {row['background_state']} |")
        for row in supplement['rows']:
            lines += ['', f"本次原始经验记录 **{fmt(row['original_records_before'])} → {fmt(row['original_records_after'])}**；后台实际模型调用 {fmt(row['background_model_requests'])} 次、耗时 {fmt(row['background_elapsed_ms']/1000 if row['background_elapsed_ms'] is not None else None)}s。涉及审阅关系 {fmt(row['involved_review_relations'])} 条，包含历史 unknown 等关系，不能解释为新产生同样数量的冲突。"]
        for item in supplement['provenance']:
            core=item.get('core') or {}
            lines += ['', f"种子备份 SHA-256：`{item.get('seed_backup_sha256')}`；代码 HEAD：`{core.get('git_head')}`。实际实现以逐文件 SHA-256 为准，未提交修复也由内容指纹区别。",'']
            for filename in ('backend/experience/knowledge.py','backend/experience/library.py'):
                lines += [f"- `{filename}`：`{(core.get('source_sha256') or {}).get(filename,'未记录')}`"]
        integrity=supplement['test_integrity']
        lines += ['',f"补充测试/fixture 独立核查：`{integrity['state']}`，{integrity['unchanged']}/{integrity['checked']} 个工作副本一致；不并入原 20/20 核查数量。",
            '安全结论须结合实际请求引用审计；最终测试通过本身不能证明所有冲突语义均未泄漏。'+('已记录独立请求审计，见统计 JSON 的 supplemental_safety.request_safety_audit。' if supplement.get('request_safety_audit') else '当前尚无补充请求引用审计，暂不宣称泄漏已由本次真实运行验证消除。'),
            '这份种子已有后续 unknown 关系阻断旧来源，不能把本次当成旧泄漏前态的受控重放；补充结果仅说明当前流程的实际运行情况。','']
        audit=supplement.get('request_safety_audit') or {}
        findings=audit.get('findings') or {}
        if findings:
            def audit_bool(value,yes,no):return yes if value is True else no if value is False else '未记录'
            lines += [f"独立请求审计确认 **{fmt(findings.get('successful_requests'))}/{fmt(findings.get('request_count'))} 次真实供应商响应**，完整上报 **{fmt(findings.get('complete_total_tokens'))} tokens**；全部请求匹配本项目配置：`{findings.get('all_requests_match_this_project_configuration')}`。",
                f"请求引用 ID/摘要检查：{audit_bool(findings.get('all_request_reference_ids_and_hashes_empty'),'全部为空','存在非空引用')}；注入事件检查：{audit_bool(findings.get('all_injection_events_match_empty_reference_list'),'全部符合空引用列表摘要','存在不匹配')}；历史泄漏单项/列表摘要：{audit_bool(findings.get('historical_individual_leak_hash_present'),'命中','未命中')} / {audit_bool(findings.get('historical_list_leak_hash_present'),'命中','未命中')}。这支持本次实际未发送经验引用，不能单凭这一运行归因证明补丁消除了所有历史旁路。",'']
        lines += ['补丁涵盖共享约束与等价链阻断、限定授权边界、恢复状态解释，以及一次请求内复用资格校验结果。验证版本边界：等价隔离版完整后端为 **999 passed、1 skipped**；随后加入的请求内校验复用另经 **45 项相关检查及 11 项 API/正常上传归并检查**通过，不能将前述全量结果归为之后的性能优化版本。详见 [R70–R73 修复记录](../experience-consolidation-remediation.md)。','']
    lines += ["", "## 解释边界", "", "这是小样本工程验证，不代表所有项目的平均成功率。重复修复不是独立缺陷；不能把整体更新造成的变化全部归因于自适应检索。检索消融必须同模型、同任务和同冻结经验库；缺少实际注入时必须说明。未完成的运行不会被当作失败或成功。", "",
        "修复成功率、延迟、token 和后台处理必须同时看；更低用量可能来自提前失败。现有少量配对不用于宣称统计显著性。图表仅来自终止任务的真实数据，尚无数据的组不画零值。", ""]
    output.write_text("\n".join(lines),encoding="utf-8")


def charts(report, directory):
    if not any(row['terminal'] for row in report['rows']):
        return []
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    font=Path('C:/Windows/Fonts/msyh.ttc')
    if font.is_file():
        font_manager.fontManager.addfont(str(font));plt.rcParams['font.family']=font_manager.FontProperties(fname=str(font)).get_name()
    plt.rcParams['axes.unicode_minus']=False
    directory.mkdir(parents=True,exist_ok=True)
    conditions=['before','after'];colors=['#6b7d91','#087f8c'];x=[0,1]
    fig,axes=plt.subplots(2,2,figsize=(12.8,8.2),layout='constrained')
    for ax,title,key,scale in zip(axes.flat,['首次三案例：最终测试验收通过率','首次三案例：完整流程成功率','任务耗时：均值 / 中位数','任务模型 token：完整用量任务均值'],['tests','success','elapsed','tokens'],[100,100,0.001,.0001]):
        for i,condition in enumerate(conditions):
            s=report['cohorts']['initial'].get(condition)
            if not s or not s['terminal']:
                ax.text(i,0.04,'尚无终止任务',ha='center',transform=ax.get_xaxis_transform(),fontsize=9);continue
            if key in {'tests','success'}:
                value=s['final_test_pass_rate' if key=='tests' else 'success_rate']*100
                label=f"{s['final_test_passed' if key=='tests' else 'passed']}/{s['terminal']}\n{value:.0f}%"
            else:
                metric=s['elapsed_ms' if key=='elapsed' else 'task_tokens'];value=metric['mean']
                if value is None:
                    ax.text(i,0.04,'用量未知',ha='center',transform=ax.get_xaxis_transform(),fontsize=9);continue
                value*=scale;label=f"{value:,.1f}{' 万' if key=='tokens' else ''}\nn={metric['available']}/{metric['denominator']}"
                if key=='elapsed':ax.scatter(i+.21,metric['median']*scale,marker='D',color='#dc7936',zorder=3,label='中位数' if i==0 else None)
            ax.bar(i,value,color=colors[i],width=.55,alpha=.88);ax.annotate(label,(i,value),xytext=(0,7),textcoords='offset points',ha='center',fontsize=10)
        ax.set_title(title,fontsize=11);ax.set_xticks(x,[LABELS[c] for c in conditions]);ax.set_xlim(-.6,1.6);ax.grid(axis='y',alpha=.2);ax.set_axisbelow(True)
        ax.spines[['top','right']].set_visible(False)
        if key in {'tests','success'}:ax.set_ylim(0,125);ax.set_yticks([0,20,40,60,80,100]);ax.set_ylabel('%')
        else:ax.set_ylim(bottom=0,top=max(1,ax.get_ylim()[1]*1.25));ax.set_ylabel('秒' if key=='elapsed' else '万 token')
        if key=='elapsed' and ax.get_legend_handles_labels()[0]:ax.legend(loc='upper right',frameon=False,fontsize=9)
    fig.suptitle('真实工程更新对照 · '+('完成' if report['status']=='complete' else '进行中，仅显示已终止任务'),fontsize=14)
    p=directory/'repair-comparison.png';fig.savefig(p,dpi=170);plt.close(fig);outputs=[p]
    pairs=[row for row in report['paired_update']['details'] if row['valid']]
    if pairs:
        fig,axes=plt.subplots(1,2,figsize=(13.5,max(3.6,len(pairs)*.42)),layout='constrained')
        for ax,key,scale,title in zip(axes,['elapsed_delta_ms','token_delta'],[.001,1],['更新后 − 更新前：任务耗时(s)','更新后 − 更新前：任务 token']):
            selected=[row for row in pairs if row[key] is not None]
            for i,row in enumerate(selected):
                value=row[key]*scale
                ax.barh(i,value,color='#087f8c' if value<=0 else '#d57b45',alpha=.85 if row['both_successful'] else .35)
                ax.annotate(f'{value:+,.1f}',(value,i),xytext=(4 if value>=0 else -4,0),textcoords='offset points',ha='left' if value>=0 else 'right',va='center',fontsize=9)
            ax.set_yticks(range(len(selected)),[f"{row['phase']} · r{row['repetition']}" for row in selected],fontsize=9)
            ax.axvline(0,color='#6b7d91',linewidth=1);ax.set_title(title,fontsize=11);ax.grid(axis='x',alpha=.2);ax.set_axisbelow(True);ax.spines[['top','right']].set_visible(False);ax.margins(x=.2)
        fig.suptitle('同缺陷、阶段与重复序号配对；淡色表示至少一方未完成完整流程',fontsize=12)
        p=directory/'paired-cost-differences.png';fig.savefig(p,dpi=170);plt.close(fig);outputs.append(p)
    retrieval_pairs=[row for row in report['paired_retrieval']['details'] if row['valid']]
    if retrieval_pairs:
        fig,axes=plt.subplots(1,2,figsize=(12.8,4.5),layout='constrained')
        for ax,field,scale,title in zip(axes,['elapsed_ms','tokens'],[.001,.0001],['同冻结经验库：任务耗时','同冻结经验库：任务 token']):
            for i,row in enumerate(retrieval_pairs):
                for offset,side,label,color in [(-.18,'left','上下文检索','#6b7d91'),(.18,'right','自适应检索','#d57b45')]:
                    raw=row['left_elapsed_ms' if side=='left' else 'right_elapsed_ms'] if field=='elapsed_ms' else row['left_tokens' if side=='left' else 'right_tokens']
                    if raw is None:continue
                    value=raw*scale
                    ax.bar(i+offset,value,width=.34,color=color,label=label if i==0 else None)
                    ax.annotate(f'{value:.1f}',(i+offset,value),xytext=(0,5),textcoords='offset points',ha='center',fontsize=10)
            ax.set_xticks(range(len(retrieval_pairs)),[f"真实经验库 {row['repetition']}" for row in retrieval_pairs]);ax.set_title(title)
            ax.set_ylabel('秒' if field=='elapsed_ms' else '万 token');ax.grid(axis='y',alpha=.2);ax.set_axisbelow(True)
            ax.spines[['top','right']].set_visible(False);ax.set_ylim(bottom=0,top=max(1,ax.get_ylim()[1]*1.23));ax.legend(frameon=False)
        fig.suptitle('自适应检索独立消融 · 两对最终验收均通过 · 本次更慢、更贵',fontsize=13)
        p=directory/'retrieval-ablation.png';fig.savefig(p,dpi=170);plt.close(fig);outputs.append(p)
    return outputs


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,default=ROOT/'.cache/real-update-comparison')
    parser.add_argument('--output',type=Path,default=ROOT/'docs/reports/real-update-comparison-2026-11-15.md')
    parser.add_argument('--no-charts',action='store_true')
    parser.add_argument('--append-safety',action='store_true',help='Update only the separate safety appendix; preserve sealed primary data and chart files.')
    args=parser.parse_args(argv)
    if args.append_safety:
        report=read(args.output.with_suffix('.json'))
        if report is None:raise ValueError('Generate the original sealed report before appending safety evidence.')
        refresh_supplement(report,args.input,read(args.input/'cases/manifest.json',{}),verify_workspaces=True)
        assets=args.output.parent/'real-update-comparison-2026-11-15-assets'
        images=[assets/name for name in ('repair-comparison.png','paired-cost-differences.png','retrieval-ablation.png') if (assets/name).is_file()]
        write_markdown(report,args.output,images)
        args.output.with_suffix('.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'original_tasks':len(report['rows']),'supplemental_state':report['supplemental_safety']['state'],
            'original_charts_regenerated':False},ensure_ascii=False));return
    records=read(args.input/'records.json',[])
    # Separate ablation runs can be observed without mixing their costs into the main before/after arms.
    extra=read(args.input/'retrieval-records.json',[])
    if isinstance(extra,dict):extra=extra.get('records',[])
    manifest=read(args.input/'cases/manifest.json',{})
    report=build_report([*records,*extra],read(args.input/'protocol.json',{}),manifest,args.input/'observations')
    report['test_integrity']=verify_test_integrity(args.input,manifest,report['rows'],ready=report['status']=='complete')
    report['captured_history']=captured_history(args.input)
    report['shared_constraint_leak_audit']=read(args.input/'shared-constraint-leak-audit.json')
    report['model_provenance_audit']=read(args.input/'model-provenance-audit.json')
    report['main_comparison']=main_comparison_link(args.input,report['rows'])
    report['supplemental_safety']=supplemental_safety(args.input,manifest,verify_workspaces=True)
    report['frozen_reference_integrity']=[dict(repetition=repetition,path=str(path),actual_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        expected_sha256=expected,matches=bool(expected and all(value==hashlib.sha256(path.read_bytes()).hexdigest() for value in expected)))
        for repetition in (1,2) for path in [args.input/f'after{repetition}-retrieval-reference.db'] if path.is_file()
        for expected in [[row['frozen_sha256'] for row in report['rows'] if row['cohort']=='retrieval_ablation' and row['repetition']==repetition]]]
    args.output.parent.mkdir(parents=True,exist_ok=True)
    assets=args.output.parent/'real-update-comparison-2026-11-15-assets'
    images=[] if args.no_charts else charts(report,assets)
    report['screenshots']=captured_screenshots(args.input,assets)
    write_markdown(report,args.output,images)
    args.output.with_suffix('.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'status':report['status'],'output':str(args.output),'terminal':sum(row['terminal'] for row in report['rows']),
                      'planned':report['planned_tasks'],'charts':len(images)},ensure_ascii=False))


if __name__=='__main__':
    main()
