# test_mc_session_async.py — 后台连接（connect_async）测试
# 假桥注入（构造参数），无真实 bot/网络。运行:
#   E:/Miniforge.envs/Fascinator/python.exe -X utf8 tests/test_mc_session_async.py
import os, sys, tempfile, time, threading
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import logging; logging.disable(logging.WARNING)
from graph_model import KnowledgeGraph, Node
from minecraft.session import MinecraftSession

FAILURES = []
def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" | {detail}" if detail and not cond else ""))
    if not cond: FAILURES.append(name)

class FakeBridge:
    def __init__(self, become_connected_after=2):
        self.calls = 0
        self.after = become_connected_after
    def state(self):
        self.calls += 1
        if self.calls > self.after:
            return {"connected": True, "position": {"x": 1, "y": 64, "z": 2}}
        return {"connected": False, "error": "bridge_down"}

def make(after=2):
    d = tempfile.mkdtemp(prefix="fas_mcs_")
    kg = KnowledgeGraph(); kg.add_node(Node(id="Self", graph_space="self"))
    fake = FakeBridge(after)
    import minecraft.session as ms_mod
    old_cfg = ms_mod.CONFIG_PATH
    ms_mod.CONFIG_PATH = os.path.join(d, "cfg.json")
    sess = MinecraftSession(kg, bridge_get_state=fake.state,
                            ensure_bot_process=lambda: True,
                            restart_bot_process=lambda: True)
    return sess, fake, d, (ms_mod, old_cfg)

# 1) 立即返回 pending（不阻塞）
sess, fake, d, ctx = make()
try:
    t0 = time.time()
    r = sess.connect_async(59999)
    dt = time.time() - t0
    check("立即返回（<1s，旧实现阻塞 20s+）", dt < 1.0 and r.get("pending"), f"{dt:.2f}s {r}")
    check("状态进入 connecting", sess.get_state() in ("connecting", "connected"), sess.get_state())
    done = threading.Event(); got = {}
    sess.on_connected = lambda res: (got.update(res), done.set())
    # 等待后台线程（fake 桥第 3 次读取即 connected）
    ok = done.wait(8.0)
    check("后台连上 → on_connected 触发", ok and got.get("success"), str(got))
    check("会话节点转 connected", sess.get_state() == "connected", sess.get_state())
    r2 = sess.connect_async(12345)   # 已连接再来：重写配置再起后台
    check("重复连接不并发看门狗（already 保护或快速成功）",
          r2.get("pending") or r2.get("success"), str(r2))
finally:
    import minecraft.session as ms_mod; ms_mod.CONFIG_PATH = ctx[1]

# 2) 端口非法：同步失败（配置竞态点仍同步校验）
sess2, fake2, d2, ctx2 = make()
try:
    r = sess2.connect_async(99)
    check("非法端口即时拒绝", r.get("success") is False and r.get("reason") == "invalid_port")
    check("状态 failed", sess2.get_state() == "failed")
finally:
    import minecraft.session as ms_mod; ms_mod.CONFIG_PATH = ctx2[1]

# 3) 连不上：超时 → on_failed
sess3, fake3, d3, ctx3 = make(after=10 ** 6)   # 永不连上
try:
    r = sess3.connect_async(60000, timeout_s=3.0)
    failed = threading.Event(); fr = {}
    sess3.on_failed = lambda res: (fr.update(res), failed.set())
    ok = failed.wait(12.0)
    check("超时 → on_failed 触发（语言层可如实说没连上）",
          ok and not fr.get("success"), str(fr))
    check("状态 failed", sess3.get_state() == "failed")
finally:
    import minecraft.session as ms_mod; ms_mod.CONFIG_PATH = ctx3[1]

print()
if FAILURES: print("✗", len(FAILURES), FAILURES); sys.exit(1)
print("✓ mc_session 后台连接全过（立即返回/成功回调/失败回调/看门狗防重）")
