import sys, os, json
sys.path.insert(0, os.getcwd()); sys.path.insert(1, os.path.join(os.getcwd(),"scripts"))
from sandbox_lab import build_stack, tick, Clock
from run_exp_reuse import TARGET, EP2_POS, make_ep
import prior_knowledge as pk
st = make_ep(TARGET, 1, "diagV3", ores=[EP2_POS[0]], noises=[EP2_POS[1]])
print("gaps-after-preopen:", [(g.get('object'), g.get('kind')) for g in pk.open_gaps(st.kg)], flush=True)
clk = Clock()
per = st.emb.perceive()
print("per.blocks:", [b.get('name') for b in per.get('blocks') or []], flush=True)
# 首拍候选
cands = st.loop._action_candidates(per, [], clk.now)
print("n_cands:", len(cands), flush=True)
for c in cands:
    sc, ex = st.loop._score_action(c, per, clk.now)
    print("  cand", c.get('action_type'), str(c.get('target'))[:16], "mot=", c.get('motivation'), "score=", round(sc,3), "reason=", [str(r)[:24] for r in (c.get('reason') or [])][:5], flush=True)
# 目标登记检查
print("goals:", [(g.get('type'), g.get('target'), g.get('source')) for g in st.loop._goals], flush=True)
tick(st, clk)
per2 = st.emb.perceive()
cands2 = st.loop._action_candidates(per2, [], clk.now)
print("tick1 cands:", [(c.get('action_type'), str(c.get('target'))[:12]) for c in cands2][:8], flush=True)
