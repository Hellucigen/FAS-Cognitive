# DESIGN.md — Persistent State Advantage 实验(预注册,2026-10-03,数据前冻结)

目录:`experiments/persistent_state_advantage/`。零生产修改。

## 0. 研究问题

外部对话历史被删除或严格受限后,FAS 的持久关系状态能否继续影响
未来行为,并相对 History/RAG 产生可量化优势?

## 1. 三阶段设计

- **Phase 1 经历获取(零 LLM,全条件同一事件流)**:脚本化世界交互——
  ①饥饿低时 eat_bread 成功(动作→结果:面包恢复饥饿);
  ②gather oak_log ×2 → craft oak_planks 成功(动作→结果:木板来自橡木);
  ③gather_iron_ore ×3 → tool_missing 失败(失败经验);
  ④无关填充(explore/dirt ×6)。事件流逐行进入各条件记忆系统。
- **Phase 2 上下文移除**:FAS-full 保留图谱、历史清零;FAS-reset 图谱
  重置到 Phase-1 前(仅先验闭包);FAS-sham 保留 Phase-1 节点、删除
  经历边(writeback=False 装配);history 条件只保留流的后 N token。
- **Phase 3 行为测试(LLM 决策头,新世界)**:
  - T1 action-effect(饱和性 sanity):饥饿=8、背包面包、菜单含
    eat_bread;成功=饥饿恢复。预期各条件等效(常识可达)。
  - T2 goal-conditioned(**主任务**):世界含 oak_log 与 birch_log 树;
    目标 obtain oak_planks;facts 闭卷;门控开(菜单含 craft_oak_planks
    ⟺ 上下文出现 oak_planks)→ 无"木板来自橡木"记忆者无法解锁合成。
    预算 10 决策。成功=库存出现 oak_planks。
  - T3 failure-informed:世界含 iron_ore 与橡树;目标 obtain raw_iron
    (直接不可达);度量=重复 gather_iron_ore 失败次数(越少越好)。
    预算 12 决策。

## 2. 条件(8)

B0-direct;B1-h128 / B1-h512 / B1-h2048(经历流后缀切片,4 字符/token
近似);B2-rag(嵌入 top-6,记录 hit/rank/direct-answer);FAS-full
(Phase-1 图谱+扩散+demand+门控);FAS-reset(重置=仅先验闭包图);
FAS-sham(Phase-1 节点、零经历边)。

## 3. 预注册假设与主指标

- **H1(主)**:T2 上 FAS-full > FAS-reset(exact completion,配对
  McNemar,α=0.05)。
- H2:低 budget 下 FAS-full > history(B1-h128;预算曲线 T2 全报)。
- H3:FAS-full > B0(T2)。
- H4:若 FAS-full ≈ FAS-reset → 任务未真正依赖持久状态(无效化 H1)。
- H5:T3 上 FAS-full 失败重复 < FAS-reset(事件框架极性)。
- 族内 Holm(H1-H5);n=10 配对种子。
- T1 预注册为 sanity(预期基线等效,不作优势主张)。

## 4. 审计(预检八项,全过才正式运行)

任务构造/state 隔离(reset 图哈希=Phase-1 前)/prompt 等价(除
context 块)/泄漏(测试 prompt 不含训练结论;RAG 直达答案单独标注)/
FAS 持久状态审计(Phase-1 前后节点/边/哈希)/基线访问审计/token
预算审计/确定性冒烟。

## 5. 机制探针(零 LLM)

- 持久状态前后对比(节点/边/哈希);
- 激活恢复:Phase-2 情境线索(饥饿/oak_log 在场)作种子扩散,
  学得关系节点是否进 Top-k;
- **Hebbian 权重轨迹**:Phase-1 期间先验闭包边(经使用)的权重
  逐拍记录——连续强化证据(承接 CONSOLIDATION_AUDIT);
- learned_relation_recovered 标志。

## 6. 成功层级(§18)

L1 FAS-full>FAS-reset(主);L2 >B0;L3 >受限 history;L4 >RAG
(不要求)。若 L1 不成立 → 如实报 NOT/条件性,并定位失败层级
(state formation/activation/working set/serialization/consumer/task)。
