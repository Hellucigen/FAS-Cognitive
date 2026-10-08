# safety_kernel.py — Safety Kernel：最小执行安全不变量（2026-09-21）
# ============================================================================
# 本模块是唯一允许"代码否决行动"的地方，且只包含**即使认知出错也不可
# 违反的物理/系统不变量**。行为知识（什么值得做、什么该避免、保护谁）
# 全部在图谱与认知里；这里不做行为裁决。
#
# 不变量清单（逐条注释归属，新增必须论证为什么属于 kernel）：
#   K1 参数非法/失联/取消/互斥/超时/不假成功 —— 执行诚实性（在
#      skills/base、ActionManager 既有实现，此处只做入口集中声明）
#   K2 资产修改需授权：目标物种带"受保护"图边 → 必须有有效授权节点或
#      本次动作来自用户明确命令（source=user 即授权，且落一条授权证据，
#      供之后的自主关联动作引用）。保护是关系+可解除，不是名单禁止。
#   K3 攻击性执行的对象核验：attack 类动作的目标必须在图上具有危险
#      因果（造成/敌对边）或用户点名命令——防"幻觉目标/误伤被动生物"
#      的执行事故；不禁止认知"想到攻击"。
#   K4 濒死停手（唯一数值兜底）：HP ≤ death_floor 且正持有占用动作 →
#      无条件 cancel 并 stop（控制原语，不指定去向；逃/吃/回血由认知
#      在压力注入后自行选择）。
#   K5 防抖：同 (action, target) 5s 内重复提交拒绝——防代码 bug 死循环，
#      与"失败学习"无关（经验压制走 causal action_prior）。
# ============================================================================

import logging
import time

logger = logging.getLogger(__name__)

# 攻击/伤害类执行器（K3 核验对象身份）
ATTACK_EXECUTORS = {"attack_entity", "attack_animal", "hunt_animal"}
# 修改世界/资产类执行器（K2 核验授权）
MODIFYING_EXECUTORS = {"gather_resource", "break_block", "chop_tree",
                       "gather_wood", "gather_stone", "collect_plant",
                       "place_block", "build_wall", "build_floor",
                       "build_simple_shelter"}
# K5 防抖窗（秒）——纯技术，不是行为规则
ANTI_LOOP_S = 5.0

# 兼容旧白名单语义的拒绝话术（因果签名可解析）
REASON_REQUIRES_PERMISSION = "requires_user_permission"


class SafetyKernel:
    """ActionManager 的前置守卫。guard() 是唯一入口；无 kg 时降级为
    不检查（离线单测），与旧行为一致。"""

    def __init__(self, kg=None, engine=None, config=None):
        self.kg = kg
        self.engine = engine
        self.config = config or {}
        self._last_submit = {}      # (atype,target) → ts（K5）

    # ── guard（ActionManager.propose 在 normalize 之后调用）──────

    def guard(self, action: dict, source: str,
              now: float = None) -> tuple:
        """返回 (ok, reason, granted)。ok=False 时 ActionManager 直接拒绝
        提交；granted=True 时向 params 注入 _kernel_granted（技能层据此
        放行受保护目标——保护判定与执行分离，技能不再兼任资产裁判）。"""
        if self.kg is None:
            return True, "", False
        import mc_knowledge as mck
        now = None if now is None else float(now)
        atype = str(action.get("action_type") or "")
        params = action.get("params") or {}
        target = (params.get("resource") or params.get("target")
                  or params.get("block") or params.get("entity") or "")
        # 归一中文名（normalize_resource 与技能层同一来源）
        try:
            from skills.observation import normalize_resource
            tnorm = normalize_resource(str(target)) if target else ""
        except Exception:
            tnorm = str(target)
        # K5 防抖（所有动作统一）
        key = (atype, str(tnorm or target))
        last = self._last_submit.get(key)
        t_now = time.time() if now is None else now
        if last is not None and t_now - last < ANTI_LOOP_S \
                and atype not in ("stop", "cancel"):
            return False, f"anti_loop:{atype}@{target}", False
        # K2 资产修改需授权："source=user 即授权"（见文件头 K2）——用户点名
        # 给授权证据并放行到技能层执行明确意图；非用户来源时，受保护目标
        # 必须有有效授权节点，否则如实拒绝（语义化 reason 供经验学习）。
        if atype in MODIFYING_EXECUTORS and tnorm:
            ttl = float(mck._cfg(self.config).get("grant_ttl_s", 600))
            if source == "user":
                mck.record_grant(self.kg, "modify", tnorm, ttl_s=ttl, now=t_now)
                self._last_submit[key] = t_now
                return True, "", True
            if mck.is_protected(self.kg, tnorm):
                okg, _left = mck.valid_grant(self.kg, "modify", tnorm,
                                             now=t_now)
                if okg:
                    self._last_submit[key] = t_now
                    return True, "", True
                return (False,
                        f"{REASON_REQUIRES_PERMISSION}:{tnorm}", False)
        # K3 攻击对象核验
        if atype in ATTACK_EXECUTORS and tnorm:
            hostile = mck.is_hostile_species(self.kg, tnorm, self.config)
            if not hostile and source != "user":
                return False, f"target_not_hostile:{tnorm}", False
            if hostile:
                mck.record_grant(self.kg, "engage", tnorm,
                                 ttl_s=float(mck._cfg(self.config).get(
                                     "grant_ttl_s", 600)),
                                 source="hostile_evidence", now=t_now)
        self._last_submit[key] = t_now
        return True, "", False

    # ── K4 濒死停手（ActionManager.tick 内调用；只停手，不裁决去向）──

    def death_floor_check(self, state: dict, current: dict) -> bool:
        try:
            floor = float((self.config.get("mc_world") or {}).get(
                "death_floor_health", 4.0))
            hp = float((state or {}).get("health", 20))
        except Exception as e:
            # 读数坏了按"没死"= 安全网静默消失（§14：必须留痕）
            logger.warning(f"[Safety] 濒死读数失败，本次 K4 跳过: {e!r}")
            return False
        return bool(current) and hp <= floor

    # ── 授权旁路：用户明确点名的攻击（reflex "攻击僵尸"）──────────

    def note_user_target(self, action_key: str, species: str,
                         ttl_s: float = None):
        import mc_knowledge as mck
        ttl = float(ttl_s if ttl_s is not None
                    else mck._cfg(self.config).get("grant_ttl_s", 600))
        return mck.record_grant(self.kg, action_key, species, ttl_s=ttl)
