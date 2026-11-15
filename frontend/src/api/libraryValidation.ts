import type { ExperienceLibrary, HistoryScan, ExperienceRecovery } from './contracts'
import { readExperiences } from './validation'
const uuid=(v:unknown)=>typeof v==='string'&&/^[\da-f]{8}-(?:[\da-f]{4}-){3}[\da-f]{12}$/i.test(v)
function object(v:unknown):Record<string,unknown>{if(!v||typeof v!=='object'||Array.isArray(v))throw new Error('Invalid library object');return v as Record<string,unknown>}
function count(v:unknown){if(!Number.isSafeInteger(v)||Number(v)<0)throw new Error('Invalid library count')}
export function readHistoryScan(value:unknown):HistoryScan {
  const d=object(value)
  if(!uuid(d.id)||!['pending','running','succeeded','failed'].includes(String(d.state))||typeof d.notice!=='string')throw new Error('Invalid scan')
  for(const k of ['scanned','scheduled','deferred','groups','sources'])count(d[k])
  for(const k of ['created_at','updated_at'])if(typeof d[k]!=='string'||!Number.isFinite(Date.parse(d[k] as string)))throw new Error('Invalid date')
  return d as unknown as HistoryScan
}
export function readExperienceLibrary(value:unknown):ExperienceLibrary {
  const d=object(value),summary=object(d.summary)
  for(const k of ['original_records','knowledge_cards','active','paused','unverified','merged','pending_reviews'])count(summary[k])
  if(!Array.isArray(d.items))throw new Error('Invalid cards')
  const seen=new Set<string>()
  for(const raw of d.items){
    const c=object(raw)
    if(!uuid(c.id)||seen.has(String(c.id))||!['rule','archive','principle','validated'].includes(String(c.kind))||!['active','paused','unverified'].includes(String(c.state)))throw new Error('Invalid card identity')
    seen.add(String(c.id))
    for(const k of ['title','summary','root_cause','reason','error_type','created_at'])if(typeof c[k]!=='string')throw new Error('Invalid card text')
    if(!['java','python','go','cpp'].includes(String(c.language)))throw new Error('Invalid card language')
    for(const k of ['applicability','constraints'])if(!Array.isArray(c[k])||!(c[k] as unknown[]).every(v=>typeof v==='string'))throw new Error('Invalid conditions')
    if(!Array.isArray(c.source_ids)||!c.source_ids.every(uuid)||new Set(c.source_ids).size!==c.source_ids.length)throw new Error('Invalid sources')
    count(c.source_count);count(c.evidence_count)
    const sources=readExperiences(c.sources)
    if(c.source_count!==c.source_ids.length||!sources.length||sources.some(s=>!(c.source_ids as string[]).includes(s.id))||typeof c.merged!=='boolean')throw new Error('Invalid provenance')
    if(c.kind==='archive'&&(c.state!=='unverified'||c.evidence_count!==0||c.merged!==true))throw new Error('Archive must stay unverified')
    if(c.kind==='validated'&&(c.state!=='active'||c.reuse_mode!=='scoped'||!c.reuse_scope||Number(c.evidence_count)<1))throw new Error('Invalid recovered evidence')
    for(const field of ['repository_ids','project_labels','tags','files','symbols'])if(c[field]!==undefined&&(!Array.isArray(c[field])||!(c[field] as unknown[]).every(v=>typeof v==='string')))throw new Error('Invalid catalog metadata')
  }
  if(d.total!==undefined){count(d.total);count(d.offset);if(d.items.length>Number(d.total))throw new Error('Invalid pagination')}
  else if(d.items.length!==summary.knowledge_cards)throw new Error('Invalid summary')
  if(d.facets!==undefined)for(const values of Object.values(object(d.facets))){if(!Array.isArray(values))throw new Error('Invalid facets');for(const raw of values){const facet=object(raw);if(typeof facet.value!=='string'||typeof facet.label!=='string')throw new Error('Invalid facet');count(facet.count)}}
  if(d.history_scan!==null)readHistoryScan(d.history_scan)
  return d as unknown as ExperienceLibrary
}

export function readRecovery(value:unknown):ExperienceRecovery {
  const d=object(value)
  if(!uuid(d.id)||!uuid(d.card_id)||!['pending','running','waiting_task','needs_evidence','needs_confirmation','succeeded','failed','invalidated','kept'].includes(String(d.state)))throw new Error('Invalid recovery')
  if(typeof d.reason!=='string'||!['system','user'].includes(String(d.actor))||typeof d.can_confirm!=='boolean'||d.task_id!==null&&!uuid(d.task_id))throw new Error('Invalid recovery state')
  count(d.version)
  for(const key of ['created_at','updated_at'])if(typeof d[key]!=='string'||!Number.isFinite(Date.parse(d[key] as string)))throw new Error('Invalid recovery date')
  const before=object(d.before);if(!['active','paused','unverified'].includes(String(before.state))||typeof before.summary!=='string')throw new Error('Invalid previous state')
  count(before.evidence_count)
  if(!Array.isArray(d.steps)||!d.steps.every(v=>{const e=object(v);return typeof e.stage==='string'&&typeof e.reason==='string'&&typeof e.at==='string'&&Number.isFinite(Date.parse(e.at))}))throw new Error('Invalid recovery steps')
  if(!Array.isArray(d.runs)||!Array.isArray(d.review_ids)||!d.review_ids.every(uuid))throw new Error('Invalid recovery evidence')
  const ids=new Set<string>()
  for(const value of d.runs){const run=object(value);if(!uuid(run.id)||ids.has(String(run.id))||!uuid(run.target_source_id)||!uuid(run.implementation_source_id)||!['passed','failed','invalid'].includes(String(run.outcome)))throw new Error('Invalid test run');ids.add(String(run.id));for(const k of ['tests','failures','errors','skipped','repeat'])count(run[k]);if(!Array.isArray(run.failed_tests)||!run.failed_tests.every(v=>typeof v==='string')||!(/^[a-f0-9]{64}$/.test(String(run.artifact_sha256))))throw new Error('Invalid test proof')}
  if(d.candidate)readExperiences([d.candidate])
  if(d.candidate_rule_index!==undefined&&d.candidate_rule_index!==null)count(d.candidate_rule_index)
  if(d.after){const after=object(d.after);if(after.state!=='active'||after.reuse_mode!=='scoped'||!uuid(after.source_id)||typeof after.summary!=='string')throw new Error('Invalid recovered state');count(after.evidence_count)}
  if(d.scope){const scope=object(d.scope);for(const field of ['project_id','project_label','repository_id','compatibility_key','source_revision','runner_hash'])if(typeof scope[field]!=='string'||!scope[field])throw new Error('Invalid reuse boundary')}
  if(d.state==='succeeded'&&(!d.after||!d.scope||d.runs.length<2))throw new Error('Recovery lacks evidence')
  if(d.state==='succeeded'||d.can_confirm){
    if(d.runs.length!==2||d.runs.some(raw=>{const r=object(raw);return r.outcome!=='passed'||Number(r.tests)<1||r.failures!==0||r.errors!==0||r.skipped!==0})
      ||object(d.runs[0]).test_identity!==object(d.runs[1]).test_identity)throw new Error('Recovery evidence is not stable')
  }
  return d as unknown as ExperienceRecovery
}
export function readRecoveries(value:unknown):ExperienceRecovery[]{if(!Array.isArray(value))throw new Error('Invalid recovery history');return value.map(readRecovery)}
