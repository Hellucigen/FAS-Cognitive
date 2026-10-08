"""fas.registry — 组件槽位台账：回答验收问题 D（哪些能力是借来的）。

每个槽位记录：当前占用者、是否临时、所属 Protocol、替换它需要动的唯一位置。
`partially_validated` 等实验状态以 docs/COGNITIVE_MODULE_STATUS.md 为准，
本表只做架构占用登记，不复制实验结论。
"""
from __future__ import annotations

from dataclasses import dataclass, asdict


@dataclass(frozen=True)
class Slot:
    slot: str
    protocol: str
    occupant: str
    temporary: bool
    replace_at: str
    notes: str = ""


SLOTS: list[Slot] = [
    Slot("LanguageBackend", "fas.protocols.LanguageBackend",
         "TemporaryLLMBackend(NLPProcessor → MiMo/DeepSeek/Ollama)",
         temporary=True,
         replace_at="fas/companion/assembly.build_language_backend",
         notes="FAS 自有语言机制 NOT_IMPLEMENTED；prompt_templates.py:277 明文"
               "『不存在预设的说话风格』，无台词库。当前方案只做『能自然交流』。"),
    Slot("CognitiveLanguageAccess", "fas.protocols.CognitiveLanguageAccess",
         "TemporaryLLMBackend.access()",
         temporary=True,
         replace_at="app.py 注入点(engine._nlp_ref/cc/reflection) + CognitiveCore.attach_language_backend",
         notes="认知逻辑内部 LLM 触点：curiosity 提问、reflection 分析、free thought、"
               "主动表达。四点各自有规则兜底/预算降级。"),
    Slot("MemoryWriter", "CognitiveCoreAPI.record_memory",
         "Unified KG（graph_model；LLM 抽取→app 写图为临时输入通道）",
         temporary=False,
         replace_at="fas/core/facade.CognitiveCore.record_memory",
         notes="KG 载体是 FAS 核心（partially_validated）；LLM 抽取段属陪伴侧临时方案。"),
    Slot("ActionSelection", "CognitiveCoreAPI.propose_action",
         "autonomy.py + action_system（FAS 自建，零 LLM tick）",
         temporary=False,
         replace_at="app.py 装配区（cc.action_manager / autonomy）",
         notes="决策头 I 实验 failed、事件框架生产默认关——状态见台账；"
               "架构上已是独立模块，可继续实验而不牵动陪伴层。"),
    Slot("GameBackend", "fas.protocols.GameBackend",
         "TemporaryMineflayerAgent（minecraft/ 包 + bot.js）",
         temporary=True,
         replace_at="fas/companion（接线在 app.py MC 区 824–831/1126–1128）",
         notes="A10 信标战役显示 FAS 记忆/目标机制在游戏域 20/20 有效——"
               "该槽是未来 FASActionController 的首选替换位。"),
    Slot("ExpressionChannel:web", "fas.protocols.ExpressionChannel",
         "index.html 轮询（/api/proactive/poll + /api/nlp 应答）",
         temporary=False,
         replace_at="app.py 路由层",
         notes="开发者控制台，长期保留为观测面。"),
    Slot("ExpressionChannel:charon", "fas.protocols.ExpressionChannel",
         "CharonBridgeChannel（127.0.0.1:17734 Bridge，默认关）",
         temporary=True,
         replace_at="fas/companion/assembly.build_companion",
         notes="陪伴外壳主通道候选：utterance 事件/toast/日记/便签。"),
    Slot("Perception:stt", "—",
         "ear/（FunASR Paraformer + emotion2vec + YAMNet，手动喂音频）",
         temporary=True,
         replace_at="—",
         notes="⏳ 缺实时麦克风流；需用户选型补充。"),
    Slot("Perception:tts", "—",
         "（未接入）",
         temporary=True,
         replace_at="fas/companion/charon_bridge（前端 Web Speech API 候选）",
         notes="⏳ 空缺件，已按任务书占位登记，等待用户补充方案。"),
    Slot("Perception:screen/vision", "—",
         "eye/（RapidOCR+YOLO）、vision/（SAM2+CLIP+FAISS）",
         temporary=True,
         replace_at="fas/companion/perception_sources（事件化后入 core.perceive）",
         notes="存量直写图违例（screen_ocr.py:322–353 等）已登记 ARCHITECTURE_PLAN §6.2。"),
    Slot("WebSearch", "—",
         "actions/web_search.py（爬 Bing/Baidu HTML）",
         temporary=True,
         replace_at="—",
         notes="⏳ 建议换正式搜索 API；属陪伴侧，不影响认知层。"),
]


def table() -> list[dict]:
    return [asdict(s) for s in SLOTS]
