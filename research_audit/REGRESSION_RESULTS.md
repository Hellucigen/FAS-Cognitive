# REGRESSION_RESULTS.md — 回归验证结果
**日期**: 2026-09-30
**基线**: git 4685726(私有图谱样本退出跟踪后)
**环境**: Python 3.12.13(`E:/Miniforge.envs/Fascinator/python.exe`),Windows 11

---

## 1. pytest 套件(真 pytest 文件)

`python -m pytest tests/ -q`:

```
82 passed, 1 warning in 2.49s
```

- **12 个真 pytest 文件,共 82 个测试,零失败零错误**。
- 覆盖模块:behavior_competition、causal_pending_attribution、curiosity_refactor、diffusion_inhibition、experience_single_writer、experience_timeline、modulation_events、modulator_dynamics、modulator_graph_presence、modulator_relations、reward_disposition、self_learning_loop。
- 仅剩 1 个 warning:test_modulator_graph_presence::test_A_presence 返回 tuple 而非 None(pytest 风格提示,非失败)。
- **结构事实**(L1-TH-01 二期修复后):114 个测试文件经 AST 全量分类——93 脚本式 + 1 装配式(test_modulation_targets)+ 12 真 pytest + 8 real_* 实机。`pytest tests/` 从"永远 INTERNALERROR"变为可运行的真实回归通道。

## 2. 脚本式回归(独立运行,退出码=结果)

生产方式:每个文件按其文件头注释 `python tests/<file>.py` 独立执行。

### 2.1 本次 L1 修复触及模块的脚本回归

| 脚本 | 产出 | 结果 |
|---|---|---|
| `python tests/test_diffusion_inhibition.py` | L1-DFU-01 回归锁(纯抑制发射/混合边不变/反向抑制) | 3/3 PASS |
| `python tests/test_dialogue_decision.py` | L1-DGR-02/04(锁序、候选收集) | PASS(桩引擎,无真实图写入) |
| `python tests/test_continuous_cognition.py` | L1-DGR-02/05(锁序、场探测失败路径) | PASS(临时 KG+桩引擎) |
| `python tests/test_cognition_modes.py` | L1-DGR-02(attention_context 锁序) | PASS(模式测试全过) |
| `python tests/test_modulation_targets.py` | R2 P8 闸门(14 靶点真旋钮;装配式脚本) | PASS(单独运行,P8 全过) |
| `python tests/test_cognitive_context.py` | 认知上下文双路编译/追溯链(七分区/证据状态) | PASS(Cognitive Context 全部通过)、`exit 0` |
| `python scripts/verify_a1_wiring.py` | **A1-A 接线回归锁(生产形态 L2 渲染)** | **10/10 PASS、`exit 0`**(详见 2.3) |
| `python tests/test_pers01_guard.py` | **A5-A 三重守卫 + PIN-11(PERS-01)** | **8/8 PASS、`exit 0`** |
| `python tests/test_a4_access_observation.py` | **A4-A 观测接入不变式(晋升 dry-run)** | **12/12 PASS、`exit 0`**(find_by_node/达标/排序/安全边界/遗忘保留) |
| `python tests/test_promote_trigger.py` | 因果晋升链末端触发验收(晋升路径不受 A4-A 扰动) | PASS |

全量复核:2026-09-30,上述均实跑(含 A4-A 改动后复跑)

### 2.3 A1-A 接线验证(scripts/verify_a1_wiring.py)

A1-A 修复本身不入 product 测试文件(nlp_processor 渲染块没有既有单测),verify_a1_wiring.py 为它新增的回归锁,按生产调用形态构造(只传 `cognitive_context`,不传 mode/attention_context 形参):

```
[PASS] L2 编译携带 attention_context 键(≥4 分区)
[PASS] L2 编译携带 cognitive_demand/gap/resource 三层
[PASS] 生产形态调用渲染【认知状态】块
[PASS] 【认知状态】含模式行
[PASS] 【认知状态】含 8 键深层渲染(self 分区)
[PASS] 生产形态调用渲染【认知资源路由】块
[PASS] 【认知资源路由】按不外显原则(只给维度名,无强度数值)
[PASS] 门=False 时【认知状态】整体消失(回滚)
[PASS] 门=False 时【认知资源路由】消失
[PASS] 门=False 时既有渲染不受影响(发言提纲仍在)
✓ A1 接线全过(10/10)
```

- 运行方式:`PYTHONIOENCODING=utf-8 /e/Miniforge.envs/Fascinator/python.exe scripts/verify_a1_wiring.py`(Windows 控制台 GBK 会拒打 ✓,不影响判定,退出码 0)。
- **回滚语义可测**:config 键 `nlp_render_cognitive_context=False` → 新渲染整体消失、既有【发言提纲】保留——正好落在 PATCH_LOG A1-A 的"零调用方行为变化"上。

### 2.2 补丁文件的语法验证
- `python -m py_compile` 通过:app.py、diffusion_engine.py、dialogue_decision.py、cognition_modes.py、continuous_cognition.py、tests/conftest.py(6 文件,2026-09-30 实跑)。

## 3. 机制证伪独立实验(mechanism_falsification/)

663 个确定性种子运行,**零错误零igo 零异常**。详见 MECHANISM_RESULTS.md:
- JCG>0 率 0.970;≥2 输入的 joint/scaling 组 MRR=r@1=1.0
- 噪声单调侵蚀 JCG(0.239→0.073,Bonf 显著)
- 拓扑方向调制(双向 ranks 升、JCG 降)
- 复杂度无规模退化证据;diffusion 20/20 击败 flat;ρ=+0.811

## 4. 修复前后对比

| | 修复前 | 修复后 |
|---|---|---|
| `pytest tests/` | INTERNALERROR(收集即崩,0 测试可跑) | **82 passed 0 errors** |
| 扩散抑制 | 纯负节点静默不发射 | 3/3 回归锁通过 |
| 空输入 /api/nlp | busy 永久置位,循环停摆 | 早退出口完整清理 |
| 锁序 | 3 处(实 4 处)kg→engine 反转,死锁风险 | 全部 engine→kg |
| 场所探测失败 | 静默变最恶劣默认值(全场消失/过冷) | None+跳过,两门不回退 |
| 失败占位串 | 记入 timeline/expression,冒充真经历 | 拦截,chat_log 保留 |
| add_edge 失败 | 草稿边无声消失 | warning+skipped 计数 |
| 损坏图静默覆写(PERS-01) | load 异常后 save 可覆写损坏源 | 三重守卫:load 标记 _load_failed → save 拒绝;源损坏备份+拒绝 |
| 裸 except 静默失效(L1-CON-6) | ≈18 处吞异常无痕 | ≈33 处 warning 化(含逐点先期补的),4 处合法默认返回保留并记录 |
| 记忆晋升 access 计数(L1-MEM-01) | mark_accessed 全仓零调用,排序退化仅 importance | faiss 命中→经历计数+[A4-Obs] 轨迹日志;自动晋升仍未启用(dry-run 观测期) |
| 生产 prompt 认知状态(A1) | attention_context/demand 编译但不渲染(CON-1/2 死代码) | 8 分区+三层结构渲染,视野门=False 可精确回滚 |

## 5. 未纳入回归范围(如实声明)
- ~~93 个脚本式回归未逐一全跑~~(**2026-09-30 A6 后不再成立**):`scripts/run_all_regression.py` 双通道全量执行,本轮 **107/107 PASS**(93 脚本 + 12 pytest + 2 新增 A4/A5 回归锁,39.2s,6 并行)。全量脚本回归清单与运行方式见 6。
- 实机 real_* 系列需真实服务,本环境不可跑(执行器默认 SKIP,--with-real 强制)。

## 6. 全仓库回归通道说明(审计发现,非缺陷)
本项目测试文化 = 脚本式回归:`tests/` 下 93 个文件"模块顶层直接执行断言 + sys.exit 状态码",pytest 从未是其运行器。**建议(进 NEXT_ACTIONS)**:CI 或文档级约定——回归=遍历运行 `python tests/test_*.py` 收集退出码;pytest 仅承载 12 个真 pytest 文件。conftest.py 的 AST 分类器同时保证新脚本文件不会破坏 pytest 通道。