# test_cog.py — 活体端到端冒烟（不是离线回归测试）
# 与 tests/ 下其它文件的分别（别把它算进离线断言数）：
#   - 它需要**正在运行的服务器**（默认 http://127.0.0.1:5000）+ 可用 LLM
#   - 它不写 check()，MISMATCH 只打印不抛错——是"看结果"的冒烟脚本
#   - 它打真实对话，用来观察 dialogue_act 分类与回答质量
# 服务器没起时脚本会明确报 SKIP 并以 0 退出，不再伪装成通过或失败。
# 运行: E:/Miniforge.envs/Fascinator/python.exe -X utf8 tests/test_cog.py

import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import urllib.request, json, sys

BASE = os.environ.get("FAS_BASE_URL", "http://127.0.0.1:5000")


def _server_alive() -> bool:
    """探活：连不上就不跑（活体测试的前置条件不满足，不是失败）。"""
    import socket
    from urllib.parse import urlparse
    _p = urlparse(BASE)
    try:
        with socket.create_connection((_p.hostname or "127.0.0.1", _p.port or 5000), timeout=2):
            return True
    except OSError:
        return False


if not _server_alive():
    print(f"[SKIP] 服务器未运行（{BASE}）——本文件是活体冒烟，需先启动 app.py：")
    print("       E:/Miniforge.envs/Fascinator/python.exe app.py")
    print("       （离线回归请跑其它 tests/test_*.py）")
    sys.exit(0)

tests = [
    ("你好啊", "greeting"),
    ("再见", "farewell"),
    ("放暑假在家好无聊啊", "emotion_expression"),
    ("我今天刚通关了木筏，上一次玩到中间放弃了，今年暑假高中同学又建议玩，也是通关了", "sharing"),
    ("阿司匹林是什么药", "question"),
    ("帮我查一下明天的天气", "request"),
    ("谢谢你帮我", "thanking"),
    ("水在100摄氏度沸腾", "information_statement"),
]

for text, expected_da in tests:
    try:
        data = json.dumps({'text': text}).encode()
        req = urllib.request.Request(BASE + '/api/nlp', data=data, headers={'Content-Type': 'application/json'})
        resp = urllib.request.urlopen(req)
        d = json.loads(resp.read())
        p = d.get('parsed', {})
        ill = p.get('illocutionary_act', '?')
        da = p.get('dialogue_act', '?')
        re_val = p.get('response_expectation', '?')
        goals = p.get('suggested_reply_goals', [])
        ans = (d.get('answer', '') or '')[:80]
        match = '✓' if da == expected_da else f'MISMATCH (expected {expected_da})'
        print(f'[{match}] "{text}"')
        print(f'  illoc={ill}  da={da}  expect={re_val}  goals={goals}')
        print(f'  answer: {ans}')
        print()
    except Exception as e:
        print(f'[ERROR] "{text}": {e}')
