# -*- coding: utf-8 -*-
# audit_production.py — Step 1 生产系统运行时审计(capability_gap_v2)
# 原则:不按文件名判断;每项机制用真实调用探测(代码存在→运行时连通→实际运行→已有实验证据)。
# 探测装配:与信标/路由战役相同的零 LLM 装配(SandboxWorld + install_fake_bridge +
# run_exp_routing_v2.FASContext),对生产机制不做任何修改。
import json
import os
import sys
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

RESULTS = []


def probe(name, fn):
    try:
        detail = fn()
        RESULTS.append({"mechanism": name, "status": "OK", "detail": detail})
        print("[OK ] %s: %s" % (name, json.dumps(detail, ensure_ascii=False)[:220]))
    except Exception as e:
        RESULTS.append({"mechanism": name, "status": "FAIL",
                        "detail": "%s: %s" % (type(e).__name__, str(e)[:300]),
                        "trace": traceback.format_exc()[-600:]})
        print("[FAIL] %s: %r" % (name, e))


def main():
    import run_exp_routing_v2 as v2
    from sandbox_lab import install_fake_bridge
    from graph_model import KnowledgeGraph, Node, Edge
    import tempfile

    world = v2.SandboxWorld()
    install_fake_bridge(world)
    import run_exp_beacon as bk
    fc = bk.BeaconFASContext(world, use_diffusion=True, use_demand=True)

    # 1) graph persistence: save/load 往返
    def p_persist():
        kg = KnowledgeGraph()
        kg.add_node(Node(id="persist:test_node", weight=1.0, label="declarative-semantic", graph_space="semantic"))
        kg.add_node(Node(id="persist:t2", weight=1.0, label="declarative-semantic", graph_space="semantic"))
        kg.add_edge(Edge(src="persist:test_node", dst="persist:t2", relation="相关", weight=0.5))
        tmp = os.path.join(tempfile.gettempdir(), "cap_audit_kg.json")
        kg.save(tmp)
        kg2 = KnowledgeGraph.load(tmp)
        ok = ("persist:test_node" in kg2.nodes and
              any(e.dst == "persist:t2" for e in kg2.edges))
        return {"roundtrip": bool(ok), "nodes": len(kg2.nodes)}
    probe("graph_persistence", p_persist)

    # 2) spreading activation: 种子注入→传播点亮
    def p_spread():
        seeds = [n for n in ("物品:oak_log", "oak_log", "实体:oak_log") if n in fc.kg.nodes]
        fc.eng.activate_from_inputs(seeds, [], source_type="external_input")
        for _ in range(4):
            fc.eng.diffuse_step()
        acts = {n: float(getattr(fc.kg.nodes[n], "activation", 0) or 0)
                for n in fc.kg.nodes}
        lit = {n: a for n, a in acts.items() if a > 0.001}
        return {"seeds": seeds, "lit_count": len(lit),
                "oak_planks_lit": acts.get("物品:oak_planks", 0) > 0.001,
                "top5": sorted(lit.items(), key=lambda x: -x[1])[:5]}
    probe("spreading_activation", p_spread)

    # 3) inhibition: 负边抑制
    def p_inhibit():
        kg = KnowledgeGraph()
        for nid in ("A", "B", "C"):
            kg.add_node(Node(id=nid, weight=1.0, label="declarative-semantic"))
        kg.add_edge(Edge(src="A", dst="C", relation="相关", weight=0.5))
        kg.add_edge(Edge(src="B", dst="C", relation="抑制", weight=-1.0))
        # 直接用生产 DiffusionEngine(独立实例,零修改)
        from diffusion_engine import DiffusionEngine
        eng2 = DiffusionEngine(kg, {"beta_spread": 1.0, "activation_epsilon": 1e-4})
        eng2.activate_from_inputs(["A"], [], source_type="external_input")
        for _ in range(3):
            eng2.diffuse_step()
        a_c_pos = float(getattr(kg.nodes["C"], "activation", 0) or 0)
        kg.reset() if hasattr(kg, "reset") else None
        eng2.activate_from_inputs(["A", "B"], [], source_type="external_input")
        for _ in range(3):
            eng2.diffuse_step()
        a_c_neg = float(getattr(kg.nodes["C"], "activation", 0) or 0)
        return {"act_pos_only": round(a_c_pos, 4), "act_with_inhibition": round(a_c_neg, 4),
                "suppressed": a_c_neg < a_c_pos}
    probe("negative_edge_inhibition", p_inhibit)

    # 4) modulation: arousal/stress → 发射增益
    def p_modulate():
        from diffusion_engine import DiffusionEngine
        kg0 = KnowledgeGraph()
        eng0 = DiffusionEngine(kg0, {"beta_spread": 1.0})
        eng0.update_param_modulation(0.0, 0.0); g1 = float(getattr(eng0, "_param_gain", float("nan")))
        eng0.update_param_modulation(1.0, 0.0); g2 = float(getattr(eng0, "_param_gain", float("nan")))
        eng0.update_param_modulation(0.0, 1.0); g3 = float(getattr(eng0, "_param_gain", float("nan")))
        return {"neutral": round(g1, 4), "high_arousal": round(g2, 4),
                "high_stress": round(g3, 4), "varies": (g1 != g2) and (g1 != g3)}
    probe("scalar_modulation", p_modulate)

    # 5) write-back / attribution: 生产结算写回开关 + 归因聚合
    def p_writeback():
        import config as cfg
        cf = getattr(cfg, "DEFAULT_CONFIG", {})
        sw = {}
        def walk(d, prefix=""):
            for k, v in (d or {}).items():
                if isinstance(v, dict):
                    walk(v, prefix + k + ".")
                elif any(t in (prefix + k).lower() for t in ("writeback", "eventframe", "render_cognitive")):
                    sw[prefix + k] = v
        walk(cf)
        sw["experience_eventframe.enabled"] = (cf.get("experience_eventframe") or {}).get("enabled")
        sw["sandbox_writeback_switch"] = "scripts/sandbox_lab.py build_stack(writeback_on) 参数级消融(生产 CausalLearner kg=None)"
        return {"config_flags": sw}
    probe("writeback_and_eventframe_switches", p_writeback)

    # 6) ledger: 生产账本(奖励/试验)
    def p_ledger():
        import internal_state as ist
        led = [n for n in dir(ist) if "ledger" in n.lower()]
        import autonomy as aut
        tled = [n for n in dir(aut) if "ledger" in n.lower() or "trial" in n.lower()]
        return {"internal_state": led, "autonomy": tled}
    probe("ledger_presence", p_ledger)

    # 7) persistent intention / CI 生命周期: 阈值参数与组件
    def p_intention():
        from continuous_cognition import ContinuousCognition
        params = {"form": 0.55, "express": 0.78, "discard": 0.10}
        # 运行时探针:最小装配(真 kg/engine,nlp 与 buffer 用最小桩)
        class _StubNlp:
            def extract(self, *a, **k):
                return []
        from episodic_buffer import EpisodicBuffer
        kg = KnowledgeGraph()
        from diffusion_engine import DiffusionEngine
        eng = DiffusionEngine(kg, {"beta_spread": 1.0, "activation_epsilon": 1e-4})
        buf = EpisodicBuffer()
        cc = ContinuousCognition(kg, eng, _StubNlp(), buf, {})
        state0 = cc.tick_once()
        return {"constructor_ok": True, "tick_return": str(type(state0).__name__),
                "thresholds": params}
    probe("continuous_cognition_lifecycle", p_intention)

    # 8) self-model: 信念节点写入(不依赖 LLM 的部分)
    def p_selfmodel():
        from self_model import SelfMemoryUpdater
        kg = KnowledgeGraph()
        up = SelfMemoryUpdater(kg, {})
        meths = [m for m in dir(up) if not m.startswith("_")]
        ok = "err"
        try:
            up._ensure_self()
            ok = any("self" in n.lower() for n in kg.nodes)
        except Exception as e:
            ok = "err:%r" % e
        return {"methods": meths[:12], "self_node_ensured": ok}
    probe("self_model_runtime", p_selfmodel)

    # 9) drives/curiosity: 好奇状态计算
    def p_curiosity():
        import curiosity_engine as ce
        kg = KnowledgeGraph()
        st = ce.get_curiosity_state(kg)
        return {"state_keys": sorted(st.keys())[:10] if isinstance(st, dict) else str(type(st))}
    probe("drives_curiosity", p_curiosity)

    # 10) forgetting: 情景缓冲容量/遗忘
    def p_forget():
        from episodic_buffer import EpisodicBuffer
        buf = EpisodicBuffer()
        meths = [m for m in dir(buf) if not m.startswith("_")][:12]
        cap = getattr(buf, "capacity", None)
        return {"capacity": cap, "methods": meths}
    probe("episodic_buffer_forgetting", p_forget)

    # 11) memory reuse: 保存-加载-再激活(跨会话激活继承)
    def p_reuse():
        kg = KnowledgeGraph()
        for nid in ("R1", "R2", "R3"):
            kg.add_node(Node(id=nid, weight=1.0, label="declarative-semantic"))
        kg.add_edge(Edge(src="R1", dst="R2", relation="相关", weight=0.8))
        tmp = os.path.join(tempfile.gettempdir(), "cap_audit_reuse.json")
        from diffusion_engine import DiffusionEngine
        eng = DiffusionEngine(kg, {"beta_spread": 1.0, "activation_epsilon": 1e-4})
        eng.activate_from_inputs(["R1"], [], source_type="external_input")
        for _ in range(2):
            eng.diffuse_step()
        acts = {n: float(getattr(kg.nodes[n], "activation", 0) or 0) for n in kg.nodes}
        kg.save(tmp)
        kg2 = KnowledgeGraph.load(tmp)
        a2 = {n: float(getattr(kg2.nodes[n], "activation", 0) or 0) for n in kg2.nodes}
        return {"before_save": {k: round(v, 3) for k, v in acts.items()},
                "after_load": {k: round(v, 3) for k, v in a2.items()},
                "activation_inherited": a2.get("R2", 0) > 0.001}
    probe("memory_reuse_activation_inheritance", p_reuse)

    # 12) routing/demand: 生产需求-缺口-路由分析(FASContext.demand_block 实跑)
    def p_routing():
        demand, gap, routing, meta = fc.demand_block("Obtain 1 oak_planks in inventory.")
        return {"demand_present": bool(demand), "gap_present": bool(gap),
                "routing_present": bool(routing)}
    probe("demand_gap_routing", p_routing)

    # 13) flat retrieval(嵌入)通道
    def p_flat():
        sel = fc.flat_focus("Obtain 1 oak_planks in inventory.", k=8)
        return {"k": len(sel), "top3": [s["id"] for s in sel[:3]]}
    probe("embedding_flat_retrieval", p_flat)

    out = os.path.join(ROOT, "experiments", "capability_gap_v2", "PRODUCTION_AUDIT.json")
    json.dump(RESULTS, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("saved ->", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
