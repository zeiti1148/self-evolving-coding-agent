"""Publish concise, auditable experiment tables and standalone figures from real artifacts."""
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import random
import statistics
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.experiment import file_hash,write_json


def wilson(success,total):
    if not total:return [None,None]
    z=1.959963984540054;p=success/total;den=1+z*z/total
    middle=(p+z*z/(2*total))/den
    radius=z*math.sqrt(p*(1-p)/total+z*z/(4*total*total))/den
    return [middle-radius,middle+radius]


def decision_characters(row):
    sizes={hashlib.sha256(json.dumps(c['references'],sort_keys=True).encode()).hexdigest():max(0,c['characters']-2)
           for c in row.get('reference_calls',[])}
    return [sizes.get(event['content_sha256'],0) for event in row['audit']['injection_events']]


def main():
    def read(name):return json.loads((ROOT/'.cache'/name/'report.json').read_text(encoding='utf-8'))
    live=read('knowledge-live-experiment');semantic=read('knowledge-semantic-experiment')
    system=read('knowledge-system-experiment');release=read('knowledge-release-validation')
    challenge=read('knowledge-adversarial-experiment')
    assert live['completed'] and release['completed']
    directory=ROOT/'docs/reports/knowledge-remediation-2026-11-14';directory.mkdir(parents=True,exist_ok=True)
    groups={}
    for arm in ['none','old','new']:
        rows=[r for r in live['tasks'] if r['phase']=='evaluation' and r['arm']==arm]
        tokens=[r['metrics']['reported_tokens']['total_tokens'] for r in rows]
        durations=[r['elapsed_seconds'] for r in rows]
        characters=[size for r in rows for size in decision_characters(r)]
        groups[arm]=dict(tasks=len(rows),passed=sum(r['outcome']=='passed' for r in rows),
            visible_passed=sum(r['visible_passed'] for r in rows),heldout_passed=sum(r['heldout_passed'] for r in rows),
            success_wilson95=wilson(sum(r['outcome']=='passed' for r in rows),len(rows)),
            mean_total_tokens=statistics.mean(tokens),median_total_tokens=statistics.median(tokens),
            mean_seconds=statistics.mean(durations),median_seconds=statistics.median(durations),
            mean_decisions=statistics.mean(r['audit']['decision_rounds'] for r in rows),
            mean_reference_characters=statistics.mean(characters),
            with_references=sum(bool(r['audit']['retrieved_experience_ids']) for r in rows),
            with_references_by_language={lang:sum(bool(r['audit']['retrieved_experience_ids']) for r in rows if f'-{lang}-' in r['case']) for lang in ['java','python']},
            requests=sum(r['metrics']['model_requests'] for r in rows),
            requests_with_token_counts=sum(r['metrics']['token_reporting_requests']['total_tokens'] for r in rows))
    # Resample project clusters, keeping the four paired repetitions together.
    paired={}
    for row in live['tasks']:
        if row['phase']=='evaluation':
            paired.setdefault(row['case'],{}).setdefault(row['repetition'],{})[row['arm']]=row['metrics']['reported_tokens']['total_tokens']
    case_deltas=[statistics.mean(v['new']-v['old'] for v in repetitions.values()) for repetitions in paired.values()]
    rng=random.Random(731)
    draws=sorted(statistics.mean(rng.choices(case_deltas,k=len(case_deltas))) for _ in range(10000))
    delta=dict(mean_tokens=statistics.mean(case_deltas),cluster_bootstrap95=[draws[249],draws[9749]],clusters=len(case_deltas))
    result=dict(live=groups,paired_token_difference_new_minus_old=delta,
        total_live_tasks=len(live['tasks']),total_live_tokens=sum(r['metrics']['reported_tokens']['total_tokens'] for r in live['tasks']),
        all_originals_unchanged=all(r['original_unchanged'] for r in live['tasks']),frozen_unchanged=live['frozen_unchanged'],
        training_saved_tasks=sum(r['result']['experience_saved'] is True for r in live['tasks'] if r['phase']=='training'),
        training_records=live['training_records'],
        semantic={mode:{split:v[split] for split in ['development','heldout']} for mode,v in semantic['results'].items()},
        old_semantic=system['old_pipeline'],embedding_retrieval=system['embedding_retrieval'],challenge=challenge['metrics'],
        capacity=release['capacity'],groups_passed=sum(g['passed'] for g in release['groups']),
        final_replay={k:v for k,v in release['replay'].items() if k!='rows'},
        environment={k:release[k] for k in ['python','platform','sqlite','packages']},
        source_archive_sha256=release['source_archive_sha256'],
        capacity_source_archive_sha256=release.get('capacity_source_archive_sha256',release['source_archive_sha256']))
    write_json(directory/'metrics.json',result)
    # Small trace table is shareable; full prompts/tool results remain in the local cache.
    write_json(directory/'trials.json',[dict(trial_id=r['trial_id'],phase=r['phase'],case=r['case'],arm=r['arm'],repetition=r['repetition'],
        outcome=r['outcome'],visible_passed=r['visible_passed'],heldout_passed=r['heldout_passed'],elapsed_seconds=r['elapsed_seconds'],
        total_tokens=r['metrics']['reported_tokens']['total_tokens'],decision_rounds=r['audit']['decision_rounds'],
        reference_count=len(r['audit']['retrieved_experience_ids']),experience_saved=r['result']['experience_saved']) for r in live['tasks']])
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.size':10,'axes.spines.top':False,'axes.spines.right':False,'figure.dpi':160})
    labels=['No memory','Old policy','New policy'];colors=['#64748b','#d97706','#0f766e']
    fig,axes=plt.subplots(1,3,figsize=(14,4.3))
    fig.subplots_adjust(left=.055,right=.985,bottom=.14,top=.76,wspace=.30)
    values=[100*groups[a]['passed']/groups[a]['tasks'] for a in groups]
    axes[0].bar(labels,values,color=colors,width=.6)
    axes[0].set_ylim(0,115);axes[0].set_ylabel('Heldout test pass rate (%)');axes[0].set_title('32 repairs per arm')
    for i,value in enumerate(values):axes[0].text(i,value+2,'32/32',ha='center')
    for ax,key,title in [(axes[1],'mean_total_tokens','Reported tokens\nMean per repair'),(axes[2],'mean_reference_characters','Reference characters\nMean per decision')]:
        values=[groups[a][key] for a in groups]
        ax.bar(labels,values,color=colors,width=.6);ax.set_title(title)
        for i,value in enumerate(values):ax.text(i,value+max(values)*.03,f'{value:,.0f}',ha='center')
        ax.set_ylim(0,max(values)*1.2)
    fig.suptitle('Real-model ablation: all tasks pass; memory coverage is 16/32 (Python only)',fontsize=12)
    fig.savefig(directory/'live-ablation.png');fig.savefig(directory/'live-ablation.svg');plt.close(fig)
    fig,axes=plt.subplots(1,3,figsize=(13,3.8),layout='constrained')
    semlabels=['Old','Deterministic','+ E5','+ NLI']
    semrows=[system['old_pipeline']['heldout'],semantic['results']['deterministic']['heldout'],semantic['results']['deterministic']['heldout'],semantic['results']['local_nli']['heldout']]
    axes[0].bar(semlabels,[v['false_conflicts'] for v in semrows],color=['#d97706','#64748b','#2563eb','#0f766e'])
    axes[0].set_title('False conflicts: 294 heldout pairs');axes[0].set_ylabel('Count')
    axes[1].bar(semlabels,[100*v['unknown_rate'] for v in semrows],color=['#d97706','#64748b','#2563eb','#0f766e'])
    axes[1].set_title('Unknown rate (%)');axes[1].set_ylim(0,100)
    sizes=[str(v['size']) for v in release['capacity']]
    axes[2].plot(sizes,[v['save_p95_ms'] for v in release['capacity']],marker='o',label='Save P95')
    axes[2].plot(sizes,[v['retrieval_p95_ms'] for v in release['capacity']],marker='o',label='Retrieval P95')
    axes[2].set_ylabel('Milliseconds');axes[2].set_xlabel('Sparse rule count');axes[2].set_title('Final-code capacity');axes[2].legend()
    fig.suptitle('Controlled fixtures measure policy behavior, not production accuracy',fontsize=12)
    fig.savefig(directory/'policy-and-capacity.png');fig.savefig(directory/'policy-and-capacity.svg');plt.close(fig)
    old,new=groups['old'],groups['new']
    token_reduction=100*(1-new['mean_total_tokens']/old['mean_total_tokens'])
    char_reduction=100*(1-new['mean_reference_characters']/old['mean_reference_characters'])
    lines=['# 自动经验整理优化：实验报告（2026-11-14）','',
        '**真实评估三组均为 32/32 通过，没有测出修复成功率提升。** 新策略相较旧策略平均已报告 token 减少 '
        f'{token_reduction:.1f}%，实际决策中的引用 JSON 长度减少 {char_reduction:.1f}%。结果支持引用压缩和处理安全性的改进，不能据此宣称能提升复杂项目的修复率。','',
        '![真实模型三组对照](live-ablation.png)','',
        '## 真实修复实验','',
        '使用已配置的 deepseek-flash，12 个训练任务和 96 个评估任务，总计 108 次，不重跑整项任务。每项上限 20 轮/600 秒，3 个工作线程；Java/Python 各 4 个评估项目，每个项目在三种策略下各重复 4 次。修复器、工具、任务和测试相同；提供商随机性未强制设种子。','',
        '| 指标 | 无经验 | 旧策略 | 新策略 |','|---|---:|---:|---:|']
    for label,key,formatting in [('原始及保留测试通过','passed',lambda v:f'{v}/32'),('有实际经验引用的任务','with_references',lambda v:f'{v}/32'),
        ('平均已报告 token','mean_total_tokens',lambda v:f'{v:,.0f}'),('平均决策轮数','mean_decisions',lambda v:f'{v:.2f}'),
        ('平均总耗时（秒）','mean_seconds',lambda v:f'{v:.2f}'),('平均引用字符/决策','mean_reference_characters',lambda v:f'{v:,.0f}')]:
        lines.append('| '+label+' | '+' | '.join(formatting(groups[a][key]) for a in groups)+' |')
    lo,hi=groups['new']['success_wilson95']
    coverage='、'.join(f'{label} {groups[arm]["requests_with_token_counts"]}/{groups[arm]["requests"]}' for label,arm in [('无经验','none'),('旧策略','old'),('新策略','new')])
    lines += ['',f'全部 108 次共报告 {result["total_live_tokens"]:,} tokens。评估请求的用量返回覆盖为：{coverage}。表格只汇总实际返回的用量，缺失请求未估算，因此不是完整账单；12.0% 是观察到的差值，置信区间包含 0，尚不能确认稳定节省。',
        f'32/32 的按运行计数 Wilson 95% 区间为 {lo:.1%}–{hi:.1%}；同项目重复并非独立任务，这个区间不能外推生产分布。按 8 个项目簇重采样的配对 token 差（新减旧）为 {delta["mean_tokens"]:,.0f}，探索性 95% 区间 [{delta["cluster_bootstrap95"][0]:,.0f}, {delta["cluster_bootstrap95"][1]:,.0f}]。并发执行、外部服务负载及输出随机性使耗时不适合当成严格提速证据。','',
        '训练阶段 12/12 修复通过，但仅 8 个任务成功保存、共 9 条经验。4 个 Java 提取因未核对的符号格式被拒绝；此问题已修复并单独回归，实验保留原始覆盖缺口，没有补造训练数据。两种记忆策略都只有 Python 的 16 个评估任务实际收到引用；Java 的缺失训练覆盖与精确异常类型筛选导致 0/16 命中。因此不能将总体 token 差当成 Java 复用效果。','',
        '20 个项目的原始失败基线均已独立验证。保留测试位于工作区之外，修复后才复制到独立验证项目执行；它们覆盖额外输入，但与原始测试共享契约模板，不代表任意隐藏业务需求。原项目哈希和两个冻结数据库哈希均保持不变；评估输出写入另一个库。','',
        '## 语义消融与补充反例','',
        '600 对规则由明确契约生成，开发集 306 对使用英文模板，保留集 294 对使用不同的中文模板。仅 6 个基础语义模板、变量名变体较多；数字并非独立自然语料样本。','',
        '| 保留集指标 | 旧策略 | 确定性 | 确定性 + E5 | 加双向 NLI |','|---|---:|---:|---:|---:|']
    for label,key in [('分类准确率','accuracy'),('unknown 比例','unknown_rate'),('误合并','false_merges'),('误报冲突','false_conflicts'),('漏识别冲突','missed_conflicts')]:
        format_value=lambda v:f'{v:.1%}' if isinstance(v,float) else str(v)
        lines.append('| '+label+' | '+' | '.join(format_value(v[key]) for v in semrows)+' |')
    lines += ['', 'E5 改变召回排名，不改变确定性关系判定，因此这两组关系指标相同。独立的 100 个同义查询对 200 个候选：Recall@1=84%，Recall@5/10=100%；代码事实检查不会被相似度绕过。',
        'NLI 的保留集等价 98/98、冲突 49/49 的按样本 Wilson 下界分别为 '
        f'{wilson(98,98)[0]:.1%} 和 {wilson(49,49)[0]:.1%}，不是“生产准确率至少 99%”的证据。阈值仅在开发集选择，保留集与额外挑战集作为启用门槛。','',
        '额外 16 对手工契约反例发现两次高分误合并：`x >= 10` 与 `x > 10`（0.993），`x == 0` 与 `x != 0`（0.970）。加入操作数、比较运算符与布尔连接保护后，误合并 2→0、误报冲突仍为 0；unknown 8→10，7 个真实冲突只识别 3 个，其余 4 个待审阅。修复前报告保留，没有通过删除难例或只报告精度隐藏漏检。','',
        '![策略消融与容量](policy-and-capacity.png)','',
        '## 最终代码、故障与容量','',
        f'最终版本独立重跑 {result["groups_passed"]}/40 组流程，覆盖重复、61 条密集组、版本、路径大小写、限制保留、局部暂停、依赖和撤销。事务、过期租约、并发决定、旧记录及 Hypothesis 状态序列另由回归测试覆盖。','',
        '| 经验数 | 保存 P95/ms | 整理秒 | 检索 P95/ms | 数据库 MiB | 原始/规则/证据 |','|---:|---:|---:|---:|---:|---|']
    for v in release['capacity']:
        lines.append(f'| {v["size"]:,} | {v["save_p95_ms"]:.1f} | {v["queue_seconds"]:.1f} | {v["retrieval_p95_ms"]:.1f} | {v["database_bytes"]/1024**2:.2f} | {v["counts"]["experiences"]}/{v["counts"]["knowledge_rules"]}/{v["counts"]["knowledge_evidence"]} |')
    lines += ['', '所有容量作业最终完成，integrity_check 均为 ok。容量样本为互不重叠文件，未运行 NLI；密集同域仍可能平方级比较。机器为 Windows、Python '+release['python']+'、SQLite '+release['sqlite']+'，默认回滚日志；详细包版本见 metrics.json。','',
        f'最终代码对实际新策略的 {release["replay"]["trials"]} 项任务、{release["replay"]["calls"]} 次查询重放，引用完全一致 {release["replay"]["identical"]} 次。重放不调用修复模型；以最终源码快照、真实训练库和独立请求审计为依据。最初实验工具的可变 error_types 引用已改为深复制，历史重放采用 Agent 当时记录的逐调用快照。','',
        '最终回归：后端 613 项通过、1 项跳过；前端 131 项通过，生产构建通过；接口集成检查通过，V3 端到端验收 13 项任务通过。后两项采用脚本化模型，不计入 108 次真实模型实验。','',
        '## 复现与原始产物','',
        '- [中文效果总览](effect-overview-zh.png)；[API 来源复核与后续配置修复](api-configuration-audit.md)。历史调用未记录逐请求凭据指纹，来源复核不冒充历史请求证据。',
        '- 汇总数据：[metrics.json](metrics.json)；108 项精简结果：[trials.json](trials.json)。图表同时提供 SVG。',
        '- 完整真实记录：`.cache/knowledge-live-experiment/`；冻结旧代码及两个数据库均保留。',
        '- 语义样本及分数：`.cache/knowledge-semantic-experiment/`；补充反例：`.cache/knowledge-adversarial-experiment/`。',
        '- 最终代码快照、容量库及重放：`.cache/knowledge-release-validation/`，源快照 SHA-256 记录在 metrics.json。',
        '- 命令和官方依据见 [修复记录](../../experience-consolidation-remediation.md) 与 [使用说明](../../experience-consolidation.md)。','',
        '结论限于这些数据：错误冲突与边界误合并得到抑制，证据和来源不再链式复制，后台故障可恢复且审阅可撤销；真实任务未测出成功率提升，记忆覆盖、语义漏检和密集场景成本仍须继续观察。','']
    (directory/'report.md').write_text('\n'.join(lines),encoding='utf-8')
    print(json.dumps({'live':groups,'token_reduction_percent':token_reduction,'reference_reduction_percent':char_reduction},ensure_ascii=False))


if __name__=='__main__':main()
