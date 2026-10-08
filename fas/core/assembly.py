"""fas.core.assembly — 认知核心的独立装配（不依赖 Flask/app.py）。

装配模式沿用实验沙箱/测试的既有裸构造（tests/test_closed_loop_consistency.py:359–367），
不新造任何认知组件。app.py 现状的"import 即装配 40 个全局"保持不变（最小改造原则），
本模块服务两条新路径：
  1. 集成测试与未来实验：拿一个不带陪伴栈的认知核心；
  2. 认知机制实验成功后的接入终点：实现方把新模块传入对应参数即可，
     Companion 侧代码零改动（验收问题 A/E）。
"""
from __future__ import annotations

import logging
import os

from fas.core.facade import CognitiveCore

logger = logging.getLogger("fas.core.assembly")


def build_cognitive_core(*, config: dict, kg=None, graph_path: str = None,
                         engine=None, buffer=None, language=None,
                         **attachments) -> CognitiveCore:
    """组装 CognitiveCore。

    参数：
      config         引擎/认知参数字典（调用方负责切分——现状 config.DEFAULT_CONFIG
                     是双侧共享真源，切分属登记债务 ARCHITECTURE_PLAN §6.3）
      kg             KnowledgeGraph 实例；缺省时新建，graph_path 存在则加载
      engine         DiffusionEngine；缺省用 kg+config 构造
      buffer         EpisodicBuffer；缺省 capacity=100（app.py:755 同参）
      language       CognitiveLanguageAccess（陪伴侧注入；None=认知闭环无语言触点）
      attachments    disposition_store/action_space/action_manager/internal_state/
                     persona/drive_evaluator/cc/emb_mgr 直通挂载（全部可选）
    """
    if kg is None:
        from graph_model import KnowledgeGraph
        if graph_path and os.path.exists(graph_path):
            kg = KnowledgeGraph.load(graph_path)
        else:
            kg = KnowledgeGraph()
    if engine is None:
        from diffusion_engine import DiffusionEngine
        engine = DiffusionEngine(kg, dict(config))
        # DiffusionEngine.__init__ 内已建 name_to_node（diffusion_engine.py:193–200）；
        # 与 tests/test_closed_loop_consistency.py:367 同款兜底重建，覆盖后装 kg 的情形。
        engine.name_to_node = {n.id: n for n in kg.nodes.values()}
    if buffer is None:
        from episodic_buffer import EpisodicBuffer
        buffer = EpisodicBuffer(capacity=100)

    core = CognitiveCore(kg=kg, engine=engine, buffer=buffer, config=config,
                         language=language)
    for name in ("disposition_store", "action_space", "action_manager",
                 "internal_state", "persona", "drive_evaluator", "cc", "emb_mgr"):
        setattr(core, name, attachments.get(name))
    if language is not None:
        core.attach_language_backend(language)
    logger.info("[fas.assembly] CognitiveCore assembled (kg=%d nodes)",
                len(kg.nodes))
    return core
