# test_narrative_trigger.py — 事件结构化叙事触发器测试（规范 Step 5 十用例）
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from narrative_trigger import narrative_score

fail = []
def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" | {detail}" if detail and not cond else ""))
    if not cond:
        fail.append(name)

def is_narr(text, profile="balanced"):
    return narrative_score(text, profile)["decision"] == "narrative"

# 1. 短但明显事件序列 → 触发
t1 = "昨天我去了学校，参加了考试，下午和朋友吃饭，晚上回到了宿舍。"
check("1 短文本+多事件+时间 → 触发", is_narr(t1), narrative_score(t1)["score"])

# 2. 长但只是知识说明 → 不触发
t2 = "人工智能的发展经历了漫长而复杂的历史，从符号主义到连接主义，再到今天的大模型，每一次范式转换都伴随着方法论的重大变革，也伴随着对其能力边界的重新审视。"
check("2 知识说明不因长度触发", not is_narr(t2), narrative_score(t2)["score"])

# 3. 多事件 + 时间顺序 → 触发
t3 = "早上他起床收拾行李，然后赶到车站，之后坐了三个小时的车，最后到了海边。"
check("3 时间顺序事件链 → 触发", is_narr(t3), narrative_score(t3)["score"])

# 4. 多事件 + 因果 → 触发
t4 = "杯子摔碎了，所以他开始打扫碎片。"
check("4 因果事件链 → 触发", is_narr(t4), narrative_score(t4)["score"])

# 5. 多事件 + 状态转换 → 触发
t5 = "门是关着的。他打开了门。房间里很黑。他打开了灯。"
check("5 状态转换链 → 触发", is_narr(t5), narrative_score(t5)["score"])

# 6. 单个复杂事件 → 不误判
t6 = "我在Minecraft里用连锁挖矿模组挖到了一整片银矿和铀矿。"
check("6 单个复杂事件 → 不触发", not is_narr(t6), narrative_score(t6)["score"])

# 7. 普通事实陈述 → 不触发
t7 = "我今天没课。"
check("7 普通事实 → 不触发", not is_narr(t7))

# 7b. 纯情绪表达 → 不触发
t7b = "又到周末了...."
check("7b 纯情绪感叹 → 不触发", not is_narr(t7b))

# 8. 用户讲述他人故事 → 触发（讲述/参与区分由 _ingest_narrative 落地）
t8 = "小明昨天去了学校，然后参加了考试，之后又和朋友去打了球。"
check("8 他人故事 → 触发", is_narr(t8))
check("8 第三人称无我的自指", "first_person" in narrative_score(t8) and
      not narrative_score(t8)["first_person"])

# 9. 超长文本 + 多事件 → 触发且分段逻辑不变
t9 = ("他先到达了营地，然后搭好了帐篷，接着生起了火，"
      "之后煮了晚饭，最后在星空下睡着了。") * 6
check("9 超长多事件 → 触发", is_narr(t9), narrative_score(t9)["score"])

# 10. economy 档阈值更高
t10 = "他到了车站，然后买了票。"
check("10 economy 档更保守", narrative_score(t10, profile="economy")["decision"] == "normal"
      and narrative_score(t10, profile="balanced")["decision"] == "narrative",
      str(narrative_score(t10)["score"]))

# 补充：低质量事件链 → 沉默轮不触发
r = narrative_score("嗯")
check("补 超短无事件 → 不触发", r["decision"] == "normal")

print()
if fail:
    print(f"✗ {len(fail)} 项失败: {fail}"); sys.exit(1)
print("✓ 触发器测试全过")
