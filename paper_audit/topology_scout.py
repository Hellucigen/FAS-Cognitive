# topology_scout.py — 机制实验前置：勘察生产建图路径产出的拓扑（只读）
import sys, collections
sys.path.insert(0, '.'); sys.path.insert(1, 'scripts')
import config as _C
from graph_model import KnowledgeGraph
from diffusion_engine import DiffusionEngine
import world_prior as wp, mc_knowledge as mck
from sandbox_lab import SandboxWorld, install_fake_bridge
import minecraft.bridge as BR

w = SandboxWorld(); install_fake_bridge(w)
cfgd = dict(_C.DEFAULT_CONFIG)
cfgd['mc_world'] = {'gatherable': sorted(set(w.recipe_table) |
                    {d for m in w.block_meta.values() for d in m.get('drops', [])} |
                    set(w.block_meta)),
                    'block_meta': {r: dict(v) for r, v in w.block_meta.items()}}
wp.bind_config(cfgd)
kg = KnowledgeGraph()
eng = DiffusionEngine(kg, {'beta_spread': 1.0, 'activation_max': 5.0})
mck.ensure_mc_world(kg, cfgd)
for tgt in sorted(set(w.recipe_table)):
    wp.build_recipe_closure(kg, eng, tgt, bridge=BR)
eng.name_to_node = dict(kg.nodes)

print('nodes=%d edges=%d' % (len(kg.nodes), len(kg.edges)))

# 邻接表（无向）
adj = collections.defaultdict(set)
for e in kg.edges:
    adj[e.src].add(e.dst); adj[e.dst].add(e.src)

def bfs(src):
    dist = {src: 0}
    q = [src]
    while q:
        nq = []
        for n in q:
            for m2 in adj[n]:
                if m2 not in dist:
                    dist[m2] = dist[n] + 1
                    nq.append(m2)
        q = nq
    return dist

# 候选输入对（世界配方表内自然存在的两类需求）
CAND = {
    'A_pickaxe': '物品:wooden_pickaxe',
    'B_furnace': '物品:furnace',
    'B_stonepick': '物品:stone_pickaxe',
    'A_iron': '物品:iron_ingot',
}
dists = {k: bfs(v) for k, v in CAND.items()}
# 找同时接近两输入的节点（dA<=2 且 dB<=2，排除输入自身与 配方: 中介）
for pair in (('A_pickaxe', 'B_furnace'), ('A_pickaxe', 'B_stonepick'),
             ('A_iron', 'B_furnace'), ('A_iron', 'B_stonepick')):
    da, db = dists[pair[0]], dists[pair[1]]
    shared = []
    for n in kg.nodes:
        if n in CAND.values() or n.startswith('配方:'):
            continue
        if da.get(n, 99) <= 2 and db.get(n, 99) <= 2:
            shared.append((n, da.get(n, 99), db.get(n, 99)))
    print(f'== {pair[0]} x {pair[1]}: 共享节点 {len(shared)}')
    for s in sorted(shared, key=lambda x: x[1] + x[2])[:10]:
        print('   ', s)
# 干扰物候选（与两输入均 >2 跳）
print()
for n in ('birch_log', 'diamond', 'dirt', 'coal'):
    k = '物品:' + n if ('物品:' + n) in kg.nodes else n
    row = {p: dists[p].get(k, 'x') for p in CAND}
    print('distractor候选', k, row, 'in_graph:', k in kg.nodes)
