"""Accounting fixtures exercise formulas only; they are never experiment observations."""
from copy import deepcopy
import hashlib
import json
import shutil

import pytest

from scripts.report_real_update_comparison import build_report, paired, tokens, verify_test_integrity, write_markdown, supplemental_safety, refresh_supplement, main_comparison_link


MANIFEST={"cases":[{"id":"case","source_digest":"same-tree","baseline":{"tests":4}}]}


def record(arm, *, state="succeeded", token=100, elapsed=1000, phase="case"):
    data=dict(key=arm+'-'+phase,arm=arm,phase=phase,case_id="case",task_id=arm,
        project={"source_digest":"same-tree","runner_config":{"test_targets":["tests"]}},
        request={"task_description":"Original upstream issue", "max_steps":30,"execution_mode":"single"},
        status={"status":state,"result":{"repair_succeeded":True,"tests_passed":True,"tool_results":[
            {"tool_name":"run_tests","success":True,"metadata":{"final_verification":True,"tests":4,"failures":0,"errors":0,"skipped":0}}]}},
        audit={"model_requests":[{"success":True,"usage":{"total_tokens":token}}]},
        metrics={"elapsed_ms":elapsed,"experience":{"state":"succeeded","token_usage":{"total_tokens":20}}},observed_complete=True)
    return data


def report(records):return build_report(records,{},MANIFEST)


def test_unfinished_is_neither_success_nor_failure_and_has_no_cost_in_terminal_mean():
    completed=record("before1",token=100,elapsed=1000)
    running=record("before2",state="running",token=900,elapsed=9000)
    running['status']['result']=None
    value=report([completed,running])['summary']['before']
    assert value['planned']==2 and value['terminal']==1 and value['unfinished']==1
    assert value['passed']==1 and value['failed']==0 and value['success_rate']==1
    assert value['task_tokens']['mean']==100 and value['elapsed_ms']['mean']==1000


def test_missing_usage_remains_unknown_and_failures_stay_in_all_terminal_cost():
    first=record("before1",token=100)
    failed=record("before2",state="failed",token=None,elapsed=3000)
    failed['audit']['model_requests'].append({'success':False,'usage':{'total_tokens':50}})
    value=report([first,failed])['summary']['before']
    assert value['success_rate']==.5 and value['failed']==1
    assert value['task_tokens']['total'] is None and value['task_tokens']['available']==1
    assert value['task_tokens']['mean']==100 and value['reported_task_tokens']==150
    assert value['elapsed_ms']['mean']==value['elapsed_ms']['median']==2000
    assert tokens(failed)['total_tokens'] is None


def test_pairing_separates_reuse_and_reports_all_terminal_and_both_successful():
    rows=[record('before1',token=200,elapsed=2000),record('after1',token=100,elapsed=1000),
          record('before1',phase='case-reuse',token=20),record('after1',phase='case-reuse',state='failed',token=10)]
    value=report(rows)
    pair=value['paired_update']
    assert pair['valid_pairs']==2 and pair['losses']==1 and pair['wins']==0
    assert pair['all_terminal']['token_delta']['mean_delta']==-55
    assert pair['both_successful']['pairs']==1 and pair['both_successful']['token_delta']['mean_delta']==-100
    assert value['cohorts']['initial']['before']['planned']==1
    assert value['cohorts']['reuse']['after']['failed']==1


def test_success_requires_independent_original_test_count_and_zero_skips():
    invalid=record('after1');invalid['status']['result']['tool_results'][0]['metadata']['tests']=3
    assert not report([invalid])['rows'][0]['verified_success']
    invalid['status']['result']['tool_results'][0]['metadata'].update(tests=4,skipped=1)
    assert not report([invalid])['rows'][0]['verified_success']


def test_unmatched_budget_or_source_is_not_a_valid_pair():
    before=record('before1');after=record('after1')
    after['request']['max_steps']=40
    assert report([before,after])['paired_update']['valid_pairs']==0
    after=record('after1');after['project']['source_digest']='changed-tree'
    assert report([before,after])['paired_update']['valid_pairs']==0


def test_retrieval_ablation_requires_identical_frozen_references():
    contextual=record('contextual1');adaptive=record('adaptive1')
    contextual['frozen_sha256']='frozen-a';adaptive['frozen_sha256']='frozen-b'
    assert report([contextual,adaptive])['paired_retrieval']['valid_pairs']==0
    adaptive['frozen_sha256']='frozen-a'
    assert report([contextual,adaptive])['paired_retrieval']['valid_pairs']==1


def test_no_model_audit_does_not_claim_zero_tokens():
    value=record('before1');value.pop('audit');value.pop('metrics')
    row=report([value])['rows'][0]
    assert row['task_tokens'] is None and row['reported_task_tokens'] is None
    assert row['background_tokens'] is None and row['injected_requests'] is None


def test_actual_token_audit_overrides_partial_cached_metrics():
    value=record('before1',token=123)
    value['metrics']['token_usage']={'total_tokens':7}
    assert report([value])['rows'][0]['task_tokens']==123


def test_unstarted_ablation_does_not_make_primary_completion_the_final_result():
    value=build_report([record('before1'),record('after1')],
        {'primary_tasks':2,'retrieval_ablation':'same frozen references','retrieval_ablation_tasks':4},MANIFEST)
    assert value['planned_tasks']==6 and value['ablation_plan_size']==4
    assert value['missing_planned_records']==4 and value['status']=='in_progress'


def test_primary_six_fresh_pairs_and_two_reuse_pairs_stay_out_of_ablation_costs():
    cases={'cases':[{'id':c,'source_digest':'same-tree','baseline':{'tests':4}} for c in ['case','case2','case3']]}
    rows=[]
    for repetition in (1,2):
        for condition in ('before','after'):
            for case in ('case','case2','case3'):
                row=record(condition+str(repetition),phase=case);row['case_id']=case;rows.append(row)
            rows.append(record(condition+str(repetition),phase='case-reuse'))
        for condition in ('contextual','adaptive'):
            row=record(condition+str(repetition),phase='retrieval-reuse');row['frozen_sha256']='frozen'+str(repetition);rows.append(row)
    value=build_report(rows,{'primary_tasks':16,'retrieval_ablation_tasks':4},cases)
    assert value['planned_tasks']==20 and value['status']=='complete'
    assert value['paired_update_by_cohort']['initial']['valid_pairs']==6
    assert value['paired_update_by_cohort']['reuse']['valid_pairs']==2
    assert value['paired_retrieval']['valid_pairs']==2
    assert value['summary']['before']['terminal']==8 and value['summary']['after']['terminal']==8
    assert value['summary']['contextual']['terminal']==value['summary']['adaptive']['terminal']==2


def test_budget_stop_with_passing_final_tests_is_not_a_failed_patch(tmp_path):
    rows=[record('before'+str(i)) for i in range(1,5)]
    stopped=rows[-1]
    stopped['status']['status']='stopped'
    stopped['status']['error_message']='Decision budget exhausted.'
    stopped['status']['result'].update(experience_saved=None,experience_ids=[])
    stopped['verified_success']=False
    value=report(rows)
    summary=value['summary']['before']
    assert summary['final_test_passed']==4 and summary['final_test_pass_rate']==1
    assert summary['passed']==3 and summary['success_rate']==.75
    assert summary['final_test_failed']==summary['final_test_unconfirmed']==0
    row=value['rows'][-1]
    assert row['final_tests_passed'] is True and row['verified_success'] is False
    assert row['registered_verified_success'] is False
    assert row['stop_reason']=='Decision budget exhausted.' and row['experience_ids']==[]
    output=tmp_path/'report.md'
    write_markdown(value,output,[])
    text=output.read_text(encoding='utf-8')
    assert '| stopped | 通过 | 未完成 / 未保存 |' in text
    assert '独立最终测试实际通过，不能称为测试失败。没有生成经验记录。' in text


def test_missing_final_evidence_is_unknown_not_a_failed_test():
    value=record('before1',state='stopped')
    value['status']['result']['tool_results']=[]
    summary=report([value])['summary']['before']
    assert summary['final_test_passed']==summary['final_test_failed']==0
    assert summary['final_test_unconfirmed']==1


def test_invalid_source_excludes_comparison_without_relabeling_a_passing_test():
    value=record('before1')
    value['project']['source_digest']='unexpected-source'
    row=report([value])['rows'][0]
    assert row['comparison_valid'] is False and row['verified_success'] is False
    assert row['final_tests_passed'] is True


def test_integrity_never_scans_before_all_observations_finish(tmp_path,monkeypatch):
    import backend.tools.workspace as workspace
    def forbid_scan(*args,**kwargs):
        raise AssertionError('Measured experiment must not be scanned.')
    monkeypatch.setattr(workspace,'inventory',forbid_scan)
    rows=report([record('before1')])['rows']
    assert verify_test_integrity(tmp_path,MANIFEST,rows)['state']=='deferred'
    rows[0]['observed_complete']=False
    assert verify_test_integrity(tmp_path,MANIFEST,rows,ready=True)['checked']==0


def integrity_fixture(tmp_path):
    from backend.tools.workspace import inventory
    source=tmp_path/'source'
    (source/'tests'/'fixtures'/'target').mkdir(parents=True)
    (source/'tests'/'test_case.py').write_text('def test_case(): pass',encoding='utf-8')
    (source/'tests'/'fixtures'/'target'/'index.rst').write_text('official fixture',encoding='utf-8')
    (source/'production.py').write_text('original',encoding='utf-8')
    original={path:value for path,value in inventory(source).items() if path.startswith(('tests/','testing/'))}
    cases={'cases':[{'id':'case','source':str(source),'tests_digest':hashlib.sha256(json.dumps(original,sort_keys=True).encode()).hexdigest(),'test_files':len(original)}]}
    root=tmp_path/'experiment'
    target=root/'runs'/'before1'/'workspaces'/'task-one'
    shutil.copytree(source,target)
    row={'key':'before1-case','arm':'before1','task_id':'task-one','case_id':'case','terminal':True,'observed_complete':True}
    return root,cases,[row],target


def test_final_integrity_checks_all_fixture_bytes_and_ignores_production_changes(tmp_path):
    root,cases,rows,target=integrity_fixture(tmp_path)
    (target/'production.py').write_text('repaired',encoding='utf-8')
    (target/'importer_helpers.tmp').write_text('working artifact',encoding='utf-8')
    (target/'tests'/'__pycache__').mkdir()
    (target/'tests'/'__pycache__'/'generated.pyc').write_bytes(b'cache')
    result=verify_test_integrity(root,cases,rows,ready=True)
    assert result['state']=='verified' and result['unchanged']==result['checked']==1
    check=result['records'][0]
    assert check['source_manifest_matches'] is True
    assert check['full_fixture_expected_files']==check['full_fixture_actual_files']==2
    assert check['full_fixture_expected_digest']==check['full_fixture_actual_digest']


@pytest.mark.parametrize('change',['changed','added','removed'])
def test_final_integrity_detects_fixture_changes_even_outside_manifest_digest(tmp_path,change):
    root,cases,rows,target=integrity_fixture(tmp_path)
    fixture=target/'tests'/'fixtures'/'target'/'index.rst'
    if change=='changed':fixture.write_text('modified assertion fixture',encoding='utf-8')
    elif change=='added':(fixture.parent/'extra.rst').write_text('extra fixture',encoding='utf-8')
    else:fixture.unlink()
    result=verify_test_integrity(root,cases,rows,ready=True)
    check=result['records'][0]
    assert result['state']=='incomplete_or_mismatch' and result['unchanged']==0
    assert check['full_fixture_'+change]


def test_final_integrity_does_not_accept_modified_reference_source(tmp_path):
    root,cases,rows,target=integrity_fixture(tmp_path)
    from pathlib import Path
    source=Path(cases['cases'][0]['source'])
    (source/'tests'/'test_case.py').write_text('altered source reference',encoding='utf-8')
    result=verify_test_integrity(root,cases,rows,ready=True)
    assert result['records'][0]['source_manifest_matches'] is False
    assert result['unchanged']==0


def test_paired_final_acceptance_cost_includes_stopped_valid_patches_but_excludes_unfixed_tasks(tmp_path):
    rows=[record('before1',token=300,elapsed=3000),record('after1',state='stopped',token=200,elapsed=2000),
          record('before2',token=400,elapsed=4000),record('after2',state='stopped',token=100,elapsed=1000),
          record('before3',token=500,elapsed=5000),record('after3',token=300,elapsed=3000)]
    rows[3]['status']['result']['tool_results'][0].update(success=False)
    rows[3]['status']['result']['tool_results'][0]['metadata']['failures']=1
    value=report(rows)
    pairs=value['paired_update']
    assert pairs['all_terminal']['pairs']==3 and pairs['all_terminal']['token_delta']['mean_delta']==-200
    assert pairs['both_final_tests_passed']['pairs']==2
    assert pairs['both_final_tests_passed']['token_delta']['mean_delta']==-150
    assert pairs['both_final_tests_passed']['elapsed_delta_ms']['mean_delta']==-1500
    assert pairs['both_successful']['pairs']==1 and pairs['both_successful']['token_delta']['mean_delta']==-200
    stopped=pairs['details'][0]
    assert stopped['both_successful'] is False and stopped['both_final_tests_passed'] is True
    output=tmp_path/'report.md'
    write_markdown(value,output,[])
    assert '双方最终验收通过 | 2 | -1.5 / 2 | -150 / 2 |' in output.read_text(encoding='utf-8')


def test_paired_acceptance_unknown_evidence_or_usage_is_not_filled_in():
    before=record('before1',token=300)
    after=record('after1',state='stopped',token=None)
    value=report([before,after])['paired_update']['both_final_tests_passed']
    assert value['pairs']==1 and value['token_delta']['complete_pairs']==0
    assert value['token_delta']['mean_delta'] is None
    after['status']['result']['tool_results']=[]
    assert report([before,after])['paired_update']['both_final_tests_passed']['pairs']==0


def test_unstarted_safety_is_neither_a_failed_nor_a_successful_observation(tmp_path):
    value=supplemental_safety(tmp_path,MANIFEST)
    assert value['state']=='尚未执行' and value['registered_tasks']==1
    assert value['submitted']==value['observed_complete']==0
    assert value['summary']['terminal']==value['summary']['passed']==value['summary']['failed']==0
    assert value['test_integrity']['state']=='deferred'


def test_supplement_refresh_never_changes_the_original_twenty_statistics(tmp_path):
    primary=build_report([record('before'+str(i)) for i in range(1,21)],{'primary_tasks':20},MANIFEST)
    original=deepcopy(primary)
    safety=record('safety1',state='stopped',phase='supplemental-safety',token=777,elapsed=1234)
    safety['core']={'git_head':'same-head','source_sha256':{'backend/experience/knowledge.py':'patched'}}
    safety['seed_backup_sha256']='actual-evolved-seed'
    (tmp_path/'safety-records.json').write_text(json.dumps([safety]),encoding='utf-8')
    refresh_supplement(primary,tmp_path,MANIFEST)
    for key,value in original.items():assert primary[key]==value
    extra=primary['supplemental_safety']
    assert len(primary['rows'])==20 and extra['excluded_from_primary'] is True
    assert extra['summary']['terminal']==extra['summary']['final_test_passed']==1
    assert extra['summary']['passed']==0 and extra['summary']['task_tokens']['total']==777
    assert extra['rows'][0]['cohort']=='supplemental_safety'
    assert extra['test_integrity']['state']=='deferred'
    text=tmp_path/'report.md';write_markdown(primary,text,[])
    assert '不能把本次当成旧泄漏前态的受控重放' in text.read_text(encoding='utf-8')
    assert '暂不宣称泄漏已由本次真实运行验证消除' in text.read_text(encoding='utf-8')


def test_running_supplement_keeps_costs_and_verdict_unfinished(tmp_path):
    row=record('safety1',state='running',phase='supplemental-safety')
    row['status']['result']=None;row['observed_complete']=False
    (tmp_path/'safety-records.json').write_text(json.dumps([row]),encoding='utf-8')
    value=supplemental_safety(tmp_path,MANIFEST,verify_workspaces=True)
    assert value['submitted']==1 and value['observed_complete']==0
    assert value['summary']['terminal']==value['summary']['passed']==value['summary']['failed']==0
    assert value['summary']['task_tokens']['total'] is None and value['test_integrity']['state']=='deferred'


def test_main_entry_selects_only_the_original_twelve_excluding_its_container(tmp_path):
    rows=report([record('before'+str(i)) for i in range(1,13)])['rows']
    selected=','.join(row['task_id'] for row in rows)
    path=tmp_path/'main-comparison-url.txt'
    path.write_text('http://127.0.0.1:5173/#/tasks/container?compare='+selected,encoding='utf-8')
    result=main_comparison_link(tmp_path,rows)
    assert result['container_counted'] is False and len(result['selected_task_ids'])==12
    path.write_text('http://127.0.0.1:5173/#/tasks/before1?compare='+selected,encoding='utf-8')
    with pytest.raises(ValueError,match='excluding its container'):
        main_comparison_link(tmp_path,rows)
