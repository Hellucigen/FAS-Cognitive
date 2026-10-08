# -*- coding: utf-8 -*-
# harness.py — Capability Gap Campaign v2 决策头层 harness
# 复用信标战役装配(BeaconFASContext=生产建图路径)与 v2 的 Task/Executor/LLMHead。
# 设计要点(EXPERIMENT_PROTOCOL.md):
#   - 练习相零 LLM 脚本化,经历流逐字节进入各条件记忆系统
#   - 闭卷门控规则全条件统一:craft_X 可见 ⟺ X 名出现在该条件本步上下文
#   - FAS 图谱播种可排除目标配方(排除项=只能由经历提供的知识)
#   - 消融为参数级:no-diffusion(v2 既有)/ no-writeback(ingest 不建边)/
#     exclude_recipes(播种范围=任务设计参数)
import json
import re
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import run_exp_routing_v2 as v2  # noqa: E402
import run_exp_beacon as bk  # noqa: E402
from sandbox_lab import install_fake_bridge  # noqa: E402

CRAFTABLES = ("oak_planks", "stick", "oak_fence", "crafting_table",
              "wooden_pickaxe", "stone_pickaxe", "furnace")


def gate_from_context(text):
    """统一门控规则:craft_X 可见 ⟺ X 名出现在本步上下文文本。"""
    low = str(text or "").lower()
    return {x for x in CRAFTABLES if x in low}


class CampaignFASContext(bk.BeaconFASContext):
    """BeaconFASContext + 任务设计参数:exclude_recipes(播种范围)、
    writeback(经历是否建边)。零生产机制修改。"""

    def __init__(self, world, use_diffusion=True, use_demand=True,
                 use_flat=False, exclude_recipes=(), writeback=True):
        self._exclude = {str(r).lower() for r in exclude_recipes}
        self._writeback = bool(writeback)
        self.exp_edges = 0  # 经历来源边计数(审计用)
        install_fake_bridge(world)
        v2.FASContext.__init__(self, world, use_diffusion=use_diffusion,
                               use_demand=use_demand, use_flat=use_flat)
        # 生产建图路径播种(复刻 BeaconFASContext.__init__,加排除过滤)
        try:
            for out, recs in (world.recipe_table or {}).items():
                if str(out).lower() in self._exclude:
                    continue
                for r in recs or []:
                    rn = self.node_id(str(r.get("result") or out).lower())
                    for mat in (r.get("ingredients") or {}):
                        mn = self.node_id(str(mat).lower())
                        if mn in self.kg.nodes and rn in self.kg.nodes:
                            self._edge(mn, rn, rel="相关")
        except Exception as e:
            raise v2.CoreModuleError(f"campaign seeding failed: {e!r}") from e
        self._sync_index()

    # 生产失败语义前缀(action_system 结算原因)
    FAIL_PREFIXES = ("tool_missing", "not_found", "missing_ingredients",
                     "needs_crafting_table", "not_in_inventory")

    def ingest(self, obs_text, action, result, goal_entities):
        """同 v2/信标,writeback=False 时不建任何经历边(只节点+激活)。
        P0-a:result 节点 id 截断 24→64。
        P1-E:ef_context=True 时,失败结算复刻生产事件框架签名
        (涉及边 failure_polarity=-0.8、结果边 +0.9、事件节点点火),
        参数与 config.experience_eventframe 一致;默认 False=现役行为。"""
        try:
            n0 = len(self.kg.edges) if hasattr(self.kg.edges, "__len__") else None
            ents = [self.node_id(e) for e in self.entities_of(obs_text)]
            an = self._node("动作:" + str(action), label="procedural") \
                if action else None
            rn = self._node("结果:" + str(result)[:64]) if result else None
            if self._writeback:
                for i in range(len(ents)):
                    for j in range(i + 1, len(ents)):
                        self._edge(ents[i], ents[j])
                if an:
                    for nid in ents:
                        self._edge(an, nid)
                        self._edge(nid, an)
                if an and rn:
                    self._edge(an, rn)
            # P1-E:失败证据的事件框架负极性编码(装配层复刻生产签名)
            rs = str(result or "")
            if (getattr(self, "ef_context", False) and self._writeback
                    and any(rs.startswith(p) for p in self.FAIL_PREFIXES)
                    and action):
                obj = None
                for pre in ("gather_", "craft_", "place_", "eat_"):
                    if str(action).startswith(pre):
                        obj = str(action)[len(pre):]
                        break
                if obj:
                    eid = "事件:failed:%s" % action
                    enode = self._node(eid, label="declarative-episodic")
                    tnode = self.node_id(obj)
                    if tnode not in self.kg.nodes:
                        self._node(tnode, label="declarative-semantic")
                    from graph_model import Edge
                    if self.kg.get_edge(eid, tnode, "涉及") is None:
                        self.kg.add_edge(Edge(
                            src=eid, dst=tnode, relation="涉及",
                            weight=-0.8,
                            relation_category="cognitive_relation"))
                    if rn and self.kg.get_edge(eid, rn, "结果") is None:
                        self.kg.add_edge(Edge(
                            src=eid, dst=rn, relation="结果",
                            weight=0.9,
                            relation_category="cognitive_relation"))
                    n = self.kg.nodes.get(eid)
                    if n is not None:
                        n.activation = min(3.0, float(n.activation or 0.0) + 0.8)
                        self.eng.mark_active([eid])
            self._sync_index()
            try:
                self.exp_edges = (len(self.kg.edges) if hasattr(self.kg.edges, "__len__")
                                  else 0) - (n0 or 0)
            except Exception:
                pass
            seeds = [n for n in ents if n in self.eng.name_to_node]
            if seeds:
                self.eng.activate_from_inputs(seeds, [],
                                              source_type="external_input")
        except v2.CoreModuleError:
            raise
        except Exception as e:
            raise v2.CoreModuleError(f"campaign ingest failed: {e!r}") from e


class HistoryStore:
    """LLM-history / retrieval 共用的原始经历流(逐字节与 FAS ingest 相同来源)。"""

    def __init__(self):
        self.lines = []

    def add(self, obs, action, result):
        self.lines.append(f"obs: {obs} | action: {action or '(start)'} | "
                          f"result: {result}")

    def window(self, k=None):
        ls = self.lines if k is None else self.lines[-k:]
        return "\n".join(ls) if ls else "(no history)"

    def retrieve(self, query, k=8):
        try:
            from embedding_manager import EmbeddingProvider
            import numpy as np
            if not self.lines:
                return "(no history)"
            if HistoryStore._EMB is None:
                HistoryStore._EMB = EmbeddingProvider()
            emb = HistoryStore._EMB
            M = emb.encode(self.lines)
            q = emb.encode_single(query)
            sims = M @ (q / (np.linalg.norm(q) + 1e-9))
            idx = sorted(range(len(self.lines)), key=lambda i: -float(sims[i]))[:k]
            return "\n".join(self.lines[i] for i in idx)
        except Exception as e:
            raise v2.CoreModuleError(f"experience retrieval failed: {e!r}")


HistoryStore._EMB = None


# ── 练习脚本(零 LLM;全部条件同一序列)────────────────────────────────
def script_practice_A(ex, stores, fc):
    """A 链:gather oak_log → craft oak_planks。stores=(hist, None);fc 可 None。"""
    acts = [("gather_oak_log", "oak_log"), ("gather_oak_log", "oak_log"),
            ("craft_oak_planks", "oak_planks")]
    return run_scripted(acts, ex, stores, fc)


def script_practice_B(ex, stores, fc):
    """B 链:预置 4 木板(任务设计,全条件相同)→ craft stick ×2。
    B 只学 stick 配方,不携带 A 的 log→planks 知识。"""
    ex.w.add_item("oak_planks", 2)
    acts = [("craft_stick", "stick")]
    return run_scripted(acts, ex, stores, fc)


def script_practice_C(ex, stores, fc):
    """C 链(无关):craft crafting_table。"""
    acts = [("craft_crafting_table", "crafting_table")]
    return run_scripted(acts, ex, stores, fc)


def script_filler(ex, stores, fc):
    acts = [("explore_north", None), ("gather_dirt", "dirt")]
    return run_scripted(acts, ex, stores, fc)


def script_practice_long(ex, stores, fc, episodes=60):
    """K 的长历史练习流(协议修订 v1.1):循环多样小情节,资源即时重生,
    生成 ~200+ 行经历流,使 history 窗口操纵(10..200)真正生效。
    全条件同一序列(确定性);不引入任何认知机制改动。"""
    hist, _ = stores
    out = []
    for e in range(episodes):
        kind = e % 4
        if kind == 0:
            ex.w.put_block(3, 65, 0, "oak_log")
            ex.w._refresh_near()
            acts = [("gather_oak_log", "oak_log"), ("craft_oak_planks", "oak_planks")]
        elif kind == 1:
            acts = [("explore_north", None), ("explore_east", None),
                    ("gather_dirt", "dirt")]
        elif kind == 2:
            ex.w.put_block(10, 65, 0, "birch_log")
            ex.w._refresh_near()
            acts = [("gather_birch_log", "birch_log"), ("wait", None)]
        else:
            acts = [("gather_dirt", "dirt"), ("explore_south", None)]
        out += run_scripted(acts, ex, stores, fc)
    return out


def run_scripted(acts, ex, stores, fc):
    hist, _ = stores
    out = []
    for act, obj in acts:
        obs = observe(ex.w, ex)
        ok, detail = ex.execute(act, obj)
        out.append({"action": act, "ok": ok, "detail": str(detail)[:60]})
        line = f"obs: {obs} | action: {act} | result: {detail}"
        hist.add(obs, act, detail)
        if fc is not None:
            fc.ingest(obs, act, detail, ())
            fc.step_dynamics()
    return out


def observe(w, ex):
    near = ", ".join(f"{b['name']}x{b['count']}" for b in w.near_names)
    inv = json.dumps(w.inv_map(), ensure_ascii=False)
    return f"near: {near or '(none)'}; inventory: {inv}; hunger: {ex.hunger}"


BASE_PROMPT = v2.BASE_PROMPT


def decide(llm, prompt):
    reply = llm.decide(prompt)
    m = re.search(r'"action"\s*:\s*(\d+)', reply)
    return m, reply


def H_gate(facts, ctx_text, ex):
    NL = chr(10)
    return gate_from_context((facts or "") + NL + ctx_state(ex) + NL
                             + (ctx_text or ""))


def run_menu_step(ex, task_goal, facts, ctx_text, llm, gate, max_menu=20):
    """一步决策:菜单+提示→LLM→执行。统一门控 v2:
    craft_X 可见 ⟺ X 名出现在 facts∪state∪context 全文(全条件同规则;
    闭卷实验的 facts 不含配方名,故不受影响)。"""
    ex.gate = H_gate(facts, ctx_text, ex)
    menu, locked = ex.menu(type("T", (), {"gatherable": ("oak_log", "birch_log",
                                                          "dirt", "cobblestone"),
                                          "phase_goal": staticmethod(lambda: task_goal)})())
    menu_text = "\n".join(f"{i}. {a}" for i, (a, _) in enumerate(menu))
    prompt = BASE_PROMPT.format(goal=task_goal, facts=facts,
                                state=ctx_state(ex), context=ctx_text,
                                menu=menu_text)
    t0 = time.time()
    m, reply = decide(llm, prompt)
    lat = time.time() - t0
    idx = -1
    if m and int(m.group(1)) < len(menu):
        idx = int(m.group(1))
    if idx < 0:
        low = reply.lower()
        for i, (a, _) in enumerate(menu):
            key = a.split("_", 1)[-1] if a.startswith(("gather_", "craft_",
                                                       "explore_", "eat_",
                                                       "place_")) else a
            if a.lower() in low or key in low:
                idx = i
                break
    if idx < 0:
        act, obj, ok, detail = "wait", None, True, "unparseable_reply"
    else:
        act, obj = menu[idx]
        ok, detail = ex.execute(act, obj)
    tb = {"prompt_tokens": llm.prompt_tokens, "completion_tokens": llm.completion_tokens,
          "latency_s": round(lat, 2)}
    return act, obj, ok, detail, tb, len(menu), locked


def ctx_state(ex):
    return observe(ex.w, ex)


class ExecutorWithGate(v2.Executor):
    """craft 菜单按统一门控规则过滤(gate=本步上下文中出现的物品名)。"""

    def __init__(self, world, gate=None):
        super().__init__(world)
        self.gate = gate or set()

    def menu(self, task):
        items = v2.Executor.menu(self, task)
        locked = 0
        out = []
        for act, obj in items:
            if act.startswith("craft_") and obj not in self.gate:
                locked += 1
                continue
            out.append((act, obj))
        return out, locked
