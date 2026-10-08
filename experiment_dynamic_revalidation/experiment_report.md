# FAS Minecraft Dynamic Environment Revalidation — Experiment Report

**Experiment:** FAS Minecraft Dynamic Environment Revalidation Experiment
**Date:** 2026-09-27, 18:47 – 19:41 local (observation 18:56:50 – 19:38:17)
**Commit:** 4685726 (git main, clean of experiment)
**Minecraft:** 26.1 LAN world via HMCL client (java PID 13736), port **64446**, cheat/op on (verified: `/setblock` ok via bridge `/say`)
**Bridge:** 127.0.0.1:5010 (minecraft_bot/bot.js → minecraft_bridge.py → FAS app.py)
**Python:** E:/Miniforge.envs/Fascinator/python.exe 3.12.13 · Node E:/Nodejs/node.exe
**No-production-modification policy:** honored. Only non-production writes: `minecraft_bot/config.json` (port rewrite, same shape as `minecraft_session.connect()`) and the `experiment_dynamic_revalidation/` artifact directory.

Experiment artifacts: `metadata.json`, `baseline.json`, `events.jsonl`, `metrics.json`, `sys_events.jsonl` (485 filtered events), `states.ndjson` (599 × 4 s bridge polls), `autonomy_snapshots.ndjson` (151 × 15 s candidate snapshots), `causal_table_snapshot.json`, `autonomy_state_snapshot_caseB.json`, `fas_console.log`.

---

## 1. Setup

FAS started fresh for the experiment at 18:55; 5-minute clean baseline 18:56:50–19:01:50. Bot: Haru, creative mode, LAN op. World day 48 (baseline) → day 49 (evening) by end. Baseline verified: **0 LLM calls on the Minecraft decision/action path** (llm.jsonl), bridge latency median 15 ms / p95 22 ms over 60 commands, all candidates below the 0.35 threshold, no goals. Baseline action mix: amble ×3, explore_direction ×1.

Environment control used exclusively through existing interfaces: bridge `/say` → `bot.chat('/setblock …')` (2 test blocks, both air-cleaned, no behavioral contact). All interventions logged as `environment_intervention` in `events.jsonl`. Observer: read-only tail of `logs/` splits + `/state` polling + `data/autonomy.json` diffing; zero writes to production paths.

## 2. Baseline

| | value |
|---|---|
| window | 18:56:50 – 19:01:50 (300 s) |
| actions | 4 (amble ×3, explore_direction(north)) — all success |
| goals | 0 |
| candidate landscape | follow_entity@Hellucigen 0.211 · screen_observe 0.12 · gather_resource@grass_block 0.03 |
| threshold | 0.35 (untouched) |
| LLM calls total / on MC action path | 0 / 0 |
| bridge latency | min 1 · med 15 · p95 22 · max 25 ms (60 commands) |
| perception updates | 14 (≈ every 21 s) |
| graph | 3749 nodes / 9875 edges |

## 3. Case A — stale goal revalidation (unavailable)

Resource-oriented target never formed. Root cause is environmental, not mechanical:

- Open gaps (`prior_knowledge.open_gaps`) = 0 → curiosity goal source silent;
- No user goals this session (single human message answered at 18:55:12 before baseline; afterwards no input of any kind);
- Probe of 8 resource types via `/find_blocks` radius 24 at baseline and again at 19:37 after ~500 blocks of wandering: **iron/coal/copper/gold/diamond/redstone/diamond ore and oak_log all = 0** in perception range;
- Drive/gap candidates that *would* lift an obtain goal never crossed the 0.35 threshold: resources never appeared; `gather_resource@grass_block` peaked at 0.051.

The 5-minute case-A watch (19:05:31–19:10:41) + extended watch to 19:37 produced 0 goals (`goals=[]` across all 151 snapshots; the goal-watcher alerted on zero changes). **Conclusion: unavailable** — the existence condition (a resource goal) never co-occurred; neither *success* nor *failure* of revalidation could be exercised. Note: the one genuine environment perturbation of the session — a dropped item near the bot at 19:37 (user activity) — produced an immediate new candidate `collect_dropped_item 0.201 (resource_opportunity)`; below threshold, no action. New-observation→new-candidate redistribution is observable; the goal lifecycle stage it feeds was not.

## 4. Case B — repeated failure on absent target (unavailable)

During both case windows the only resource-oriented candidates were `gather_resource@grass_block` (0.027–0.051) and later `collect_dropped_item` (0.201). Preconditions:

- `gather_resource` targets grass_block — **present** in the world (its failure condition, a removed/absent target, never arose);
- `collect_dropped_item` fired only at 19:37 with a real dropped item in range (target present).

No candidate whose *target was absent* ever existed, so the repeated-failure loop (attempt → fail → score demotion → redistribution → retry) could not be executed. Repeated-failure count in-session: **0** (33 settled actions, 0 failed). The only in-session retry-like events were two transient night-preemptions of `explore_direction@south` (19:06:10, 19:09:59 — 229 s apart, non-escalating), correctly handled as transient (no `_attempts`, no causal outcome; see Case D).

**Conclusion: unavailable.** Mechanism (recency/backoff/preempt gates at `autonomy._on_action_settled`, §P5 goal demotion, exactly-once settle) present by code and prior test evidence, but its runtime refusal/attempt path was not exercisable.

## 5. Case C — transport-timeout reconciliation (not_observed)

302 bridge commands in-session: **0 failures, 0 with latency ≥ 1000 ms** (max observed 25 ms in baseline; observer kept ok=False or ≥500 ms entries anyway — none occurred). Bridge localhost-to-localhost stability plus quiet LAN directly falsified the intervention condition; timeouts were **not manufactured** (protocol prohibition honored).

Design-level reconciliation chain (code inspection, pre-run tests 103/103):
- `ActionManager._tick` polls `/action_result` with `timeout_s=120` default (action_system.py:388-393); timeout settles as `failed` exactly-once;
- no double-callback path (the exactly-once fix is regression-locked in tests);
- historical runtime record (pre-experiment) shows the chain functioning against a real transport timeout: `craft_item(furnace)` failed `bridge_error:timed out` → settled failure → causal persistence confidence 0.333.

**Conclusion: not observed** (with design + historical verification noted separately).

## 6. Case D — experience/knowledge reuse (partially confirmed, mechanism level)

Evidence chain: failure → belief/causal update → score change, **persisted and live**:

1. **Persistence:** `/api/debug/causal` reports 12 aggregations. Cross-session rule with outcomes: `craft_item(furnace): bridge_error:timed out` → confidence 0.333 (outcomes map non-empty only at 0.333 — this is the ONLY rule with non-empty outcomes: `explore_area(cobblestone) succeeded` 0.333); all others `outcomes={}`.
2. **In-session learning (correctly bounded):** the two night-preemptions aggregated as `explore_direction(south)` obs=1 with **empty outcomes** — transient blockers excluded from causal priors by design (cf. memory: transient-blockers-not-causal). The system recorded "it happened", refrained from treating a transient as a structural cause.
3. **Live score attribution:** candidate explain strings at 19:35–19:38 show active causal priors applying numeric down-weights:
   - `gather_resource@grass_block` → `causal=['Digging aborted'] pb=-0.00`
   - `explore_direction@west` → `causal=['preempted:night','not_connected'] pb=-0.03` (capacity from a prior session)
   - Credible knowledge → candidate scoring → redistribution is visible end-to-end inside the elected candidate set, with scores moved below what they would have been absent the prior.
4. **Not confirmed:** a same-session behavioral loop (failure → stored → next decision measurably different on the *same* task). There was no same-task second attempt: zero failures in-session. `memory_stored_but_behavioral_reuse_not_confirmed` does not apply, but the equivalent caveat does: reuse was demonstrated at the scoring level, not at the execution level.

**Conclusion: partially confirmed (mechanism level).**

## 7. Metrics

Full table in `metrics.json`. Summary: observation/revalidation/goal-invalid/recovery latencies all **N/A** (cases unavailable/not observed → no valid samples); repeated_failure_count **0**; candidate_score_change **observed (mechanism-level)** incl. a live environment→candidate response at 19:37:48; settlement_accuracy **N/A in-session / design-verified**; knowledge_reuse_count **3 rule instances active on the candidate set** (2 distinct rules). LLM accounting: 2 unique calls all session, both `nlp_processor.ask` periodic self-thought (`_llm_thought`), **0 on the Minecraft decision/action/settlement path** — non-regression vs baseline.

## 8. Failure attribution & research conclusion

Attribution legend applied strictly (mechanism vs environment vs bridge vs Minecraft vs measurement):

- **Q1** (environment change → revalidation → stale/demote → redistribute): **not confirmed** — *environment limitation* (goal never formed: zero resource finds, zero gaps, zero user goals), with the pre-goal stage (new observation → new candidate) observed once at 19:37:48.
- **Q2** (transport timeout ≠ world failure → reconciliation): **not observed** — *bridge/environment limitations* (localhost, 0 timeouts; not manufactured). Design + historical record verified separately.
- **Q3** (failure → belief/causal → candidate redistribution): **partially confirmed** at the mechanism level (persisted causal aggregations; live causal priors in candidate explain strings; transient/structural separation honored), **not confirmed** at the same-session behavioral-loop level (no failures to repeat — *measurement limitation*, sample absent).

**Overall verdict: Q1 not confirmed · Q2 not observed (design-verified) · Q3 partially confirmed.** No mechanism failure was encountered; the experiment's blocking factors were environmental (no resource scarcity in perception range, no user interaction, no transport instability) — precisely the conditions under which "revalidation" has no trigger. Suggested next experiments: pre-populated mining site near spawn (goal-forming supply), or transport-injected latency on a dedicated loopback (with explicit protocol approval) to exercise Case C.

Honesty notes: metrics marked N/A are genuinely unmeasured, not smoothed over; the two `setblock` probes were the only interventions and are logged with coordinates and cleanup status; no log, graph, or memory was modified during the run (graph growth +38 nodes / +59 edges is innate drift: replay ingestion of observed scenery, no authoring by the experimenter).

**Post-hoc world-type verification (2026-09-27 20:10):** the LAN world is **superflat**. Evidence: 599 /state samples spanning ~187 x + ~340 z blocks show surface y confined to [-60.0, -58.7] (block-level jitter only); nearbyBlocks footprint was `grass_block×9` on 100% of samples (flat preset = 1 grass + 3 stone layers); zero ore/tree findings and the recurring `furnace_not_found` history are consistent with a flat world without structures. **Attribution refinement:** the "environment limitation" blocking Case A/B is world-type-structural, not incidental luck — a superflat world can never supply the resource goal that the revalidation chain requires. Recommendation stands, now with a concrete lever: use a default-generation world (or a flat preset with villages/trees) plus optionally a pre-placed mining site at spawn.