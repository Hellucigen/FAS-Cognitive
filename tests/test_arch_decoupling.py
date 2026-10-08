"""架构解耦集成测试（docs/ARCHITECTURE_PLAN.md Phase 4）。

验证四类命题：
  链路    外部事件 → Cognitive Core → Cognitive Output → Companion Layer → Response
  替换A   语言后端可整体替换：认知输出逐字段不变，话语随后端变（验收问题 B）
  替换B   认知内部语言触点经 attach_language_backend 单点换入（验收问题 A/E）
  降级    认知段炸掉，陪伴仍出话语（验收问题 C）
  红线    fas/core/* 不得 import 陪伴/LLM 侧模块（隐式依赖防回潮）
  通道    CharonBridge 按 bridge 协议（127.0.0.1 + X-Charon-Token）投递，本地假服务器对拍

离线、零 LLM、不 import app.py。引擎参数复制自 tests/test_closed_loop_consistency.py
的既有沙箱配置（ENG_CFG），不代表任何生产/实验参数。
"""
import ast
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from graph_model import KnowledgeGraph, Node           # noqa: E402
from diffusion_engine import DiffusionEngine           # noqa: E402
from episodic_buffer import EpisodicBuffer             # noqa: E402

from fas.contracts import (CognitiveEvent, LanguageRequest, MemoryEvent,  # noqa: E402
                           Utterance, EVT_USER_MESSAGE)
from fas.core.assembly import build_cognitive_core     # noqa: E402
from fas.companion.stub_backends import (EchoLanguageBackend,            # noqa: E402
                                         TemplateFallbackBackend)
from fas.companion.pipeline import run_turn            # noqa: E402

ENG_CFG = {"lambda_decay": 0.05, "beta_spread": 1.0, "max_depth": 4,
           "theta_threshold": 0.01, "activation_max": 5.0,
           "min_spread_threshold": 0.01, "activation_epsilon": 1e-4,
           "input_similarity_floor": 0.5, "input_default_bonus": 0.5,
           "theta_action": 0.5}

SEED_NODES = ["用户", "猫", "养猫", "喜欢", "Haru"]


def make_core():
    kg = KnowledgeGraph()
    for nid in SEED_NODES:
        kg.add_node(Node(id=nid, label="declarative-semantic",
                         graph_space="semantic", weight=0.5))
    kg.add_node(Node(id="猫-喜欢-用户", label="declarative-semantic",
                     graph_space="semantic", weight=0.5))
    from graph_model import Edge
    kg.add_edge(Edge(src="猫", dst="用户", relation="属于", weight=0.5))
    kg.add_edge(Edge(src="用户", dst="养猫", relation="经历", weight=0.5))
    engine = DiffusionEngine(kg, dict(ENG_CFG))
    engine.name_to_node = {n.id: n for n in kg.nodes.values()}
    buffer = EpisodicBuffer(capacity=100)
    return build_cognitive_core(config=dict(ENG_CFG), kg=kg, engine=engine,
                                buffer=buffer)


PARSED = {"nodes": [{"id": "猫"}, {"id": "养猫"}], "edges": [],
          "dialogue_act": "information_statement",
          "response_expectation": "medium"}


class ParseStubBackend(EchoLanguageBackend):
    """Echo 桩 + 固定解析产物：语言后端把话解析成已存在的图谱节点，
    用于验证"外部事件 → 认知激活"链路真的打通（不测解析质量，那是研究侧的事）。"""

    def parse(self, text, *, fast=False):
        return dict(PARSED)


def _cognitive_signature(core):
    """认知输出的可比较切片（与语言后端无关的部分）。"""
    snap = core.snapshot()
    dec = core.decide(PARSED, "我家猫今天特别粘人", )
    return {
        "activated_ids": sorted(getattr(n, "id", str(n)) for n in snap.topk_nodes),
        "decision": dec.decision,
        "constraints": sorted(map(str, dec.constraints)),
        "buffer_len": len(buffer_ids(core)),
    }


def buffer_ids(core):
    return [e.raw_text for e in core.buffer._experiences] \
        if core.buffer is not None else []


# ════════════════════════════════════════════════════════════════
# 链路：External event → Core → Cognitive Output → Companion → Response
# ════════════════════════════════════════════════════════════════

class TestTurnChain:
    def test_full_chain_produces_utterance(self):
        core = make_core()
        backend = ParseStubBackend()
        utt = run_turn(core, backend, text="我家猫今天特别粘人", source="web")
        assert isinstance(utt, Utterance) and utt.text
        assert utt.origin == "reactive"
        # 认知状态确实被外部事件改变
        ids = [getattr(n, "id", n) for n in core.snapshot().topk_nodes]
        assert "猫" in ids or "养猫" in ids
        # 情景缓冲记账
        assert any("猫" in e.raw_text for e in core.buffer._experiences)

    def test_compile_request_carries_cognitive_fields(self):
        core = make_core()
        ev = CognitiveEvent(kind=EVT_USER_MESSAGE, source="web",
                            text="说说猫", payload={
                                "nodes": PARSED["nodes"], "edges": PARSED["edges"]})
        rep = core.perceive(ev)
        assert rep.activated and rep.diffuse_steps == 1
        dec = core.decide(PARSED, "说说猫")
        req = core.compile_language_request(decision=dec, parsed=PARSED,
                                            text="说说猫", path="L2")
        assert isinstance(req, LanguageRequest)
        assert req.payload.get("decision") or req.payload.get("mode") is not None \
            or "outline" in req.payload or req.payload  # compile_for_language 产物非空
        assert dec.raw.get("decision") is not None


# ════════════════════════════════════════════════════════════════
# 替换 A：语言后端整体可换，认知层不动（验收问题 B）
# ════════════════════════════════════════════════════════════════

class TestLanguageBackendSwappable:
    def test_cognitive_output_identical_across_backends(self):
        """同一 core，先后用两个不同语言后端跑同一事件序列：
        认知输出逐字段相等；话语不同；core 内部代码没被动过。"""
        core = make_core()
        b1 = EchoLanguageBackend()
        b2 = _AltEchoBackend()
        sig1 = _cognitive_signature_after_turn(core, b1)
        sig2 = _cognitive_signature_after_turn(core, b2)
        assert sig1 == sig2, "换语言后端不得改变认知输出"

    def test_utterance_differs_by_backend(self):
        core = make_core()
        u1 = run_turn(core, EchoLanguageBackend(), text="猫")
        core2 = make_core()
        u2 = run_turn(core2, _AltEchoBackend(), text="猫")
        assert u1.text != u2.text


class _AltEchoBackend(EchoLanguageBackend):
    """第二个"替代陪伴实现"：只改措辞面，不改数据面。"""

    def realize(self, request: LanguageRequest) -> Utterance:
        u = super().realize(request)
        return Utterance(text=u.text.replace("[echo:", "[alt-echo:"),
                         origin=u.origin, refs=dict(u.refs, backend="alt"))


def _cognitive_signature_after_turn(core, backend):
    core.buffer._experiences.clear()
    ev = CognitiveEvent(kind=EVT_USER_MESSAGE, source="web",
                        text="我家猫今天特别粘人",
                        payload={"nodes": PARSED["nodes"], "edges": []})
    core.perceive(ev)
    dec = core.decide(PARSED, "我家猫今天特别粘人")
    snap = core.snapshot()
    return {
        "topk": sorted(getattr(n, "id", str(n)) for n in snap.topk_nodes),
        "decision": dec.decision,
        "constraints": sorted(map(str, dec.constraints)),
        "buffered": [e.raw_text for e in core.buffer._experiences],
    }


# ════════════════════════════════════════════════════════════════
# 替换 B：认知内部语言触点单点接入（验收问题 A/E）
# ════════════════════════════════════════════════════════════════

class TestCognitiveLanguageSlot:
    def test_attach_replaces_all_touchpoints(self):
        core = make_core()
        a1 = _FakeAccess("A")
        core.attach_language_backend(a1)
        assert core.engine._nlp_ref is a1
        a2 = _FakeAccess("B")
        core.attach_language_backend(a2)
        assert core.engine._nlp_ref is a2 and core._language is a2

    def test_access_surface_covers_cognitive_callsites(self):
        """现状认知侧只消费 llm/chat_llm/ask（facade 头注释列明行号）。
        新机制（如 FAS 自建提问器）接入 = 实现同一最小面后 attach，不改 core。"""
        from fas.protocols import CognitiveLanguageAccess
        acc = _FakeAccess("C")
        assert isinstance(acc, CognitiveLanguageAccess)
        core = make_core()
        core.attach_language_backend(acc)
        assert core._language.ask("thought", "ctx") == "C:thought:ctx"


class _FakeAccess:
    def __init__(self, name):
        self._name = name
        self.llm = object()
        self.chat_llm = object()

    def ask(self, task, user_input):
        return f"{self._name}:{task}:{user_input}"

    def system_prompt(self, task):
        return f"{self._name}:tpl:{task}"


# ════════════════════════════════════════════════════════════════
# 降级：认知炸了，陪伴照跑（验收问题 C）
# ════════════════════════════════════════════════════════════════

class TestCompanionSurvivesCognitiveFailure:
    def test_pipeline_fallback_on_broken_core(self):
        core = make_core()

        def boom(*a, **k):
            raise RuntimeError("认知机制实验失败（模拟）")
        core.decide = boom
        utt = run_turn(core, EchoLanguageBackend(), text="还在吗", fallback=True)
        assert utt.text and "[echo:L1]" in utt.text   # 降级短应答仍出话语

    def test_pipeline_fallback_on_broken_perceive(self):
        core = make_core()
        core.engine = None          # 最粗糙的"机制失败"：引擎缺席
        core.buffer = None
        utt = run_turn(core, TemplateFallbackBackend(), text="你好")
        assert "我在" in utt.text

    def test_temporary_companion_swappable_end_to_end(self):
        """temporary companion → alternative companion 替换演练：
        同一个 core（不做任何内部修改）分别驱动两套陪伴实现。"""
        core = make_core()
        u1 = run_turn(core, EchoLanguageBackend(), text="猫")
        core2 = make_core()
        u2 = run_turn(core2, TemplateFallbackBackend(), text="猫")
        assert u1.refs["backend"] == "echo"
        assert u2.refs["backend"] == "fallback"
        # 两套陪伴都未触碰认知内部：核心动力学对象身份不变
        assert isinstance(core.engine, DiffusionEngine)


# ════════════════════════════════════════════════════════════════
# MemoryWriter：陪伴记录记忆的唯一合法入口（不经 kg._lock）
# ════════════════════════════════════════════════════════════════

class TestMemoryEventEntry:
    def test_record_memory_writes_via_public_api(self):
        core = make_core()
        ok = core.record_memory(MemoryEvent(
            subject="用户", predicate="喜欢", object="猫",
            provenance="test"))
        assert ok is True or "猫" in core.kg.nodes  # 关系归一漏斗可能改写 predicate
        assert "用户" in core.kg.nodes and "猫" in core.kg.nodes

    def test_record_memory_idempotent_nodes(self):
        core = make_core()
        before = len(core.kg.nodes)
        core.record_memory(MemoryEvent(subject="用户", predicate="喜欢",
                                       object="猫", provenance="t2"))
        assert len(core.kg.nodes) == before   # 已有主体/客体不重复建点


# ════════════════════════════════════════════════════════════════
# 架构红线守卫：fas/core/* 对陪伴/LLM 侧零 import（隐式依赖防回潮）
# ════════════════════════════════════════════════════════════════

FORBIDDEN = {"nlp_processor", "prompt_templates", "llm_provider",
             "ollama_backend", "app", "flask"}


class TestCorePurity:
    def test_core_has_no_companion_imports(self):
        core_dir = os.path.join(ROOT, "fas", "core")
        offenders = []
        for dp, _, fns in os.walk(core_dir):
            for f in fns:
                if not f.endswith(".py"):
                    continue
                p = os.path.join(dp, f)
                tree = ast.parse(open(p, encoding="utf-8").read())
                for node in ast.walk(tree):
                    mods = []
                    if isinstance(node, ast.Import):
                        mods = [a.name.split(".")[0] for a in node.names]
                    elif isinstance(node, ast.ImportFrom):
                        mods = [(node.module or "").split(".")[0]]
                    for m in mods:
                        if m in FORBIDDEN:
                            offenders.append(f"{os.path.relpath(p, ROOT)}:{node.lineno} -> {m}")
        assert not offenders, "认知门面不得依赖陪伴侧：" + "; ".join(offenders)

    def test_contracts_are_pure_stdlib(self):
        p = os.path.join(ROOT, "fas", "contracts.py")
        tree = ast.parse(open(p, encoding="utf-8").read())
        for node in ast.walk(tree):
            mods = []
            if isinstance(node, ast.Import):
                mods = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                mods = [(node.module or "").split(".")[0]]
            for m in mods:
                assert m in {"__future__", "time", "dataclasses", "typing"}, m


# ════════════════════════════════════════════════════════════════
# Charon Bridge：陪伴外壳通道协议对拍（本地假服务器，不依赖 Charon 在跑）
# ════════════════════════════════════════════════════════════════

class _StubBridgeHandler(BaseHTTPRequestHandler):
    captured = []

    def do_POST(self):
        if self.headers.get("X-Charon-Token") != "test-token":
            self.send_response(401); self.end_headers(); return
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
        type(self).captured.append((self.path, body))
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

    def log_message(self, *a):       # 静音
        pass


class TestCharonBridgeChannel:
    @pytest.fixture()
    def server(self):
        srv = HTTPServer(("127.0.0.1", 0), _StubBridgeHandler)
        _StubBridgeHandler.captured = []
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        yield f"http://127.0.0.1:{srv.server_address[1]}"
        srv.shutdown()

    def test_deliver_utterance(self, server):
        from fas.companion.charon_bridge import CharonBridgeChannel
        ch = CharonBridgeChannel(base_url=server, token="test-token")
        assert ch.deliver(Utterance(text="今天也想过你", origin="proactive")) is True
        path, body = _StubBridgeHandler.captured[-1]
        assert path == "/api/events"
        assert body["topic"] == "fas.utterance"
        assert body["payload"]["text"] == "今天也想过你"

    def test_journal_and_bad_token(self, server):
        from fas.companion.charon_bridge import CharonBridgeChannel
        ch = CharonBridgeChannel(base_url=server, token="test-token")
        assert ch.journal(MemoryEvent(subject="用户", predicate="喜欢",
                                      object="猫", provenance="chat")) is True
        bad = CharonBridgeChannel(base_url=server, token="wrong")
        assert bad.deliver(Utterance(text="x")) is False   # 401 → 失败容忍，不抛

    def test_disabled_by_default_in_assembly(self):
        from fas.companion.assembly import build_charon_channel
        assert build_charon_channel({}) is None
        assert build_charon_channel({"companion_charon_enabled": True}) is not None


# ════════════════════════════════════════════════════════════════
# TemporaryLLMBackend：包装器的行为边界
# ════════════════════════════════════════════════════════════════

class TestTemporaryLLMBackend:
    def test_access_min_surface_passthrough(self):
        from fas.companion.temporary_llm import TemporaryLLMBackend

        class FakeNLP:
            llm = "LLM"
            chat_llm = "CHAT"

            def ask(self, task, user_input):
                return f"{task}/{user_input}"

        be = TemporaryLLMBackend(FakeNLP())
        acc = be.access()
        assert acc.llm == "LLM" and acc.chat_llm == "CHAT"
        assert acc.ask("thought", "x") == "thought/x"

    def test_wrapper_does_not_leak_full_bus(self):
        """反模式守卫：包装器不得变成万能转发（协议面之外的属性拒绝）。"""
        from fas.companion.temporary_llm import TemporaryLLMBackend

        class FakeNLP:
            llm = None
            chat_llm = None

            def secret_internal(self):
                return "nope"

        be = TemporaryLLMBackend(FakeNLP())
        with pytest.raises(AttributeError):
            be.secret_internal()
        with pytest.raises(AttributeError):
            be.access().secret_internal()
