# real_0_connect.py — B8 前置：经真实对话路径连 56988（不重启服务器）
# 用户路径："进我的世界" →（若索取端口）"56988" → connect_async →
# 桥 connected + bot 进程唯一（netstat 侧证在报告里人工核对）。
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from real_common import *

hr("0 连接 56988")
st = mc_state()
if st.get("connected"):
    print("已在世界中，跳过连接")
    sys.exit(0)

r = say("进我的世界")
ans = str(r.get("answer") or "")
print("第一轮回答:", ans[:80])
time.sleep(1.0)
sess = (get("/api/autonomy/state") or {})
# 若状态是 awaiting_port，补端口
r2 = say("56988")
print("第二轮回答:", str(r2.get("answer") or "")[:80])

ok, val, el = wait_for(lambda: mc_state().get("connected"), 90, 3, "桥连接")
if not ok:
    # 可能第一轮就带上了端口或直接开始连；再补一次含端口说法
    say("连接Minecraft，端口是56988")
    ok, val, el = wait_for(lambda: mc_state().get("connected"), 90, 3, "桥连接2")
st = mc_state() if ok else {}
v = verdict("真实连入 56988（桥 connected）", ok,
            f"{el}s state={ {k: st.get(k) for k in ('username','position','health')} if ok else 'TIMEOUT' }")
if v:
    w = get("/api/debug/world", timeout=20)
    print("连接事件近轴:", str(w.get("recent_events", []))[:200])
sys.exit(0 if v else 1)
