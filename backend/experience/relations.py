"""Separate candidate similarity, applicability, and behavioral contradiction."""
import re

from backend.projects import same_source_revision
from .knowledge import canonical, rule_text

RELATION_POLICY_VERSION = 'rules-v4-action-entailment'


def overlaps(left, right):
    a, b = left['issue'], right['issue']
    if a.get('symbols') and b.get('symbols'):
        return bool(set(a['symbols']) & set(b['symbols']))
    return not a['files'] or not b['files'] or bool(set(a['files']) & set(b['files']))


def protected_facts(text):
    # Comparisons include their operands: a set of bare numbers/operators would
    # lose the distinction between x<0 / y>10 and x>0 / y<10.
    literals=re.findall(r'[`\"\'][^`\"\'\n]*[`\"\']|(?<![\w])[-+]?\d+(?:\.\d+)?(?![\w])',text)
    comparisons=re.findall(r'[\w.\[\]]+\s*(?:==|!=|<=|>=|<|>)\s*[-+]?[\w.\[\]]+',text)
    operators=re.findall(r'==|!=|<=|>=|&&|\|\||(?<!<)<(?!<)|(?<!>)>(?!>)',text)
    if comparisons:
        operators+=re.findall(r'\b(?:and|or|not)\b',text)
    return sorted(set([*literals,*operators,*(re.sub(r'\s+','',value) for value in comparisons)]))


def protected_literals(data):
    return protected_facts(rule_text(data))


def source_derived_compatibility(connection, rule_id, compatibility_key):
    """Prove derivation from immutable sources, never from a key's shape alone."""
    rows = connection.execute(
        "SELECT json_extract(e.details,'$.source_revision'), json_extract(e.details,'$.compatibility_key') "
        "FROM knowledge_sources s JOIN experiences e ON e.id=s.experience_id WHERE s.rule_id=?", (rule_id,)).fetchall()
    return bool(rows) and all(same_source_revision(compatibility_key, revision)
        and (contract is None or same_source_revision(contract, revision)) for revision, contract in rows)


def classify(left, right, semantic=None, *, verified_source_compatibility=False):
    same_key = left['compatibility_key'] == right['compatibility_key']
    same_content = verified_source_compatibility and same_source_revision(left['compatibility_key'], right['compatibility_key'])
    if any(left[k] != right[k] for k in ('language','repository_id','scope')) or not (same_key or same_content):
        return 'different_scope', {'reason':'语言、仓库或兼容域不同'}
    if not overlaps(left, right):
        return 'compatible', {'reason':'代码目标范围不重叠'}
    # The stored fingerprints and rule payloads remain unchanged. Only this
    # proven same-content comparison disregards historical Git metadata.
    comparable = left if same_key else left | {'compatibility_key': right['compatibility_key']}
    if canonical(comparable) == canonical(right):
        return 'equivalent', {'reason':'完整规则和条件精确相同', 'method':'exact'}
    if left['required_capabilities'] != right['required_capabilities']:
        return 'unknown', {'reason':'能力要求不同，未证明适用条件相交'}
    a, b = left['issue'], right['issue']
    # A different error type is not proof of compatibility: the same target may still
    # be one root cause described two ways, so the pair stays for review.
    # Identical behavior under identical qualifiers is compatible, even if the explanation differs.
    same_conditions = left['applicability'] == right['applicability'] and left['constraints'] == right['constraints']
    if same_conditions and a['solution'] == b['solution']:
        return 'compatible', {'reason':'相同条件下的行为一致，根因解释不同'}
    if semantic is None:
        return 'unknown', {'reason':'需要语义审阅；文字差异不构成矛盾证据'}
    conditions_a = '\n'.join([*left['applicability'], *left['constraints']])
    conditions_b = '\n'.join([right['applicability'][i] for i in range(len(right['applicability']))] + right['constraints'])
    condition_scores = None if same_conditions else semantic.infer(conditions_a, conditions_b)
    eq_threshold = semantic.threshold('equivalent')
    cf_threshold = semantic.threshold('conflict')
    equivalent_conditions = same_conditions or bool(condition_scores and eq_threshold is not None
                                                     and protected_facts(conditions_a)==protected_facts(conditions_b)
                                                     and condition_scores['entailment'] >= eq_threshold)
    scores = semantic.infer(rule_text(left), rule_text(right))
    details = {'reason':'双向自然语言推断', 'scores':scores, 'condition_scores':condition_scores,
               'model_version':semantic.version, 'method':'nli'}
    if scores is None:
        return 'unknown', details | {'reason':'模型不可用或完整条件超过模型窗口'}
    if (eq_threshold is not None and equivalent_conditions and protected_literals(left) == protected_literals(right)
            and scores['entailment'] >= eq_threshold):
        # Shared task constraints can dominate the full-text score. The actual
        # repair must independently entail its peer in both directions too.
        same_target = all(a.get(k) == b.get(k) for k in ('error_type', 'files', 'symbols'))
        same_action_facts = protected_facts(a['solution']) == protected_facts(b['solution'])
        action_scores = semantic.infer(a['solution'], b['solution']) if same_target and same_action_facts else None
        details['action_scores'] = action_scores
        if action_scores and action_scores['entailment'] >= eq_threshold:
            return 'equivalent', details
        return 'unknown', details | {'reason':'修复动作、错误类型或代码目标未独立通过等价检查'}
    if cf_threshold is not None and equivalent_conditions and scores['contradiction'] >= cf_threshold:
        return 'conflict', details
    return 'unknown', details | {'reason':'未达到经领域校准的自动判定门槛'}
