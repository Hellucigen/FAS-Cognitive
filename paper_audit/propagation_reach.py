# propagation_reach.py — 只读诊断：当前图拓扑下 forward 扩散的最大可达深度
import sys, collections
sys.path.insert(0, '.'); sys.path.insert(1, 'scripts')
import run_exp_mechanism as R

fas = R.MechanismFAS(use_diffusion=True)
kg = fas.kg
print('nodes=%d edges=%d' % (len(kg.nodes), len(kg.edges)))

# 出边/入边统计
out_deg = collections.Counter()
in_deg = collections.Counter()
for e in kg.edges:
    out_deg[e.src] += 1
    in_deg[e.dst] += 1
nodes_with_out = [n for n in kg.nodes if out_deg[n] > 0]
print('有出边的节点数:', len(nodes_with_out), '/', len(kg.nodes))
sinks = [n for n in kg.nodes if out_deg[n] == 0]
print('汇点数(无出边):', len(sinks))
print('汇点样例:', sinks[:10])

# 每个 node 的 2 步 forward 可达新节点数
two_hop = {}
for n in nodes_with_out:
    reach1 = set()
    for e in kg.edges:
        if e.src == n:
            reach1.add(e.dst)
    reach2 = set(reach1)
    for m in reach1:
        for e in kg.edges:
            if e.src == m:
                reach2.add(e.dst)
    new2 = reach2 - reach1 - {n}
    two_hop[n] = len(new2)
print()
print('2 步 forward 能到达新节点的源（>0）:')
for n, c in sorted(two_hop.items(), key=lambda kv: -kv[1])[:12]:
    print('  ', n, c)
print('最大 2 步新增:', max(two_hop.values()) if two_hop else 0)

# 方向语义核对：需要/产生/掉落 均为 forward → 配方→物品，块→物品
print()
print('结论核对: 物品:* 类节点出边数分布:')
item_out = [out_deg[n] for n in kg.nodes if n.startswith('物品:')]
print('  物品:* 节点数:', len(item_out), '有出边者:', sum(1 for x in item_out if x > 0))
print('  配方:* 出边均值:', sum(out_deg[n] for n in kg.nodes if n.startswith('配方:')) /
      max(sum(1 for n in kg.nodes if n.startswith('配方:')), 1))
