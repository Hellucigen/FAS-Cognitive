# capability_registry.py — Fascinator 能力注册表
# ============================================================================
# 发育阶段核心模块。
#
# 设计原则（最高优先级）：
#   这不是聊天模块，不是 personality 模块。
#   这是 Fascinator 对自己"能做什么 / 不能做什么"的统一记录。
#
#   目标：
#     1. 让系统可以查询"我是否具备某种能力"
#     2. 记录新获得的能力
#     3. 发现能力缺口
#     4. 为未来自主决策提供依据："我能处理吗？还是需要外部帮助？"
#
#   与图谱的关系：
#     - 每个能力在图谱中对应一个 infrastructure 节点
#     - 能力之间的依赖关系作为边存储
#     - registry 本身提供快速查询接口，图谱提供关系推理
#
#   设计约束：
#     - 不替代 KnowledgeGraph，而是补充
#     - 不保存认知状态，只保存能力清单
#     - 高内聚低耦合：独立模块，仅依赖 graph_model
# ============================================================================

import os
import json
import logging
import threading
from typing import Optional

from graph_model import KnowledgeGraph, Node, Edge, now_str

logger = logging.getLogger(__name__)

# ── 能力状态枚举 ──────────────────────────────────────────

CAPABILITY_STATUS = {
    "acquired":    "已获得 — 系统可以自主完成此能力",
    "partial":     "部分具备 — 系统可以在外部辅助下完成",
    "developing":  "发育中 — 正在建设此能力",
    "missing":     "缺失 — 系统尚不具备此能力",
    "external":    "外部依赖 — 此能力由外部提供（如 Development Companion）",
}

# ── 持久化路径 ────────────────────────────────────────────

REGISTRY_PATH = os.path.join("data", "capability_registry.json")


class CapabilityRegistry:
    """Fascinator 能力注册表。

    每个能力条目：
      {
        "id": "semantic_search",
        "name": "语义搜索",
        "description": "通过 Embedding 向量在图谱中查找语义相似节点",
        "status": "acquired",
        "category": "perception",
        "related_modules": ["embedding_manager.py"],
        "related_nodes": ["Embedding检索", "FAISS索引"],
        "dependencies": [],
        "acquired_at": "2026/07/04 17:00:00",
        "notes": ""
      }
    """

    def __init__(self, path: str = None):
        self._path = path or REGISTRY_PATH
        self._lock = threading.RLock()
        self._capabilities: dict[str, dict] = {}
        self._kg_ref: Optional[KnowledgeGraph] = None
        self._load()

    # ── 持久化 ──────────────────────────────────────────

    def _load(self):
        """从磁盘加载能力注册表"""
        if os.path.exists(self._path):
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self._capabilities = data.get("capabilities", {})
                logger.info(f"[CapabilityRegistry] 加载: {len(self._capabilities)} 条能力记录")
            except Exception as e:
                logger.warning(f"[CapabilityRegistry] 加载失败: {e}")
                self._capabilities = {}

    def _save(self):
        """持久化到磁盘"""
        try:
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump({
                    "capabilities": self._capabilities,
                    "updated_at": now_str(),
                }, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"[CapabilityRegistry] 保存失败: {e}")

    # ── 绑定图谱 ────────────────────────────────────────

    def set_graph(self, kg: KnowledgeGraph):
        """绑定知识图谱引用，用于注入能力节点"""
        self._kg_ref = kg

    # ── 核心查询接口 ────────────────────────────────────

    def _graph_status(self, capability_id: str):
        """图优先读（统一 Capability 层 2026-09-20）：能力节点的
        extra_attrs.status 是真相源，JSON 降为缓存。图谱未绑定/节点
        未建时返回 None → 回退缓存（启动窗口兼容）。"""
        kg = getattr(self, "_kg_ref", None)
        if kg is None:
            return None
        cap = self._capabilities.get(capability_id)
        if cap is None:
            return None
        node = kg.nodes.get(f"能力_{cap['name']}")
        if node is None:
            return None
        return str((node.extra_attrs or {}).get("status") or "") or None

    def has(self, capability_id: str) -> bool:
        """检查是否具备某能力（仅 acquired 和 external 返回 True）"""
        cap = self._capabilities.get(capability_id)
        if cap is None:
            return False
        status = self._graph_status(capability_id) or cap["status"]
        return status in ("acquired", "external")

    def can_handle(self, capability_id: str) -> bool:
        """检查是否能自主处理（仅 acquired 返回 True）"""
        cap = self._capabilities.get(capability_id)
        if cap is None:
            return False
        status = self._graph_status(capability_id) or cap["status"]
        return status == "acquired"

    def get(self, capability_id: str) -> Optional[dict]:
        """获取能力详情"""
        return self._capabilities.get(capability_id)

    def get_status(self, capability_id: str) -> str:
        """获取能力状态（图优先，JSON 缓存兜底）"""
        cap = self._capabilities.get(capability_id)
        if cap is None:
            return "missing"
        return self._graph_status(capability_id) or cap["status"]

    def list_all(self) -> list[dict]:
        """列出所有已注册能力"""
        return list(self._capabilities.values())

    def list_by_status(self, status: str) -> list[dict]:
        """按状态列出能力"""
        return [c for c in self._capabilities.values() if c["status"] == status]

    def list_by_category(self, category: str) -> list[dict]:
        """按分类列出能力"""
        return [c for c in self._capabilities.values() if c["category"] == category]

    def list_missing(self) -> list[dict]:
        """列出所有缺失或发育中的能力"""
        return [c for c in self._capabilities.values()
                if c["status"] in ("missing", "developing", "partial")]

    def list_acquired(self) -> list[dict]:
        """列出所有已获得的能力"""
        return [c for c in self._capabilities.values() if c["status"] == "acquired"]

    # ── 注册 / 更新接口 ─────────────────────────────────

    def register(self, capability_id: str, name: str, description: str,
                 status: str = "acquired", category: str = "general",
                 related_modules: list = None, related_nodes: list = None,
                 dependencies: list = None, notes: str = ""):
        """注册一个新能力或更新已有能力"""
        with self._lock:
            is_new = capability_id not in self._capabilities

            self._capabilities[capability_id] = {
                "id": capability_id,
                "name": name,
                "description": description,
                "status": status,
                "category": category,
                "related_modules": related_modules or [],
                "related_nodes": related_nodes or [],
                "dependencies": dependencies or [],
                "acquired_at": now_str() if status == "acquired" and is_new else
                               self._capabilities.get(capability_id, {}).get("acquired_at", now_str()),
                "notes": notes,
            }

            self._save()

            if is_new:
                logger.info(f"[CapabilityRegistry] + 新能力: {capability_id} ({status})")
            else:
                logger.info(f"[CapabilityRegistry] ~ 更新能力: {capability_id} → {status}")

            # 同步到图谱
            self._sync_to_graph(capability_id)

    def update_status(self, capability_id: str, status: str, notes: str = ""):
        """更新能力状态（如 partial → acquired）"""
        if capability_id not in self._capabilities:
            logger.warning(f"[CapabilityRegistry] 能力不存在: {capability_id}")
            return

        with self._lock:
            old_status = self._capabilities[capability_id]["status"]
            self._capabilities[capability_id]["status"] = status
            if status == "acquired" and old_status != "acquired":
                self._capabilities[capability_id]["acquired_at"] = now_str()
            if notes:
                self._capabilities[capability_id]["notes"] = notes

            self._save()
            logger.info(f"[CapabilityRegistry] 状态变更: {capability_id} {old_status} → {status}")

            # 同步到图谱
            self._sync_to_graph(capability_id)

    def remove(self, capability_id: str):
        """移除一个能力记录"""
        with self._lock:
            if capability_id in self._capabilities:
                del self._capabilities[capability_id]
                self._save()
                logger.info(f"[CapabilityRegistry] - 移除能力: {capability_id}")

    # ── 图谱同步 ────────────────────────────────────────

    def _sync_to_graph(self, capability_id: str):
        """将能力节点同步到知识图谱。

        能力节点是自认知内容（"我能做什么"），不是引擎零件：
        - self_capability=True + description → 能被语义召回命中，
          且参与回答上下文（见 graph_model.is_cognitive_visible）
        - 接线到 Self / 能力注册表 → 被扩散点亮，不靠每轮硬塞清单
        """
        if self._kg_ref is None:
            return

        cap = self._capabilities.get(capability_id)
        if cap is None:
            return

        node_id = f"能力_{cap['name']}"
        attrs = {
            "capability_id": capability_id,
            "status": cap["status"],
            "category": cap["category"],
            "self_capability": True,
            "description": cap.get("description") or cap["name"],
            # 统一 Capability 层（2026-09-20）：与具身 能力:xx 节点同族，
            # CapabilityIndex 按 type=capability 识别；status 的真相源即此节点。
            "type": "capability", "channel": "engine",
            "executors": list(cap.get("executors") or []),
        }
        aliases = [a for a in (cap.get("related_nodes") or []) if a]
        if aliases:
            attrs["aliases"] = aliases

        # 创建或更新能力节点
        if node_id not in self._kg_ref.nodes:
            node = Node(
                id=node_id,
                weight=0.4,
                label="infrastructure",
                confidence=1.0,
                graph_space="self",
                extra_attrs=attrs,
            )
            self._kg_ref.add_node(node)
            logger.debug(f"[CapabilityRegistry] 图谱节点已创建: {node_id}")
        else:
            node = self._kg_ref.nodes[node_id]
            node.extra_attrs.update(attrs)
            node.graph_space = "self"
            node.weight = 0.6 if cap["status"] == "acquired" else 0.3

        # 接线：Self/能力注册表 → 能力（幂等，缺边才建）
        for src, rel, w in (("Self", "能", 0.7), ("能力注册表", "包含", 0.6)):
            if src in self._kg_ref.nodes and not self._kg_ref.get_edge(src, node_id, rel):
                self._kg_ref.add_edge(Edge(src=src, dst=node_id, relation=rel,
                                           weight=w,
                                           relation_category="cognitive_relation"))

    # ── 发育阶段专用接口 ────────────────────────────────

    def bootstrap_development_stage(self, kg: KnowledgeGraph):
        """初始化发育阶段基础设施。

        在知识图谱中创建发育阶段相关节点，让 Fascinator 知道：
        - 自己处于发育阶段
        - 有 Development Companion 在帮助自己
        - 能力在逐步增长中
        """
        self.set_graph(kg)

        # ── 发育阶段核心节点 ──────────────────────────
        dev_nodes = [
            ("发育阶段", "infrastructure", {
                "phase": "development",
                "description": "Fascinator 正处于发育阶段，能力正在逐步建设中",
            }),
            ("DevelopmentCompanion", "infrastructure", {
                "role": "external_mentor",
                "description": "外部导师，帮助 Fascinator 生长能力，最终退出核心推理流程",
                "principle": "帮助 Fascinator 长出能力，而不是替 Fascinator 工作",
            }),
            ("能力注册表", "infrastructure", {
                "module": "capability_registry.py",
                "description": "Fascinator 自身能力的统一记录和查询入口",
                "self_capability": True,
                "aliases": ["我能做什么", "我会什么", "我有哪些能力", "能力清单",
                            "我有什么本事", "技能", "我的技能"],
            }),
            ("元认知", "infrastructure", {
                "description": "对自己认知状态的认知，包括能力边界、知识缺口、发育进度",
                "status": "developing",
            }),
            ("自主成长", "infrastructure", {
                "description": "Fascinator 的长期目标：摆脱外部依赖，实现自主学习、认知、行动",
                "status": "developing",
            }),
        ]

        for node_id, label, attrs in dev_nodes:
            if node_id not in kg.nodes:
                kg.add_node(Node(
                    id=node_id,
                    weight=0.5,
                    label=label,
                    confidence=0.9,
                    extra_attrs=attrs,
                ))
                logger.info(f"[CapabilityRegistry] 发育节点已创建: {node_id}")

        # ── 确保 Self 节点存在 ────────────────────────
        if "Self" not in kg.nodes:
            kg.add_node(Node(
                id="Self",
                weight=1.0,
                label="self",
                graph_space="self",
                confidence=1.0,
                extra_attrs={"description": "Fascinator 的自我节点，认知图谱聚合中心"},
            ))
            logger.info("[CapabilityRegistry] Self 节点已创建")

        # ── 发育阶段关系边 ────────────────────────────
        dev_edges = [
            ("Self", "处于", "发育阶段", 0.9),
            ("Self", "能力", "能力注册表", 0.8),
            ("Self", "正在学习", "元认知", 0.7),
            ("Self", "目标", "自主成长", 0.9),
            ("Self", "有导师", "DevelopmentCompanion", 0.8),
            ("DevelopmentCompanion", "帮助建设", "发育阶段", 0.8),
            ("DevelopmentCompanion", "目标", "自主成长", 0.9),
            ("能力注册表", "包含", "元认知", 0.6),
        ]

        for src, rel, dst, weight in dev_edges:
            exists = any(
                e.src == src and e.dst == dst and e.relation == rel
                for e in kg.edges
            )
            if not exists:
                kg.add_edge(Edge(src=src, dst=dst, relation=rel, weight=weight))
                logger.debug(f"[CapabilityRegistry] 发育边已创建: {src} -[{rel}]→ {dst}")

        # ── 注册初始能力清单 ─────────────────────────
        self._register_initial_capabilities()

        # ── 历史能力节点对齐（补自认知属性 + Self→能力 接线）──
        self.sync_all_to_graph()

        logger.info(f"[CapabilityRegistry] 发育阶段初始化完成 "
                    f"(能力: {len(self._capabilities)}, "
                    f"节点: {len(kg.nodes)}, "
                    f"边: {len(kg.edges)})")

    def _register_initial_capabilities(self):
        """注册系统初始能力清单。

        这些能力基于当前已有代码模块推断。
        只注册真实存在的能力，不虚构。
        """

        initial = [
            # ── 感知能力 ────────────────────────────
            ("nlp_extraction", "NLP实体关系提取", "从自然语言中提取实体、关系和言语行为分类",
             "acquired", "perception",
             ["nlp_processor.py"],
             ["NLP处理器"]),

            ("semantic_search", "语义搜索", "通过Embedding向量进行语义相似节点检索",
             "acquired", "perception",
             ["embedding_manager.py"],
             ["Embedding检索", "FAISS索引"]),

            ("ear_audio_perception", "听觉感知", "接收音频输入，分类语音/音乐/环境声并注入图谱",
             "acquired", "perception",
             ["ear/ear_processor.py"],
             ["Ear感知"]),

            ("vision_perception", "视觉感知", "从图像中发现对象、生成嵌入、匹配已有对象、注入图谱",
             "acquired", "perception",
             ["vision/vision_processor.py"],
             ["Vision感知"]),

            # ── 认知能力 ────────────────────────────
            ("activation_spreading", "激活扩散", "基于图谱的激活扩散机制，模拟注意力转移和认知聚焦",
             "acquired", "cognition",
             ["diffusion_engine.py", "graph_model.py"],
             ["激活扩散引擎"]),

            ("self_awareness", "自我感知", "通过Self节点聚合偏好、情绪、目标、经历的自我认知",
             "acquired", "cognition",
             ["self_graph.py"],
             ["Self"]),

            ("curiosity_driven_inquiry", "好奇心驱动提问", "遇到未知概念/关系时自动产生好奇并提问",
             "acquired", "cognition",
             ["curiosity_engine.py"],
             ["好奇", "主动提问"]),

            ("episodic_memory", "情景记忆", "短期经历存储、遗忘和晋升长期记忆",
             "acquired", "memory",
             ["episodic_buffer.py"],
             ["情景缓冲区"]),

            ("conversation_gap_detection", "对话缺口检测", "分析对话发现知识图谱中的缺口",
             "acquired", "cognition",
             ["conversation_gap_detector.py"],
             ["对话缺口检测"]),

            # ── 行动能力 ────────────────────────────
            ("wasd_keyboard_action", "WASD键盘操作", "执行WASD键盘按键的物理操作",
             "acquired", "action",
             ["Action/wasd.py", "Action/__init__.py"],
             ["按下W", "按下A", "按下S", "按下D"]),

            ("action_registry", "Action注册与分发", "Action模块的自动发现、注册和调用机制",
             "acquired", "action",
             ["Action/__init__.py"],
             ["Action注册表"]),

            # ── 知识管理能力 ────────────────────────
            ("knowledge_pack_management", "Knowledge Pack管理", "模块化知识包的加载、合并、启用/禁用",
             "acquired", "knowledge",
             ["knowledge_pack_manager.py"],
             ["KnowledgePack"]),

            ("graph_persistence", "图谱持久化", "知识图谱的JSON序列化/反序列化与自动保存",
             "acquired", "knowledge",
             ["graph_model.py"],
             ["知识图谱"]),

            # ── 元认知能力（发育中 / 缺失）───────────
            ("capability_self_awareness", "能力自我认知", "知道自己能做什么、不能做什么",
             "developing", "metacognition",
             ["capability_registry.py"],
             ["能力注册表", "元认知"],
             [],
             "刚创建，需要逐步填充和验证"),

            ("development_phase_awareness", "发育阶段感知", "知道自己在发育阶段，有外部导师在帮助",
             "developing", "metacognition",
             ["capability_registry.py"],
             ["发育阶段", "DevelopmentCompanion"],
             [],
             "刚创建，需要在交互中逐步强化"),

            ("autonomous_growth", "自主成长", "不依赖外部导师，自主发现缺口、学习、长出新能力",
             "missing", "metacognition",
             [],
             ["自主成长"],
             ["capability_self_awareness", "development_phase_awareness"],
             "长期目标，需要先具备基础元认知能力"),

            ("self_reflection", "自我反思", "定期对自身认知状态进行反思和总结",
             "partial", "metacognition",
             ["self_graph.py"],
             ["反思"],
             [],
             "已有反思机制，但还不够系统化"),

            ("tool_use_autonomy", "工具使用自主性", "自主发现、学习和调用新工具的能力",
             "missing", "metacognition",
             [],
             [],
             ["action_registry"],
             "目前工具需要手动注册"),

            ("social_interaction", "社交互动识别与回应", "识别问候/寒暄/道别等社交仪式性表达，通过图谱节点扩散触发LLM回答生成自然回复",
             "acquired", "social",
             ["nlp_processor.py", "app.py"],
             ["社交互动", "LLM回答"],
             [],
             "万物皆图：社交互动节点注入图谱 → 扩散激活LLM回答节点 → 生成回复"),

            ("alias_resolution", "输入别名解析", "将用户输入中的缩写/俗称/别称映射到图谱规范节点ID，通过节点extra_attrs.aliases实现",
             "acquired", "perception",
             ["app.py"],
             ["Minecraft", "Fascinator"],
             [],
             "别名长在节点上（extra_attrs.aliases），_fuzzy_match_node优先匹配别名"),

            ("auto_knowledge_consolidation", "提问驱动自动知识沉淀", "FAS提问（正式好奇或随口问答）后用户教学时，自动将知识写入图谱，无需人工审批",
             "acquired", "learning",
             ["app.py"],
             ["好奇", "知识完善", "更新记忆"],
             ["curiosity_driven_inquiry", "episodic_memory"],
             "双触发：curiosity_resolved（正式好奇）或 llm_answer以？结尾（随口问答）"),

            ("knowledge_gap_auto_fill", "知识缺口自动填补", "发现知识缺口后自主搜索和学习填补",
             "missing", "metacognition",
             [],
             [],
             ["curiosity_driven_inquiry", "conversation_gap_detection"],
             "目前只能检测缺口，不能自主填补"),

            # ── 行动能力（Action Concept 架构升级 2026-09-19）──
            ("action_intent_resolution", "动作意图解析",
             "从用户自然语言解析显式动作命令为结构化 Action Intent"
             "（fast pattern 快速入口 + 动作概念语义层 + 否定优先 + 命令性门）",
             "acquired", "action",
             ["action_concepts.py", "action_resolver.py", "minecraft/reflex.py"],
             ["网络搜索", "文件操作", "看屏幕", "FOLLOW", "进入Minecraft世界"],
             ["action_registry"],
             "正则仍是快速入口，但动作理解长在 Action Concept 上（图谱提供候选，执行经 Intent）"),
        ]

        for cap_data in initial:
            cap_id = cap_data[0]
            if cap_id not in self._capabilities:
                self.register(
                    capability_id=cap_id,
                    name=cap_data[1],
                    description=cap_data[2],
                    status=cap_data[3],
                    category=cap_data[4],
                    related_modules=cap_data[5],
                    related_nodes=cap_data[6],
                    dependencies=cap_data[7] if len(cap_data) > 7 else [],
                    notes=cap_data[8] if len(cap_data) > 8 else "",
                )

    # ── 能力缺口分析 ────────────────────────────────────

    def sync_all_to_graph(self):
        """把注册表里所有能力重新同步进图谱（启动时对齐，幂等）。

        历史能力节点建于旧版同步逻辑（无自认知属性、无边），启动重同步
        让它们补齐 description/self_capability 与 Self→能力 的接线。
        """
        for capability_id in list(self._capabilities.keys()):
            try:
                self._sync_to_graph(capability_id)
            except Exception as e:
                logger.warning(f"[CapabilityRegistry] 同步失败 {capability_id}: {e}")

    def analyze_gaps(self) -> list[dict]:
        """分析能力缺口，返回需要建设的能力列表。

        用于 Development Companion 决策：
        "下一步应该帮 Fascinator 建设什么能力？"
        """
        gaps = []
        for cap in self._capabilities.values():
            if cap["status"] in ("missing", "developing", "partial"):
                # 检查依赖是否满足
                deps_ready = all(
                    self.has(dep) for dep in cap.get("dependencies", [])
                )
                gaps.append({
                    "id": cap["id"],
                    "name": cap["name"],
                    "status": cap["status"],
                    "dependencies": cap.get("dependencies", []),
                    "dependencies_ready": deps_ready,
                    "priority": (
                        0 if cap["status"] == "developing" and deps_ready
                        else 1 if cap["status"] == "partial" and deps_ready
                        else 2 if cap["status"] == "missing" and deps_ready
                        else 3  # missing 且依赖未就绪
                    ),
                })

        # 按优先级排序：越小越优先建设
        gaps.sort(key=lambda g: g["priority"])
        return gaps

    def get_next_development_target(self) -> Optional[dict]:
        """获取下一个最值得建设的能力目标"""
        gaps = self.analyze_gaps()
        return gaps[0] if gaps else None


# ── 全局单例 ────────────────────────────────────────────

_registry: Optional[CapabilityRegistry] = None


def get_capability_registry() -> CapabilityRegistry:
    """获取全局能力注册表单例"""
    global _registry
    if _registry is None:
        _registry = CapabilityRegistry()
    return _registry
