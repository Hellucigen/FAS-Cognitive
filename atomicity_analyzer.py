# atomicity_analyzer.py — 图谱原子化程度检测器
# ============================================================================
# 规范（FAS 原子化重构）第十四节：只检测、不删除。
# 输出每节点: node / atomicity_score / suspected_components / reason /
#             recommended_action / confidence / suggested_canon
#
# 原子性判据（第二节）：可独立激活/可独立衰减/可独立建关系/可独立学习。
# 判据是"认知语义单位"，不是字数——焦糖玛奇朵/环世界 合法，句子型非法。
# ============================================================================

import re

# A. 句子型开头特征
_SENT_PREFIX = re.compile(r'^(用户|我|她|他|因为|所以|正在|准备|由于|感觉|觉得|事件[:：]|如果|虽然)')
# E/D. 事件+结果 / 状态+原因 混合词特征
_MIX_TAIL = re.compile(r'(结束|失败|成功|完成|后感想|无动静|看错规则)$')
# B. 命名式事件（9-2 规范 {活动}—{方面}—{实体}）
_NAMED_EVENT = re.compile(r'^[\u4e00-\u9fa5A-Za-z0-9]+—[\u4e00-\u9fa5A-Za-z0-9]+—')
# C. 过程/机制节点（label 判定为主，词面辅助）
_PROCESS_WORDS = re.compile(r'^(生成|处理|检查|执行|进行|等待)')
# 合法长实体白名单特征：专有名（字母数字/品牌/地名）通常安全
_PROPER = re.compile(r'[A-Za-z0-9]')

CANON_BY_LABEL = {
    'procedural': 'action',
    'infrastructure': 'capability',
    'intention': 'intention_instance',
    'disposition': 'cognitive_state',
}


def analyze_node(node, out_edges=None) -> dict:
    """对单节点给出原子性评估。out_edges: [(relation, dst)] 用于辅助判断。"""
    nid = node.get('id', '')
    label = node.get('label', '')
    space = node.get('graph_space', '')
    ea = node.get('extra_attrs') or {}
    out_edges = out_edges or []
    score, comps, reason, rec, conf = 1.0, [], '', 'keep', 0.5
    canon = CANON_BY_LABEL.get(label, {
        'semantic': 'concept', 'episodic': 'event_instance',
        'cognitive': 'cognitive_state', 'self': 'cognitive_state'}.get(space, 'concept'))

    # 机制记录节点：独立类别，不算复合
    if re.match(r'^(回答记录_|搜索记录_|文件操作记录_|反思_|思考_|CI_|eye_text_|ActionIntent_)', nid):
        return {'node': nid, 'atomicity_score': 0.9, 'suspected_components': [],
                'reason': '机制记录/意图实例节点（有独立类型标记）',
                'recommended_action': 'keep', 'confidence': 0.9,
                'suggested_canon': canon}

    # A. 句子型（含 事件: 前缀的情绪注入句）
    if nid.startswith('事件:') or _SENT_PREFIX.match(nid):
        if len(nid) >= 8:
            score = 0.25
            comps = re.split(r'[，,。：:\s]+', re.sub(r'^事件[:：]\s*', '', nid))
            comps = [c for c in comps if len(c) >= 2]
            reason = '句子型节点：混合事件/状态/情绪多个语义单位，无法独立激活各成分'
            # 已有原子等价物时可直接合并
            rec = 'merge_to_atomic'
            conf = 0.8

    # D/E. 结果/状态混合尾缀（仅 episodic，非命名式）
    elif label == 'declarative-episodic' and space == 'episodic' \
            and not _NAMED_EVENT.match(nid):
        m = _MIX_TAIL.search(nid)
        if m:
            score = 0.55
            comps = [m.group(1), nid[:nid.find(m.group(1))]]
            reason = '事件+结果混合命名：状态变化与事件本体绑在一名'
            rec = 'keep_with_log'   # 争议项：信息唯一，保守保留
            conf = 0.55

    # B. 命名式事件（9-2 规范）：本质是 EventInstance 的可读 ID + 槽位边，结构已原子
    elif _NAMED_EVENT.match(nid):
        score = 0.8
        comps = nid.split('—')
        reason = '命名式事件实例：活动—方面—实体；已通过槽位边表达结构，ID 可读非复合语义'
        rec = 'keep'
        conf = 0.7

    # C. 过程节点
    elif label in ('procedural', 'infrastructure'):
        score = 0.85
        reason = '动作/能力/机制节点（规范十一允许图化），label 已区分类型'
        rec = 'keep'
        conf = 0.8

    # 长句式概念（非事件空间，无分隔符，含多个虚词）
    elif space == 'semantic' and len(nid) >= 12 and not _PROPER.search(nid) \
            and re.search(r'(的|了|与|和|对)', nid):
        score = 0.45
        reason = '疑似主题句式概念：可作为主题标签，但成分无法独立激活'
        rec = 'keep_with_log'
        conf = 0.5

    return {'node': nid, 'atomicity_score': score, 'suspected_components': comps,
            'reason': reason, 'recommended_action': rec, 'confidence': conf,
            'suggested_canon': canon}


def analyze_graph(graph: dict) -> list:
    """对整个图出审计报告，按原子分升序（最复合的在前）。"""
    nodes = graph['nodes']
    out_adj = {}
    for e in graph['edges']:
        out_adj.setdefault(e.get('src'), []).append((e.get('relation'), e.get('dst')))
    report = [analyze_node(n, out_adj.get(n.get('id'), [])) for n in nodes]
    report.sort(key=lambda r: r['atomicity_score'])
    return report
