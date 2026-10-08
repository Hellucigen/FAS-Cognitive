"""fas.companion — 陪伴层：语言后端（临时）、表达通道、感知事件源。

这里允许 import LLM/外部方案；被认知侧允许的只有一只最小接口
（fas.protocols.CognitiveLanguageAccess），由本目录包装后注入。
每个临时组件在 fas/registry.py 登记了占用者与替换位。
"""
