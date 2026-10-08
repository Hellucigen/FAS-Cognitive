# FAS 大规模性能分析（fas_scale_performance.md）

> 认知架构重评估 · 附加分析
> 问题：当系统图谱到达大规模时，是否会产生性能问题？
> 方法：以当前实现源码为证据（file:line 可查），按复杂度逐热点推演；
> 区分**实现事故**（可修，不动架构）与**架构固有代价**（需要设计决策）。
> 当前基线：458 节点 / 595 边（data/runtime_graph.json 实测）。

---

## 一、结论（TL;DR）

**会产生，但全部是可以不触动认知架构的工程问题。**

当前规模（<1k 节点）下没有任何可感知的性能问题；所有热点在毫秒级。
按现有实现线性外推，四堵墙按规模依次出现：

| 墙 | 规模阈值（约） | 根源 | 性质 |
|---|---|---|---|
| ① 扩散每步 O(N) 行动队列重建 | ~5k 节点开始可感，20k 明显卡顿 | 实现事故 | 每步全图扫描，扩散越活跃越贵 |
| ② 1.5s 自动线程的 O(N+E) 全图衰减 | ~50k 节点持续占用单核，线程锁竞争放大延迟 | 实现事故（+一个设计问题：衰减是否该全图周期做） | 后台线程常驻开销 |
| ③ JSON 全量持久化（双写、持锁） | ~20k 节点保存秒级；100k 节点十秒级且**阻塞所有请求** | 实现事故 | 锁内序列化 = 全系统停顿 |
| ④ 无邻接索引的边查找 O(E) | 与①③复合：每次边查找线性扫全边表 | 实现事故 | 把扩散从 O(触发前沿) 变成 O(全图) |

真正的架构层好消息：**激活扩散模型天然稀疏**。衰减把激活压到 0，
任意时刻的"活跃前沿"（activation > 0 的节点）远小于全图——算法的
正确实现复杂度是 O(前沿×平均出度)，与图规模基本无关。性能问题的
本质是**实现没有利用模型自带的稀疏性**，而不是扩散模型本身不可扩展。

---

## 二、热点清单（按代码证据）

### 2.1 扩散主循环（每条消息 + 每 1.5s 后台线程）

| 热点 | 位置 | 现状复杂度 | 说明 |
|---|---|---|---|
| `_refresh_action_queue()` | diffusion_engine.py `diffuse_step()` 末尾 | **O(N) / 每步** | 每扩散一步就全图扫描重建行动队列。一轮 6 步 = 6 次全图扫描；自动线程每 1.5s 再来一轮 |
| `get_out_edges(nid)` | graph_model.py | **O(E) / 每个点火节点** | 边存 `list[Edge]`，无邻接索引，每个点火节点线性扫全边表 |
| `get_edge(src,dst,rel)` | graph_model.py | **O(E) / 次** | 同上；check_and_inject、bootstrap、启动 overlay 都在调用 |
| `decay_step()` | diffusion_engine.py，每 1.5s | **O(N+E) / 次** | 构建全图 node_space_map 后逐节点衰减——包括激活已经为 0 的绝大多数节点 |
| `get_topk(k)` | diffusion_engine.py，每条回答 | O(N log N) + O(E) 过滤 | heapq.nlargest 全节点 + 全边扫描过滤 infra |
| DEBUG 块 | app.py 扩散前调试段 | **O(N) 持锁 / 每消息** | 在 `kg._lock` 内遍历全部节点拼调试字符串——锁内做日志格式化 |

**复合效应**：一条用户消息的完整路径 ≈ 注入（O(E) incident 扫描）+
6 步扩散（每步 O(N) 队列刷新 + 每点火节点 O(E) 出边查找）+ Top-k
（O(N log N)+O(E)）+ INFRA_IDS 重算（O(N)，见 2.3）——渐近上
**每条消息是 O(N·E) 级**，被小图的常数掩盖着。

### 2.2 持久化（当前最实际的墙）

| 热点 | 位置 | 现状 | 说明 |
|---|---|---|---|
| `_save()` 双写 | app.py `_save()`，30s 节流 | kg.save(GRAPH_PATH) **和** pack_mgr.save_runtime_graph(data/runtime_graph.json) | 同一份图两次全量 JSON 序列化（indent=2，体积×1.5+） |
| 持锁序列化 | graph_model.py `save()` → `to_dict()` | 在 `self._lock` 内完成 | 序列化期间所有请求线程与自动线程全部阻塞。458 节点毫秒级无感；节点数×边数线性恶化，**10 万节点约数百 MB 文本、秒到十秒级的全系统停顿，每 30s 一次** |
| FAISS 删除即重建 | embedding 管理路径 | IndexFlatIP 不支持删除，删节点 → 全索引重建 | 重建本身 O(N) 拷贝（非重编码）；知识清理/迁移脚本批量删节点时代价集中释放 |

### 2.3 每请求杂项

| 热点 | 位置 | 复杂度 | 说明 |
|---|---|---|---|
| INFRA_IDS 重算 | app.py 每条回答 | O(N) | 每次回答重新扫描全部节点找 label∈(infrastructure, procedural)——一个可缓存的集合每次重算 |
| 情绪节点扫描 | curiosity_engine.check_and_inject | O(N) / 输入 | 遍历全图找 type=emotion 的节点做子串匹配 |
| 自指扫描 | self_graph.generate_thought / decay_preferences | O(E) | 全边扫描找 Self 邻接 / 喜欢 边 |
| name_to_node 全量重建 | pack enable/disable 端点 | O(N) | 运行时 reload 时可接受，但与 2.1 叠加造成停顿感 |

---

## 三、规模推演（数量级估算）

设 N=节点数，E≈2N（当前 595/458≈1.3，知识密集后向 2-3 走），
d=平均出度，F=活跃前沿大小（衰减后通常 ≪ N）。

| N（节点） | ①每消息扩散路径 | ②衰减/1.5s | ③保存/30s | 体感 |
|---|---|---|---|---|
| 1k（≈当前×2） | <10ms | <5ms | ~5ms | 无感 |
| 10k | 数十 ms（队列刷新开始占比过半） | ~10-20ms 常驻 | ~100ms×2 双写，锁内偶发卡顿 | 打字流畅，偶发抖动 |
| 50k | 数百 ms（O(E) 边查找主导） | ~100ms 常驻单核 | ~1s 锁内停顿 | 明显卡顿；自动线程与请求互相拖拽 |
| 500k-1M | 秒级每消息 | 不可行（每 1.5s 扫百万节点） | 数十秒全系统停顿 | 架构性不可用，必须改造 |

内存侧：Python Node 对象（含 dict attrs）约 0.5-1KB/节点，1M 节点
≈ 0.5-1GB 常驻 + JSON 序列化瞬时双倍 + FAISS float32 向量
（384 维 ≈ 1.5KB/条，1M ≈ 1.5GB）。内存不是最先到的墙，但持久化是。

---

## 四、改造建议（按 P 级，均不动扩散数学）

### P0 级（当前就值得做，小改动）

1. **邻接索引**：`kg` 维护 `out_index: {nid: list[Edge]}` 与
   `edge_index: {(src,dst,rel): Edge}`，增删边时同步维护——
   get_out_edges/get_edge 变 O(d)/O(1)，扩散回到"只碰点火节点"。
2. **行动队列刷新改为按需/脏标记**：只有 procedural 节点激活变化超过
   阈值才刷新，而不是每步全图重建。
3. **衰减惰性化**：只衰减 `activation > 0`（或 dirty）的节点；
   衰减后为 0 的节点出集。衰减复杂度从 O(N) 变 O(F)。
4. **保存去双写 + 出锁**：单份持久化（runtime_graph.json 即真相，
   或 kg.save 即真相——二者只能选一）；序列化在锁外对快照做
   （`to_dict()` 在锁内拷贝引用、锁外序列化），异步写盘。
5. **INFRA_IDS 缓存**：启动时算一次，增删 infrastructure/procedural
   节点时失效重建。
6. **删 DEBUG 全图遍历**（或改从 get_topk 取样）。

### 规模级（N > ~10k 时再做）

7. **持久化升级**：SQLite/LMDB 或 append-only journal + 周期快照，
   替换 JSON 全量重写；保存与请求路径彻底解耦。
8. **FAISS**：IndexFlatIP 在 10 万向量内其实够用（毫秒级），
   到 50 万+ 再换 IVF/HNSW；删除用 tombstone + 定期 compact
   替代全量重建。
9. **锁粒度**：按 graph_space 分片，或读写锁（扩散读多写少）；
   自动线程与请求线程不再互斥全图。
10. **反思/统计类全图扫描**（情绪扫描、Self 邻接、gap 统计）：
    加 type/label 倒排索引，O(N) → O(该类节点数)。

### 不需要做的

- **不是**把扩散换成 GNN/张量运算——稀疏前沿传播本就是对的形态
  （GNN 实验在 P2，定位是研究性增强而非性能救火）。
- **不是**给图换数据结构框架（networkx 等）——瓶颈在持久化与
  索引缺失，不在 Python 本身；先让算法复杂度对，再谈常数。

---

## 五、与认知架构的关系

性能改造与 V2 认知架构是同构的：V2 要求"扩散在活跃前沿上运行、
无全图旁路"，性能要求"算法复杂度贴住活跃前沿"。**把实现改对，
恰好就是把架构做对**——没有需要为性能牺牲的认知原则，也没有
需要为架构保留的低效。

一句话回答用户的问题：**会——四堵墙都是实现事故而非架构宿命；
按 P0 级清单改造后，同一套"万物皆图 + 激活扩散"可以撑到十万节点级，
持久化改造后更高。当前 458 节点规模下无需任何紧急动作，但
①（邻接索引）与④（保存去双写）建议随 P0 重构顺手完成。**

---

## 六、P0 实施记录（2026-09-03）

P0 清单已全部落地，扩散数学与认知语义零改动。唯一数值放宽：新增
config `activation_epsilon`（1e-4）——衰减后低于它的激活吸附为 0 并
移出活跃前沿。该值远低于 min_spread 0.05 与全库最低决策阈值 0.01，
吸附在行为上不可观察，只为把浮点噪声尾巴归零、保证前沿稀疏性。

### 实施内容

1. **邻接索引（graph_model.py）**：`_out_index`/`_in_index`（列表语义，
   允许重复边）/`_pair_index`（first-wins，匹配 get_edge 现状）+
   `Edge._seq` 模块级单调序号（不序列化，按列表序重建）。不变量写入
   代码注释：**`kg.edges` 列表是唯一真相，索引是派生缓存**。
   新增 `rename_node()`（拒绝冲突而非静默覆盖）、`remove_edges()`
   （任一维度可省的批量删）、`rebuild_indexes()`、三类结构回调
   （renamed/edge_removed/node_removed）。
2. **活跃前沿（diffusion_engine.py）**：`_active_nodes`（dict-as-ordered-set）
   /`_active_edges`（identity set）。diffuse_step/decay_step/get_topk 只遍历
   前沿；mark_active/mark_edges_active 供一切绕过 activate_from_inputs 的
   直写点调用（app/curiosity/drive/vision 全部接通）；回合间衰减
   （聊天主路径的最后一堵 O(N) 墙）同样前沿化（apply_inter_round_decay）。
   行动队列改增量刷新 + 脏标记（note_action_dirty），`changed_ids=None`
   保持"无条件全量重建"语义，两种模式同一排序键。
3. **持久化**：删除根目录 knowledge_graph.json 化石写（全库无读者）；
   graph_model.save 原子替换（同目录 temp + fsync + os.replace）；
   app.py 单飞写入器（daemon 线程 + 折叠补写 + 补写重取快照 +
   force 等待语义 + atexit 冲洗 + 启动 temp 清理）。
4. **杂项**：INFRA_IDS 全图扫描集合 → 逐项 label 判断；删除锁内
   DEBUG 全节点遍历；响应展示改 engine.active_snapshot()（省每消息
   O(N+E) 锁内扫描）；pack 启停/删除/重载 4 处换图点统一
   rebuild_indexes + reattach_graph。

### 性能对照（bench_diffusion.py --timing，PYTHONHASHSEED=0）

| 规模 | diffuse_round 改前 | 改后 | 每消息合计改前 | 改后 |
|------|----------|------|----------|------|
| 1k   | 62.1 ms  | 7.2 ms  | 63.6 ms  | 7.8 ms  |
| 10k  | 3691.2 ms | 50.3 ms | 3693.3 ms | 52.5 ms |
| 50k  | 24510.5 ms | 90.6 ms | 24520.2 ms | 98.7 ms |

每消息路径 = activate_from_inputs + diffuse_round(6 步) + get_topk。
复杂度随规模亚线性增长（活跃前沿稀疏性生效），50k 节点每消息 ~0.1s，
满足交互式响应要求。

### 验证（不验证不宣称）

- **行为等价指纹**（scripts/bench_diffusion.py，改造前基线
  scripts/bench_baseline.json）：固定种子合成图 1k×12 轮 + 10k×8 轮，
  覆盖 activate_from_inputs / mark_active 直写 / 清零直写 / 好奇两段
  扩散 / 独立衰减 / rename / 重复三元组 / 自环 / remove_edge /
  update_edge / remove_node，全检查点一致 ✔
- **索引一致性断言**：邻接索引与 kg.edges 逐条交叉验证 +
  rebuild_indexes() 前后等价 ✔
- **回归**：verify_p0_1 / verify_p0_2 / verify_p0_all /
  verify_event_normalize 全部通过 ✔
- **集成冒烟**：app.py 整体导入、写入器线程、force 落盘 ✔

### 可观察行为变化（3 处，均已确认为改进）

1. get_topk 不再用零激活节点填充 top-k（前沿外激活已吸附为 0，
   零激活节点无认知意义）；
2. 节点改名冲突返回 409 而非静默覆盖旧节点；
3. disposition remove 现在写 evolution log（此前整表重赋值绕过了日志）。

### 明确未做（遗留清单，见 §四）

SQLite/分片锁/倒排索引/反思类全图扫描——当前规模不需要，
扩图到 10 万+ 时按 §四 规模级清单另立项。
