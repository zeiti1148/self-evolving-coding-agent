"""Publish measured evidence without replacing failed attempts with successes."""
import argparse
import difflib
import hashlib
import json
import re
from pathlib import Path
import shutil
import zipfile

ROOT=Path(__file__).resolve().parents[1]
DEST=ROOT/'docs/reports/knowledge-remediation-2026-11-14'


def read(path):return json.loads(path.read_text(encoding='utf-8'))
def save(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
def score(row):return f"{row['passed']} 通过 / {row['failures']} 失败 / {row['skipped']} 跳过"


def main():
    p=argparse.ArgumentParser();p.add_argument('--run',type=Path,required=True)
    p.add_argument('--first',type=Path,required=True);p.add_argument('--expanded',type=Path,required=True)
    p.add_argument('--visual',type=Path,required=True)
    args=p.parse_args();run=args.run.resolve()
    first=read(args.first/'report.json');expanded=read(args.expanded/'report.json');final=read(run/'report.json')
    validation=read(run/'validation.json');initial_validation=read(args.first/'validation.json')
    merge=read(run/'merge.json');obs=read(run/'observations.json')
    assert final['completed'] and validation['completed'] and merge['completed']
    principle=merge['principles'][0]
    tasks={t['task_id']:t for r in [first,expanded,final] for t in r['tasks']}
    requests=[q for t in tasks.values() for q in t['requests']]+merge['requests']
    assert all(q['api_configuration']==final['api_configuration'] for q in requests)
    assert all(not q.get('request_experience_ids') for t in tasks.values() for q in t['requests'])
    package=DEST/'real-engineering';package.mkdir(parents=True,exist_ok=True)
    outcomes=[];table=[];sources={};complexity=[]
    for task in final['tasks']:
        case=task['case'];name=case['id']
        source=Path(case.get('validation_base_source',case['source']));work=Path(task['workspace'])
        files=list((source/case['source_prefix']).rglob('*.py'))
        complexity.append(dict(project=name,production_python_files=len(files),production_lines=sum(len(f.read_bytes().splitlines()) for f in files)))
        patch=[];changed=[]
        for before in files:
            relative=before.relative_to(source);after=work/relative
            a=before.read_text(encoding='utf-8');b=after.read_text(encoding='utf-8')
            if a!=b:
                changed.append(relative.as_posix())
                patch.extend(difflib.unified_diff(a.splitlines(True),b.splitlines(True),fromfile='a/'+relative.as_posix(),tofile='b/'+relative.as_posix()))
        (package/(name+'-final.patch')).write_text(''.join(patch),encoding='utf-8')
        checks=next(t['checks'] for t in validation['tasks'] if t['case']==name)
        holdout=next(c for c in checks if c['label']=='model-holdout')
        for c in checks:table.append('| '+name+' | '+c['label']+' | '+score(c)+' |')
        exp=next(e for e in task['experiences'] if e['id'] in principle['source_ids'])
        evidence=exp['evidence']
        acceptance=sum(e['tests'] for e in evidence)
        outcomes.append(dict(name=name,acceptance=f'{acceptance}/{acceptance}',holdout=f"{holdout['passed']}/{holdout['tests']}",changed_files=changed))
        sources[name]=exp['root_cause']+'\n\n'+exp['solution']
        save(package/(name+'-experience.json'),exp)
    audits=dict(tasks_executed=len(tasks),requests=len(requests),successful_requests=sum(bool(q['success']) for q in requests),
        total_tokens=sum(q.get('usage',{}).get('total_tokens',0) for q in requests),
        stopped_tasks=[t['task_id'] for t in tasks.values() if t['result']['status']=='stopped'],
        api_configuration=final['api_configuration'],no_cross_task_experience_injection=True)
    summary=dict(outcomes=outcomes,complexity=complexity,api_audit=audits,principle=principle,
        observations=obs,validation=validation,first_validation=initial_validation,
        final_tasks=[{k:t[k] for k in ['case','task_id','workspace','database','result','experiences','source_unchanged','original_tests_unchanged']} for t in final['tasks']],
        merge_candidates=merge['candidate_results'],
        project_relation_review=read(run/'project-relation-review.json'),
        checks={k:merge[k] for k in ['idempotent','original_records_unchanged','source_databases_unchanged','namespace_isolated']})
    save(DEST/'real-engineering-merge.json',summary)
    with zipfile.ZipFile(package/'evidence.zip','w',zipfile.ZIP_DEFLATED) as z:
        for label,directory in [('first',args.first),('expanded',args.expanded),('continued',run)]:
            for filename in ['report.json','validation.json','merge.json','merge-gate.json','observations.json','project-relation-review.json']:
                path=directory/filename
                if path.exists():z.write(path,label+'/'+filename)
            for case in ['pytest','sphinx']:
                path=directory/case
                for file in path.rglob('*') if path.exists() else []:
                    if file.is_file() and file.suffix in {'.json','.xml','.patch'}:
                        z.write(file,label+'/'+file.relative_to(directory).as_posix())
            evaluation=directory/'evaluation'
            for file in evaluation.glob('*/*') if evaluation.exists() else []:
                if file.is_file():z.write(file,label+'/'+file.relative_to(directory).as_posix())
    cases=final['tasks']
    sections=[
        '# 真实工程：独立修复、经验合并与扩展验证',
        '本次使用两个真实开源工程的历史缺陷源码，不再用小型业务函数代替工程。合并输入来自两个项目分别修复后，由现有提取器保存的原始经验；原文没有为匹配合并主题而改写。',
        '## 来源与规模',
        '| 工程 | 上游缺陷提交 | 生产 Python 规模 | 上游修复 |\n|---|---|---|---|',
    ]
    for task,metrics in zip(cases,complexity):
        c=task['case'];sections.append(f"| {c['repo']} | `{c['base_commit']}` | {metrics['production_python_files']} 文件 / {metrics['production_lines']:,} 行 | [#{c['instance'].rsplit('-',1)[1]}]({c['upstream_pr']}) |")
    sections += [
        '\n来源为 [SWE-bench Verified 官方数据](https://huggingface.co/datasets/princeton-nlp/SWE-bench_Verified)，两项难度标签均为人工 1–4 小时。源码完整归档和官方 test_patch 固定；许可证随源码保留。没有向修复模型提供官方 patch 或 hints_text。',
        '## 不掩盖首次失败',
        '第一轮 pytest 的 89 项和 Sphinx 的 6 项选定测试通过，但扩展测试揭示：pytest 的单个 MarkDecorator 被错误当作列表展开，导致 2 项原有测试回归；Sphinx 只处理当前模块缓存，跨模块真实 HTML 的 6 个用例仍失败。第一轮资料被合并门槛拦截。',
        '第二轮将这些用例明确提升为回归测试，从原始缺陷代码重新独立修复。pytest 通过 266 项；Sphinx 在 45 次决策预算内仍有 6 项失败，运行状态为 stopped，未生成新经验。接着只继续 Sphinx 的失败副本，并提供“检查成员发现之后的过滤与 inherited-members 默认 object 截断”的诊断摘要；未提供 pytest 经验或官方补丁。故最终 Sphinx 结果是有诊断辅助的续跑，不能称为一次无提示成功。',
        '初轮使用 Python 3.10 时 Sphinx 有 6 个不相关的兼容失败，参考补丁也失败。最终改用该历史项目测试矩阵支持的 Python 3.9；此前兼容失败消失。旧结果仍保留。',
        '## 最终修复与同条件对照',
        '| 工程 | 版本与测试集 | 实测结果 |\n|---|---|---|',
        '\n'.join(table),
        'component 包含完整选定组件及第一轮转入的回归测试；holdout 是新的未见测试，pytest 为多层 27 种标记表示组合、6 种基类排列及 3 种非类输入，Sphinx 为默认继承和显式基类截断的实际 HTML。上游已有的 pytest xfail 与缺少可选 Cython 的 Sphinx 测试始终单列；没有把它们计作通过。',
        'Sphinx 官方参考补丁在额外 HTML 场景失败，说明这些场景超出该基准原有回归覆盖；这里展示真实输出差异，不将参考补丁当作所有新需求的正确性判据。',
        '## 自动合并得到的共享原则',
        '**'+principle['title']+'**',principle['principle'],
        '\n'.join('- '+s for s in principle['boundaries']),
        'pytest 需要累积各级标记并处理单项/列表表示；Sphinx 需要保留 MRO 中优先类的同名文档，并遵守显式基类排除。两份来源的完整 constraints、applicability、issues 与 evidence 作为实例附在原则中，未用某个项目的具体操作覆盖另一个项目。',
        '最终两项任务共提炼 3 条原始经验，其中 Sphinx 还产生一条“清理临时调试日志”的经验。程序枚举全部 2 个跨项目候选对，而不是静默挑选方便合并的记录。候选判断与原始全文见审计包的 merge.json。',
        '同项目的日志清理与继承修复涉及同一函数，确定性整理先将它们标为 unknown 并阻止继续。Codex 核对补丁及操作范围后，显式确认 compatible（不是 equivalent）；审阅及版本历史保存在 project-relation-review.json。这一步是明确审阅，不计作自动判断。随后跨项目的原则归纳及复核由模型自动完成。',
        '合并流程为原始资料导入 → 项目规则整理与明确审阅 → 枚举跨项目候选 → 通用模型提出原则 → 新上下文逐来源复核 → 精确引文校验 → 保存共享原则。重复执行命中同一 ID，不重复付费；原始记录、源库哈希与命名空间隔离检查通过。案例由人定向选择，原则内容由程序自动归纳及复核；并未演示全库无人值守的候选发现。',
        '## 两份原始经验',
        '### pytest\n\n'+sources['pytest'], '### Sphinx\n\n'+sources['sphinx'],
        '## API 来源与审计',
        f"共 {audits['tasks_executed']} 次修复任务执行（包含预算停止的运行），以及原则合并请求；总计 {audits['requests']} 次请求记录，{audits['successful_requests']} 次成功返回。逐请求均绑定用户指定的当前仓库 `.env`，请求地址 `{audits['api_configuration']['origin']}`，模型 `{audits['api_configuration']['model']}`。没有检索或注入另一任务的经验。日志不含密钥。提供商累计报告 {audits['total_tokens']:,} tokens，包含重复上下文，不能直接换算为计费金额。",
        '## 文件与复现',
        '- [完整结构化结果](real-engineering-merge.json)\n- [原始执行、JUnit、参考对照和 API 审计包](real-engineering/evidence.zip)\n- [pytest 最终生产补丁](real-engineering/pytest-final.patch)\n- [Sphinx 最终生产补丁](real-engineering/sphinx-final.patch)\n- [pytest 原始经验](real-engineering/pytest-experience.json)\n- [Sphinx 原始经验](real-engineering/sphinx-experience.json)\n- [实验脚本说明](../../../benchmarks/engineering-merge/README.md)',
        '## 结论的范围',
        '两项定向选择的历史案例只能证明本次修复和有条件原则归纳确实执行，不能估计总体准确率、生产故障率或修复成功率提升。未运行全仓库所有测试，未使用官方 Docker 评测，因此不是 SWE-bench 排名成绩。公开案例可能有训练污染；未向模型提供补丁不等于能证明模型从未见过该问题。共享原则仅存放于显式实验命名空间，未自动发布到所有项目；新目标仍需验证。',
        '模型提炼文字不等于逐句验证的事实。例如日志清理记录要求工作树无日志产物，但实验副本保留了 DEBUGLOG/XDEBUG 文件作为审计材料。最终生产源码已另行检查，没有调试写文件语句；交付生产补丁只包含源码。原文保留这一限制，不将测试通过包装成所有文字断言均得到证明。',
        '相关后端原则与来源追踪回归：20 项通过。R22–R24 的处理及默认产品尚未覆盖的范围见 [持续修复记录](../../experience-consolidation-remediation.md)。',
    ]
    document='\n\n'.join(sections)+'\n'
    document=re.sub(r'(?m)(^\|[^\n]*\|)\n\n(?=\|)',r'\1\n',document)
    (DEST/'real-engineering-merge.md').write_text(document,encoding='utf-8')
    payload=dict(observations=obs,principle=principle,
        rule_summary='按继承顺序逐类读取元数据；累积、去重与覆盖策略保留各项目自己的约束。',
        sources=sources,tests=outcomes,merge_state='自动归纳并复核通过 · 1 条共享原则',
        limits='定向选择 2 个历史工程问题；Sphinx 含诊断辅助续跑。2 项上游特殊测试单列。非官方 Docker 评分。',
        api='请求来源：本项目 .env · '+audits['api_configuration']['origin']+' · '+audits['api_configuration']['model'])
    html=args.visual.read_text(encoding='utf-8')
    start=html.index('>',html.index('<script type="application/json" id="engineering-payload"'))+1
    end=html.index('</script>',start)
    html=html[:start]+json.dumps(payload,ensure_ascii=False).replace('<','\\u003c')+html[end:]
    args.visual.write_text(html,encoding='utf-8')
    print(json.dumps(dict(outcomes=outcomes,audit=audits,report=str(DEST/'real-engineering-merge.md')),ensure_ascii=False,indent=2))


if __name__=='__main__':main()
