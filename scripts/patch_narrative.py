# patch_narrative.py — 叙事分段抽取：模板+方法+接线（一次性补丁）
import ast

# ── 1. prompt_templates.py: NARRATIVE_EXTRACT 模板 ──
p = open('prompt_templates.py', encoding='utf-8').read()
if 'NARRATIVE_EXTRACT' not in p:
    tpl = '''
# ── 叙事分段抽取（长叙事输入：电影剧情/故事） ──────────────

NARRATIVE_EXTRACT = FAS_IDENTITY + "\\n\\n" + """【任务】叙事分段抽取——把一段故事/剧情拆解为按序事件实例

输入是一段叙事（电影剧情、故事、小说片段）。把它拆成按时间顺序的
事件节拍（beats），每个节拍是一个独立事件实例。

【硬性规则】
- 事件按叙事顺序编号，每个事件一个短标题（不超过 12 字）
- 每个事件列出参与者（角色名，原子化：不合并、不加修饰）
- 角色名必须原子化（"弟弟"“神秘老人”这类稳定称谓可以，形容词修饰去掉）
- summary 是该节拍的一句话客观描述，不加评价
- story：如果原文指明作品名/故事名则填，否则填 null
- 不要编造原文没有的情节；不要把整个故事压成一个事件

""" + OUTPUT_JSON + """

输出格式：
{
  "story": "作品名" 或 null,
  "characters": ["角色A", "角色B"],
  "events": [
    {"title": "短标题", "actors": ["角色A"], "summary": "一句话", "entities": ["关键物"]}
  ]
}"""

'''
    anchor = '# ── 记忆抽取 ──'
    assert anchor in p
    p = p.replace(anchor, tpl + anchor, 1)
    p = p.replace('    "file_action_extract": FILE_ACTION_EXTRACT,\n    "proactive_express": PROACTIVE_EXPRESS,\n}',
                  '    "file_action_extract": FILE_ACTION_EXTRACT,\n    "proactive_express": PROACTIVE_EXPRESS,\n    "narrative_extract": NARRATIVE_EXTRACT,\n}')
    open('prompt_templates.py', 'w', encoding='utf-8').write(p)
import ast
ast.parse(open('prompt_templates.py', encoding='utf-8').read())
print('prompt ok')

# ── 2. nlp_processor.py: extract_narrative 方法 ──
n = open('nlp_processor.py', encoding='utf-8').read()
if 'def extract_narrative' not in n:
    anchor = '    def answer_question(self,'
    assert anchor in n
    method = '''    @staticmethod
    def split_narrative(text: str, chunk_size: int = 600) -> list:
        """长叙事按句子边界切分为 ≤chunk_size 的段。"""
        import re as _re
        sents = _re.split(r'(?<=[。！？!?\\n])', text.strip())
        chunks, cur = [], ''
        for s in sents:
            if not s.strip():
                continue
            if len(cur) + len(s) > chunk_size and cur:
                chunks.append(cur)
                cur = s
            else:
                cur += s
        if cur.strip():
            chunks.append(cur)
        return chunks

    def extract_narrative(self, text: str) -> dict:
        """叙事分段抽取：长文本按段调用 NARRATIVE_EXTRACT，合并事件序列。"""
        from langchain_core.prompts import ChatPromptTemplate
        from prompt_templates import build_prompt
        chunks = self.split_narrative(text)
        story, characters, events = None, [], []
        system = build_prompt("narrative_extract")
        for ci, chunk in enumerate(chunks):
            try:
                prompt = ChatPromptTemplate.from_messages(
                    [("system", system),
                     ("human", "【第 {{idx}} 段（共 {{total}} 段）】\\n{{chunk}}")],
                    template_format="mustache")
                resp = (prompt | self.chat_llm).invoke(
                    {"idx": str(ci + 1), "total": str(len(chunks)), "chunk": chunk})
                raw = resp.content if hasattr(resp, 'content') else str(resp)
                if raw.startswith("```"):
                    raw = "\\n".join(raw.split("\\n")[1:-1])
                data = json.loads(raw.strip())
            except Exception as e:
                logger.warning(f"[Narrative] 段 {ci+1} 抽取失败: {e}")
                continue
            if data.get("story") and not story:
                story = data["story"]
            for c in data.get("characters") or []:
                if c and c not in characters:
                    characters.append(c)
            for ev in data.get("events") or []:
                if isinstance(ev, dict) and ev.get("title"):
                    ev["_chunk"] = ci
                    events.append(ev)
        return {"story": story, "characters": characters, "events": events,
                "chunks": len(chunks)}

''' + anchor
    n = n.replace(anchor, method, 1)
    open('nlp_processor.py', 'w', encoding='utf-8').write(n)
ast.parse(open('nlp_processor.py', encoding='utf-8').read())
print('nlp_processor ok')

# ── 3. app.py: 叙事触发分支 + 图构建函数 ──
a = open('app.py', encoding='utf-8').read()
if '_ingest_narrative' not in a:
    # 3a. 图构建函数（放 _inject_eye_result_to_graph 之前）
    anchor_fn = 'def _inject_eye_result_to_graph(kg: KnowledgeGraph, eye_result: dict):'
    assert anchor_fn in a
    fn = '''def _ingest_narrative(kg: KnowledgeGraph, text: str) -> dict:
    """叙事分段抽取入图：故事节点+按序事件实例+角色原子节点。

    结构：用户-[讲述]->故事X；故事X-[包含情节]->事件i；
          事件i-1 -[时间顺序]-> 事件i；角色-[参与]->事件i。
    返回 {story, events(节点id列表), characters}。
    """
    import time as _t
    nd = nlp.extract_narrative(text)
    if not nd.get("events"):
        return {}
    story_name = nd.get("story") or ("剧情_" + text.strip()[:10])
    now = datetime.now().strftime("%Y/%m/%d %H:%M:%S")

    def _add(nid, extra, space="semantic", label="declarative-semantic", w=0.5):
        if nid in kg.nodes:
            n0 = kg.nodes[nid]
            for k, v in extra.items():
                n0.extra_attrs.setdefault(k, v)
            return n0
        n0 = Node(id=nid, weight=w, label=label, graph_space=space,
                  extra_attrs=extra)
        kg.add_node(n0)
        engine.name_to_node[nid] = n0
        return n0

    if story_name not in kg.nodes:
        _add(story_name, {"canon": "concept", "source": "narrative",
                          "created": now}, w=0.6)
    if not any(e.src == "用户" and e.dst == story_name and e.relation == "讲述"
               for e in kg.edges):
        kg.add_edge(Edge(src="用户", dst=story_name, relation="讲述",
                         weight=0.8, relation_category="social_relation"))
    event_ids = []
    prev = None
    for i, ev in enumerate(nd["events"], 1):
        title = str(ev.get("title", ""))[:20] or f"情节{i}"
        nid = f"情节{i}—{story_name}" if nid_ok(title) else f"情节{i}—{story_name}"
        if nid in kg.nodes:
            nid = f"{nid}({int(_t.time()) % 10000})"
        _add(nid, {"canon": "event_instance", "sequence": i,
                   "summary": str(ev.get("summary", ""))[:150],
                   "source": "narrative", "created": now},
             space="episodic", label="declarative-episodic", w=0.5)
        if not any(e.src == story_name and e.dst == nid and e.relation == "包含情节"
                   for e in kg.edges):
            kg.add_edge(Edge(src=story_name, dst=nid, relation="包含情节",
                             weight=0.8, relation_category="semantic_relation"))
        if prev:
            if not kg.get_edge(prev, nid, "时间顺序"):
                kg.add_edge(Edge(src=prev, dst=nid, relation="时间顺序",
                                 weight=0.8, relation_category="temporal_relation"))
        for actor in (ev.get("actors") or [])[:4]:
            actor = str(actor).strip()[:20]
            if not actor:
                continue
            _add(actor, {"canon": "concept"})
            if not any(e.src == actor and e.dst == nid and e.relation == "参与"
                       for e in kg.edges):
                kg.add_edge(Edge(src=actor, dst=nid, relation="参与",
                                 weight=0.7, relation_category="social_relation"))
        for ent in (ev.get("entities") or [])[:3]:
            ent = str(ent).strip()[:20]
            if ent and ent in kg.nodes:
                if not kg.get_edge(nid, ent, "涉及"):
                    kg.add_edge(Edge(src=nid, dst=ent, relation="涉及",
                                     weight=0.5))
        prev = nid
        event_ids.append(nid)
    # 用户讲述关系 + 角色归属故事
    for c in nd.get("characters", [])[:8]:
        c = str(c).strip()[:20]
        if c and c in kg.nodes:
            if not kg.get_edge(story_name, c, "涉及"):
                kg.add_edge(Edge(src=story_name, dst=c, relation="涉及",
                                 weight=0.5))
    return {"story": story_name, "events": event_ids,
            "characters": nd.get("characters", [])[:8]}


def nid_ok(title):
    return bool(re.match(r"^[\\u4e00-\\u9fa5A-Za-z0-9—]+$", title))


''' + anchor_fn
    a = a.replace(anchor_fn, fn, 1)

# 3b. 叙事触发分支（放在记忆提取守卫之前）
if '叙事分段抽取触发' not in a:
    anchor_tr = '''        memory_draft = None
        _turn_memory_draft = None
        _auto_added = None'''
    assert anchor_tr in a
    tr = '''        # ── 叙事分段抽取触发：长叙事输入走专用管线 ──
        _narrative_result = None
        if len(text) >= 300 and text.count("。") + text.count("！") + text.count("\\n") >= 3:
            try:
                if llm_budget.can_call("reason"):
                    llm_budget.register("reason", tokens=1200)
                    _narrative_result = _ingest_narrative(kg, text)
                    if _narrative_result:
                        logger.info(f"[Narrative] 入图: 故事={_narrative_result['story']} "
                                    f"情节={len(_narrative_result['events'])}个")
                        _save(force=True)
                else:
                    logger.info("[LLMBudget] 叙事抽取预算不足，降级为常规抽取")
            except Exception as _ne:
                logger.warning(f"[Narrative] 抽取失败: {_ne}")

''' + anchor_tr
    a = a.replace(anchor_tr, tr, 1)

# 3c. 叙事成功时跳过常规单句抽取（避免把剧情再压成摘要事件）
if '_skip_regular_extract' not in a:
    a = a.replace('''        _da = parsed.get("dialogue_act", "")
        _non_teaching_das = {''', '''        _da = parsed.get("dialogue_act", "")
        _skip_regular_extract = bool(_narrative_result)  # 叙事已分段入图
        _non_teaching_das = {''', 1)
    a = a.replace('''        if _da:
            _should_extract_memory = (
                _da not in _non_teaching_das
                or parsed.get("memory_type") in ("episodic", "semantic")
            )''', '''        if _da:
            _should_extract_memory = (
                not _skip_regular_extract
                and (_da not in _non_teaching_das
                     or parsed.get("memory_type") in ("episodic", "semantic"))
            )''', 1)
open('app.py', 'w', encoding='utf-8').write(a)
ast.parse(a)
print('app.py ok')
