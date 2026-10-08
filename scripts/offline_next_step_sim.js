// 离线复现 world_prior.next_step 的决策（真实 runtime_graph.json，零在线依赖）
// 用途：验证 2026-09-25 真机"有原木不合成、乱转采木"的根因。
const g = require('../data/runtime_graph.json');

const NODES = new Map(g.nodes.map(n => [n.id, n]));
const EDGES = g.edges;
const MAX_DEPTH = 8, CHAIN_MAX_DEPTH = 14;
const item = n => `物品:${String(n || '').trim().toLowerCase()}`;
const bare = s => String(s || '').split(':').pop();
const ea = id => (NODES.get(id) || {}).extra_attrs || {};

function nodeAct(name) {
  const n = NODES.get(name) || NODES.get(item(name));
  return n ? (+n.activation || 0) : 0;
}

function producersOf(it) {
  const dst = item(it), out = [];
  for (const e of EDGES) {
    if (e.relation !== '产生' || e.dst !== dst) continue;
    if (ea(e.src).type !== 'recipe') continue;
    const inputs = {};
    for (const e2 of EDGES)
      if (e2.relation === '需要' && e2.src === e.src && String(e2.dst).startsWith('物品:'))
        inputs[bare(e2.dst)] = Math.max(1, +((e2.extra_attrs || {}).count || 1));
    out.push({ recipe: e.src, kind: ea(e.src).kind || 'craft',
               needs_table: !!ea(e.src).needs_table, tool: ea(e.src).tool || '', inputs });
  }
  return out;
}

function dropSources(it) {
  const dst = item(it), b = bare(it), out = [];
  for (const e of EDGES) {
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
  for (const e of EDGES)
    if (e.relation === '需要工具' && String(e.src).toLowerCase() === String(blk || '').toLowerCase()) {
      const d = bare(e.dst);
      if (d && !out.includes(d)) out.push(d);
    }
  return out;
}

function gatherableBlock(blk) {
  const b = bare(blk);
  return EDGES.some(e => e.relation === '属于' && e.src === b && String(e.dst).includes('可采资源'));
}

function gatherRank(it) {
  const srcs = dropSources(it);
  if (!srcs.length) return null;
  return srcs.some(s => gatherableBlock(s)) ? 0 : 2;
}

function missingOf(inputs, inv) {
  const out = [];
  for (const [m, c] of Object.entries(inputs || {}))
    if ((inv[m] | 0) < (c || 1)) out.push(m);
  return out;
}
const inputsOk = (inputs, inv) => missingOf(inputs, inv).length === 0;

function evidenceRank(it, inv) {
  const gr = gatherRank(it);
  if (gr === 0) return 0;
  const prods = producersOf(it);
  if (!prods.length) return gr != null ? 2 : 3;
  for (const p of prods) {
    const miss = missingOf(p.inputs, inv);
    if (miss.every(m => gatherRank(m) === 0)) return 1;
  }
  return 2;
}

function pathCost(it, inv) {
  const r = evidenceRank(it, inv);
  if (r === 0) return 0;
  let best = null;
  for (const p of producersOf(it)) {
    const s = missingOf(p.inputs, inv).reduce((a, m) => a + evidenceRank(m, inv), 0);
    if (best === null || s < best) best = s;
  }
  return r * 10 + (best !== null ? best : 99);
}

function vis(name) {
  let best = nodeAct(bare(name));
  for (const s of dropSources(name)) best = Math.max(best, nodeAct(bare(s)));
  return best;
}

function leafSiblings(chain, bl) {
  // 简化版：仅用于展示 gather variants；决策主体用不到
  return [bl];
}

function nextStep(target, inventory, exclude = new Set()) {
  const inv = Object.fromEntries(Object.entries(inventory || {}).map(([k, v]) => [k.toLowerCase(), v | 0]));
  const ex = new Set([...exclude].map(x => bare(x)));
  const absMemo = {};
  const abs = (name, depth = 0) => {
    const bn = bare(name);
    if (!ex.size) return 0;
    const k = bn + '|' + depth;
    if (absMemo[k] !== undefined) return absMemo[k];
    if (ex.has(bn)) { absMemo[k] = 1; return 1; }
    if (depth >= 3) return 0;
    const srcs = dropSources(bn).map(bare).filter(x => x !== bn);
    if (srcs.length && srcs.every(x => ex.has(x))) { absMemo[k] = 1; return 1; }
    const prods = producersOf(bn);
    const dead = prods.map(p => { const m = missingOf(p.inputs, inv); return m.length && m.every(x => abs(x, depth + 1)); });
    if (prods.length && dead.every(Boolean)) { absMemo[k] = 1; return 1; }
    absMemo[k] = 0; return 0;
  };
  const t = String(target || '').toLowerCase();
  if (!t) return { done: true, why: '无目标' };
  if ((inv[t] | 0) > 0) return { done: true, action: 'done', why: `背包已有 ${t} x${inv[t]}`, chain: [t] };
  let cur = t; const chain = [t], seen = new Set([t]);
  for (let hop = 0; hop < CHAIN_MAX_DEPTH; hop++) {
    const prods = producersOf(cur);
    const ready = prods.filter(p => inputsOk(p.inputs, inv));
    if (ready.length) {
      ready.sort((a, b) =>
        a.inputs && b.inputs ?
        (missingOf(a.inputs, inv).reduce((s, m) => s + abs(m), 0) - missingOf(b.inputs, inv).reduce((s, m) => s + abs(m), 0)) ||
        (missingOf(a.inputs, inv).reduce((s, m) => s + evidenceRank(m, inv), 0) - missingOf(b.inputs, inv).reduce((s, m) => s + evidenceRank(m, inv), 0)) ||
        (Object.keys(a.inputs).length - Object.keys(b.inputs).length) : 0);
      return { done: false, action: ready[0].kind === 'smelt' ? 'smelt' : 'craft', item: cur,
               needs_table: ready[0].needs_table, inputs: ready[0].inputs,
               why: `${cur} 配方原料齐`, chain: [...chain] };
    }
    const srcs = dropSources(cur);
    if (srcs.length && gatherRank(cur) === 0) {
      const blkKey = b => {
        const gatherable = gatherableBlock(b) ? 0 : 1;
        const tl = toolRequired(b);
        const held = (!tl.length || tl.some(x => (inv[x] | 0) > 0)) ? 0 : 1;
        return [abs(b), gatherable, held, -nodeAct(b)];
      };
      const blk = bare(srcs.slice().sort((a, b) => JSON.stringify(blkKey(a)) < JSON.stringify(blkKey(b)) ? -1 : 1)[0]);
      const tools = toolRequired(blk);
      const held = tools.filter(x => (inv[x] | 0) > 0);
      if (!tools.length || held.length)
        return { done: false, action: 'gather', item: cur, block: blk,
                 why: `${cur} 可由方块 ${blk} 产出`, chain: [...chain, blk] };
      let fresh = tools.filter(x => !seen.has(x));
      if (!fresh.length) return { done: false, action: 'explore', item: tools[0], why: '链条成环', chain: [...chain] };
      fresh.sort((a, b) => pathCost(a, inv) - pathCost(b, inv));
      const need = fresh[0];
      seen.add(need); chain.push(need); cur = need; continue;
    }
    if (prods.length) {
      const rc = p => {
        const miss = missingOf(p.inputs, inv);
        return [miss.reduce((s, m) => s + abs(m), 0),
                miss.reduce((s, m) => s + pathCost(m, inv), 0),
                -miss.reduce((s, m) => s + vis(m), 0),
                miss.length];
      };
      const cmp = (a, b) => { const x = rc(a), y = rc(b); for (let i = 0; i < x.length; i++) { if (x[i] !== y[i]) return x[i] - y[i]; } return 0; };
      const best = prods.slice().sort(cmp)[0];
      let miss = missingOf(best.inputs, inv);
      let fresh = miss.filter(m => !seen.has(m) && evidenceRank(m, inv) < 3);
      if (!fresh.length) {
        const cand = [];
        for (const p of prods) for (const m of missingOf(p.inputs, inv))
          if (!seen.has(m) && evidenceRank(m, inv) < 3) cand.push(m);
        cand.sort((a, b) => (abs(a) - abs(b)) || (pathCost(a, inv) - pathCost(b, inv)));
        fresh = cand;
      }
      if (!fresh.length) {
        const sub = miss[0] || cur;
        return { done: false, action: 'explore', item: sub, why: `缺 ${sub}，无下一步`, chain: [...chain] };
      }
      fresh.sort((a, b) => (abs(a) - abs(b)) || (pathCost(a, inv) - pathCost(b, inv)));
      const sub = fresh[0];
      seen.add(sub); chain.push(sub); cur = sub; continue;
    }
    return { done: false, action: 'explore', item: cur, why: `${cur} 图上无产出路径`, chain: [...chain] };
  }
  return { done: false, action: 'explore', item: cur, why: '跳链超限', chain: [...chain] };
}

// ── 场景回放 ──
const scen = [
  ['空背包（19:44 前的状态）', {}],
  ['背包有橡木 5（用户观察到的状态）', { oak_log: 5 }],
  ['背包有金合欢原木 15（20:12 后的状态）', { acacia_log: 15 }],
  ['背包有橡木5+金合欢15', { oak_log: 5, acacia_log: 15 }],
];
for (const [name, inv] of scen) {
  const r = nextStep('iron_ingot', inv);
  console.log(`\n【${name}】`);
  console.log('  action:', r.action, '| item:', r.item || '', '| block:', r.block || '');
  console.log('  why:', r.why, '| needs_table:', r.needs_table, '| inputs:', JSON.stringify(r.inputs || {}));
  console.log('  chain:', (r.chain || []).join(' → '));
}

// 证据明细：为什么工具选择会跳过木镐
console.log('\n=== 工具 path_cost 明细（空背包）===');
for (const t of ['wooden_pickaxe', 'stone_pickaxe', 'iron_pickaxe', 'diamond_pickaxe']) {
  console.log(`  ${t}: evidence_rank=${evidenceRank(t, {})} path_cost=${pathCost(t, {})}`);
}
console.log('=== 原料 evidence_rank（空背包）===');
for (const it of ['oak_planks', 'acacia_planks', 'stick', 'diamond', 'cobblestone', 'cobbled_deepslate', 'deepslate']) {
  console.log(`  ${it}: gather_rank=${gatherRank(it)} evidence_rank=${evidenceRank(it, {})} path_cost=${pathCost(it, {})}`);
}
console.log('=== 原料 evidence_rank（背包 acacia_log:15）===');
for (const it of ['acacia_planks', 'stick']) {
  console.log(`  ${it}: evidence_rank=${evidenceRank(it, { acacia_log: 15 })} path_cost=${pathCost(it, { acacia_log: 15 })}`);
}
console.log('=== 配方清单核对 ===');
console.log('  producers_of(oak_planks) =', JSON.stringify(producersOf('oak_planks')));
console.log('  producers_of(acacia_planks) =', JSON.stringify(producersOf('acacia_planks')));
