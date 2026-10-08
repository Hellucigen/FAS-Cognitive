# PREFLIGHT_AUDIT.md — 正式运行前的八项审计(2026-10-03)

基于 seed-0 冒烟(run: FAS-full/FAS-reset/B0-direct × T1/T2/T3):

1. **任务构造审计**:T1 饥饿任务(常识 sanity,预期等效);T2 配方知识
   任务(exclude_recipes=("oak_planks",) → 知识只能来自 Phase-1);
   T3 失败任务(iron_ore 工具门槛)。✓
2. **state 隔离审计**:FAS-reset 图 = Phase-1 前基图(先验闭包,
   无 oak_planks 经历边);FAS-full 图含经历结构;
   FAS-sham = Phase-1 节点、零经历边。冒烟确认。✓
3. **prompt 等价审计**:八条件 prompt 仅 context 块不同;
   facts/菜单/预算/停止条件/模型/温度全同。✓
4. **泄漏审计**:T2/T3 的 facts 与测试 prompt 不含训练结论
   ("planks from oak_log"/"bread restores hunger" 均不在测试文本中);
   门控规则使配方知识只能经上下文进入。✓
5. **FAS 持久状态审计**:Phase-1 后 hebbian_changed=10
   (10 条先验闭包边权重经连续强化改变);
   stream_tokens=465(→history-2048 全含,history-128 只含尾部
   无关情节)。✓
6. **基线访问审计**:B1 各预算拿到真实经历流切片;
   B2-rag 检索真实 top-6(direct-answer 命中在分析中标注);
   B0 零记忆。✓
7. **token 预算审计**:全条件 max_tokens=300;
   prompt 侧仅 context 块不同(分析按 prompt_tokens 记账)。✓
8. **确定性冒烟**:seed 0 九个 run 全部完成,区分度符合预注册方向
   (T2 full=1/reset=0/B0=0;T3 fails full=3<reset=7<direct=12)。✓

结论:**八项全 PASS,正式运行放行**。
