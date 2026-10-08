// 离线端到端复现（v2）：真实 minecraft-data 26.1 闭包 + 新 _chain_cost。
// 模拟 bot.js /recipe_for 修复后（slice 8→32）bridge 会返回的配方全集，
// 在既有 runtime_graph.json 上叠加闭包，再跑 next_step 四种背包场景。
// 目的：不连游戏证明 ① 橡木木板配方进图 ② 背包有原木 → 下一步=合成木板
//      ③ 工具选择不再绕钻石镐 ④ 空背包仍能给出可执行的一步。
const mcd = require('../minecraft_bot/node_modules/minecraft-data')('26.1');
const g = require('../data/runtime_graph.json');

// ── 图的内存表示（叠加层直接写进同一结构）──
const nodes = new Map(g.nodes.map(n => [n.id, { ...n, extra_attrs: { ...(n.extra_attrs || {}) } }]));
const edges = g.edges.map(e => ({ ...e, extra_attrs: { ...(e.extra_attrs || {}) } }));
const item = n => `物品:${String(n || '').trim().toLowerCase()}`;
const bare = s => String(s || '').split(':').pop();

function ensureNode(id, attrs) {
  if (!nodes.has(id)) { nodes.set(id, { id, activation: 0, extra_attrs: attrs || {} }); return true; }
  return false;
}
function addEdge(src, rel, dst, extra) {
  if (edges.some(e => e.src === src && e.relation === rel && e.dst === dst)) return false;
  edges.push({ src, dst, relation: rel, weight: 0.6, extra_attrs: extra || {} });
  return true;
}

// ── 复刻 bot.js /recipe_for（含修复后的 32 上限）──
const nameOf = id => { const x = mcd.items[id] || mcd.blocks[id]; return x ? x.name : null; };
function recipeFor(name) {
  const it = mcd.itemsByName[name.toLowerCase()] || mcd.blocksByName[name.toLowerCase()];
  if (!it) return null;
  const raw = (mcd.recipes || {})[it.id] || [];
  const producers = [];
  for (const r of raw) {
    if (!r.result || r.result.id !== it.id) continue;
    const ings = {};
    const flat = [];
    for (const row of (r.inShape || [])) flat.push(...row);
    for (const x of (r.ingredients || [])) flat.push(x);
    for (const x of flat) {
      const id = (x && typeof x === 'object') ? x.id : x;
      const nm = (id != null && id >= 0) ? nameOf(id) : null;
      if (nm) ings[nm] = (ings[nm] || 0) + 1;
    }
    if (!Object.keys(ings).length) continue;
    producers.push({ result: nameOf(r.result.id) || name, yield: r.result.count || 1,
                     ingredients: ings,
                     needs_table: !!(r.inShape && (r.inShape.length > 2 ||
                                   (r.inShape[0] || []).length > 2)) });
  }
  return producers.slice(0, 32);          // ← 修复后的上限（旧 8）
}
function blockMeta(name) {
  const b = mcd.blocksByName[name.toLowerCase()];
  if (!b) return null;
  const drops = (b.drops || []).map(d => (d && typeof d === 'object') ? nameOf(d.item ?? d) : nameOf(d)).filter(Boolean);
  return { block: b.name, drops, harvest_tools: b.harvestTools ? Object.keys(b.harvestTools).map(id => nameOf(+id)).filter(Boolean) : [] };
}

// ── 复刻 build_recipe_closure：从 iron_ingot BFS 入图（叠加层）──
function buildClosure(target) {
  const seen = new Set([target]); let frontier = [target]; let d = 0;
  while (frontier.length && d < 8) {
    const nxt = [];
    for (const it of frontier) {
      ensureNode(item(it), { type: 'mc_item' });
      const recs = recipeFor(it) || [];
      recs.forEach((r, i) => {
        const res = String(r.result).toLowerCase();
        const rid = `配方:${res}:${r.needs_table ? 'table' : 'hand'}:${i}`;
        ensureNode(rid, { type: 'recipe', kind: 'craft', needs_table: !!r.needs_table, yield: r.yield });
        addEdge(rid, '产生', item(res));
        for (const [mat, cnt] of Object.entries(r.ingredients)) {
          ensureNode(item(mat), { type: 'mc_item' });
          addEdge(rid, '需要', item(mat), { count: cnt });
          nxt.push(mat);
        }
        if (r.needs_table) nxt.push('crafting_table');
      });
      const metas = [it, ...edges.filter(e => e.relation === '产生' && (e.dst === item(it) || e.dst === it)
                       && (nodes.get(e.src)?.extra_attrs?.type === 'mc_species')).map(e => e.src)];
      for (const blk of metas) {
        const meta = blockMeta(blk);
        if (!meta) continue;
        ensureNode(bare(meta.block), { type: 'mc_species' });
        for (const dn of meta.drops) {
          ensureNode(item(dn), { type: 'mc_item' });
          addEdge(bare(meta.block), '掉落', item(dn));
          nxt.push(dn);
        }
        for (const tn of meta.harvest_tools) {
          ensureNode(item(tn), { type: 'mc_item' });
          addEdge(bare(meta.block), '需要工具', item(tn));
          nxt.push(tn);
        }
      }
    }
    frontier = [...new Set(nxt.map(bare))].filter(x => !seen.has(x));
    for (const x of frontier) seen.add(x);
    d++;
  }
  return d;
}
buildClosure('iron_ingot');
console.log(`闭包叠加完成：节点=${nodes.size} 边=${edges.length}`);

// ── 读图函数（与 Python world_prior 同形）──
const ea = id => (nodes.get(id) || {}).extra_attrs || {};
function producersOf(it) {
  const dst = item(it), out = [];
  for (const e of edges) {
    if (e.relation !== '产生' || e.dst !== dst) continue;
    if (ea(e.src).type !== 'recipe') continue;
    const inputs = {};
    for (const e2 of edges)
      if (e2.relation === '需要' && e2.src === e.src && String(e2.dst).startsWith('物品:'))
        inputs[bare(e2.dst)] = Math.max(1, +((e2.extra_attrs || {}).count || 1));
    out.push({ recipe: e.src, kind: ea(e.src).kind || 'craft', needs_table: !!ea(e.src).needs_table,
               tool: ea(e.src).tool || '', inputs });
  }
  return out;
}
function dropSources(it) {
  const dst = item(it), b = bare(it), out = [];
  for (const e of edges) {
    if (!['掉落', '产生'].includes(e.relation)) continue;
    if (e.dst !== dst && e.dst !== b) continue;
    if ((e.src === dst || e.src === b) && e.relation !== '掉落') continue;
    const t = ea(e.src).type;
    if (t === 'mc_species' || e.relation === '掉落') out.push(e.src);
  }
  return out;
}
function toolRequired(blk) {
  const out = [];
  for (const e of edges)
    if (e.relation === '需要工具' && String(e.src).toLowerCase() === String(blk || '').toLowerCase()) {
      const d2 = bare(e.dst);
      if (d2 && !out.includes(d2)) out.push(d2);
    }
  return out;
}
function gatherableBlock(blk) {
  const b = bare(blk);
  return edges.some(e => e.relation === '属于' && e.src === b && String(e.dst).includes('可采资源'));
}
function gatherRank(it) {
  const srcs = dropSources(it);
  if (!srcs.length) return null;
  return srcs.some(s => gatherableBlock(s)) ? 0 : 2;
}
function evidenceRank(it, inv) {
  if ((inv[bare(it)] | 0) > 0) return 0;
  const gr = gatherRank(it);
  if (gr === 0) return 0;
  const prods = producersOf(it);
  if (!prods.length) return gr != null ? 2 : 3;
  for (const p of prods) if (Object.keys(p.inputs).every(m => (inv[m] | 0) >= p.inputs[m] || gatherRank(m) === 0)) return 1;
  return 2;
}

const CHAIN_DEAD = 1e9;
function chainCost(it, inv, seen = new Set(), depth = 10, memo = {}, qc = {}) {
  const bn = bare(it);
  if (!bn) return CHAIN_DEAD;
  if ((inv[bn] | 0) > 0) return 0;
  if (depth <= 0) return CHAIN_DEAD;
  if (seen.has(bn)) return CHAIN_DEAD;
  const key = `${bn}|${depth}`;
  if (memo[key] !== undefined) return memo[key];
  memo[key] = CHAIN_DEAD;
  let best = null;
  if (!qc.srcs) qc.srcs = {}; if (!qc.tools) qc.tools = {}; if (!qc.prods) qc.prods = {};
  if (!(bn in qc.srcs)) qc.srcs[bn] = dropSources(bn).map(bare).filter(b => gatherableBlock(b));
  for (const b of qc.srcs[bn]) {
    let cost = 1;
    if (!(b in qc.tools)) qc.tools[b] = toolRequired(b);
    const tools = qc.tools[b];
    if (tools.length) {
      const tc = Math.min(...tools.map(t => chainCost(t, inv, seen, depth - 1, memo, qc)));
      if (tc >= CHAIN_DEAD) continue;
      cost += tc;
    }
    if (best === null || cost < best) best = cost;
  }
  if (!(bn in qc.prods)) qc.prods[bn] = producersOf(bn);
  for (const p of qc.prods[bn]) {
    const inputs = { ...p.inputs };
    if (p.tool) inputs[p.tool] = Math.max(1, inputs[p.tool] || 0);
    if (p.needs_table && (inv.crafting_table | 0) <= 0) inputs.crafting_table = Math.max(1, inputs.crafting_table || 0);
    let sub = 1, ok = true;
    for (const [m, c] of Object.entries(inputs)) {
      const cc = chainCost(m, inv, seen, depth - 1, memo, qc);
      if (cc >= CHAIN_DEAD) { ok = false; break; }
      sub += cc * Math.max(1, c);
    }
    if (ok && (best === null || sub < best)) best = sub;
  }
  memo[key] = best === null ? CHAIN_DEAD : best;
  return memo[key];
}
function nodeAct(name) { return +(nodes.get(bare(name)) || nodes.get(item(name)) || {}).activation || 0; }
function vis(name) {
  let best = nodeAct(bare(name));
  for (const s of dropSources(name)) best = Math.max(best, nodeAct(bare(s)));
  return best;
}
const CHAIN_MAX = 14;
function nextStep(target, inventory, exclude = new Set()) {
  const inv = Object.fromEntries(Object.entries(inventory || {}).map(([k, v]) => [k.toLowerCase(), v | 0]));
  const ex = new Set([...exclude].map(bare));
  const absMemo = {};
  const abs = (name, depth = 0) => {
    const bn = bare(name);
    if (!ex.size) return 0;
    const k = `${bn}|${depth}`;
    if (absMemo[k] !== undefined) return absMemo[k];
    if (ex.has(bn)) { absMemo[k] = 1; return 1; }
    if (depth >= 3) return 0;
    const srcs = dropSources(bn).map(bare).filter(x => x !== bn);
    if (srcs.length && srcs.every(x => ex.has(x))) { absMemo[k] = 1; return 1; }
    const prods = producersOf(bn);
    const dead = prods.map(p => { const m = Object.entries(p.inputs).filter(([, c]) => (inv[bare('')] , 0) === 0 && (inv[Object.keys({})] | 0), Object.entries(p.inputs).filter(([mm, cc]) => (inv[mm] | 0) < cc).map(([mm]) => mm)); return m.length && m.every(x => abs(x, depth + 1)); });
    if (prods.length && dead.every(Boolean)) { absMemo[k] = 1; return 1; }
    absMemo[k] = 0; return 0;
  };
  const t = String(target || '').toLowerCase();
  if (!t) return { done: true };
  if ((inv[t] | 0) > 0) return { done: true, action: 'done', why: `背包已有 ${t} x${inv[t]}`, chain: [t] };
  let cur = t; const chain = [t], seen = new Set([t]);
  for (let hop = 0; hop < CHAIN_MAX; hop++) {
    const prods = producersOf(cur);
    const ready = prods.filter(p => Object.entries(p.inputs).every(([m, c]) => (inv[m] | 0) >= c));
    if (ready.length) {
      ready.sort((a, b) =>
        Object.keys(a.inputs).filter(m => (inv[m] | 0) < 0).length -
        Object.keys(b.inputs).filter(m => (inv[m] | 0) < 0).length ||
        Object.entries(a.inputs).reduce((s, [m, c]) => s + (evidenceRank(m, inv)), 0) -
        Object.entries(b.inputs).reduce((s, [m, c]) => s + (evidenceRank(m, inv)), 0) ||
        Object.keys(a.inputs).length - Object.keys(b.inputs).length);
      return { done: false, action: ready[0].kind === 'smelt' ? 'smelt' : 'craft', item: cur,
               needs_table: ready[0].needs_table, inputs: ready[0].inputs, chain: [...chain],
               why: `${cur} 配方原料齐（${Object.entries(ready[0].inputs).map(([m, c]) => `${m}x${c}`).join(' + ')}）` };
    }
    const srcs = dropSources(cur);
    if (srcs.length && gatherRank(cur) === 0) {
      const blkKey = b => {
        const tl = toolRequired(b);
        const held = (!tl.length || tl.some(x => (inv[x] | 0) > 0)) ? 0 : 1;
        return [abs(b), gatherableBlock(b) ? 0 : 1, held, -nodeAct(b)];
      };
      const blk = bare(srcs.slice().sort((a, b) => {
        const ka = blkKey(a), kb = blkKey(b);
        for (let i = 0; i < ka.length; i++) if (ka[i] !== kb[i]) return ka[i] - kb[i];
        return 0;
      })[0]);
      const tools = toolRequired(blk);
      const held = tools.filter(x => (inv[x] | 0) > 0);
      if (!tools.length || held.length)
        return { done: false, action: 'gather', item: cur, block: blk,
                 why: `${cur} 可由方块 ${blk} 产出${held.length ? '（工具已备）' : '（无需工具）'}`, chain: [...chain, blk] };
      let fresh = tools.filter(x => !seen.has(x));
      if (!fresh.length) return { done: false, action: 'explore', item: tools[0], why: '链条成环', chain: [...chain] };
      fresh.sort((a, b) => chainCost(a, inv, seen) - chainCost(b, inv, seen));
      seen.add(fresh[0]); chain.push(fresh[0]); cur = fresh[0]; continue;
    }
    if (prods.length) {
      const missOf = p => Object.entries(p.inputs).filter(([m, c]) => (inv[m] | 0) < c).map(([m]) => m);
      const rc = p => {
        const miss = missOf(p);
        return [miss.reduce((s, m) => s + abs(m), 0),
                miss.reduce((s, m) => s + chainCost(m, inv, seen), 0),
                -miss.reduce((s, m) => s + vis(m), 0),
                miss.length];
      };
      const cmp = (a, b) => { const x = rc(a), y = rc(b); for (let i = 0; i < x.length; i++) if (x[i] !== y[i]) return x[i] - y[i]; return 0; };
      const best = prods.slice().sort(cmp)[0];
      let miss = missOf(best);
      let fresh = miss.filter(m => !seen.has(m) && evidenceRank(m, inv) < 3);
      if (!fresh.length) {
        const cand = [];
        for (const p of prods) for (const m of missOf(p))
          if (!seen.has(m) && evidenceRank(m, inv) < 3) cand.push(m);
        cand.sort((a, b) => (abs(a) - abs(b)) || (chainCost(a, inv, seen) - chainCost(b, inv, seen)));
        fresh = cand;
      }
      if (!fresh.length) {
        const sub = miss[0] || cur;
        return { done: false, action: 'explore', item: sub, why: `缺 ${sub}，无下一步`, chain: [...chain] };
      }
      fresh.sort((a, b) => (abs(a) - abs(b)) || (chainCost(a, inv, seen) - chainCost(b, inv, seen)));
      seen.add(fresh[0]); chain.push(fresh[0]); cur = fresh[0]; continue;
    }
    return { done: false, action: 'explore', item: cur, why: `${cur} 图上无产出路径`, chain: [...chain] };
  }
  return { done: false, action: 'explore', item: cur, why: '跳链超限', chain: [...chain] };
}

// ── 场景回放 ──
const scen = [
  ['空背包', {}],
  ['背包橡木 5（用户观察的状态）', { oak_log: 5 }],
  ['背包金合欢原木 15', { acacia_log: 15 }],
  ['橡木5 + 金合欢15', { oak_log: 5, acacia_log: 15 }],
];
for (const [name, inv] of scen) {
  const r = nextStep('iron_ingot', inv);
  console.log(`\n【${name}】${r.action} ${r.item || ''}${r.block ? '@' + r.block : ''} | ${r.why}`);
  console.log('  链:', (r.chain || []).join(' → '));
}
console.log('\n=== 工具链价（空背包，seen 含 iron_ingot/raw_iron/stone_pickaxe/cobblestone）===');
const seen0 = new Set(['iron_ingot', 'raw_iron', 'stone_pickaxe', 'cobblestone']);
for (const t of ['wooden_pickaxe', 'stone_pickaxe', 'iron_pickaxe', 'diamond_pickaxe'])
  console.log(`  ${t}: ${chainCost(t, {}, seen0)}`);
console.log('=== oak 闭包核对 ===');
console.log('  producers_of(oak_planks) =', JSON.stringify(producersOf('oak_planks')));
console.log('  stick 配方数 =', producersOf('stick').length, '| crafting_table 配方数 =', producersOf('crafting_table').length);
console.log('  oak_planks chain_cost（背包 oak_log:5）=', chainCost('oak_planks', { oak_log: 5 }));
