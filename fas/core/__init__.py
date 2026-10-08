"""fas.core — Cognitive Core 门面与独立装配。

红线：本目录不得 import 陪伴/LLM 侧模块（nlp_processor / prompt_templates /
llm_provider / ollama_backend / app）。语言触点只经 CognitiveLanguageAccess
注入（fas.protocols）。由 tests/test_arch_decoupling.py::test_core_has_no_companion_imports 守护。
"""
