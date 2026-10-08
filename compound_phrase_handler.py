# compound_phrase_handler.py — 复合短语判定系统
# ============================================================================
# FAS Phase 2: Compound Phrase Handling.
#
# 定位：插在 NLP 解析（nlp_processor.py）之后、未知概念检测（curiosity_engine.py）
#       之前，作为一层过滤器。不改变现有图谱基础设施（复用已有 graph_space /
#       relation_category）。
#
# 语言学基础：
#   - 配价理论（Valency Theory）— 关系名词 vs 绝对名词判定
#   - 向心结构理论（Head-driven Structure）— ATT 定中关系识别
#   - 组合性原则（Principle of Compositionality）— 组合性 vs 词汇化判定
#   - 框架语义学（Frame Semantics）— HAS_SALIENT_ATTRIBUTE 等 Frame 映射
#
# 五步判定流程：
#   Step 1 — 依存句法分析（ATT 定中关系）：head + modifier 识别
#   Step 2 — 配价测试：head 是关系名词还是绝对名词？
#   Step 3 — 向心性检查：head 在图谱中是否已知？
#   Step 4 — 组合性/词汇化判定：可替换性 + 语义透明度
#   Step 5 — 框架语义映射：按 Attribute Frame 生成图查询
#
# 设计原则（最高优先级）：
#   1. 不依赖 LLM — 全部判定逻辑基于规则/统计
#   2. 判定结果可解释 — 每个短语的分类附带原因
#   3. 不建 Concept 节点 — 关系型短语映射为关系边
#   4. 关系短语表开放扩展 — surface_forms 可持续添加
# ============================================================================

import logging
import json
import os
import re
from typing import Optional
from collections import OrderedDict

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
# 常量
# ═══════════════════════════════════════════════════════════════

# 高置信度关系名词中心语
# 这些词作中心语时，整个短语天然需要一个实体来绑定论元
# 例如："产品" → "谁的/什么的产品" → 天然关系型
RELATIONAL_HEAD_SET = {
    "产品", "创始人", "创办人", "创立者", "创建者", "奠基人",
    "总部", "位置", "地址", "地点", "所在地",
    "价格", "价钱", "售价", "定价", "单价", "费用",
    "颜色", "色彩", "色调",
    "材质", "材料", "原料", "成分", "用料",
    "味道", "口味", "风味", "滋味",
    "类型", "种类", "分类", "类别", "流派", "风格",
    "成员", "成员国", "会员",
    "作者", "发明人", "发明者", "设计者", "开发者", "制作人",
    "语言", "官方语言", "母语",
    "首都", "首府", "省会",
    "面积", "体积", "容量", "容积",
    "人口", "人口数", "居民数",
    "时间", "日期", "年份",
    "网址", "官网", "网站", "主页",
    "规模", "人数", "员工数",
    "CEO", "总裁", "总经理", "老板",
    "母公司", "控股公司",
    "配料", "食材", "佐料",
    "用户群", "目标用户", "受众", "客户群",
    "竞争对手", "竞品",
}

# 绝对名词白名单 — 这些中心语即使出现在"__的X"框架中也保持语义自足
ABSOLUTE_HEAD_WHITELIST = {
    "人", "地方", "东西", "事", "问题", "情况", "方法", "原因",
    "结果", "效果", "作用", "关系", "区别", "联系", "影响",
    "时候", "方式", "过程", "状态", "程度", "方面", "角度",
}

# 组合性测试：可替换语素对
# key = target, value = near-synonym substitutions for the modifier
SUBSTITUTION_PAIRS = {
    "代表": ["招牌", "主打", "核心", "拳头", "标志性"],
    "招牌": ["代表", "主打", "核心"],
    "主打": ["代表", "招牌", "核心"],
    "核心": ["代表", "主打", "主要"],
    "创始": ["创办", "创建", "发起"],
    "总部": ["主要办公", "核心办公"],
    "官方": ["正式", "授权"],
    "主要": ["核心", "重要", "关键"],
    "重要": ["主要", "核心", "关键"],
}

# 词汇化短语白名单 — 意义不可由字面推导
# 这些短语即使看起来是 modifier+head 结构，也不能按组合性原则拆解
LEXICALIZED_WHITELIST = {
    "黑马", "跳槽", "马路", "火车", "电脑", "手机",
    "热心", "伤心", "开心", "关心",
    "吃香", "吃醋", "吃紧", "吃苦",
    "打的", "买单", "买单", "出马",
    "加油", "充电", "攻关",
}

# 框架名 → relation_category 映射
FRAME_CATEGORY_MAP = {
    "HAS_SALIENT_ATTRIBUTE": "semantic_relation",
    "FOUNDED_BY": "semantic_relation",
    "HAS_HEADQUARTERS": "semantic_relation",
    "HAS_CEO": "semantic_relation",
    "FOUNDED_AT": "semantic_relation",
    "HAS_EMPLOYEE_COUNT": "semantic_relation",
    "LOCATED_AT": "semantic_relation",
    "HAS_WEBSITE": "semantic_relation",
    "CREATED_BY": "semantic_relation",
    "PUBLISHED_AT": "semantic_relation",
    "HAS_PRICE": "semantic_relation",
    "HAS_SIZE": "semantic_relation",
    "HAS_COLOR": "semantic_relation",
    "MADE_OF": "semantic_relation",
    "OWNED_BY": "semantic_relation",
    "HAS_POPULATION": "semantic_relation",
    "HAS_CAPITAL": "semantic_relation",
    "HAS_LANGUAGE": "semantic_relation",
    "HAS_MEMBER": "semantic_relation",
    "HAS_GENRE": "semantic_relation",
    "HAS_INGREDIENT": "semantic_relation",
    "HAS_TASTE": "semantic_relation",
    "HAS_CAPACITY": "semantic_relation",
    "TARGETS_AUDIENCE": "semantic_relation",
    "COMPETES_WITH": "semantic_relation",
}


# ═══════════════════════════════════════════════════════════════
# 关系短语表
# ═══════════════════════════════════════════════════════════════

class RelationalPhraseTable:
    """关系短语表：加载、查询、扩展 surface_forms → slot 映射。"""

    def __init__(self, table_path: str = None):
        self._slots: dict = {}
        self._surface_to_slot: dict = {}  # surface_form → slot_id
        self._path = table_path or os.path.join(
            os.path.dirname(__file__), "data", "relation_phrase_table.json"
        )
        self._load()

    def _load(self):
        if not os.path.exists(self._path):
            logger.warning(f"[PhraseTable] 关系短语表不存在: {self._path}，使用内置默认")
            return

        try:
            with open(self._path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._slots = data.get("slots", {})
            self._surface_to_slot.clear()

            for slot_id, slot_info in self._slots.items():
                for sf in slot_info.get("surface_forms", []):
                    if sf not in self._surface_to_slot:
                        self._surface_to_slot[sf] = slot_id
                    else:
                        logger.warning(
                            f"[PhraseTable] surface_form '{sf}' 重复: "
                            f"{self._surface_to_slot[sf]} vs {slot_id}"
                        )

            logger.info(
                f"[PhraseTable] 已加载 {len(self._slots)} 个槽位, "
                f"{len(self._surface_to_slot)} 个表面形式"
            )
        except Exception as e:
            logger.error(f"[PhraseTable] 加载失败: {e}")

    def lookup(self, phrase: str) -> Optional[dict]:
        """查找短语对应的槽位信息。"""
        slot_id = self._surface_to_slot.get(phrase)
        if slot_id and slot_id in self._slots:
            return {
                "slot_id": slot_id,
                **self._slots[slot_id],
            }
        return None

    def get_surface_forms(self, slot_id: str) -> list:
        """返回给定槽位的所有 surface_forms，用于边匹配 boost。"""
        slot = self._slots.get(slot_id, {})
        return slot.get("surface_forms", [])

    def lookup_by_head(self, head: str, head_category: str = None) -> list:
        """按中心语查找匹配的槽位（可能有多个候选）。"""
        matches = []
        for slot_id, slot_info in self._slots.items():
            constraint = slot_info.get("head_category_constraint", [])
            if not constraint or not head_category:
                matches.append({"slot_id": slot_id, **slot_info})
            elif head_category in constraint:
                matches.append({"slot_id": slot_id, **slot_info})
        return matches

    def is_known_surface_form(self, phrase: str) -> bool:
        return phrase in self._surface_to_slot

    @property
    def slot_count(self) -> int:
        return len(self._slots)

    @property
    def surface_form_count(self) -> int:
        return len(self._surface_to_slot)


# ═══════════════════════════════════════════════════════════════
# 轻量级中文依存句法分析器（仅 ATT 定中关系）
# ═══════════════════════════════════════════════════════════════

class LightweightDependencyParser:
    """轻量级中文依存句法分析器。

    仅识别 ATT（定中关系），不构建完整句法树。
    使用 jieba 分词 + POS 标注 + 规则模式匹配。
    """

    # 中文定中关系模式
    # 格式: (modifier_POS_pattern, head_POS_pattern, description)
    ATT_PATTERNS = [
        # 名词 + 名词：苹果/公司，代表/产品
        ({"n", "nr", "ns", "nt", "nz"}, {"n", "nr", "ns", "nt", "nz"}, "N+N 定中"),
        # 动词 + 名词：代表/产品，学习/目标
        ({"v", "vn"}, {"n", "nr", "ns", "nt", "nz"}, "V+N 定中"),
        # 形容词 + 名词：美丽/风景
        ({"a", "an"}, {"n", "nr", "ns", "nt", "nz"}, "ADJ+N 定中"),
        # 区别词 + 名词：女/老师，主要/原因
        ({"b"}, {"n", "nr", "ns", "nt", "nz"}, "DIST+N 定中"),
        # 名词 + 的 + 名词（"的"字结构）
        ({"n", "nr", "ns", "nt", "nz"}, {"n", "nr", "ns", "nt", "nz"}, "N+的+N"),
        # 方位词 + 名词：西部/城市
        ({"f"}, {"n", "nr", "ns", "nt", "nz"}, "LOC+N 定中"),
    ]

    def __init__(self):
        self._jieba_available = False
        try:
            import warnings
            with warnings.catch_warnings():
                # jieba 0.42.1 内部用 pkg_resources 取版本，setuptools 已弃用该 API
                # （上游未跟进）。这条 UserWarning 与我们无关，也不影响分词/POS 结果。
                warnings.filterwarnings(
                    "ignore", message="pkg_resources is deprecated as an API")
                import jieba
                import jieba.posseg as pseg
            # 词典构建/加载进度（"Building prefix dict ..."）默认打 stderr，
            # 属于首次运行的正常开销，不进日志；jieba 自身的 warning 仍照常。
            jieba.setLogLevel(logging.WARNING)
            self._pseg = pseg
            self._jieba_available = True
            logger.info("[DepParser] jieba 分词/POS 已就绪")
        except ImportError:
            logger.warning("[DepParser] jieba 不可用，使用字符级 heuristic fallback")

    def parse_att(self, text: str) -> list:
        """解析文本中的 ATT 定中关系。

        Returns:
            list of dict: [{"modifier": str, "head": str, "phrase": str, "pattern": str}, ...]
        """
        if self._jieba_available:
            return self._parse_with_jieba(text)
        else:
            return self._parse_heuristic(text)

    def _parse_with_jieba(self, text: str) -> list:
        """使用 jieba POS 标注解析定中关系。"""
        words = list(self._pseg.cut(text))
        if len(words) < 2:
            return []

        results = []
        # 滑动窗口 2-3 词
        for i in range(len(words) - 1):
            # ── 双词窗口 ──
            w1, p1 = words[i].word, words[i].flag
            w2, p2 = words[i + 1].word, words[i + 1].flag

            # 跳过单字虚词
            if len(w1) < 1 or len(w2) < 1:
                continue
            if p1 in {"u", "p", "c", "e", "y", "o", "w", "x"}:
                continue
            if p2 in {"u", "p", "c", "e", "y", "o", "w", "x"}:
                continue

            for mod_pos_set, head_pos_set, desc in self.ATT_PATTERNS:
                if p1 in mod_pos_set and p2 in head_pos_set:
                    phrase = w1 + w2
                    # 排除纯数词/量词组合
                    if p1 in {"m", "mq"} and p2 in {"m", "mq", "q"}:
                        continue
                    results.append({
                        "modifier": w1,
                        "head": w2,
                        "phrase": phrase,
                        "pattern": desc,
                    })
                    break

            # ── 三词窗口（含"的"的定中结构） ──
            if i < len(words) - 2:
                w3, p3 = words[i + 2].word, words[i + 2].flag
                if p2 == "u" and w2 == "的":
                    for mod_pos_set, head_pos_set, desc in self.ATT_PATTERNS:
                        if p1 in mod_pos_set and p3 in head_pos_set:
                            phrase = w1 + w2 + w3
                            results.append({
                                "modifier": w1,
                                "head": w3,
                                "phrase": phrase,
                                "pattern": f"N+的+N (via {w1})",
                            })
                            break

        return results

    def _parse_heuristic(self, text: str) -> list:
        """无 jieba 时的字符级 heuristic 回退。"""
        results = []

        # 规则 1："的"字结构：X的Y → mod=X, head=Y
        de_pattern = re.compile(r'(.{1,6})的(.{1,6})')
        for m in de_pattern.finditer(text):
            mod, head = m.group(1), m.group(2)
            if self._is_likely_content_word(head):
                results.append({
                    "modifier": mod,
                    "head": head,
                    "phrase": f"{mod}的{head}",
                    "pattern": "的-结构 (heuristic)",
                })

        # 规则 2：二字短语，常见 modifier+head 模式
        # 遍历文本中的连续二字片段
        for i in range(len(text) - 1):
            # 跳过标点和数字
            if text[i] in '，。！？；：、""''（）《》…—·\n\r\t ':
                continue
            if text[i + 1] in '，。！？；：、""''（）《》…—·\n\r\t ':
                continue
            bigram = text[i:i + 2]
            if len(bigram.strip()) != 2:
                continue

            # 简单判定：如果两个字都是汉字，且第一个字可能是修饰语
            if '一' <= bigram[0] <= '鿿' and '一' <= bigram[1] <= '鿿':
                # 常见的 modifier head 模式
                if self._is_common_modifier_head(bigram):
                    results.append({
                        "modifier": bigram[0],
                        "head": bigram[1],
                        "phrase": bigram,
                        "pattern": "Bigram NN (heuristic)",
                    })

        return results

    def _is_likely_content_word(self, s: str) -> bool:
        """简单判断是否为实词（非虚词/标点）。"""
        if len(s) < 1:
            return False
        if all(c in '的了着过吗呢吧啊呀' for c in s):
            return False
        return True

    def _is_common_modifier_head(self, bigram: str) -> bool:
        """判断二字组合是否符合常见 modifier+head 模式。"""
        # 常见的修饰语素
        common_modifiers = {
            "代", "招", "主", "核", "创", "首", "官", "大", "小",
            "新", "旧", "高", "低", "长", "短", "前", "后", "左", "右",
            "红", "蓝", "绿", "白", "黑", "黄",
        }
        # 常见的中心语素
        common_heads = {
            "品", "人", "地", "址", "格", "色", "料", "道", "型",
            "类", "员", "者", "言", "都", "积", "口", "间", "期",
            "年", "址", "站", "模", "数", "群", "手",
        }
        return bigram[0] in common_modifiers or bigram[1] in common_heads

    def find_head_modifier(self, phrase: str) -> Optional[dict]:
        """给定单个短语，识别其 head 和 modifier。

        Returns:
            dict or None: {"head": str, "modifier": str, "head_pos": str, "mod_pos": str}
        """
        if not phrase or len(phrase) < 2:
            return None

        if self._jieba_available:
            return self._find_hm_jieba(phrase)
        else:
            return self._find_hm_heuristic(phrase)

    def _find_hm_jieba(self, phrase: str) -> Optional[dict]:
        words = list(self._pseg.cut(phrase))
        if len(words) < 2:
            return None

        # 对于 2-3 词短语，找 ATT 关系
        for i in range(len(words) - 1):
            w1, p1 = words[i].word, words[i].flag
            w2, p2 = words[i + 1].word, words[i + 1].flag

            for mod_pos_set, head_pos_set, desc in self.ATT_PATTERNS:
                if p1 in mod_pos_set and p2 in head_pos_set:
                    return {
                        "head": w2, "head_pos": p2,
                        "modifier": w1, "mod_pos": p1,
                        "pattern": desc,
                    }

        # 回退：最后一个词作中心语
        return {
            "head": words[-1].word, "head_pos": words[-1].flag,
            "modifier": "".join(w.word for w in words[:-1]),
            "mod_pos": words[0].flag if words else "n",
            "pattern": "Fallback(last=HEAD)",
        }

    def _find_hm_heuristic(self, phrase: str) -> Optional[dict]:
        """Fallback: 假设中文复合名词最后一个语素是中心语。"""
        if len(phrase) < 2:
            return None
        # 中文通常是 modifier+head，head 在右侧
        # 例如：代表产品 → head=产品, mod=代表
        return {
            "head": phrase[-1] if len(phrase) <= 2 else phrase[-2:],
            "modifier": phrase[:-1] if len(phrase) <= 2 else phrase[:-2],
            "head_pos": "n",
            "mod_pos": "n",
            "pattern": "Heuristic(R-most=HEAD)",
        }


# ═══════════════════════════════════════════════════════════════
# 五步判定 Pipeline
# ═══════════════════════════════════════════════════════════════

class CompoundPhraseHandler:
    """复合短语判定处理器。

    对 NLP 解析出的候选短语执行五步判定：
      Step 1 — 依存句法分析（ATT 定中关系）
      Step 2 — 配价测试（关系名词 vs 绝对名词）
      Step 3 — 向心性检查（head 在图谱中已知？）
      Step 4 — 组合性/词汇化判定
      Step 5 — 框架语义映射
    """

    def __init__(self, kg, config: dict = None):
        self.kg = kg
        self.config = config or {}
        self.phrase_table = RelationalPhraseTable()
        self.dep_parser = LightweightDependencyParser()

        self._classifications: dict = {}  # phrase → classification cache
        self._frame_queries: list = []    # 本轮生成的 Frame 查询

    # ── 主入口 ──────────────────────────────────────────────

    def process(self, user_text: str, parsed_nodes: list,
                parsed_edges: list, cognitive_context: dict = None) -> dict:
        """对 NLP 解析出的候选节点进行分类。

        Args:
            user_text: 原始用户输入
            parsed_nodes: NLP 解析出的节点名列表
            parsed_edges: NLP 解析出的边列表
            cognitive_context: 认知分类上下文（dialogue_act 等）

        Returns:
            {
                "filtered_nodes": list,      # 预处理后的节点（供 curiosity 检测）
                "classifications": dict,     # {phrase: classification} 每节点的判定
                "frame_queries": list,       # 生成的 Frame 查询（供 Respond 使用）
                "relational_nodes": list,    # 被分类为关系型的节点
                "absolute_nodes": list,      # 被分类为绝对名词的节点
                "lexicalized_nodes": list,   # 被分类为词汇化的节点
            }
        """
        self._classifications.clear()
        self._frame_queries.clear()

        if not user_text or not parsed_nodes:
            return self._empty_result(parsed_nodes)

        # 对每个 parsed node 进行分类
        relational_nodes = []
        absolute_nodes = []
        lexicalized_nodes = []
        skipped_nodes = []

        for nid in parsed_nodes:
            nid_str = str(nid).strip()
            if not nid_str:
                skipped_nodes.append(nid_str)
                continue

            classification = self._classify(nid_str, user_text)
            self._classifications[nid_str] = classification

            cat = classification.get("category", "absolute")
            if cat == "relational":
                relational_nodes.append(nid_str)

                # Step 5: 生成 Frame 查询
                fq = self._step5_frame_mapping(nid_str, classification, user_text)
                if fq:
                    self._frame_queries.append(fq)
            elif cat == "lexicalized":
                lexicalized_nodes.append(nid_str)
            else:
                absolute_nodes.append(nid_str)

        # 构建 filtered_nodes：从 parsed_nodes 中移除关系型节点
        filtered_nodes = [
            n for n in parsed_nodes
            if str(n).strip() not in set(relational_nodes)
        ]

        logger.info(
            f"[CompoundPhrase] 判定完成: "
            f"relational={len(relational_nodes)}, "
            f"absolute={len(absolute_nodes)}, "
            f"lexicalized={len(lexicalized_nodes)}, "
            f"frame_queries={len(self._frame_queries)}"
        )

        return {
            "filtered_nodes": filtered_nodes,
            "classifications": dict(self._classifications),
            "frame_queries": list(self._frame_queries),
            "relational_nodes": relational_nodes,
            "absolute_nodes": absolute_nodes,
            "lexicalized_nodes": lexicalized_nodes,
        }

    def _empty_result(self, parsed_nodes: list) -> dict:
        return {
            "filtered_nodes": list(parsed_nodes),
            "classifications": {},
            "frame_queries": [],
            "relational_nodes": [],
            "absolute_nodes": list(parsed_nodes),
            "lexicalized_nodes": [],
        }

    # ── 单短语分类（执行 Step 1-4） ─────────────────────────

    def _classify(self, phrase: str, context_text: str = "") -> dict:
        """对单个短语执行 Step 1-4 判定。"""
        result = {
            "phrase": phrase,
            "category": "absolute",     # absolute | relational | lexicalized
            "step1_hm": None,           # head-modifier analysis
            "step2_valency": None,      # valency test result
            "step3_head_known": None,   # is head in graph?
            "step4_compositional": None,  # compositionality test
            "reason": "",
        }

        # ── 快速路径 1：词汇化白名单 ──
        if phrase in LEXICALIZED_WHITELIST:
            result["category"] = "lexicalized"
            result["reason"] = "词汇化白名单 — 不可按字面拆解"
            return result

        # ── 快速路径 2：关系短语表精确匹配 ──
        slot_match = self.phrase_table.lookup(phrase)
        if slot_match:
            result["category"] = "relational"
            result["frame_slot"] = slot_match
            result["reason"] = f"关系短语表精确匹配 → slot={slot_match['slot_id']}"
            return result

        # ── Step 1: 依存句法分析 ──
        hm = self.dep_parser.find_head_modifier(phrase)
        if hm is None:
            # 单字短语：按绝对名词处理
            result["category"] = "absolute"
            result["reason"] = "单语素无法分析 head/modifier"
            return result

        result["step1_hm"] = hm
        head = hm["head"]

        # ── Step 2: 配价测试 ──
        valency = self._step2_valency_test(head, phrase, hm)
        result["step2_valency"] = valency

        if valency["is_relational"]:
            # 关系名词 → 关系型短语
            result["category"] = "relational"
            result["reason"] = f"配价测试判定为关系名词: head='{head}' → {valency['reason']}"
            # 尝试匹配 phrase table（按 head）
            if not slot_match:
                head_matches = self.phrase_table.lookup_by_head(head)
                if head_matches:
                    result["frame_slot"] = head_matches[0]
            return result

        # ── Step 3: 向心性检查（仅绝对名词短语） ──
        head_known = self._step3_head_check(head)
        result["step3_head_known"] = head_known

        # ── Step 4: 组合性/词汇化判定 ──
        comp_test = self._step4_compositionality(phrase, hm)
        result["step4_compositional"] = comp_test

        if comp_test["is_compositional"]:
            # 组合性 → 不建节点，归一化到关系短语表
            result["category"] = "relational"
            result["reason"] = (
                f"组合性判定: 可替换且语义透明 → 建议归入关系短语表 "
                f"(也许需要新建槽位)"
            )
            # 尝试生成简化的 frame 信息
            result["frame_slot"] = {
                "slot_id": f"COMPOSITIONAL_{head}",
                "relation": "HAS_ATTRIBUTE",
                "attribute_type": head.upper(),
                "head_category_constraint": [head],
                "surface_forms": [phrase],
                "description": f"组合性短语: {phrase} (未在短语表中注册)",
                "_suggested": True,
            }
        else:
            # 词汇化 → 走正常 Concept 节点流程
            result["category"] = "lexicalized"
            result["reason"] = "组合性测试未通过 — 按词汇化处理，走正常未知检测"

        return result

    # ── Step 2: 配价测试 ──────────────────────────────────

    def _step2_valency_test(self, head: str, phrase: str, hm: dict) -> dict:
        """配价测试：head 是关系名词还是绝对名词？

        判定规则（优先级从高到低）：
        1. ABSOLUTE_HEAD_WHITELIST → 绝对名词
        2. RELATIONAL_HEAD_SET 精确匹配 → 关系名词
        3. 关系短语表的 head_category_constraint 包含 head → 关系名词
        4. 启发式："__的X"框架语义自足性测试
        """
        result = {
            "head": head,
            "is_relational": False,
            "reason": "",
        }

        # 规则 1: 绝对名词白名单
        if head in ABSOLUTE_HEAD_WHITELIST:
            result["reason"] = "绝对名词白名单"
            return result

        # 规则 2: 高置信度关系名词集合
        if head in RELATIONAL_HEAD_SET:
            result["is_relational"] = True
            result["reason"] = f"匹配高置信度关系名词集合"
            return result

        # 规则 3: 检查关系短语表的 head_category_constraint
        for slot_id, slot_info in self.phrase_table._slots.items():
            constraint = slot_info.get("head_category_constraint", [])
            if head in constraint:
                result["is_relational"] = True
                result["reason"] = f"head 在短语表的 {slot_id}.head_category_constraint 中"
                return result

        # 规则 4: 启发式判定
        # 如果短语长度 >= 3 且 modifier 是常见修饰词，head 可能是关系名词
        # 这个判定偏保守（宁可漏判不可误判）
        if len(phrase) >= 3:
            mod = hm.get("modifier", "")
            # 常见的转喻/修饰模式: "的"字结构中 head 为关系名词的概率高
            if "的" in phrase:
                result["is_relational"] = True
                result["reason"] = '"的"字结构 → 大概率关系型'
                return result

        # 默认：保守判定为非关系型（让 curiosity 处理）
        result["reason"] = "未命中任何关系型规则，保守判定为绝对名词"
        return result

    # ── Step 3: 向心性检查 ──────────────────────────────────

    def _step3_head_check(self, head: str) -> dict:
        """检查 head 是否在图谱 name_to_node 中已知。

        注意：这里检查的是图谱自身的节点映射（包括 engine.name_to_node），
        该映射在 compound phrase handler 初始化后由 app.py 传入。
        """
        # 通过传入的 kg 直接查
        node = self.kg.get_node(head)
        if node:
            return {
                "head": head,
                "known": True,
                "node_exists": True,
                "node_label": node.label,
                "node_space": node.graph_space,
            }

        # 可能 head 在 extra_attrs 的别名中
        for nid, n in self.kg.nodes.items():
            aliases = n.extra_attrs.get("aliases", [])
            if isinstance(aliases, list) and head in aliases:
                return {
                    "head": head,
                    "known": True,
                    "node_exists": True,
                    "node_label": n.label,
                    "node_space": n.graph_space,
                    "matched_via": "alias",
                    "matched_node": nid,
                }

        return {
            "head": head,
            "known": False,
            "node_exists": False,
        }

    # ── Step 4: 组合性/词汇化判定 ──────────────────────────

    def _step4_compositionality(self, phrase: str, hm: dict) -> dict:
        """组合性/词汇化判定。

        两项测试（任一不通过 → 词汇化）：
          a. 可替换性测试：用近义语素替换 modifier，意义是否合理保留
          b. 语义透明度测试：字面拼出的意思是否等于实际使用的意思
        """
        mod = hm.get("modifier", "")
        head = hm.get("head", "")

        result = {
            "is_compositional": True,
            "tests": {},
        }

        # ── 测试 a: 可替换性 ──
        substitutions = SUBSTITUTION_PAIRS.get(mod, [])
        if substitutions:
            # 有可用替换词 → 说明 modifier 是功能性语素而非固化搭配
            result["tests"]["substitution"] = {
                "passed": True,
                "sample": f"{substitutions[0]}{head}",
                "candidates": substitutions[:3],
            }
        else:
            # 无替换词 → 不通过（可能是词汇化的信号）
            # 但如果短语很短（2字）且都是常见字，放宽判定
            if len(phrase) <= 2:
                result["tests"]["substitution"] = {
                    "passed": True,
                    "note": "二字短语，放宽可替换性要求",
                }
            else:
                result["tests"]["substitution"] = {
                    "passed": False,
                    "note": f"modifier='{mod}' 无可替换语素",
                }
                result["is_compositional"] = False

        # ── 测试 b: 语义透明度 ──
        transparent = self._semantic_transparency_test(phrase, mod, head)
        result["tests"]["transparency"] = transparent
        if not transparent.get("passed", False):
            result["is_compositional"] = False

        return result

    def _semantic_transparency_test(self, phrase: str, mod: str, head: str) -> dict:
        """语义透明度测试：字面意思是否等于实际意思。

        规则：
        - modifier + head 的字面组合可以自然推得整体含义 → transparent
        - 如果整体含义 ≠ modifier字面 + head字面 → opaque（词汇化）
        """
        # 主要依赖已知的词汇化白名单和结构特征
        # 对于中文复合词，大多数二字 N+N/V+N 结构是透明的

        # 检测整体转喻/隐喻信号：
        # - 包含身体部位且不指实际身体部位 → 可能词汇化
        body_parts = {"头", "手", "脚", "眼", "口", "心", "脑", "面", "背"}
        for bp in body_parts:
            if bp in phrase:
                # 检查是否是身体部位的常规用法
                if bp == "手" and head in {"手", "脚"}:
                    return {"passed": True, "note": f"身体部位 '{bp}' 常规用法"}

        # 对于中文：大多数非列表中的复合词都是语义透明的
        if phrase in LEXICALIZED_WHITELIST:
            return {"passed": False, "reason": f"'{phrase}' 在词汇化白名单中"}

        return {"passed": True, "note": "字面组合可推导整体含义"}

    # ── Step 5: 框架语义映射 ──────────────────────────────

    def _step5_frame_mapping(self, phrase: str, classification: dict,
                             user_text: str) -> Optional[dict]:
        """按 Attribute Frame 结构生成图查询。

        Frame: HAS_SALIENT_ATTRIBUTE
          Possessor:      <上下文中的主体>
          Attribute-Type: <归一化后的槽位类型>
          Value:          <待填，即图查询目标>
          Head-Category:  <中心语类别>
        """
        slot_info = classification.get("frame_slot")
        if not slot_info:
            return None

        frame_name = slot_info.get("relation", "HAS_ATTRIBUTE")
        attribute_type = slot_info.get("attribute_type", slot_info.get("slot_id", ""))
        head_category = slot_info.get("head_category_constraint", [""])[0] if slot_info.get("head_category_constraint") else ""

        # 从上下文中提取可能的 Possessor
        # 策略：在当前激活节点、NLP 解析的实体中寻找最可能的主体
        possessor = self._infer_possessor(user_text, classification)

        return {
            "frame": frame_name,
            "attribute_type": attribute_type,
            "possessor": possessor,
            "value_placeholder": True,
            "head_category": head_category,
            "surface_phrase": phrase,
            "slot_id": slot_info.get("slot_id", ""),
            "relation_category": FRAME_CATEGORY_MAP.get(frame_name, "semantic_relation"),
            "query_description": f"查询 {possessor or '?'} 的 {attribute_type} ({phrase})",
        }

    def _infer_possessor(self, user_text: str, classification: dict) -> Optional[str]:
        """从用户输入和当前图谱状态推断 Frame 的 Possessor。

        策略（优先级递减）：
        1. 在用户输入中寻找与关系短语相邻的已知实体
        2. 图谱中当前激活度最高的实体节点
        """
        # 策略 1: 从用户文本中提取 "X的Y" 结构中的 X
        phrase = classification.get("phrase", "")
        # 在 user_text 中寻找 "...的{phrase}" 或 "{entity}的{phrase}" 模式
        de_pattern = re.compile(r'(.{1,10})的' + re.escape(phrase))
        m = de_pattern.search(user_text)
        if m:
            candidate = m.group(1)
            # 检查 candidate 是否在图谱中
            node = self.kg.get_node(candidate)
            if node:
                return candidate
            # 也检查是否在 parsed nodes 中
            return candidate  # 返回候选，即使不在图谱中（可能是新实体）

        # 策略 2: "X是什么/是谁" 模式 — X 可能是 Possessor
        what_pattern = re.compile(r'(.{1,10})的?' + re.escape(phrase) + r'是什么')
        m = what_pattern.search(user_text)
        if m:
            candidate = m.group(1).rstrip("的")
            if candidate:
                node = self.kg.get_node(candidate)
                if node:
                    return candidate
                return candidate

        # 策略 3: 使用图谱中当前激活值最高的实体节点
        if hasattr(self.kg, '_all_nodes'):
            top_entities = sorted(
                [n for n in self.kg._all_nodes() if n.activation > 0.3],
                key=lambda n: n.activation,
                reverse=True
            )[:5]
            for node in top_entities:
                if node.label in ("declarative-semantic", "declarative-episodic"):
                    return node.id

        return None

    # ── 辅助方法 ──────────────────────────────────────────

    def get_classification(self, phrase: str) -> Optional[dict]:
        """获取指定短语的分类结果（需先调用 process）。"""
        return self._classifications.get(phrase)

    def get_frame_queries(self) -> list:
        """获取本轮生成的所有 Frame 查询。"""
        return list(self._frame_queries)

    def is_relational(self, phrase: str) -> bool:
        """快速查询：短语是否被判定为关系型。"""
        c = self._classifications.get(phrase, {})
        return c.get("category") == "relational"

    def get_relational_edges(self) -> list:
        """从 Frame 查询生成图谱边的建议列表。

        这些边可以被 Respond 路径使用，填充 HAS_SALIENT_ATTRIBUTE 等关系。
        """
        edges = []
        for fq in self._frame_queries:
            possessor = fq.get("possessor")
            if possessor:
                edges.append({
                    "src": possessor,
                    "dst": f"?{fq['attribute_type']}",
                    "relation": fq["frame"],
                    "relation_category": fq.get("relation_category", "semantic_relation"),
                    "weight": 0.8,
                    "_meta": {
                        "surface_phrase": fq.get("surface_phrase"),
                        "attribute_type": fq.get("attribute_type"),
                        "query_description": fq.get("query_description"),
                    },
                })
        return edges

    def reset(self):
        """清空缓存（每个对话轮次开始时调用）。"""
        self._classifications.clear()
        self._frame_queries.clear()


# ═══════════════════════════════════════════════════════════════
# 独立测试接口
# ═══════════════════════════════════════════════════════════════

def test_pipeline(phrases: list, user_text: str = "",
                  kg=None, verbose: bool = True) -> dict:
    """对一组短语运行完整 Pipeline 并打印结果。"""
    if kg is None:
        # 创建空图谱用于测试
        from graph_model import KnowledgeGraph
        kg = KnowledgeGraph()

    handler = CompoundPhraseHandler(kg)
    result = handler.process(user_text, phrases, [])

    if verbose:
        print(f"\n{'='*60}")
        print(f"输入: {user_text or '(无)'}")
        print(f"候选短语: {phrases}")
        print(f"\n判定结果:")
        print(f"  Relational  ({len(result['relational_nodes'])}): {result['relational_nodes']}")
        print(f"  Absolute    ({len(result['absolute_nodes'])}): {result['absolute_nodes']}")
        print(f"  Lexicalized ({len(result['lexicalized_nodes'])}): {result['lexicalized_nodes']}")
        print(f"\n分类详情:")
        for phrase, c in result["classifications"].items():
            print(f"  [{c['category']:12s}] {phrase:20s} — {c.get('reason', 'N/A')}")
        if result["frame_queries"]:
            print(f"\nFrame 查询:")
            for fq in result["frame_queries"]:
                print(f"  {fq['frame']}[{fq['attribute_type']}] ← {fq.get('possessor', '?')}")
        print(f"{'='*60}")

    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    # 空图谱测试
    from graph_model import KnowledgeGraph
    kg = KnowledgeGraph()

    # 测试用例
    test_cases = [
        ("麦当劳的代表产品是什么", ["麦当劳", "代表产品"]),
        ("苹果公司的创始人是谁", ["苹果公司", "创始人"]),
        ("故宫的代表景点是什么", ["故宫", "代表景点"]),
        ("这个黑马是什么意思", ["黑马"]),
        ("木筏通关有什么技巧", ["木筏", "通关", "技巧"]),
        ("我想了解量子计算的基本原理", ["量子计算", "基本原理"]),
        ("你听过吸吐这首歌吗", ["吸吐", "歌"]),
    ]

    for text, nodes in test_cases:
        test_pipeline(nodes, text, kg=kg)

    print("\n✅ 测试完成")
