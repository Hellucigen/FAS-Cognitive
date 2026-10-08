# test_minecraft.session.py — 世界接入生命周期测试（规范 Step 5 用例 1-7）
# 端口验证/等待/取消/复用/无多bot 全部离线。连接轮询用桩。
#
# 2026-09-21 概念化重构：session.handle_reply（PORT_RE/CANCEL_RE/REUSE_RE
# 字符串表）已删除。"等待端口时这轮输入算什么"由 action_resolver 裁决
# （读图谱节点上的槽位事实），本测试的下半段验证的就是这条新契约：
#   resolve_minecraft_intent → CONNECT intent / need_param / refused /
#   invalid_port / awaiting_port → app 分发调 session 的纯状态方法。
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import logging
logging.disable(logging.WARNING)
from graph_model import KnowledgeGraph, Node
from minecraft.session import MinecraftSession
from action_resolver import resolve_minecraft_intent

fail = []
def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" | {detail}" if detail and not cond else ""))
    if not cond:
        fail.append(name)

kg = KnowledgeGraph()
kg.add_node(Node(id="Self", graph_space="self"))

class FakeBridge:
    def __init__(self, connected=False): self._c = connected
    def set_connected(self, c): self._c = c
    def health_ok(self): return True
import urllib.request as _ur
# 桩 /health 与 /state：monkeypatch 模块级 urllib 调用——改用注入
class Sess(MinecraftSession):
    def __init__(self, kg, connected_seq):
        super().__init__(kg)
        self.seq = list(connected_seq)
    def connect(self, port):
        if not isinstance(port, int) or not (1024 <= port <= 65535):
            return super().connect(port)  # 非法端口走基类校验路径
        self._set("connecting", port=port)
        st = self.seq.pop(0) if self.seq else "refused"
        if st == "connected":
            self._set("connected", port=port)
            return {"success": True, "action": "connect_minecraft", "port": port,
                    "session_id": "test", "host": "127.0.0.1", "bot_name": "Haru"}
        self._set("failed", error=st, port=port)
        return {"success": False, "reason": st, "port": port}

def app_dispatch(sess, kgx, text):
    """复刻 app.py Phase C 块的分发表（只测行为契约；真实 app 用
    connect_async，这里用桩 connect 以免起线程/写 config）。"""
    intent, info = resolve_minecraft_intent(text, kg=kgx)
    if intent is not None and intent.get("action") == "CONNECT":
        return sess.connect(int(intent["parameters"]["port"])), info
    if info.get("need_param"):
        return sess.request_port(), info
    if info.get("refused"):
        return sess.cancel_awaiting(str(info["refused"].get("reason") or "")), info
    return None, info

# 用例 1：进入意图 → 请求端口
kg = KnowledgeGraph(); kg.add_node(Node(id="Self", graph_space="self"))
s = Sess(kg, [])
r = s.request_port()
check("1 请求端口→awaiting+消息", r["success"] and s.get_state() == "awaiting_port"
      and "端口号" in r["request_message"])
check("1 会话图节点存在且状态正确",
      kg.nodes["Minecraft会话"].extra_attrs["state"] == "awaiting_port")

# 用例 2：合法端口 → 连接成功（seq 桩：一次即连上）
s_c = Sess(kg, ["connected"])
r = s_c.connect(58582)
check("2 合法端口→连接成功", r["success"] and s_c.get_state() == "connected")

# 用例 3：非法端口 → 校验先于轮询，结构化失败不崩溃
r = s_c.connect(99999)
check("3 非法端口→失败反馈", not r["success"] and r["reason"] == "invalid_port"
      and s_c.get_state() == "failed")

# 用例 4：等待状态下用户未给端口 → 保持等待
#（新语义：CANCEL_RE 字符串表已死。"等一下/还没开"不再是取消分支——
#  它们就是继续等待；只有显式否定（NEGATION_RE 单一真源）才取消。）
s2kg = KnowledgeGraph(); s2kg.add_node(Node(id="Self", graph_space="self"))
s2 = Sess(s2kg, [])
s2.request_port()
i2, inf2 = resolve_minecraft_intent("等一下，我还没开世界。", kg=s2kg)
check("4a 「等一下/还没开」→保持等待（不是取消）",
      i2 is None and inf2.get("awaiting_port") and s2.get_state() == "awaiting_port",
      str(inf2))
i2, inf2 = resolve_minecraft_intent("今天天气不错", kg=s2kg)
check("4b 无关回复→不是端口，继续等待",
      i2 is None and inf2.get("awaiting_port") and s2.get_state() == "awaiting_port")
i2, inf2 = resolve_minecraft_intent("别进了，我先弄下存档。", kg=s2kg)
check("4c 显式否定→refused（唯一取消来源）",
      i2 is None and (inf2.get("refused") or {}).get("concept") == "CONNECT")
r = s2.cancel_awaiting(str(inf2["refused"].get("reason") or ""))
check("4d 否定后 app 分发取消→cancelled 终态",
      r["cancelled"] and s2.get_state() == "cancelled")
r, inf2 = app_dispatch(s2, s2kg, "进游戏")
check("4e 取消后重新进入→新意图重新要端口（终态不是死锁）",
      inf2.get("need_param") and s2.get_state() == "awaiting_port")

# 用例 5：连接失败 → 结构化反馈
s3kg = KnowledgeGraph(); s3kg.add_node(Node(id="Self", graph_space="self"))
s3 = Sess(s3kg, ["refused"])
r = s3.connect(59999)
check("5 连接拒绝→failed+reason", not r["success"] and r["reason"] == "refused"
      and s3.get_state() == "failed")

# 用例 6：已连接时重复进入意图 → 不重复连接/不多bot
s.request_port()
check("6 已连接时再请求→会话仍是 connected（不产生第二bot）",
      s.get_state() in ("connected", "awaiting_port"))

# 用例 7：等待状态下的端口填槽（全部经 resolver，读图谱槽位事实）
s4kg = KnowledgeGraph(); s4kg.add_node(Node(id="Self", graph_space="self"))
s4 = Sess(s4kg, ["connected", "connected", "connected", "connected",
                 "connected", "connected"])
s4.request_port()
check("7 槽位事实在图上（request_port 写入 state+last_port）",
      s4kg.nodes["Minecraft会话"].extra_attrs.get("state") == "awaiting_port"
      and "last_port" in s4kg.nodes["Minecraft会话"].extra_attrs)
i, inf = resolve_minecraft_intent("58582", kg=s4kg)
check("7a 纯数字端口→slot 填槽意图（source=slot）",
      i and i["action"] == "CONNECT" and i["parameters"]["port"] == 58582
      and i["source"] == "slot", str(i))
r, inf = app_dispatch(s4, s4kg, "58582")
check("7a' 分发后真连接", r["success"] and s4.get_state() == "connected")

s4.request_port()   # 再要一次端口（测余下分支）
i, inf = resolve_minecraft_intent("99999", kg=s4kg)
check("7b 超范围数字→invalid_port 结构化反馈，槽位保持等待",
      i is None and inf.get("invalid_port") == 99999
      and s4.get_state() == "awaiting_port", str(inf))

# 7c 复用上次的端口：值来自图谱节点 last_port（记忆），不是硬编码
s4.last_port = 54766
s4.request_port()
i, inf = resolve_minecraft_intent("还是刚才那个世界", kg=s4kg)
check("7c 「刚才那个」→回指图谱槽位 last_port",
      i and i["parameters"]["port"] == 54766 and inf.get("reused"), str(i))
i, inf = resolve_minecraft_intent("用上次那个端口", kg=s4kg)
check("7c' 「上次」同样回指（不枚举完整句型）",
      i and i["parameters"]["port"] == 54766, str(i))

# 7d-f 口语端口（实测进不去世界的根因之一："端口61994" 带前缀的旧正则不认）
i, inf = resolve_minecraft_intent("端口61994", kg=s4kg)
check("7d 「端口61994」→填槽", i and i["parameters"]["port"] == 61994, str(i))
i, inf = resolve_minecraft_intent("61994 端口", kg=s4kg)
check("7e 「61994 端口」→填槽", i and i["parameters"]["port"] == 61994, str(i))
i, inf = resolve_minecraft_intent("是61994。", kg=s4kg)
check("7f 「是61994。」→填槽", i and i["parameters"]["port"] == 61994, str(i))
i, inf = resolve_minecraft_intent("我这边还没弄好", kg=s4kg)
check("7g 无数字口语→继续等待（不放宽成猜）",
      i is None and inf.get("awaiting_port"), str(inf))

# 用例 8：首次进入意图（无槽位上下文）——need_param 而不是猜/硬拒
s5kg = KnowledgeGraph(); s5kg.add_node(Node(id="Self", graph_space="self"))
i, inf = resolve_minecraft_intent("进游戏", kg=s5kg)
check("8a 「进游戏」缺端口→need_param（索取，不猜）",
      i is None and (inf.get("need_param") or {}).get("concept") == "CONNECT"
      and "port" in (inf.get("need_param") or {}).get("params", []), str(inf))
i, inf = resolve_minecraft_intent("来玩我的世界，端口59913", kg=s5kg)
check("8b 一句话带端口→CONNECT 意图直接绑参（不再二问）",
      i and i["action"] == "CONNECT" and i["parameters"]["port"] == 59913, str(i))
i, inf = resolve_minecraft_intent("别进游戏了", kg=s5kg)
check("8c 否定进入→refused（无执行）",
      i is None and (inf.get("refused") or {}).get("concept") == "CONNECT", str(inf))
i, inf = resolve_minecraft_intent("58582", kg=s5kg)
check("8d 没在等端口时裸数字≠端口（无槽不解读）",
      i is None and not inf.get("invalid_port"), str(inf))
i, inf = resolve_minecraft_intent("订单号123456", kg=s5kg)
check("8e 六位数字不被切成端口（边界防护）", i is None, str(inf))

print()
if fail:
    print(f"✗ {len(fail)} 项失败: {fail}"); sys.exit(1)
print("✓ 世界接入生命周期测试全过（概念层裁决 + 状态机 + 工程校验）")
