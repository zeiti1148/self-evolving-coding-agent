// Experience walkthrough is bound to a verified real request and the SQLite snapshot.
const reuseLabels = ['结构化经验', '检索匹配', '注入模型', '用于新修复', '继续积累'];
const fieldInfo = {
  task_summary: ['任务摘要', '库存转移的校验与状态一致性', '用一句话记录这条经验解决过什么任务，帮助模型理解适用场景。'],
  error_type: ['异常类型', 'IllegalArgumentException', '存储标准 Java 异常类名，作为当前 Agent 的精确检索条件。提取结果经 normalize_error_type 归一化。'],
  root_cause: ['问题根因', '先扣源仓，随后才检查目标容量', '记录导致故障的原因，说明为什么原来的实现会出错。'],
  solution: ['修复方法', '先完成校验，再修改两边状态', '记录可迁移的修复规则。下一次任务把这段内容作为参考，根据新项目的类和业务规则生成修改。'],
  verification: ['验证依据', '12 项 Maven 测试通过', '由程序根据独立最终测试写入，带测试数量和验证调用 ID。'],
  tags: ['检索标签', 'java · validation · atomicity', '用于筛选技术环境和问题主题。当前 Agent 固定要求 java 标签。'],
  source_task_id: ['来源任务', '关联库存修复任务', '由程序绑定来源任务，能够从经验追到这次修复。'],
  id: ['经验编号', '贯穿存储、检索、模型请求', '保存时生成 UUID。同一编号在检索结果和模型请求中出现，串起完整复用路径。'],
  created_at: ['保存时间', '用于按新旧排序', '保存时生成 UTC 时间；检索按创建时间、ID 降序排列，最多返回 5 条。'],
};

function reuseFieldSchema(host, r) {
  heading(host, '', '一次成功修复，沉淀成 9 个字段', '模型提炼修复知识；程序补齐验证和来源，经 Pydantic 校验后写入 SQLite。');
  const groups = el('div','field-groups');
  const detail = el('div','field-detail');
  const buttons = [];
  function select(key) {
    buttons.forEach(([k,b])=>b.classList.toggle('selected',k===key));
    const actual=['error_type','id','source_task_id'].includes(key)?r.source_experience[key]:key==='tags'?r.source_experience.tags.join(' · '):key==='created_at'?new Date(r.source_experience.created_at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}):fieldInfo[key][1];
    detail.replaceChildren(el('strong','',fieldInfo[key][0]+' · '+key),el('p','field-example','本条经验：'+actual),el('p','',fieldInfo[key][2]));
    details(detail,'查看本字段的已保存原文',typeof r.source_experience[key]==='string'?r.source_experience[key]:JSON.stringify(r.source_experience[key],null,2));
  }
  for(const [title,keys] of [['用于查找',['error_type','tags']],['用于复用',['task_summary','root_cause','solution']],['用于追溯',['verification','source_task_id','id','created_at']]]) {
    const group=el('div','field-group'); group.append(el('h3','',title));
    for(const key of keys) {
      const b=el('button','field-button');b.append(el('strong','',fieldInfo[key][0]),el('code','',key));b.onclick=()=>select(key);group.append(b);buttons.push([key,b]);
    } groups.append(group);
  } host.append(groups,detail);select('solution');
  const pipeline=el('div','reuse-pipeline');
  for(const t of ['修复轨迹 + 测试结果','模型提炼 JSON','校验与补齐字段','SQLite'])pipeline.append(el('span','',t));host.append(pipeline);
}

function reuseSearch(host,r) {
  heading(host,'','新任务来了，先从经验库找参考','积分转赠失败后发送方仍被扣分；任务描述包含 IllegalArgumentException。');
  const query=el('div','query-banner');
  query.append(el('strong','','本次实际检索'),el('code','',`tags = ["java"]  ·  error_type = "${r.query.error_type}"  ·  limit = ${r.query.limit}`));host.append(query);
  const cards=el('div','retrieval-cards');const ids=new Set(r.matched_experiences.map(e=>e.id));
  for(const exp of r.experiences_before) {
    const matched=ids.has(exp.id),c=el('div','retrieval-card'+(matched?' matched':''));
    c.append(el('span','match-label',matched?'✓ 命中':'异常类型不同'),el('h3','',exp.id===r.source_experience.id?'库存转移经验':'订单结算经验'),el('code','',exp.error_type),el('p','',exp.id===r.source_experience.id?'先校验全部约束，再修改状态':'数量、折扣与运费的计算规则'),el('small','',`java 标签：${exp.tags.includes('java')?'匹配':'未匹配'} · ID ${exp.id.slice(0,8)}`));cards.append(c);
  }host.append(cards);
  host.append(el('div','callout good',`${r.experiences_before.length} 条已有经验 → 按异常类型和 Java 标签筛选 → 命中 ${r.matched_experiences.length} 条`));
  details(host,'查看检索调用',`search_experiences("", tags=["java"], error_type="${r.query.error_type}", limit=${r.query.limit})\n\n排序：created_at DESC, id DESC\n返回经验 ID：${r.matched_experiences.map(e=>e.id).join(', ')}`);
}

function reuseInjection(host,r) {
  heading(host,'','把经验正文放进模型这一次的上下文','新任务描述和命中的经验一起发送，模型读取根因与方案，再生成工具调用。');
  const grid=el('div','injection-grid');
  const task=el('div','injection-card');task.append(el('span','pill','task'),el('h3','','积分转赠任务'),el('p','','接收方关闭收礼时，应保留发送方积分；成功时发送方承担赠送积分和手续费。'));
  const exp=el('div','injection-card knowledge');exp.append(el('span','pill','reference_experiences[0]'),el('h3','','库存修复经验'),el('p','','根因：状态修改发生在后续校验之前。'),el('p','','方案：先校验全部前置条件，再修改状态。'),el('small','','经验 ID：'+r.source_experience.id.slice(0,8)));
  grid.append(task,el('div','injection-plus','+'),exp,el('div','injection-plus','→'),el('div','model-box','模型决策\n生成工具调用'));host.append(grid);
  const proof=el('div','request-proof');
  proof.append(el('strong','',`成功请求已携带 ${r.request.user_content.reference_experiences.length} 条经验`),el('span','',`HTTP ${r.request.event.http_status} · 经验 ID 与正文 SHA-256 对应一致`));host.append(proof);
  const code=`{"role": "user", "content": json.dumps({
  "task": request.task_description,
  "max_decisions": request.max_steps,
  "reference_experiences": reference_data
}, ensure_ascii=False)}`;
  host.append(el('pre','terminal',code));
  details(host,'展开该次模型请求的真实任务与经验正文',JSON.stringify(r.request.user_content,null,2));
}

function reuseApply(host,r) {
  const t=r.target, edit=repairEdits(t).find(e=>shortFile(e.path)==='PointsGiftService.java');
  heading(host,'','把“先校验、再改状态”用到积分转赠','库存案例检查目标容量；积分案例检查接收权限和发送方余额，另处理手续费。');
  const bridge=el('div','reuse-bridge');bridge.append(el('span','','旧经验：先校验目标约束'),el('b','','→'),el('strong','','新代码：先检查接收方是否允许收礼'));host.append(bridge);
  if(edit) {
    const pair=el('div','code-pair');
    for(const [after,label,source] of [[false,'修复前',edit.before],[true,'模型收到经验后的修复',edit.after]]) {
      const card=el('div','code-card');card.append(el('div','code-title '+(after?'after':'before'),label));const code=el('div','excerpt');
      source.replaceAll('\r\n','\n').split('\n').forEach((line,i)=>{if(/recipient\.acceptsGifts|sender\.debit|recipient\.credit/.test(line)){const row=el('div','code-line mark'+(after?'':' bad'));row.append(el('span','ln',String(i+1)),el('code','',line.trimStart()));code.append(row)}});card.append(code);pair.append(card);
    }host.append(pair);
    const d=el('details');d.append(el('summary','','展开 PointsGiftService 的完整修改'));drawDiff(d,edit.before,edit.after);host.append(d);
  }
  const final=finalTest(t)?.result.metadata;
  host.append(el('div','reuse-result',`发送方：1000 → 1000 分　｜　接收方：100 → 100 分　｜　${final?.tests} 项测试全部通过`));
}

function reuseGrowth(host,r) {
  const before=r.experiences_before.length,after=r.experiences_after.length;
  heading(host,'','新的成功修复，再变成下一条经验','积分转赠经验已保存，后续相似任务可继续检索这些记录。');
  const growth=el('div','growth-row');
  for(const [label,value] of [['开始时的经验库',before],['本次命中',r.matched_experiences.length],['新增成功经验',after-before],['完成后的经验库',after]]){const card=el('div','growth-card');card.append(el('span','',label),el('strong','',String(value)),el('small','','条'));growth.append(card)}host.append(growth);
  const loop=el('div','knowledge-loop');for(const label of ['修复任务','测试验证','提炼经验','存入知识库','检索并注入下一任务'])loop.append(el('span','',label));host.append(loop);
  host.append(el('div','callout good','项目核心：把修复过程沉淀为知识，让知识进入下一次修复。'));
  details(host,'查看这次新增的积分转赠经验',JSON.stringify(r.target.experience,null,2));
}

function renderReuseScene(scene,host) {
  [reuseFieldSchema,reuseSearch,reuseInjection,reuseApply,reuseGrowth][scene](host,recording.reuse);
}
