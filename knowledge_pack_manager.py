# knowledge_pack_manager.py — Knowledge Pack 管理器
# ============================================================================
# Fascinator V4 Phase 1 核心模块。
#
# 职责：
#   1. 扫描 knowledge_packs/ 目录，发现所有可用的 Knowledge Pack
#   2. 加载/保存/创建/删除/导入/导出 Pack
#   3. 启用/禁用 Pack 并自动重新合并生成 Runtime KnowledgeGraph
#   4. 合并所有启用的 Pack 的 Embedding → 统一的 Runtime FAISS 索引
#
# 设计原则：
#   - KnowledgeGraph 无需知道 Pack 存在
#   - 运行时始终只有一个 KnowledgeGraph
#   - 认知流程（NLP/Diffusion/Activation）完全不变
# ============================================================================

import os
import json
import shutil
import logging
import threading
from typing import Optional

from graph_model import KnowledgeGraph

logger = logging.getLogger(__name__)


# ── Pack 元数据 ──────────────────────────────────────────────────

def _resolve_graph_path(pack_dir: str) -> str:
    kg_path = os.path.join(pack_dir, "knowledge_graph.json")
    if os.path.exists(kg_path):
        return kg_path
    return os.path.join(pack_dir, "graph.json")

class KnowledgePackMeta:
    """单个 Knowledge Pack 的元数据 + 运行时状态"""

    def __init__(self, pack_dir: str):
        self.pack_dir = os.path.abspath(pack_dir)
        self.name = os.path.basename(pack_dir)
        self.graph_path = _resolve_graph_path(self.pack_dir)
        self.meta_path = os.path.join(self.pack_dir, "metadata.json")
        self.embedding_dir = os.path.join(self.pack_dir, "embeddings")

        # 默认值
        self.type = "semantic"
        self.enabled = True
        self.priority = 10
        self.description = ""
        self.author = ""
        self.version = "1.0"

        # 运行时状态
        self.node_count = 0
        self.edge_count = 0
        self.has_embedding_cache = False

        self._load_meta()

    def _load_meta(self):
        if not os.path.exists(self.meta_path):
            return
        try:
            with open(self.meta_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.type = data.get("type", "semantic")
            self.enabled = bool(data.get("enabled", True))
            self.priority = int(data.get("priority", 10))
            self.description = data.get("description", "")
            self.author = data.get("author", "")
            self.version = data.get("version", "1.0")
        except Exception as e:
            logger.warning(f"[Pack] 读取 metadata 失败: {self.name} ({e})")

    def save_meta(self):
        data = {
            "name": self.name,
            "type": self.type,
            "enabled": self.enabled,
            "priority": self.priority,
            "description": self.description,
            "author": self.author,
            "version": self.version,
        }
        with open(self.meta_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def scan_embedding(self):
        """检查 embedding 缓存是否存在"""
        idx_path = os.path.join(self.embedding_dir, "faiss.index")
        map_path = os.path.join(self.embedding_dir, "node_mapping.json")
        self.has_embedding_cache = os.path.exists(idx_path) and os.path.exists(map_path)
        return self.has_embedding_cache

    def count_graph(self):
        """快速统计节点和边数量"""
        if not os.path.exists(self.graph_path):
            self.node_count = 0
            self.edge_count = 0
            return
        try:
            fsize = os.path.getsize(self.graph_path)
            if fsize < 5 * 1024 * 1024:  # < 5 MB
                with open(self.graph_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.node_count = len(data.get("nodes", []))
                self.edge_count = len(data.get("edges", []))
            else:
                self.node_count = -1  # 大型文件标记
                self.edge_count = -1
        except Exception:
            self.node_count = 0
            self.edge_count = 0

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "type": self.type,
            "enabled": self.enabled,
            "priority": self.priority,
            "description": self.description,
            "author": self.author,
            "version": self.version,
            "node_count": self.node_count,
            "edge_count": self.edge_count,
            "has_embedding_cache": self.has_embedding_cache,
        }

# ── Knowledge Pack Manager ────────────────────────────────────────

class KnowledgePackManager:
    """管理所有 Knowledge Pack，生成统一的 Runtime KnowledgeGraph"""

    def __init__(self, packs_root: str = "knowledge_packs",
                 runtime_data_dir: str = "data/faiss_runtime"):
        self.packs_root = os.path.abspath(packs_root)
        self.runtime_data_dir = os.path.abspath(runtime_data_dir)
        self._lock = threading.RLock()

        self.packs: dict[str, KnowledgePackMeta] = {}
        self.runtime_graph: Optional[KnowledgeGraph] = None
        self.runtime_node_map: dict[str, str] = {}  # node_id -> pack_name
        self._embedding_provider = None

        os.makedirs(self.packs_root, exist_ok=True)
        os.makedirs(self.runtime_data_dir, exist_ok=True)

        logger.info(f"[PackManager] 初始化完成, packs_root={self.packs_root}")

    def set_embedding_provider(self, provider):
        self._embedding_provider = provider

    # ── 扫描 ─────────────────────────────────────────────────

    def scan_packs(self):
        """扫描 knowledge_packs/ 目录"""
        with self._lock:
            self.packs.clear()
            if not os.path.isdir(self.packs_root):
                logger.warning(f"[PackManager] 目录不存在: {self.packs_root}")
                return
            for entry in os.listdir(self.packs_root):
                pack_dir = os.path.join(self.packs_root, entry)
                if not os.path.isdir(pack_dir):
                    continue
                meta_path = os.path.join(pack_dir, "metadata.json")
                graph_path = _resolve_graph_path(pack_dir)
                if not os.path.exists(meta_path) or not os.path.exists(graph_path):
                    continue
                try:
                    pack = KnowledgePackMeta(pack_dir)
                    pack.count_graph()
                    pack.scan_embedding()
                    self.packs[pack.name] = pack
                    status = "v" if pack.enabled else "x"
                    logger.info(f"[Pack] [{status}] {pack.name} "
                                f"(nodes={pack.node_count}, edges={pack.edge_count})")
                except Exception as e:
                    logger.warning(f"[Pack] 跳过 {entry}: {e}")
            logger.info(f"[PackManager] 扫描完成: {len(self.packs)} packs")

    def get_enabled_packs(self) -> list:
        with self._lock:
            enabled = [p for p in self.packs.values() if p.enabled]
            enabled.sort(key=lambda p: -p.priority)
            return enabled

    # ── Merge ─────────────────────────────────────────────────

    def merge_runtime_graph(self) -> KnowledgeGraph:
        """合并所有启用 Pack -> Runtime KnowledgeGraph"""
        with self._lock:
            enabled = self.get_enabled_packs()
            logger.info(f"[PackManager] 合并 {len(enabled)} 个启用 Pack...")

            kg = KnowledgeGraph()
            self.runtime_node_map.clear()

            for pack in enabled:
                logger.info(f"[PackManager]   加载 {pack.name} (priority={pack.priority})...")
                pack_kg = self._load_pack_graph(pack)
                if pack_kg is None:
                    continue

                added_nodes = 0
                skipped_nodes = 0
                added_edges = 0
                skipped_edges = 0

                for nid, node in pack_kg.nodes.items():
                    if nid in kg.nodes:
                        skipped_nodes += 1
                        continue
                    kg.add_node(node)
                    self.runtime_node_map[nid] = pack.name
                    added_nodes += 1

                for edge in pack_kg.edges:
                    if kg.get_edge(edge.src, edge.dst, edge.relation):
                        skipped_edges += 1
                        continue
                    kg.add_edge(edge)
                    added_edges += 1

                logger.info(f"[PackManager]   {pack.name}: +{added_nodes} nodes, +{added_edges} edges "
                            f"(skipped {skipped_nodes} nodes, {skipped_edges} edges)")

            self.runtime_graph = kg
            logger.info(f"[PackManager] Runtime Graph: {len(kg.nodes)} nodes, {len(kg.edges)} edges")
            return kg

    def _load_pack_graph(self, pack: KnowledgePackMeta) -> Optional[KnowledgeGraph]:
        """加载单个 Pack 的图谱文件"""
        if not os.path.exists(pack.graph_path):
            logger.warning(f"[PackManager] Graph file not found: {pack.graph_path}")
            return None
        try:
            return KnowledgeGraph.load(pack.graph_path)
        except Exception as e:
            logger.error(f"[PackManager] 加载 Pack {pack.name} 失败: {e}")
            return None

    # ── Pack CRUD ─────────────────────────────────────────────

    def create_pack(self, name: str, pack_type: str = "semantic",
                    description: str = "", author: str = "",
                    priority: int = 10, enabled: bool = True) -> KnowledgePackMeta:
        """创建新的空 Knowledge Pack"""
        with self._lock:
            name = name.strip().lower().replace(" ", "_")
            if not name:
                raise ValueError("Pack name cannot be empty")
            if name in self.packs:
                raise ValueError(f"Pack already exists: {name}")

            pack_dir = os.path.join(self.packs_root, name)
            os.makedirs(pack_dir, exist_ok=True)
            os.makedirs(os.path.join(pack_dir, "embeddings"), exist_ok=True)

            empty_graph = {"nodes": [], "edges": []}
            with open(os.path.join(pack_dir, "knowledge_graph.json"), "w", encoding="utf-8") as f:
                json.dump(empty_graph, f, ensure_ascii=False, indent=2)

            pack = KnowledgePackMeta(pack_dir)
            pack.type = pack_type
            pack.enabled = enabled
            pack.priority = priority
            pack.description = description
            pack.author = author
            pack.save_meta()
            pack.count_graph()
            pack.scan_embedding()

            self.packs[name] = pack
            logger.info(f"[PackManager] 创建 Pack: {name}")
            return pack

    def delete_pack(self, name: str) -> bool:
        with self._lock:
            if name not in self.packs:
                return False
            pack = self.packs[name]
            pack_dir = pack.pack_dir
            try:
                shutil.rmtree(pack_dir)
            except Exception as e:
                logger.error(f"[PackManager] 删除 Pack 目录失败: {e}")
                return False
            del self.packs[name]
            logger.info(f"[PackManager] 删除 Pack: {name}")
            return True

    def rename_pack(self, old_name: str, new_name: str) -> bool:
        with self._lock:
            new_name_orig = new_name.strip()
            new_name = new_name_orig.lower().replace(" ", "_")
            if not new_name or old_name not in self.packs:
                return False
            if new_name in self.packs and new_name != old_name:
                raise ValueError(f"Target name already exists: {new_name}")

            pack = self.packs[old_name]
            old_dir = pack.pack_dir
            new_dir = os.path.join(self.packs_root, new_name)

            try:
                os.rename(old_dir, new_dir)
            except Exception as e:
                logger.error(f"[PackManager] 重命名失败: {e}")
                return False

            del self.packs[old_name]
            new_pack = KnowledgePackMeta(new_dir)
            new_pack.name = new_name_orig  # Keep display name as user entered
            new_pack.save_meta()
            new_pack.count_graph()
            new_pack.scan_embedding()
            self.packs[new_name] = new_pack
            # Update runtime_node_map entries that reference the old pack name
            for node_id, pname in list(self.runtime_node_map.items()):
                if pname == old_name:
                    self.runtime_node_map[node_id] = new_name
            logger.info(f"[PackManager] 重命名: {old_name} -> {new_name}")
            return True

    def enable_pack(self, name: str) -> bool:
        with self._lock:
            if name not in self.packs:
                return False
            pack = self.packs[name]
            if not pack.enabled:
                pack.enabled = True
                pack.save_meta()
                logger.info(f"[PackManager] 启用 Pack: {name}")
            return True

    def disable_pack(self, name: str) -> bool:
        with self._lock:
            if name not in self.packs:
                return False
            pack = self.packs[name]
            if pack.enabled:
                pack.enabled = False
                pack.save_meta()
                logger.info(f"[PackManager] 禁用 Pack: {name}")
            return True

    # ── Import / Export ──────────────────────────────────────

    def import_pack(self, source_path: str, pack_name: str = None) -> KnowledgePackMeta:
        """从外部目录导入 Pack"""
        source_path = os.path.abspath(source_path)
        if not os.path.isdir(source_path):
            raise ValueError(f"Source is not a directory: {source_path}")
        graph_path = _resolve_graph_path(source_path)
        if not os.path.exists(graph_path):
            raise ValueError("Source must contain knowledge_graph.json or graph.json")
        if pack_name is None:
            meta_path = os.path.join(source_path, "metadata.json")
            if os.path.exists(meta_path):
                try:
                    with open(meta_path, "r", encoding="utf-8") as f:
                        meta = json.load(f)
                    pack_name = meta.get("name", os.path.basename(source_path))
                except Exception:
                    pack_name = os.path.basename(source_path)
            else:
                pack_name = os.path.basename(source_path)
        pack_name = pack_name.strip().lower().replace(" ", "_")
        dest_dir = os.path.join(self.packs_root, pack_name)
        if os.path.exists(dest_dir):
            raise ValueError(f"Pack already exists: {pack_name}")
        shutil.copytree(source_path, dest_dir)
        os.makedirs(os.path.join(dest_dir, "embeddings"), exist_ok=True)
        if not os.path.exists(os.path.join(dest_dir, "metadata.json")):
            default_meta = {
                "name": pack_name, "type": "semantic", "enabled": True,
                "priority": 10, "description": f"Imported from {source_path}",
                "author": "", "version": "1.0",
            }
            with open(os.path.join(dest_dir, "metadata.json"), "w", encoding="utf-8") as f:
                json.dump(default_meta, f, ensure_ascii=False, indent=2)
        pack = KnowledgePackMeta(dest_dir)
        pack.count_graph()
        pack.scan_embedding()
        self.packs[pack_name] = pack
        logger.info(f"[PackManager] 导入 Pack: {pack_name} from {source_path}")
        return pack

    def export_pack(self, name: str, dest_path: str) -> str:
        """导出 Pack 到指定目录"""
        if name not in self.packs:
            raise ValueError(f"Pack not found: {name}")
        pack = self.packs[name]
        dest_path = os.path.abspath(dest_path)
        if os.path.exists(dest_path):
            raise ValueError(f"Destination already exists: {dest_path}")
        shutil.copytree(pack.pack_dir, dest_path)
        logger.info(f"[PackManager] 导出 Pack: {name} -> {dest_path}")
        return dest_path

    # ── Runtime Embedding ────────────────────────────────────

    def build_runtime_embedding(self) -> dict:
        """合并所有启用 Pack 的 Embedding -> 统一 Runtime FAISS"""
        if self.runtime_graph is None:
            self.merge_runtime_graph()
        if self._embedding_provider is None:
            logger.warning("[PackManager] 无 EmbeddingProvider，跳过 Runtime FAISS")
            return {"success": False, "error": "No embedding provider"}
        import faiss
        import numpy as np
        import pickle

        all_vectors = []
        all_node_ids = []
        enabled = self.get_enabled_packs()

        for pack in enabled:
            idx_path = os.path.join(pack.embedding_dir, "faiss.index")
            map_path = os.path.join(pack.embedding_dir, "node_mapping.json")
            vec_path = os.path.join(pack.embedding_dir, "vectors.pkl")
            if not os.path.exists(idx_path) or not os.path.exists(map_path):
                logger.info(f"[PackManager] {pack.name}: 无 Embedding 缓存，跳过")
                continue
            try:
                pack_index = faiss.read_index(idx_path)
                with open(map_path, "r", encoding="utf-8") as f:
                    pack_mapping = json.load(f)
                if os.path.exists(vec_path):
                    with open(vec_path, "rb") as f:
                        vectors = pickle.load(f)
                else:
                    vectors = faiss.vector_to_array(pack_index).reshape(
                        -1, self._embedding_provider.dim)
                for i, nid in enumerate(pack_mapping):
                    if nid in self.runtime_graph.nodes:
                        all_vectors.append(vectors[i])
                        all_node_ids.append(nid)
                logger.info(f"[PackManager] {pack.name}: 合并 {len(pack_mapping)} vectors")
            except Exception as e:
                logger.warning(f"[PackManager] {pack.name}: Embedding 合并失败: {e}")

        if not all_vectors:
            logger.warning("[PackManager] 无可合并的 Embedding")
            return {"success": False, "error": "No vectors to merge"}

        vectors_array = np.array(all_vectors, dtype=np.float32)
        runtime_index = faiss.IndexFlatIP(self._embedding_provider.dim)
        runtime_index.add(vectors_array)

        os.makedirs(self.runtime_data_dir, exist_ok=True)
        idx_path = os.path.join(self.runtime_data_dir, "faiss.index")
        map_path = os.path.join(self.runtime_data_dir, "node_mapping.json")
        vec_path = os.path.join(self.runtime_data_dir, "vectors.pkl")

        faiss.write_index(runtime_index, idx_path)
        with open(map_path, "w", encoding="utf-8") as f:
            json.dump(all_node_ids, f, ensure_ascii=False)
        with open(vec_path, "wb") as f:
            pickle.dump(vectors_array, f)

        logger.info(f"[PackManager] Runtime FAISS 构建完成: {len(all_node_ids)} vectors")
        return {"success": True, "total_vectors": len(all_node_ids),
                "dim": self._embedding_provider.dim}

    def load_runtime_embedding(self) -> bool:
        idx_path = os.path.join(self.runtime_data_dir, "faiss.index")
        map_path = os.path.join(self.runtime_data_dir, "node_mapping.json")
        if not os.path.exists(idx_path) or not os.path.exists(map_path):
            return False
        try:
            import faiss
            index = faiss.read_index(idx_path)
            with open(map_path, "r", encoding="utf-8") as f:
                mapping = json.load(f)
            logger.info(f"[PackManager] 加载 Runtime FAISS 缓存: {len(mapping)} vectors")
            return True
        except Exception as e:
            logger.warning(f"[PackManager] Runtime FAISS 加载失败: {e}")
            return False

    # ── 保存 ─────────────────────────────────────────────────

    @property
    def runtime_graph_path(self) -> str:
        """Runtime Graph 的落盘路径（app.py 异步保存写入器共用此常量）"""
        return os.path.join("data", "runtime_graph.json")

    def save_runtime_graph(self):
        if self.runtime_graph is None:
            return
        try:
            self.runtime_graph.save(self.runtime_graph_path)
            logger.info(f"[PackManager] Runtime Graph 已保存")
        except Exception as e:
            logger.error(f"[PackManager] 保存失败: {e}")

    def save_pack(self, name: str) -> bool:
        """将 Runtime Graph 中属于该 Pack 的节点/边写回 Pack"""
        if name not in self.packs or self.runtime_graph is None:
            return False
        pack = self.packs[name]
        with self.runtime_graph._lock:
            pack_nodes = {}
            pack_edges = []
            for nid, node in self.runtime_graph.nodes.items():
                if self.runtime_node_map.get(nid) == name:
                    pack_nodes[nid] = node
            for edge in self.runtime_graph.edges:
                if edge.src in pack_nodes and edge.dst in pack_nodes:
                    pack_edges.append(edge)
            data = {
                "nodes": [n.to_dict() for n in pack_nodes.values()],
                "edges": [e.to_dict() for e in pack_edges],
            }
        with open(pack.graph_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        pack.count_graph()
        logger.info(f"[PackManager] 保存 Pack: {name}")
        return True

    # ── 统计 ─────────────────────────────────────────────────

    def stats(self) -> dict:
        with self._lock:
            enabled = self.get_enabled_packs()
            return {
                "total_packs": len(self.packs),
                "enabled_packs": len(enabled),
                "runtime_nodes": len(self.runtime_graph.nodes) if self.runtime_graph else 0,
                "runtime_edges": len(self.runtime_graph.edges) if self.runtime_graph else 0,
                "packs": [p.to_dict() for p in self.packs.values()],
            }

    def list_packs(self) -> list:
        return [p.to_dict() for p in self.packs.values()]

    def get_enabled_pack_list(self) -> list:
        return [p.to_dict() for p in self.get_enabled_packs()]


# ── 全局单例 ──────────────────────────────────────────────────────

_pack_manager: Optional[KnowledgePackManager] = None


def get_pack_manager() -> KnowledgePackManager:
    global _pack_manager
    if _pack_manager is None:
        _pack_manager = KnowledgePackManager()
    return _pack_manager


