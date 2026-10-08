# embedding_manager.py — Embedding Retrieval Layer for Fascinator
# FAISS + BGE embeddings for semantic node lookup
# 只负责找到激活入口，不替代扩散引擎

import os
import json
import logging
import threading
import numpy as np
from typing import Optional

logger = logging.getLogger(__name__)

# faiss 在 import 时会先尝试加载 AVX2 版 SWIG 模块再回退，并把这两步都打成
# INFO。faiss-cpu 的 Windows wheel 里只有 swigfaiss（无 swigfaiss_avx2），
# 所以每次启动都会出现"Loading faiss with AVX2 support." +
# "Could not load library with AVX2 support due to: ModuleNotFoundError"
# ——看着像故障，其实是正常回退：回退后的构建功能完整（本图索引只有几千条
# 512 维向量，AVX2 与否无感）。这里按掉这条噪音，warning/error 仍照常输出。
logging.getLogger("faiss.loader").setLevel(logging.WARNING)



def _resolve_local_model_path() -> str:
    """Resolve BGE model path: E:/Models > env var > HuggingFace fallback."""
    central = "E:/Models/bge-small-zh-v1.5"
    if os.path.isdir(central):
        return os.path.abspath(central)
    env_path = os.environ.get("BGE_MODEL_PATH")
    if env_path and os.path.isdir(env_path):
        return os.path.abspath(env_path)
    return "BAAI/bge-small-zh-v1.5"

def _resolve_embedding_device() -> str:
    """嵌入推理设备：config.embedding_device = auto | cpu | cuda。

    默认 auto（有 CUDA 就用 GPU）。选择理由与实测（RTX 4070 Laptop，
    bge-small-zh，789 条节点文本）：
      全量索引重建：GPU 1.25s vs CPU 8s（GPU 快 6 倍，受益明显）
      单条查询：    GPU 8ms   vs CPU 17ms
    但 GPU 走 WDDM 时首次调用要等显卡从低功耗态唤醒（可到数十秒），
    启动即重建索引的进程会把这笔开销算进启动时间。想稳就显式写 cpu。
    """
    choice = "auto"
    try:
        import config as _cfg
        choice = str(_cfg.DEFAULT_CONFIG.get("embedding_device", "auto")).lower()
    except Exception:
        pass
    if choice == "cpu":
        return "cpu"
    if choice in ("cuda", "gpu"):
        return "cuda"
    try:
        import torch
        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:
        return "cpu"


class EmbeddingProvider:
    """Embedding 模型封装，未来可替换"""

    def __init__(self, model_name: str = None):
        self.model_name = model_name or _resolve_local_model_path()
        self._model = None
        self._device = _resolve_embedding_device()

        logger.info(f"[Embedding] Provider init: {self.model_name} (device={self._device})")

    @property
    def model(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(self.model_name, device=self._device)
        return self._model

    @property
    def dim(self) -> int:
        return self.model.get_sentence_embedding_dimension()

    def encode(self, texts: list) -> np.ndarray:
        return self.model.encode(texts, normalize_embeddings=True, show_progress_bar=False)

    def encode_single(self, text: str) -> np.ndarray:
        return self.encode([text])[0]


class EmbeddingManager:
    """节点 Embedding 索引管理器 (FAISS IndexFlatIP + L2 归一化)"""

    def __init__(self, provider: EmbeddingProvider = None, cache_dir: str = "data/faiss"):
        self.provider = provider or EmbeddingProvider()
        self.cache_dir = cache_dir
        self.index = None
        self.node_mapping: list[str] = []      # idx → node_id
        self.attr_mapping: list[dict] = []      # idx → {node_id, attr_key, attr_value}
        self._pending: list[str] = []          # 待增量索引的节点 id（图写入侧登记）
        self._needs_rebuild = False            # 有节点被删除 → FAISS 无法摘向量，需全量重建
        self._signature = ""                   # 索引对应的图谱内容签名（启动时比对）
        self._lock = threading.RLock()

        os.makedirs(self.cache_dir, exist_ok=True)
        self._load_cache()

    # ── 构建统一文本 ──────────────────────────────────────

    @staticmethod
    def _build_node_text(node) -> str:
        """节点+属性 → 统一文本（参与 embedding）"""
        parts = [node.id]

        extra = getattr(node, "extra_attrs", {}) or {}
        aliases = extra.get("aliases", [])
        if isinstance(aliases, list):
            parts.extend(aliases)
        elif isinstance(aliases, str):
            parts.append(aliases)

        desc = extra.get("description", "")
        if desc:
            parts.append(desc)

        tags = extra.get("tags", [])
        if isinstance(tags, list):
            parts.extend(tags)
        elif isinstance(tags, str):
            parts.append(tags)

        return " ".join(str(p) for p in parts if p)

    @staticmethod
    def _build_attr_text(node_id: str, key: str, value) -> str:
        v = str(value) if value else ""
        if v and not v.startswith(str(node_id)):
            return f"{node_id} {key} {v}"
        return v

    # ── 索引构建 ──────────────────────────────────────────

    def build_index(self, graph):
        """全量重建 FAISS 索引（节点 + 属性一起参与）

        编码很慢（数百节点可达数十秒），不能持有 emb 锁——否则图写入侧
        mark_dirty 会跟着一起卡。这里先快照、再编码、最后原子换索引。
        """
        texts = []
        id_list = []

        # 快照：持 kg 锁只做遍历取值，不动图
        kg_lock = getattr(graph, "_lock", None)
        if kg_lock is not None:
            kg_lock.acquire()
        try:
            nodes_snapshot = list(graph.nodes.values())
        finally:
            if kg_lock is not None:
                kg_lock.release()

        for node in nodes_snapshot:
            t, i = self._node_texts(node)
            texts.extend(t)
            id_list.extend(i)

        if not texts:
            logger.warning("[Embedding] 图谱为空，跳过索引构建")
            return

        vectors = self.provider.encode(texts)

        import faiss
        new_index = faiss.IndexFlatIP(self.provider.dim)
        new_index.add(vectors.astype(np.float32))

        with self._lock:
            self.index = new_index
            self.node_mapping = id_list
            self._pending.clear()
            self._needs_rebuild = False
            self._signature = self._graph_signature(graph)
            self._save_cache()

        logger.info(f"[Embedding] 索引构建完成: {len(texts)} 条目, {len(set(id_list))} 唯一节点")

    # ── 搜索 ──────────────────────────────────────────────

    def search(self, query: str, top_k: int = 20, min_similarity: float = 0.4) -> list:
        """搜索最相似的节点 (返回余弦相似度, 即 L2 归一化后内积)"""
        if not self.index or not self.node_mapping:
            return []

        with self._lock:
            vec = self.provider.encode_single(query).astype(np.float32).reshape(1, -1)
            distances, indices = self.index.search(vec, min(top_k, len(self.node_mapping)))

            results = []
            seen = set()
            for idx, dist in zip(indices[0], distances[0]):
                if idx < 0 or idx >= len(self.node_mapping):
                    continue
                nid = self.node_mapping[idx]
                sim = float(dist)
                if sim < min_similarity:
                    continue
                if nid in seen:
                    continue
                seen.add(nid)
                results.append({"node_id": nid, "similarity": round(sim, 4)})

            return results

    def search_debug(self, query: str, top_k: int = 10) -> dict:
        """调试搜索：返回候选+距离详情"""
        raw = self.search(query, top_k=top_k, min_similarity=0.0)
        return {
            "query": query,
            "candidates": raw[:top_k],
            "top_hit": raw[0]["node_id"] if raw else None,
            "top_similarity": raw[0]["similarity"] if raw else 0.0,
        }

    # ── 增量更新 ──────────────────────────────────────────

    @staticmethod
    def _graph_signature(graph) -> str:
        """索引内容签名 = 所有节点参与 embedding 的文本。变了就该重建。"""
        import hashlib
        h = hashlib.sha1()
        kg_lock = getattr(graph, "_lock", None)
        if kg_lock is not None:
            kg_lock.acquire()
        try:
            nodes_snapshot = list(graph.nodes.values())
        finally:
            if kg_lock is not None:
                kg_lock.release()
        for node in nodes_snapshot:
            texts, _ids = EmbeddingManager._node_texts(node)
            for t in texts:
                h.update(t.encode("utf-8"))
                h.update(b"\x1e")
        return h.hexdigest()

    def refresh(self, graph):
        """启动时对齐索引与图谱：签名一致就复用，不一致（或首次）全量重建。"""
        sig = self._graph_signature(graph)
        with self._lock:
            stale = (self.index is None
                     or not self.node_mapping
                     or self._signature != sig)
            idx_n = len(set(self.node_mapping)) if self.node_mapping else 0
        if stale:
            logger.info(f"[Embedding] 索引与图谱不一致 (图 {len(graph.nodes)} vs 索引 {idx_n}) → 全量重建")
            self.build_index(graph)
            return True
        logger.info(f"[Embedding] 索引命中缓存: {self.stats()}")
        self.flush_pending(graph)
        return False

    # ── 增量同步（图写入 → 索引跟随）──────────────────────

    def mark_dirty(self, node_id: str):
        """登记待索引节点。只记 id 不编码——图谱写路径不能被 embedding 阻塞。"""
        if not node_id:
            return
        with self._lock:
            self._pending.append(str(node_id))

    def flush_pending(self, graph, limit: int = 400):
        """把待索引节点增量编码进索引（启动时 + 持续认知循环周期性调用）。

        图谱是唯一真源：这里只从 graph 读节点、补进 FAISS，不改变图谱任何状态。
        索引构建失败/模型缺失时静默跳过——召回退化，认知照常。
        """
        with self._lock:
            need_rebuild = self._needs_rebuild
            if need_rebuild:
                self._needs_rebuild = False
            pending, self._pending = self._pending[:limit], self._pending[limit:]

        if need_rebuild:
            # 删除节点后 FAISS 无法摘向量 → 全量重建（图谱为真源）
            logger.info("[Embedding] 检测到待重建标记 → 全量重建")
            self.build_index(graph)
            return len(graph.nodes)

        todo, seen = [], set()
        for nid in pending:
            if nid in seen:
                continue
            seen.add(nid)
            node = graph.nodes.get(nid)
            if node is not None:
                todo.append(node)

        if not todo:
            return 0

        try:
            texts, ids = [], []
            for node in todo:
                t, i = self._node_texts(node)
                texts.extend(t)
                ids.extend(i)
            if not texts:
                return 0
            vectors = self.provider.encode(texts)
            with self._lock:
                self.index.add(vectors.astype(np.float32))
                self.node_mapping.extend(ids)
            self._signature = self._graph_signature(graph)
            self._save_cache()
            logger.info(f"[Embedding] 增量同步: +{len(todo)} 节点 / {len(texts)} 条目 "
                        f"(待索引剩余 {len(self._pending)})")
            return len(todo)
        except Exception as e:
            logger.warning(f"[Embedding] 增量同步失败: {e}")
            return 0

    @staticmethod
    def _node_texts(node):
        """节点 → (文本列表, 对应 node_id 列表)。节点文本 + 属性文本一并参与。"""
        nid = getattr(node, "id", str(node))
        texts = [EmbeddingManager._build_node_text(node)]
        ids = [nid]
        extra = getattr(node, "extra_attrs", {}) or {}
        for k, v in extra.items():
            if k in ("aliases", "description", "tags"):
                continue
            at = EmbeddingManager._build_attr_text(nid, k, v)
            if at.strip():
                texts.append(at)
                ids.append(nid)
        return texts, ids

    def add_node(self, node):
        """新增节点 → 增量更新索引（图写入侧调用，允许阻塞单次编码）"""
        with self._lock:
            if not self.index:
                return
        try:
            texts, ids = self._node_texts(node)
            vectors = self.provider.encode(texts)
            with self._lock:
                self.index.add(vectors.astype(np.float32))
                self.node_mapping.extend(ids)
            self._save_cache()
            logger.info(f"[Embedding] 新增节点: {getattr(node, 'id', '?')}")
        except Exception as e:
            logger.warning(f"[Embedding] add_node failed: {e}")

    def remove_node(self, node_id: str):
        """删除节点 → 全量重建（FAISS 不支持直接删除）"""
        with self._lock:
            if not self.node_mapping:
                return
            old_count = len(self.node_mapping)
            # 过滤掉所有指向该 node_id 的映射
            keep = [(i, m) for i, m in enumerate(self.node_mapping) if m != node_id]
            if len(keep) == old_count:
                return  # 没找到，无需重建
            self.node_mapping = [m for _, m in keep]
            # 从 index 重建（FAISS IndexFlatIP 不能删向量）
            self._rebuild_index_from_mapping()

    def _rebuild_index_from_mapping(self):
        """从现有 node_mapping 重建 FAISS 索引（仅在有删除时调用）"""
        import faiss
        if not self.node_mapping:
            self.index = faiss.IndexFlatIP(self.provider.dim)
            self._save_cache()
            return

        # 删除后重建需要 graph（文本不可从 id 回退）→ 标记待重建，
        # 由 flush_pending 在下一个刷新点全量重建；期间旧索引继续可用（
        # 已删节点可能被召回，由注入侧的存在性校验挡掉）。
        self._needs_rebuild = True
        logger.info("[Embedding] 标记待重建（删除节点后 FAISS 需全量重建）")

    # ── 缓存 ──────────────────────────────────────────────

    def _save_cache(self):
        if self.index is None:
            return
        try:
            import faiss
            idx_path = os.path.join(self.cache_dir, "faiss.index")
            map_path = os.path.join(self.cache_dir, "node_mapping.json")

            faiss.write_index(self.index, idx_path)
            with open(map_path, "w", encoding="utf-8") as f:
                json.dump(self.node_mapping, f, ensure_ascii=False)
            with open(os.path.join(self.cache_dir, "index_meta.json"), "w",
                      encoding="utf-8") as f:
                json.dump({"signature": self._signature,
                           "entries": len(self.node_mapping)}, f)

            logger.info(f"[Embedding] 缓存已保存: {len(self.node_mapping)} 条目")
        except Exception as e:
            logger.warning(f"[Embedding] 缓存保存失败: {e}")

    def _load_cache(self):
        idx_path = os.path.join(self.cache_dir, "faiss.index")
        map_path = os.path.join(self.cache_dir, "node_mapping.json")

        if not os.path.exists(idx_path) or not os.path.exists(map_path):
            logger.info("[Embedding] 无缓存，将全量构建")
            return

        try:
            import faiss
            self.index = faiss.read_index(idx_path)
            with open(map_path, "r", encoding="utf-8") as f:
                self.node_mapping = json.load(f)

            meta_path = os.path.join(self.cache_dir, "index_meta.json")
            if os.path.exists(meta_path):
                with open(meta_path, "r", encoding="utf-8") as f:
                    self._signature = (json.load(f) or {}).get("signature", "")

            logger.info(f"[Embedding] 缓存已加载: {len(self.node_mapping)} 条目")
        except Exception as e:
            logger.warning(f"[Embedding] 缓存加载失败: {e}")
            self.index = None
            self.node_mapping = []

    # ── 状态 ──────────────────────────────────────────────

    def stats(self) -> dict:
        return {
            "total_entries": len(self.node_mapping),
            "unique_nodes": len(set(self.node_mapping)),
            "pending": len(self._pending),
            "index_dim": self.provider.dim,
            "model": self.provider.model_name,
            "cached": self.index is not None and os.path.exists(os.path.join(self.cache_dir, "faiss.index")),
        }

    def ready(self) -> bool:
        return self.index is not None and len(self.node_mapping) > 0


# ── 全局单例 ──────────────────────────────────────────────

_embedding_manager: Optional[EmbeddingManager] = None


def get_embedding_manager() -> EmbeddingManager:
    global _embedding_manager
    if _embedding_manager is None:
        _embedding_manager = EmbeddingManager()
    return _embedding_manager
