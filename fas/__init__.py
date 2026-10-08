"""FAS 架构解耦层（2026-10-08，Phase 3 最小改造）。

目标：让 Cognitive Core（FAS 自身认知机制）与 Companion Layer（陪伴功能，
允许临时借用外部成熟方案）之间只通过本包的稳定契约交换数据，
使任一侧的组件可以被逐个替换而牵动面最小。

设计依据见 docs/ARCHITECTURE_PLAN.md；现状耦合证据见 docs/ARCHITECTURE_AUDIT.md；
认知机制的实验验证状态见 docs/COGNITIVE_MODULE_STATUS.md。

结构：
    fas/contracts.py            跨界数据结构（纯 stdlib，两侧唯一共享物）
    fas/protocols.py            稳定接口（Protocol）
    fas/registry.py             临时/自建组件槽位台账（回答"哪些是借来的"）
    fas/core/                   认知侧：CognitiveCore 门面 + 独立装配
    fas/companion/              陪伴侧：语言后端包装、表达通道、感知事件源

红线：
    - fas/core/* 不得 import 任何陪伴/LLM 侧模块（nlp_processor、prompt_templates、
      llm_provider、ollama_backend、app、index）。由 tests/test_arch_decoupling.py 守护。
    - 陪伴侧不得直接触碰 kg._lock / 节点 activation / engine.mark_active；
      状态变化一律经 CognitiveCore 的 API 进入（存量违例见 ARCHITECTURE_PLAN.md §6）。
    - 本包只做委托与装配，不复制、不修改任何认知算法（研究有效性优先）。
"""
