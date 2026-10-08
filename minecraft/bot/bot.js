// bot.js — Haru 的 Minecraft 协议级实体（Mineflayer）+ 具身动作面
// ============================================================
// HTTP 桥 127.0.0.1:5010：
//   GET  /state                 结构化状态（含玩家相对坐标/时间/天气/水情）
//   POST /say   {text}          游戏内聊天
//   POST /move  {dir, secs}     forward/back/left/right/jump
//   POST /sneak {on}            潜行
//   POST /sprint {on}           疾跑
//   POST /look  {yaw, pitch}    视角
//   POST /look_at {x,y,z}       看向世界坐标
//   POST /follow {player}       跟随玩家（bot 侧低层控制器，接近自动停）
//   POST /stopfollow            停止跟随
//   POST /stop                  停止移动
//   POST /goto {x,y,z} /goto_player {player} /goto_entity {entity}
//   POST /stop_goto             取消寻路
//   POST /dig {block,count}     按名挖附近方块
//   POST /dig_pos {x,y,z}       挖指定坐标方块
//   POST /find_blocks {block,radius,count}  查询附近方块位置
//   POST /place {x,y,z,item}    放置方块
//   POST /craft {item,count,table}          合成
//   POST /smelt {input,fuel,furnace}        熔炼（投料）
//   POST /furnace_take {furnace}            取出熔炼产物
//   POST /eat {item}            进食
//   POST /combat {entity,retreat_health,max_ms}  战斗循环（bot 侧实时控制）
//   POST /stop_combat /flee {distance}
//   POST /collect_item {radius} 捡掉落物
//   POST /interact {x,y,z}      使用方块（门/箱子/工作台/耕地）
//   POST /interact_entity {entity}  与实体交互（喂食/繁殖）
//   POST /equip {item} /equip_off {item} /unequip
//   POST /drop {item,count} /sort_inventory
//   GET  /inventory /inventory_slots
//   POST /sleep_at {x,y,z}      睡觉
//   GET  /action_result         最近动作真实回执
//   POST /quit                  换世界退出
// 断线 10s 自动重连。config.json: host/port/username/auth/version/bridge_port
// ============================================================

const fs = require('fs')
const path = require('path')
const http = require('http')
let config = {}
function loadConfig() {
  try { config = JSON.parse(fs.readFileSync(path.join(__dirname, 'config.json'), 'utf8')) } catch (e) {}
}
loadConfig()

const mineflayer = require('mineflayer')
const { pathfinder, Movements, goals: { GoalNear, GoalBlock, GoalFollow } } = require('mineflayer-pathfinder')
const vec3 = require('vec3')

let bot = null
let chatLog = []
let chatSeq = 0
let connectedAt = null      // 进世界时间（state() 会读它；原来只读不声明，一进世界 /state 就 500）
let lastError = ''
let worldSpawn = null       // 服务器 login 包里的出生点（/tp_spawn 用它；没收到=如实 unknown）
let followTarget = null
let followStart = null
let followTimer = null
// ── 战斗循环状态（bot 侧实时控制；Python 侧只轮询回执与生存兜底）──
let combat = null           // {target, retreatHealth, deadline, lastSwing, timer}
// ── 动作回执 ──
// 每个动作把真实结果记在这里，供 HARU 侧轮询 GET /action_result 取用。
// 为什么需要：/dig 这类动作用 HTTP 提前返回（开挖要几秒），没有回执的话
// 上层只能假设成功——自主行动闭环要求"失败必须真实返回"。
let lastAction = { name: null, status: 'idle', detail: {}, startedAt: null, endedAt: null }

function recordAction(name, status, detail) {
  const now = Date.now()
  if (status === 'running') {
    lastAction = { name, status, detail: detail || {}, startedAt: now, endedAt: null }
  } else {
    lastAction = Object.assign({}, lastAction, {
      name: name || lastAction.name,
      status,
      detail: Object.assign({}, lastAction.detail, detail || {}),
      endedAt: now,
    })
  }
  return lastAction
}

// 兜底：任何一个请求处理里的意外都不该让 bot 进程退出（退出=她整局掉线）
process.on('uncaughtException', (e) => {
  lastError = `uncaught: ${String(e).slice(0, 160)}`
  console.error('[Haru] 未捕获异常（已忽略，进程继续）:', e && e.message ? e.message : e)
})
process.on('unhandledRejection', (e) => {
  lastError = `unhandled: ${String(e).slice(0, 160)}`
  console.error('[Haru] 未处理的 Promise 拒绝（已忽略）:', e && e.message ? e.message : e)
})

// 2026-09-23 断连事故：本版 mineflayer 没有 bot.clearControlStates()——重连清理
// 连抛 3 次异常，每次都把后续清理（pathfinder.setGoal(null) 等）跳掉。
// 手动逐状态解除，并保持旧 API 可用时优先用它。
function clearCtl () {
  if (!bot) return
  try {
    if (typeof bot.clearControlStates === 'function') { bot.clearControlStates(); return }
    for (const s of ['forward', 'back', 'left', 'right', 'jump', 'sprint', 'crouch']) {
      try { bot.setControlState(s, false) } catch (e) {}
    }
  } catch (e) {}
}

// ── 原始步态兜底（2026-09-25 真机定位，2026-09-27 复测修正）────────
// 现象：MC 26.1 下 /goto 交 pathfinder 曾**零位移**（当天全程 gather/explore
// 一次都没挪过：位置从 13:24 到 13:44 恒为 (-15.5,68,163.5)），而
// POST /move {forward,4s} 实测 4 秒走 17 格。当时把"坏的是寻路器"当根因，
// 加了执行层兜底。
// 2026-09-27 真机复测修正：带观测探针拆查后，26.1 的 A* 实测全通——
// 8 次 getPathTo 零失败，最近 120 格跨 chunk 直行到位，三方向往返全到达；
// "26.1 寻路器静默无路"不成立，当日冻结是"无活动目标空闲 + 回执监听泄漏"
// （armPathWatch 修复见下）等一系列表象的误归因。
// 原始步态保留为**安全网**：只用于 A* 明确 noPath（目标在墙内/虚空等真不可达）
// 和看门狗超时的执行层代偿，正常寻路仍交给 pathfinder（绕障碍、搭桥，比直走好）。
// Python 侧契约不变（照样 GET /action_result 拿 running/done/failed），技能层零改动。
let rawGait = null
// 原始步态"如实认输"的时刻（落差不可走/前方液体/连腿都挪不动/超时）。
// 看门狗用它做判据：接管已经发生就说明这条版本的 pathfinder 是死的，
// 重启整进程既治不好 pathfinder，还会把桥端口打断十几秒（上层看到一串
// ECONNREFUSED，背包读数也被清空——2026-09-25 真机的重启循环就是这么来的）。
let __gaitHonestFail = 0

function rawGaitStop () {
  if (rawGait && rawGait.timer) clearInterval(rawGait.timer)
  rawGait = null
  clearCtl()
}

function rawGaitDrive (gx, gz, tag) {
  if (!bot || !bot.entity) return false
  rawGaitStop()
  const st = {
    gx, gz, timer: null,
    deadline: Date.now() + 60000,
    lastPos: bot.entity.position.clone(),
    stuckSince: Date.now(),
    // 绕行偏航角（弧度）与已绕行次数：地形挡住去路时不是站着，是先侧移
    // 几步再朝目标收回来。2026-09-25 真机：她站在 y=73 的台地边缘，
    // 上层每 12 秒重发同一个 goto，每次都"如实停下"→ 一整天挪不动一步。
    offset: 0, turns: 0, lastDist: null,
  }
  st.timer = setInterval(() => {
    try {
      if (!bot || !bot.entity) { rawGaitStop(); return }
      const p = bot.entity.position
      const dx = st.gx - p.x, dz = st.gz - p.z
      if (Math.hypot(dx, dz) <= 2.0) {
        rawGaitStop()
        if (lastAction.name === tag && lastAction.status === 'running') {
          recordAction(tag, 'done', { via: 'raw_gait' })
        }
        console.log(`[Haru] 原始步态到达 ${st.gx},${st.gz}（用时靠 pathfinder 让位后的腿走）`)
        return
      }
      const moved = p.distanceTo(st.lastPos)
      if (moved >= 0.4) st.stuckSince = Date.now()
      st.lastPos = p.clone()
      if (Date.now() > st.deadline) {
        rawGaitStop()
        __gaitHonestFail = Date.now()
        if (lastAction.name === tag && lastAction.status === 'running') {
          recordAction(tag, 'failed', { reason: 'raw_gait_timeout' })
        }
        return
      }
      if (Date.now() - st.stuckSince > 20000) {   // 连原始步态都挪不动=面前有墙
        rawGaitStop()
        __gaitHonestFail = Date.now()
        if (lastAction.name === tag && lastAction.status === 'running') {
          recordAction(tag, 'failed', { reason: 'raw_gait_stalled' })
        }
        return
      }
      // 在水里就先把头抬起来游出去（26.1 真机：兜底腿直走，把 her 带进了一处
      // 积水坑，血量 20→16 反复溺水）。这是执行层的自保，不是决策。
      let underwater = false
      try {
        underwater = !!(bot.isUnderWater && bot.isUnderWater())
      } catch (e) { underwater = false }
      if (underwater) {
        const wy = Math.atan2(-dx, -dz)
        try { bot.look(wy, -0.7, false) } catch (e) {}
        try { bot.setControlState('forward', true) } catch (e) {}
        try { bot.setControlState('jump', true) } catch (e) {}
        return
      }
      // 坠落守卫：看的是**下一步脚下的承重**，不是"前面是不是空气"——
      // 第一版拿脚所在格当参照，站在草丛里时那一格是 short_grass（非空气），
      // 前方空气就判悬崖，每次接管 1 秒内就 self-abort（实测 13:57 连刷
      // 4 次接管而位移恒为 0）。现在：脚下确有支撑、而前方两格深都无支撑
      // 才算悬崖；掉一格（台阶/下坡）正常走。
      const yawGoal = Math.atan2(-dx, -dz)
      const yawWalk = yawGoal + (st.offset || 0)
      const fx = -Math.sin(yawWalk), fz = -Math.cos(yawWalk)
      // 前探看的是**将要迈进去的那一格**，不是固定 1.6 外的点（2026-09-25
      // 真机坑：脚前方一格平正还通身，1.6 却跨过它探到两格外的石墙，判
      // "落差 99 绕不开"——安全格永远轮不到被验证）。按格边界做射线：
      // 探测距离 = 到本格出口边 + 一小步，恰好落在下一个进入的格子里。
      const cellD = (c, v) => (v > 1e-6 ? (Math.floor(c) + 1 - c) / v
                          : v < -1e-6 ? (c - Math.floor(c)) / -v
                          : Infinity)
      const probeD = Math.min(cellD(p.x, fx), cellD(p.z, fz), 1.5) + 0.05
      const ax = Math.floor(p.x + fx * probeD), az = Math.floor(p.z + fz * probeD)
      const fy = Math.floor(p.y)
      const isSolid = (x, y, z) => {
        let b = null
        try { b = bot.blockAt(vec3(x, y, z)) } catch (e) { return true }
        if (!b) return true                       // 区块未加载：看不见=当地有
        const nm = String(b.name || 'air')
        return nm !== 'air' && nm !== 'water' && nm !== 'lava'
      }
      // 绕行的代价是"晚一点到"，不是"到不了"：只要离目标更近了就把偏航角
      // 收回来一半，几步后自然重新对准目标（避免绕着障碍转圈）。
      const distNow = Math.hypot(dx, dz)
      if (st.lastDist !== null && distNow <= st.lastDist - 0.3) {
        st.offset = Math.abs(st.offset) < 0.12 ? 0 : st.offset * 0.5
      }
      if (st.lastDist !== null && distNow <= st.lastDist - 1.5) {
        st.turns = 0        // 真的走近了一段：扇形从头开始，不必一直大偏航
      }
      st.lastDist = distNow
      // 挡路时的扇形搜索：朝目标左右交替放大偏航（±45°、±90°、±135°），
      // 总有一档是通的。前一版只会朝一个方向累计转三次，2026-09-25 真机
      // 现场 = 北边空气、三面石墙，而她三次全转向石墙那侧 → 白绕。
      const OFFSET_LADDER = [45, -45, 90, -90, 135, -135]
      const detour = (why) => {
        if (st.turns >= OFFSET_LADDER.length) return false
        st.turns++
        st.offset = OFFSET_LADDER[st.turns - 1] * Math.PI / 180
        // 真横移（2026-09-26 真机：只转 yaw 朝斜向走 1 秒，在柱子+人挡路的
        // 组合下推不出位移，20 秒卡死循环）。绕行拍先向一侧横移 0.7s
        // （left/right 交替），把身体挪出障碍格再朝目标收敛。
        try {
          bot.setControlState('forward', false)
          bot.setControlState('sprint', false)
          const side = st.turns % 2 ? 'left' : 'right'
          bot.setControlState(side, true)
          setTimeout(() => { try { if (bot) bot.setControlState(side, false) } catch (e) {} }, 700)
        } catch (e) {}
        console.log(`[Haru] 原始步态：${why} → 侧移绕行（第 ${st.turns} 次，偏航 ${OFFSET_LADDER[st.turns - 1]}°）`)
        return true
      }
      const nameAt = (x, y, z) => {
        let b = null
        try { b = bot.blockAt(vec3(x, y, z)) } catch (e) { return '' }
        return b ? String(b.name || '') : ''
      }
      // 树叶/花草/树苗这类"挡不住身体"的东西：穿过它们不算撞墙。第一版把
      // 它们当实心，于是站在树边上"落点上方不通"→ 每次都绕→绕不开→认输
      // （2026-09-25 真机在 25,121 树边实测到这条误判，位置原地打转）。
      const passable = (x, y, z) => {
        const n = nameAt(x, y, z)
        return n === '' || n === 'air' ||
          /(_leaves|leaves$|sapling|_grass$|fern|flower|vine|snow$|carpet$|water|lava)/.test(n)
      }
      try {
        const frontName = nameAt(ax, fy, az)
        if (frontName === 'water' || frontName === 'lava') {
          // 不涉水（她此刻不在水里时）：液体当障碍，先绕，绕不开如实失败
          if (detour(`前方 ${frontName}`)) return
          rawGaitStop()
          __gaitHonestFail = Date.now()
          if (lastAction.name === tag && lastAction.status === 'running') {
            recordAction(tag, 'failed', { reason: 'raw_gait_liquid' })
          }
          console.log('[Haru] 原始步态：绕不开液体，如实返回')
          return
        }
      } catch (e) { /* 观察者不添乱 */ }
      try {
        const supportHere = isSolid(Math.floor(p.x), fy - 1, Math.floor(p.z))
        // "那一列我站不站得下"：从脚同高往下找一层承重，且它上面两格过身。
        // 脚同高那格也算 —— 那是**上一级台阶**（Minecraft 里 1 格高的坎直接
        // 跳上去就行），前一版把"脚下这一格是实心"当成墙，于是四面有 1 格
        // 坎的坑沿上她每个方向都判"不可走"，绕三次全退回原地（2026-09-25
        // 真机 25,121 实测）。落差 >3 格仍然拦（摔落伤害），液体另判。
        // 承重搜索同步放宽到 5 格深（原 4）：落差上限 4 需要能看见 fy-5 的
        // 地面（drop = (fy-1)-base，base=fy-5 ⇒ 落 4 格，至多半颗心）。
        // 落地格上方两格过身照旧在候选判定里保证（passable y+1/y+2）。
        let base = null
        for (const y of [fy, fy - 1, fy - 2, fy - 3, fy - 4, fy - 5]) {
          if (isSolid(ax, y, az) && passable(ax, y + 1, az) &&
              passable(ax, y + 2, az)) { base = y; break }
        }
        const drop = base === null ? 99 : (fy - 1) - base
        // 落差上限 4（原 3）：4 格落地至多半颗心（fall 4 = 1 伤害），且落地格
        // 上方两格过身已在 base 搜索里保证（passable y+1/y+2）。2026-09-26
        // 真机：出生点立在崖沿，前方全是 4 格落差，3 格上限让垫底腿每个方向
        // 都判"绕不开"，整晚原地快败循环下不了地。
        if (supportHere && (base === null || drop > 4)) {
          if (detour(`前方 ${ax},${az} 落差 ${drop} 格`)) return
          rawGaitStop()
          __gaitHonestFail = Date.now()
          if (lastAction.name === tag && lastAction.status === 'running') {
            recordAction(tag, 'failed', { reason: 'raw_gait_edge' })
          }
          console.log(`[Haru] 原始步态：绕不开落差 ${drop} 格，如实停下` +
            // 绕不开时把身周一圈的实际方块打出来（不猜：2026-09-25 调试这场
            // "站着不动"花了一小时，缺的就是这份现场证据）
            ' 现场=' + ['n', 'e', 's', 'w'].map((d, i) => {
              const dd = [[0, 1], [1, 0], [0, -1], [-1, 0]][i]
              const nx = Math.floor(p.x) + dd[0], nz = Math.floor(p.z) + dd[1]
              return `${d}:${nameAt(nx, fy, nz) || '-'}|${nameAt(nx, fy + 1, nz) || '-'}`
            }).join(' '))
          return
        }
      } catch (e) { /* 观察者不添乱 */ }
      // 方块探测说"能走"却连续 4 拍零位移：看不见的障碍（玩家/生物挡路——
      // 实体碰撞不进方块探测，2026-09-26 真机：顶着用户推 20 秒卡死循环，
      // 绕行逻辑从不触发，因为探出来的全是空气）。强制进绕行扇形。
      st.noProg = (moved >= 0.4) ? 0 : (st.noProg || 0) + 1
      if (st.noProg === 4 && st.turns < OFFSET_LADDER.length) {
        detour('连续零位移（疑似实体挡路）')
      }
      const yaw = yawWalk
      try { bot.look(yaw, -0.1, false) } catch (e) {}
      try { bot.setControlState('sprint', false) } catch (e) {}
      bot.setControlState('forward', true)
      try { bot.setControlState('jump', true) } catch (e) {}
      setTimeout(() => { try { if (bot) bot.setControlState('jump', false) } catch (e) {} }, 400)
    } catch (e) { /* 观察者不添乱 */ }
  }, 1000)
  rawGait = st
  return true
}

// 监视一次 /goto：pathfinder 若 12 秒零位移就接管（真在走则退出，不插手）。
// 2026-09-26 深夜真机：26.1 的 pathfinder 还会**背道而驰**——一直动、离目标
// 越来越远（bot 沿 x=-11 走出 2100 格）。所以判据不止"动没动"：连续 3 拍
// 离目标更远也要剥夺，换原始步态（它的朝向数学是独立验证过的）。
function armGotoFallback (gx, gz) {
  if (!bot || !bot.entity) return
  const t0 = Date.now(), p0 = bot.entity.position.clone()
  let lastGd = null, wrong = 0
  const gd = () => Math.hypot(gx - bot.entity.position.x,
                              gz - bot.entity.position.z)
  const iv = setInterval(() => {
    try {
      if (!bot || !bot.entity) { clearInterval(iv); return }
      if (lastAction.name !== 'goto' || lastAction.status !== 'running') {
        clearInterval(iv); return          // 已结算/被别的动作顶掉
      }
      if (rawGait) { clearInterval(iv); return }   // noPath 已经叫腿走了
      if (Date.now() - t0 < 12000) return
      const moved = bot.entity.position.distanceTo(p0)
      if (moved >= 0.5) {
        const dNow = gd()
        if (lastGd !== null && dNow > lastGd + 1.0) wrong++
        else wrong = 0
        lastGd = dNow
        if (wrong < 3) { p0.copy(bot.entity.position); return }   // 在接近
      }
      clearInterval(iv)
      try { bot.pathfinder.stop() } catch (e) {}
      // 看门狗别再因为"静态目标零位移"杀进程——现在换腿走了
      try { __goalStatic = false } catch (e) {}
      console.log(`[Haru] pathfinder 12s 零位移/背道而驰 → 原始步态接管 goto (${gx},${gz})`)
      rawGaitDrive(gx, gz, 'goto')
    } catch (e) { clearInterval(iv) }
  }, 3000)
}

// ── 寻路回执监听管理（2026-09-27 真机复测修复）────────────────────
// 此前各动作 handler 直接 bot.once('goal_reached'/'path_update') 且从不清理：
// once 只在"真的发生"时自清——被后续动作顶掉/中途停掉的 goto 会永久泄漏。
// 实测 5 次 goto 后残留 6 个 goal_reached + 3 个 path_update 监听（历史上堆到
// 11+ 触发 MaxListenersExceededWarning）；泄漏到一定程度，旧动作的迟到回执
// 还有误结算新动作的风险。统一走这里：新动作先 disarm（旧监听立刻拔掉），
// 正常到账的 once 自清，泄漏被夹在"下一次动作开始"这个界内。
let __pathWatches = []   // [{event, fn}]
function armPathWatch (event, fn) {
  bot.once(event, fn)
  __pathWatches.push({ event, fn })
  return fn
}
function disarmPathWatches () {
  for (const w of __pathWatches) {
    try { bot.removeListener(w.event, w.fn) } catch (e) {}
  }
  __pathWatches = []
}

function createBot() {
  loadConfig()   // FAS 写入新端口后，重连循环自动拿到新配置
  const opts = {
    host: config.host || '127.0.0.1',
    port: config.port || 25565,
    username: config.username || 'Haru',
    auth: config.auth || 'offline',
    hideErrors: true,
  }
  if (config.version) opts.version = config.version
  console.log(`[Haru] 连接 ${opts.host}:${opts.port} as ${opts.username} (version=${opts.version || 'auto'})`)
  bot = mineflayer.createBot(opts)

  bot.loadPlugin(pathfinder)
  bot.once('login', () => {
    lastError = ''
    connectedAt = new Date().toISOString()
    console.log(`[Haru] 已进入世界 version=${bot.version} spawn=${JSON.stringify(bot.entity && bot.entity.position)}`)
  })
  // 出生点以服务器 login 包为准（不同版本字段名不同，逐个探测；查不到就
  // 如实 spawn_unknown，绝不拿"我猜的坐标"顶替）
  bot._client.on('login', (p) => {
    try {
      const w = p.world_spawn_position ||
        (p.world_spawn_x !== undefined ? { x: p.world_spawn_x, z: p.world_spawn_z } : null) ||
        (p.spawn_x !== undefined ? { x: p.spawn_x, z: p.spawn_z } : null)
      if (w && Number.isFinite(w.x) && Number.isFinite(w.z)) {
        worldSpawn = { x: w.x, z: w.z }
      }
    } catch (e) { /* 解析失败=不知道出生点 */ }
  })
  bot.on('chat', (username, message) => {
    if (username === bot.username) return
    // seq：单调递增序号——上层据此去重（时间戳同秒会撞、文本可能重复）
    chatLog.push({ seq: ++chatSeq, sender: username, text: String(message).slice(0, 200),
                   time: new Date().toISOString() })
    if (chatLog.length > 10) chatLog = chatLog.slice(-10)
  })
  bot.on('error', (err) => { lastError = String(err && err.message || err) })
  bot.on('kicked', (reason) => { lastError = 'kicked: ' + String(reason).slice(0, 120) })
  bot.on('end', () => {
    connectedAt = null
    followTarget = null
    stopCombat()
    if (followTimer) { clearInterval(followTimer); followTimer = null }
    console.log(`[Haru] 连接断开 (${lastError || 'unknown'})，10 秒后重连`)
    setTimeout(createBot, 10000)
  })
}

function stopFollow() {
  followTarget = null
  if (followTimer) { clearInterval(followTimer); followTimer = null }
  if (bot) {
    clearCtl()
    // 动态 goal 必须显式解除，否则 pathfinder 会按自己的 goal 继续追
    try { bot.pathfinder.setGoal(null) } catch (e) {}
  }
  // 不在这里清 followStart：/stopfollow 要用它算跟随时长，由调用方 reset
}

function startFollow(playerName) {
  followTarget = playerName
  if (!followStart) followStart = Date.now()   // 记开始时间，否则 followed_seconds 恒 0
  if (followTimer) clearInterval(followTimer)
  // 移动交给 pathfinder（2026-09-21 修复）：旧版是手搓 900ms lookAt+forward
  // 定时环——只写前进/疾跑、从不按跳跃键，1 格台阶都上不去（"跟随不自动跳"根因）。
  // pathfinder 的 physicTick 会做物理模拟（canWalkJump/canSprintJump），
  // 自动跳跃、绕行、坑沿停步全部归它管；本循环只做"人在不在、够不够近"。
  followTimer = setInterval(() => {
    if (!bot || !bot.entity) return
    const pl = bot.players[followTarget]
    if (!pl || !pl.entity) {
      try { bot.pathfinder.setGoal(null) } catch (e) {}
      clearCtl(); return
    }
    const d = pl.entity.position.distanceTo(bot.entity.position)
    if (d <= 2.5) {   // 距离足够 → 停，面朝对方
      try { bot.pathfinder.setGoal(null) } catch (e) {}
      clearCtl()
      bot.lookAt(pl.entity.position.offset(0, pl.entity.height, 0)).catch(() => {})
      return
    }
    try {
      bot.pathfinder.setGoal(new GoalFollow(pl.entity, 2.5), true)
    } catch (e) { lastError = 'follow: ' + String(e.message || e).slice(0, 100) }
  }, 900)
}

// ── 战斗循环（bot 侧实时控制：接近/挥剑/冷却/残血撤退）──────────
function startCombat(entityName, retreatHealth, maxMs) {
  stopCombat()
  combat = { target: entityName, retreatHealth: retreatHealth || 8,
             deadline: Date.now() + (maxMs || 45000), lastSwing: 0, timer: null }
  recordAction('combat', 'running', { entity: entityName, hits: 0 })
  combat.timer = setInterval(() => {
    if (!bot || !bot.entity) return
    if (Date.now() > combat.deadline) {
      recordAction('combat', 'done', { killed: false, reason: 'timeout' })
      stopCombat(); return
    }
    if (bot.health <= combat.retreatHealth) {
      const targetName = combat.target
      recordAction('combat', 'done', { killed: false, reason: 'retreat_low_health' })
      stopCombat()
      // 紧急脱离：朝远离目标的抖动方向疾跑几步（Python 侧会接手正式撤退）
      clearCtl()
      bot.setControlState('sprint', true)
      bot.setControlState('forward', true)
      setTimeout(() => { if (bot) { bot.setControlState('forward', false); bot.setControlState('sprint', false) } }, 1200)
      return
    }
    const target = Object.values(bot.entities).find(
      e => e !== bot.entity && (e.name || '') === combat.target)
    if (!target || !target.position) {
      recordAction('combat', 'done', { killed: true, reason: 'target_gone' })
      stopCombat(); return
    }
    const d = target.position.distanceTo(bot.entity.position)
    // 目标死亡判定：health 元数据存在且 <= 0
    if (target.health !== undefined && target.health <= 0) {
      recordAction('combat', 'done', { killed: true, reason: 'target_dead' })
      stopCombat(); return
    }
    // 举盾防御姿态（shield 在副手时）：对苦力怕/骷髅保持 3.5 格不贴脸
    let offhandShield = false
    try {
      offhandShield = bot.inventory && bot.inventory.selectedWindow !== undefined
        && (bot.inventory.items().find(i => i.name === 'shield') !== undefined)
        && (bot.heldItem === undefined || true)
    } catch (e) {}
    const rangedOrBoom = ['creeper', 'skeleton', 'stray', 'drowned'].includes(combat.target)
    if (offhandShield && rangedOrBoom && d > 3.5) {
      bot.pathfinder.setGoal(new GoalFollow(target, 3.5), true)
      return
    }
    if (d > 3.0) {
      // 接近：pathfinder 追击
      bot.pathfinder.setGoal(new GoalFollow(target, 2), true)
      return
    }
    clearCtl()
    bot.lookAt(target.position.offset(0, target.height || 1, 0), true)
      .then(() => {
        if (!combat) return
        const now = Date.now()
        if (now - combat.lastSwing >= 550) {
          combat.lastSwing = now
          if (lastAction.name === 'combat') lastAction.detail.hits = (lastAction.detail.hits || 0) + 1
          bot.attack(target)
        }
      })
      .catch(() => {})
  }, 120)
}

function stopCombat() {
  if (combat && combat.timer) clearInterval(combat.timer)
  combat = null
  if (bot) {
    try { bot.pathfinder.setGoal(null) } catch (e) {}
    try { bot.pathfinder.stop() } catch (e) {}
    clearCtl()
  }
}

// ── 小工具 ─────────────────────────────────────────────
function findEntityByName(name) {
  if (!bot) return null
  const want = String(name || '').toLowerCase()
  return Object.values(bot.entities).find(e => e !== bot.entity &&
    ((e.name || '').toLowerCase() === want || (e.displayName || '').toLowerCase() === want))
}

function findBlockByName(name) {
  if (!bot) return null
  const mcData = require('minecraft-data')(bot.version)
  const blk = mcData.blocksByName[String(name || '').toLowerCase()]
  return blk || null
}

function findItemByName(name) {
  if (!bot) return null
  const mcData = require('minecraft-data')(bot.version)
  return mcData.itemsByName[String(name || '').toLowerCase()]
    || mcData.blocksByName[String(name || '').toLowerCase()] || null
}

// 物品 id → 名（配方 delta 用 id 记账）
function mcNameOf(id) {
  try {
    const mcData = require('minecraft-data')(bot.version)
    const it = mcData.itemsArray.find(i => i.id === id) || mcData.blocksArray.find(b => b.id === id)
    return it ? it.name : null
  } catch (e) { return null }
}

function state() {
  if (!bot || !bot.entity) return { connected: false, error: lastError, follow: followTarget, chat: chatLog.slice(-10) }
  const p = bot.entity.position
  // 采样脚边周围的方块名（探索未知方块用；±2×2 高度）
  const blockNames = {}
  for (let dx = -2; dx <= 2; dx += 2)
    for (let dy = -1; dy <= 2; dy += 1)
      for (let dz = -2; dz <= 2; dz += 2) {
        try {
          const b = bot.blockAt(bot.entity.position.offset(dx, dy, dz))
          if (b && b.name && b.name !== 'air') blockNames[b.name] = (blockNames[b.name] || 0) + 1
        } catch (e) { /* 卸载区块忽略 */ }
      }
  const nearbyBlocks = Object.entries(blockNames)
    .map(([name, count]) => ({ name, count }))
    .sort((a, b) => b.count - a.count).slice(0, 6)
  // 附近生物/掉落物（排除玩家与自身）
  const nearbyEntities = Object.values(bot.entities)
    .filter(e => e !== bot.entity && e.name && e.name !== 'player' && e.position)
    .map(e => {
      const p = bot.entity.position
      return {
        name: e.name,
        displayName: e.displayName || e.name,
        dist: Math.round(e.position.distanceTo(p) * 10) / 10,
        // 相对坐标：自主行动要"朝它看/靠近它"就必须有方向（原先只有距离）
        rel: {
          dx: Math.round((e.position.x - p.x) * 10) / 10,
          dy: Math.round((e.position.y - p.y) * 10) / 10,
          dz: Math.round((e.position.z - p.z) * 10) / 10,
        },
      }
    })
    .sort((a, b) => a.dist - b.dist).slice(0, 5)
  const near = Object.values(bot.players)
    .filter(pl => pl.username !== bot.username && pl.entity)
    .map(pl => ({
      name: pl.username,
      dist: Math.round(pl.entity.position.distanceTo(bot.entity.position) * 10) / 10,
      rel: {
        dx: Math.round((pl.entity.position.x - p.x) * 10) / 10,
        dy: Math.round((pl.entity.position.y - p.y) * 10) / 10,
        dz: Math.round((pl.entity.position.z - p.z) * 10) / 10,
      },
    }))
  return {
    connected: true,
    username: bot.username,
    position: { x: Math.round(p.x * 10) / 10, y: Math.round(p.y * 10) / 10, z: Math.round(p.z * 10) / 10 },
    health: bot.health, food: bot.food,
    gameMode: bot.game ? bot.game.gameMode : undefined,
    playersNearby: near,
    heldItem: bot.heldItem ? bot.heldItem.name : null,
    nearbyBlocks,
    nearbyEntities,
    follow: followTarget,
    chat: chatLog.slice(-10),
    connectedAt: connectedAt,
    // 世界/自身环境事实（感知技能直接读，零 LLM）
    timeOfDay: bot.time ? bot.time.timeOfDay : undefined,
    day: bot.time ? bot.time.day : undefined,
    isRaining: bot.isRaining ? true : false,
    thunder: bot.thunderState ? true : false,
    inWater: typeof bot.entity.isInWater === 'boolean' ? bot.entity.isInWater : undefined,
    onFire: typeof bot.entity.isOnFire === 'boolean' ? bot.entity.isOnFire : undefined,
    fallSpeed: bot.entity.velocity ? Math.round(bot.entity.velocity.y * 100) / 100 : undefined,
    combat: combat ? { entity: combat.target } : null,
  }
}

// 统一响应：同一请求只允许回一次。异步动作（lookAt/goto/equip）在 promise 里
// 回包时，若客户端已超时断开或同请求被回了两次，旧写法会抛 ERR_HTTP_HEADERS_SENT
// 直接把 bot 进程打死——一局游戏就没了。这里全部走 send()，重复即忽略。
function send(res, payload, code) {
  if (!res || res.headersSent || res.writableEnded) return false
  try {
    res.writeHead(code || 200, { 'Content-Type': 'application/json' })
    res.end(typeof payload === 'string' ? payload : JSON.stringify(payload))
    return true
  } catch (e) { lastError = String(e).slice(0, 120); return false }
}

const server = http.createServer((req, res) => {
  let body = ''
  req.on('data', c => { body += c })
  req.on('end', async () => {
    try {
      const json = () => { send(res, { ok: true }) }
      if (req.url === '/state' && req.method === 'GET') {
        send(res, state()); return
      }
      if (req.url === '/health') { send(res, 'ok'); return }
      if (req.url === '/quit' && req.method === 'POST') {
        // 换世界专用：旧进程只读启动时 config，必须退出才能让新端口生效
        send(res, 'quitting')
        setTimeout(() => process.exit(0), 200)
        return
      }
      if (req.url === '/say' && req.method === 'POST') {
        const d = JSON.parse(body || '{}')
        if (bot) bot.chat(String(d.text || '').slice(0, 240))
        json(); return
      }
      if (req.url === '/move' && req.method === 'POST') {
        const d = JSON.parse(body || '{}')
        if (bot) {
          clearCtl()
          const map = { forward: 'forward', back: 'back', left: 'left', right: 'right', jump: 'jump' }
          if (map[d.dir]) bot.setControlState(map[d.dir], true)
          if (d.secs) setTimeout(() => { if (bot) clearCtl() }, Math.min(10000, (d.secs || 1) * 1000))
        }
        json(); return
      }
      if (req.url === '/sneak' && req.method === 'POST') {
        const d = JSON.parse(body || '{}')
        if (bot) bot.setControlState('sneak', !!d.on)
        json(); return
      }
      if (req.url === '/sprint' && req.method === 'POST') {
        const d = JSON.parse(body || '{}')
        if (bot) bot.setControlState('sprint', !!d.on)
        json(); return
      }
      if (req.url === '/look' && req.method === 'POST') {
        const d = JSON.parse(body || '{}')
        if (bot) bot.look(d.yaw || 0, d.pitch || 0)
        json(); return
      }
      if (req.url === '/look_at' && req.method === 'POST') {
        const d = JSON.parse(body || '{}')
        const x = Number(d.x), y = Number(d.y), z = Number(d.z)
        if (!bot || !bot.entity) { send(res, { ok: false, reason: 'not_connected' }); return }
        if (!Number.isFinite(x) || !Number.isFinite(y) || !Number.isFinite(z)) {
          send(res, { ok: false, reason: 'invalid_coords' }); return
        }
        // 用 bot.lookAt：yaw/pitch 的换算交给 mineflayer，避免上层重实现坐标约定
        bot.lookAt(new (require('vec3'))(x, y, z), true)
          .then(() => { recordAction('look_at', 'done', { x, y, z })
            send(res, { ok: true }) })
          .catch(e => { recordAction('look_at', 'failed', { error: String(e).slice(0, 120) })
            send(res, { ok: false, reason: 'look_failed' }) })
        return
      }
      if (req.url === '/follow' && req.method === 'POST') {
        const d = JSON.parse(body || '{}')
        startFollow(String(d.player || '').slice(0, 32))
        json(); return
      }
      if (req.url === '/dig' && req.method === 'POST') {
        const d = JSON.parse(body || '{}')
        const mcData = require('minecraft-data')(bot.version)
        const blk = mcData.blocksByName[String(d.block || '').toLowerCase()]
        if (!blk) { send(res, { ok: false, reason: 'unknown_block' }); return }
        const targets = bot.findBlocks({ matching: blk.id, maxDistance: 12, count: (d.count || 1) })
        if (!targets.length) { send(res, { ok: false, reason: 'block_not_found' }); return }
        // 逐块挖并写动作回执：HTTP 提前返回（开挖耗时数秒），上层轮询
        // /action_result 取真实结果（done / partial / failed + 逐块明细）
        recordAction('dig', 'running', { block: String(d.block || ''), requested: targets.length, dug: 0, results: [] })
        ;(async () => {
          for (const pos of targets) {
            try {
              const b = bot.blockAt(pos)
              await bot.dig(b)
              lastAction.detail.results.push({ pos: { x: pos.x, y: pos.y, z: pos.z }, dug: b.name })
              lastAction.detail.dug = (lastAction.detail.dug || 0) + 1
            } catch (e) {
              lastAction.detail.results.push({ error: String(e).slice(0, 80) })
            }
          }
          const dug = lastAction.detail.dug || 0
          recordAction('dig', dug > 0 ? (dug === targets.length ? 'done' : 'partial') : 'failed',
            { dug, requested: targets.length })
        })()
        send(res, { ok: true, digging: targets.length })
        return
      }
      if (req.url === '/goto_player' && req.method === 'POST') {
        const d = JSON.parse(body || '{}')
        const pl = bot.players[String(d.player || '')]
        if (!pl || !pl.entity) { send(res, { ok: false, reason: 'player_not_found' }); return }
        const goal = new GoalNear(pl.entity.position.x, pl.entity.position.y, pl.entity.position.z, 2)
        recordAction('goto_player', 'running', { player: String(d.player || '') })
        disarmPathWatches()
        bot.pathfinder.setGoal(goal, true)
        // pathfinder 的到达/失败由 bot 事件回调，写进同一条回执
        const onGoalReached = () => { if (lastAction.name === 'goto_player' && lastAction.status === 'running') recordAction('goto_player', 'done', {}) }
        const onPathFail = (reason) => { if (lastAction.name === 'goto_player' && lastAction.status === 'running') recordAction('goto_player', 'failed', { reason: String(reason || 'path_failed') }) }
        armPathWatch('goal_reached', onGoalReached)
        armPathWatch('path_update', (r) => { if (r && r.status === 'noPath') onPathFail('noPath') })
        send(res, { ok: true, goto: d.player })
        return
      }
      if (req.url === '/goto' && req.method === 'POST') {
        const d = JSON.parse(body || '{}')
        const gx = Number(d.x), gy = Number(d.y), gz = Number(d.z)
        if (!Number.isFinite(gx) || !Number.isFinite(gy) || !Number.isFinite(gz)) {
          send(res, { ok: false, reason: 'invalid_coords' }); return
        }
        const goal = new GoalNear(gx, gy, gz, 1.5)
        recordAction('goto', 'running', { x: gx, y: gy, z: gz })
        disarmPathWatches()
        bot.pathfinder.setGoal(goal, true)
        armPathWatch('goal_reached', () => { if (lastAction.name === 'goto' && lastAction.status === 'running') recordAction('goto', 'done', {}) })
        armPathWatch('path_update', (r) => {
          if (r && r.status === 'noPath' && lastAction.name === 'goto' && lastAction.status === 'running') {
            // 真不可达兜底（2026-09-27 复测修正：26.1 的 A* 实测 8/8 全走通，
            // "秒回 noPath"是误归因）。此分支保留语义：A* 明确 noPath（目标在
            // 墙内/虚空等）才交给腿试一次；腿也走不动才如实 failed。上层契约
            // 与失败语义不变。
            if (!rawGait && !rawGaitDrive(gx, gz, 'goto')) {
              recordAction('goto', 'failed', { reason: 'noPath' })
            }
          }
        })
        armGotoFallback(gx, gz)
        send(res, { ok: true })
        return
      }
      if (req.url === '/equip' && req.method === 'POST') {
        const d = JSON.parse(body || '{}')
        const mcData = require('minecraft-data')(bot.version)
        const item = mcData.itemsByName[String(d.item || '').toLowerCase()]
        if (!item) { send(res, { ok: false, reason: 'unknown_item' }); return }
        const found = bot.inventory.items().find(i => i.type === item.id)
        if (!found) { send(res, { ok: false, reason: 'item_not_in_inventory' }); return }
        bot.equip(found, 'hand').then(() => { send(res, { ok: true }) })
          .catch(e => { send(res, { ok: false, error: String(e).slice(0, 80) }) })
        return
      }
      if (req.url === '/attack' && req.method === 'POST') {
        const d = JSON.parse(body || '{}')
        const want = String(d.entity || '').toLowerCase()
        const ent = Object.values(bot.entities)
          .filter(e => e !== bot.entity && e.name !== 'player')
          .sort((a, b) => a.position.distanceTo(bot.entity.position) - b.position.distanceTo(bot.entity.position))
          .find(e => !want || (e.name || '').includes(want))
        if (!ent) { send(res, { ok: false, reason: 'no_target' }); return }
        bot.attack(ent)
        send(res, { ok: true, attacked: ent.name })
        return
      }
      if (req.url === '/inventory' && req.method === 'GET') {
        const items = bot.inventory.items().map(i => {
          const o = { name: i.name, count: i.count }
          // 工具耐久上报（技能层据此排除快断的工具）
          try {
            if (i.maxDurability) {
              const rem = (typeof i.durability === 'number')
                ? i.durability
                : (i.maxDurability - (i.damage || 0))
              o.dur_percent = Math.max(0, Math.round(rem / i.maxDurability * 100))
            }
          } catch (e) {}
          return o
        })
        send(res, { ok: true, items })
        return
      }
      if (req.url === '/chest_store' && req.method === 'POST') {
        // 把背包内容存进附近箱子（keep 列表默认保住工具/武器/食物）
        const d = JSON.parse(body || '{}')
        if (!bot || !bot.entity) { send(res, { ok: false, reason: 'not_connected' }); return }
        const mcData = require('minecraft-data')(bot.version)
        const chestId = (mcData.blocksByName.chest || {}).id
        if (!chestId) { send(res, { ok: false, reason: 'no_chest_def' }); return }
        const pos = bot.findBlocks({ matching: chestId, maxDistance: 8, count: 1 })[0]
        if (!pos) { send(res, { ok: false, reason: 'chest_not_found' }); return }
        let b = null
        try { b = bot.blockAt(pos) } catch (e) {}
        if (!b) { send(res, { ok: false, reason: 'chest_not_found' }); return }
        const keep = new Set((Array.isArray(d.keep) && d.keep.length) ? d.keep : [
          'iron_pickaxe', 'stone_pickaxe', 'wooden_pickaxe', 'diamond_pickaxe',
          'iron_axe', 'stone_axe', 'wooden_axe', 'diamond_axe',
          'iron_sword', 'stone_sword', 'wooden_sword', 'diamond_sword', 'shield',
          'bread', 'cooked_beef', 'cooked_porkchop', 'cooked_mutton', 'cooked_chicken',
          'apple', 'carrot', 'potato', 'baked_potato', 'torch'])
        recordAction('chest_store', 'running', {})
        ;(async () => {
          try {
            const chest = await bot.openChest(b)
            let moved = 0
            for (const it of bot.inventory.items().slice()) {
              if (keep.has(it.name) && !d.all) continue
              try { await chest.deposit(it.type, null, it.count); moved += it.count } catch (e) {}
            }
            chest.close()
            recordAction('chest_store', 'done', { moved })
            send(res, { ok: true, moved })
          } catch (e) {
            recordAction('chest_store', 'failed', { reason: String(e).slice(0, 80) })
            send(res, { ok: false, reason: 'chest_failed' })
          }
        })()
        return
      }
      if (req.url === '/chest_take' && req.method === 'POST') {
        const d = JSON.parse(body || '{}')
        if (!bot) { send(res, { ok: false, reason: 'not_connected' }); return }
        const mcData = require('minecraft-data')(bot.version)
        const chestId = (mcData.blocksByName.chest || {}).id
        const item = mcData.itemsByName[String(d.item || '').toLowerCase()]
        if (!chestId || !item) { send(res, { ok: false, reason: 'unknown_item' }); return }
        const pos = bot.findBlocks({ matching: chestId, maxDistance: 8, count: 1 })[0]
        if (!pos) { send(res, { ok: false, reason: 'chest_not_found' }); return }
        let b = null
        try { b = bot.blockAt(pos) } catch (e) {}
        ;(async () => {
          try {
            const chest = await bot.openChest(b)
            await chest.withdraw(item.id, null, Math.max(1, Number(d.count) || 1))
            chest.close()
            send(res, { ok: true })
          } catch (e) { send(res, { ok: false, reason: 'take_failed' }) }
        })()
        return
      }
      if (req.url === '/inventory_slots' && req.method === 'GET') {
        if (!bot) { send(res, { ok: false, reason: 'not_connected' }); return }
        const slots = bot.inventory.slots ? bot.inventory.slots.length : 46
        const used = bot.inventory.items().reduce((a, i) => a + 1, 0)
        // 主背包 36 格（9 热键 + 27 主格）
        send(res, { ok: true, free: Math.max(0, 36 - used), used })
        return
      }
      if (req.url === '/find_blocks' && req.method === 'POST') {
        const d = JSON.parse(body || '{}')
        if (!bot || !bot.entity) { send(res, { ok: false, reason: 'not_connected' }); return }
        const blk = findBlockByName(d.block)
        if (!blk) { send(res, { ok: false, reason: 'unknown_block' }); return }
        const radius = Math.min(64, Math.max(1, Number(d.radius) || 12))
        const count = Math.min(64, Math.max(1, Number(d.count) || 8))
        let positions = []
        try {
          positions = bot.findBlocks({ matching: blk.id, maxDistance: radius, count })
            .map(p => ({ x: p.x, y: p.y, z: p.z }))
        } catch (e) { send(res, { ok: false, reason: 'chunks_unloaded' }); return }
        send(res, { ok: true, positions })
        return
      }
      if (req.url === '/dig_pos' && req.method === 'POST') {
        const d = JSON.parse(body || '{}')
        if (!bot) { send(res, { ok: false, reason: 'not_connected' }); return }
        const x = Number(d.x), y = Number(d.y), z = Number(d.z)
        if (!Number.isFinite(x) || !Number.isFinite(y) || !Number.isFinite(z)) {
          send(res, { ok: false, reason: 'invalid_coords' }); return
        }
        let b = null
        try { b = bot.blockAt(new vec3(Math.floor(x), Math.floor(y), Math.floor(z))) } catch (e) {}
        if (!b || b.name === 'air') { send(res, { ok: false, reason: 'block_not_found' }); return }
        recordAction('dig_pos', 'running', { block: b.name, pos: { x, y, z } })
        bot.dig(b)
          .then(() => recordAction('dig_pos', 'done', { block: b.name }))
          .catch(e => recordAction('dig_pos', 'failed', { reason: String(e && e.message || e).slice(0, 80) }))
        send(res, { ok: true, digging: b.name })
        return
      }
      if (req.url === '/place' && req.method === 'POST') {
        const d = JSON.parse(body || '{}')
        if (!bot) { send(res, { ok: false, reason: 'not_connected' }); return }
        const x = Math.floor(Number(d.x)), y = Math.floor(Number(d.y)), z = Math.floor(Number(d.z))
        if (!Number.isFinite(x) || !Number.isFinite(y) || !Number.isFinite(z)) {
          send(res, { ok: false, reason: 'invalid_coords' }); return
        }
        // 物品在手（可指定或用手持）
        const want = String(d.item || '').toLowerCase()
        if (want && (!bot.heldItem || bot.heldItem.name !== want)) {
          const item = findItemByName(want)
          const found = item && bot.inventory.items().find(i => i.type === item.id)
          if (!found) { send(res, { ok: false, reason: 'item_not_in_inventory' }); return }
          try { await bot.equip(found, 'hand') } catch (e) {
            send(res, { ok: false, reason: 'equip_failed' }); return
          }
        }
        if (!bot.heldItem) { send(res, { ok: false, reason: 'empty_hand' }); return }
        const target = new vec3(x, y, z)
        // 找支撑面：六邻中第一个实心块
        const faces = [[0, -1, 0], [1, 0, 0], [-1, 0, 0], [0, 0, 1], [0, 0, -1], [0, 1, 0]]
        let placed = false
        let lastErr = 'no_support'
        for (const [fx, fy, fz] of faces) {
          let ref = null
          try { ref = bot.blockAt(target.offset(fx, fy, fz)) } catch (e) {}
          if (ref && ref.boundingBox !== 'empty' && ref.name !== 'air') {
            try {
              await bot.placeBlock(ref, new vec3(-fx, -fy, -fz))
              placed = true
              break
            } catch (e) { lastErr = String(e && e.message || e).slice(0, 60) }
          }
        }
        if (placed) { recordAction('place', 'done', { item: bot.heldItem.name, pos: { x, y, z } }); send(res, { ok: true }) }
        else { recordAction('place', 'failed', { reason: lastErr }); send(res, { ok: false, reason: lastErr }) }
        return
      }
      if (req.url === '/craft' && req.method === 'POST') {
        const d = JSON.parse(body || '{}')
        if (!bot) { send(res, { ok: false, reason: 'not_connected' }); return }
        const item = findItemByName(d.item)
        if (!item) { send(res, { ok: false, reason: 'unknown_item' }); return }
        const count = Math.max(1, Math.min(64, Number(d.count) || 1))
        let tableBlock = null
        if (d.table && d.table.x !== undefined) {
          try { tableBlock = bot.blockAt(new vec3(Math.floor(d.table.x), Math.floor(d.table.y), Math.floor(d.table.z))) } catch (e) {}
        }
        let recipes = []
        try { recipes = bot.recipesFor(item.id, null, 1, tableBlock) } catch (e) {}
        if (!recipes.length) {
          // 无工作台配方时若给了 table 也试一次（有些配方只在台前可见）
          try { recipes = bot.recipesFor(item.id, null, 1, tableBlock || undefined) } catch (e) {}
        }
        if (!recipes.length) { send(res, { ok: false, reason: 'no_recipe' }); return }
        // 选第一个原料够的配方
        const inv = bot.inventory.items()
        const counts = {}
        inv.forEach(i => { counts[i.name] = (counts[i.name] || 0) + i.count })
        let recipe = null
        for (const r of recipes) {
          let ok = true
          const need = {}
          for (const delta of (r.delta || [])) {
            if (delta.count < 0) {
              const nm = (mcNameOf(delta.id) || '').toString()
              need[nm] = (need[nm] || 0) + (-delta.count)
            }
          }
          for (const [nm, c] of Object.entries(need)) {
            if ((counts[nm] || 0) < c) { ok = false; break }
          }
          if (ok) { recipe = r; break }
        }
        if (!recipe) { send(res, { ok: false, reason: 'missing_ingredients' }); return }
        recordAction('craft', 'running', { item: d.item, count })
        try {
          // mineflayer 的合成入口是 bot.craft(recipe, count, table)——
          // Recipe 对象本身没有 .craft 方法（2026-09-26 真机实锤
          // "recipe.craft is not a function"：/craft 一直坏的根因）。
          await new Promise((resolve, reject) => {
            try {
              bot.craft(recipe, count, tableBlock, (err) => err ? reject(err) : resolve())
            } catch (e) { reject(e) }
          })
          recordAction('craft', 'done', { item: d.item, count })
          send(res, { ok: true, crafted: count })
        } catch (e) {
          const msg = String(e && e.message || e).slice(0, 80)
          recordAction('craft', 'failed', { reason: msg })
          // 真实异常透传（2026-09-26：'craft_failed' 裸码把 mineflayer 的
          // 合成异常吞掉，执行层与人都看不到失败原因）
          send(res, { ok: false, reason: `craft_failed: ${msg}` })
        }
        return
      }
      if (req.url === '/recipes' && req.method === 'GET') {
        // P3/§8 环境事实通道："此刻背包能做出什么"。零攻略语义——纯由
        // minecraft-data 配方表 ∧ 真实背包计算；30s 缓存防高频重扫；
        // 只报"原料已齐"的配方（多级的下一级不算，那是诚实事实）。
        if (!bot) { send(res, { ok: false, reason: 'not_connected' }); return }
        const nowMs = Date.now()
        if (globalThis.__recipes_cache && nowMs - (globalThis.__recipes_cache.at || 0) < 30000) {
          send(res, globalThis.__recipes_cache.data); return
        }
        try {
          const inv = bot.inventory.items()
          const counts = {}
          inv.forEach(i => { counts[i.name] = (counts[i.name] || 0) + i.count })
          let tableBlock = null
          try {
            const mcd = require('minecraft-data')(bot.version)
            const ct = mcd.itemsByName['crafting_table']
            if (ct) tableBlock = bot.findBlock({ matching: ct.id, maxDistance: 8 })
          } catch (e) {}
          const outMap = {}
          const seen = new Set()
          for (const item of inv.slice(0, 24)) {
            for (const withTable of [false, true]) {
              if (withTable && !tableBlock) continue
              let rs = []
              try { rs = bot.recipesAll(item.type, null, withTable ? tableBlock : null) || [] } catch (e) { rs = [] }
              for (const r of rs) {
                const rk = (r && r.id != null) ? ('r' + r.id) : null
                if (rk && seen.has(rk)) continue
                if (rk) seen.add(rk)
                const need = {}
                for (const delta of (r.delta || [])) {
                  if (delta.count < 0) {
                    const nm = mcNameOf(delta.id)
                    if (nm) need[nm] = (need[nm] || 0) + (-delta.count)
                  }
                }
                let okAll = Object.keys(need).length > 0
                for (const [nm, c] of Object.entries(need)) {
                  if ((counts[nm] || 0) < c) { okAll = false; break }
                }
                if (!okAll) continue
                let rname = null, ycount = 1
                if (r.resultItem) {
                  rname = r.resultItem.name || mcNameOf(r.resultItem.id)
                  ycount = r.resultItem.count || 1
                } else if (r.outputs && r.outputs.length) {
                  const o = r.outputs[0]
                  rname = (o.item && (o.item.name || mcNameOf(o.item.id))) || o.name || null
                  ycount = o.count || 1
                }
                if (!rname) continue
                if (!outMap[rname] || Object.keys(need).length < Object.keys(outMap[rname].ingredients).length) {
                  outMap[rname] = { result: rname, ingredients: need,
                                    needs_table: !!withTable, yield: ycount }
                }
              }
            }
          }
          const data = { ok: true, craftable: Object.values(outMap).slice(0, 12),
                         has_table_nearby: !!tableBlock }
          globalThis.__recipes_cache = { at: nowMs, data }
          send(res, data)
        } catch (e) {
          send(res, { ok: false, reason: 'recipes_scan_failed' })
        }
        return
      }
      if (req.url.startsWith('/recipe_for') && req.method === 'GET') {
        // 学习实验 §6/§10（2026-09-25）："什么东西能由什么做出来"的**运行时
        // 版本正确查询**——直读协商后版本的 minecraft-data 配方表，零攻略
        // 硬编码。查不到=如实报缺（26.1 的上游数据没有熔炉配方，就报没有）。
        try {
          const q = (req.url.split('?')[1] || '')
          const itemName = decodeURIComponent(
            (q.match(/item=([^&]+)/) || [])[1] || '')
          if (!itemName) { send(res, { ok: false, reason: 'missing_item' }); return }
          const mcd = require('minecraft-data')(bot ? bot.version : '26.1')
          const it = mcd.itemsByName[itemName.toLowerCase()] ||
                     mcd.blocksByName[itemName.toLowerCase()]
          if (!it) { send(res, { ok: false, reason: 'unknown_item',
                                 mc_version: mcd.version && mcd.version.minecraftVersion })
                     return }
          const producers = []
          const raw = (mcd.recipes || {})[it.id] || []
          const nameOf = id => {
            const x = mcd.items[id] || mcd.blocks[id]
            return x ? x.name : null
          }
          for (const r of raw) {
            if (!r.result || r.result.id !== it.id) continue   // 只留"产生它"的配方
            const ings = {}
            const flat = []
            for (const row of (r.inShape || [])) flat.push(...row)
            for (const row of (r.ingredients || [])) flat.push(row)
            for (const x of flat) {
              const id = (x && typeof x === 'object') ? x.id : x
              const nm = id != null && id >= 0 ? nameOf(id) : null
              if (nm) ings[nm] = (ings[nm] || 0) + 1
            }
            if (!Object.keys(ings).length) continue
            producers.push({ result: nameOf(r.result.id) || itemName,
                             yield: r.result.count || 1,
                             ingredients: ings,
                             needs_table: !!(r.inShape &&
                               (r.inShape.length > 2 || (r.inShape[0] || []).length > 2)) })
          }
          // 截断上限必须容得下"并列等价配方"的全集：26.1 里 stick 有 13 条、
          // crafting_table 有 12 条，mc-data 的表序恰好把 oak/spruce/birch/
          // jungle 四种排在第 9 位以后——旧版 slice(0,8) 把它们截掉，闭包里
          // 这四种木板从此无配方，背包里的橡木在图上成了永远用不上的死物
          // （2026-09-25 真机：有橡木不去合成工作台，全天乱转收别的木头）。
          send(res, { ok: true, item: itemName, recipes: producers.slice(0, 32),
                      mc_version: mcd.version && mcd.version.minecraftVersion,
                      mcd_version: mcd.version && mcd.version.version })
        } catch (e) {
          send(res, { ok: false, reason: 'recipe_query_failed' })
        }
        return
      }
      if (req.url.startsWith('/block_at') && req.method === 'GET') {
        // 调试端点：读指定坐标的方块名（不挖不动）。x,y,z 为方块坐标。
        try {
          const q = (req.url.split('?')[1] || '')
          const gv = k => Number((q.match(new RegExp(k + '=(-?[0-9.]+)')) || [])[1])
          const x = Math.floor(gv('x')), y = Math.floor(gv('y')), z = Math.floor(gv('z'))
          if (!Number.isFinite(x)) { send(res, { ok: false, reason: 'missing_x' }); return }
          let b = null
          try { b = bot.blockAt(new vec3(x, y, z)) } catch (e) {}
          send(res, { ok: true, x, y, z,
                      name: b ? String(b.name || '') : null,
                      loaded: !!b })
        } catch (e) { send(res, { ok: false, reason: 'block_at_failed' }) }
        return
      }
      if (req.url.startsWith('/block_meta') && req.method === 'GET') {
        // 学习实验 §5：方块掉落物/工具要求按**协商版本**数据查询（iron_ore
        // 掉什么、该用什么镐），先验闭环优先用运行时数据而非人工表。
        try {
          const q = (req.url.split('?')[1] || '')
          const blkName = decodeURIComponent(
            (q.match(/name=([^&]+)/) || [])[1] || '')
          const mcd = require('minecraft-data')(bot ? bot.version : '26.1')
          const blk = mcd.blocksByName[String(blkName || '').toLowerCase()]
          if (!blk) { send(res, { ok: false, reason: 'unknown_block' }); return }
          const nameOf = id => {
            const x = mcd.items[id] || mcd.blocks[id]
            return x ? x.name : null
          }
          const drops = (blk.drops || []).map(nameOf).filter(Boolean)
          const tools = Object.keys(blk.harvestTools || {}).map(nameOf).filter(Boolean)
          send(res, { ok: true, block: blk.name, drops, harvest_tools: tools,
                      hardness: blk.hardness, diggable: blk.diggable,
                      requires_tool: !!blk.requiresTool,
                      mc_version: mcd.version && mcd.version.minecraftVersion })
        } catch (e) {
          send(res, { ok: false, reason: 'block_meta_failed' })
        }
        return
      }
      if (req.url === '/smelt' && req.method === 'POST') {
        const d = JSON.parse(body || '{}')
        if (!bot) { send(res, { ok: false, reason: 'not_connected' }); return }
        const f = d.furnace || {}
        let furnaceBlock = null
        try { furnaceBlock = bot.blockAt(new vec3(Math.floor(f.x), Math.floor(f.y), Math.floor(f.z))) } catch (e) {}
        if (!furnaceBlock || !furnaceBlock.name || !furnaceBlock.name.includes('furnace')) {
          send(res, { ok: false, reason: 'furnace_not_found' }); return
        }
        const input = findItemByName(d.input)
        const fuel = findItemByName(d.fuel)
        if (!input || !fuel) { send(res, { ok: false, reason: 'unknown_item' }); return }
        const inInv = bot.inventory.items().find(i => i.type === input.id)
        const fuInv = bot.inventory.items().find(i => i.type === fuel.id)
        if (!inInv) { send(res, { ok: false, reason: 'item_not_in_inventory' }); return }
        if (!fuInv) { send(res, { ok: false, reason: 'no_fuel' }); return }
        try {
          const furnace = await bot.openFurnace(furnaceBlock)
          // mineflayer 签名是 putInput(itemType, metadata, count)——旧版把
          // count 写进了 metadata 位（元数据=1 匹配不到任何物品，熔炼永远
          // smelt_failed）。2026-09-26 离线验收发现。
          await furnace.putInput(inInv.type, inInv.metadata ?? 0, 1)
          // 燃料按"烧得完这批货要几根"放（调用方用 world_prior 燃料表
          // 算好传 fuel_count）：旧版硬放 1 根——木棍 5s < 熔一件 10s，
          // 炉子中途熄火，原料干卡在炉里（2026-09-27 真机）。
          const fuelCount = Math.max(1, Math.min(parseInt(d.fuel_count || 1, 10) || 1, fuInv.count))
          await furnace.putFuel(fuInv.type, fuInv.metadata ?? 0, fuelCount)
          furnace.close()
          send(res, { ok: true })
        } catch (e) {
          send(res, { ok: false, reason: 'smelt_failed' })
        }
        return
      }
      if (req.url === '/furnace_take' && req.method === 'POST') {
        const d = JSON.parse(body || '{}')
        if (!bot) { send(res, { ok: false, reason: 'not_connected' }); return }
        const f = d.furnace || {}
        let furnaceBlock = null
        try { furnaceBlock = bot.blockAt(new vec3(Math.floor(f.x), Math.floor(f.y), Math.floor(f.z))) } catch (e) {}
        if (!furnaceBlock) { send(res, { ok: false, reason: 'furnace_not_found' }); return }
        try {
          const furnace = await bot.openFurnace(furnaceBlock)
          let taken = null
          if (furnace.outputItem()) {
            taken = { name: furnace.outputItem().name, count: furnace.outputItem().count }
            await furnace.takeOutput()
          }
          furnace.close()
          send(res, { ok: true, taken })
        } catch (e) {
          send(res, { ok: false, reason: 'furnace_take_failed' })
        }
        return
      }
      if (req.url === '/eat' && req.method === 'POST') {
        const d = JSON.parse(body || '{}')
        if (!bot) { send(res, { ok: false, reason: 'not_connected' }); return }
        const item = findItemByName(d.item)
        if (!item) { send(res, { ok: false, reason: 'unknown_item' }); return }
        const found = bot.inventory.items().find(i => i.type === item.id)
        if (!found) { send(res, { ok: false, reason: 'item_not_in_inventory' }); return }
        bot.equip(found, 'hand')
          .then(() => bot.consume())
          .then(() => { recordAction('eat', 'done', { item: d.item }); send(res, { ok: true }) })
          .catch(e => { recordAction('eat', 'failed', { reason: String(e).slice(0, 60) }); send(res, { ok: false, reason: 'eat_failed' }) })
        return
      }
      if (req.url === '/combat' && req.method === 'POST') {
        const d = JSON.parse(body || '{}')
        if (!bot || !bot.entity) { send(res, { ok: false, reason: 'not_connected' }); return }
        const ent = findEntityByName(d.entity)
        if (!ent) { send(res, { ok: false, reason: 'no_target' }); return }
        startCombat(String(d.entity), Number(d.retreat_health) || 8, Number(d.max_ms) || 45000)
        send(res, { ok: true })
        return
      }
      if (req.url === '/stop_combat' && req.method === 'POST') {
        stopCombat()
        send(res, { ok: true })
        return
      }
      if (req.url === '/flee' && req.method === 'POST') {
        const d = JSON.parse(body || '{}')
        if (!bot || !bot.entity) { send(res, { ok: false, reason: 'not_connected' }); return }
        // 远离最近敌对生物跑
        const hostileNames = ['zombie', 'skeleton', 'creeper', 'spider', 'witch', 'husk', 'drowned', 'stray', 'enderman']
        const hostiles = Object.values(bot.entities).filter(e => e !== bot.entity && hostileNames.includes(e.name || ''))
        if (!hostiles.length) { send(res, { ok: true, note: 'no_hostile' }); return }
        hostiles.sort((a, b) => a.position.distanceTo(bot.entity.position) - b.position.distanceTo(bot.entity.position))
        const h = hostiles[0]
        const me = bot.entity.position
        const dx = me.x - h.position.x, dz = me.z - h.position.z
        const len = Math.max(0.01, Math.sqrt(dx * dx + dz * dz))
        const dist = Math.min(24, Number(d.distance) || 10)
        const goal = new GoalNear(me.x + dx / len * dist, me.y, me.z + dz / len * dist, 1.5)
        recordAction('flee', 'running', { from: h.name })
        disarmPathWatches()
        bot.pathfinder.setGoal(goal, true)
        armPathWatch('goal_reached', () => { if (lastAction.name === 'flee' && lastAction.status === 'running') recordAction('flee', 'done', {}) })
        armPathWatch('path_update', (r) => { if (r && r.status === 'noPath' && lastAction.name === 'flee' && lastAction.status === 'running') recordAction('flee', 'failed', { reason: 'noPath' }) })
        send(res, { ok: true })
        return
      }
      if (req.url === '/collect_item' && req.method === 'POST') {
        const d = JSON.parse(body || '{}')
        if (!bot || !bot.entity) { send(res, { ok: false, reason: 'not_connected' }); return }
        const radius = Math.min(16, Math.max(1, Number(d.radius) || 8))
        const items = Object.values(bot.entities)
          .filter(e => e !== bot.entity && (e.name === 'item' || e.displayName === 'Item')
                       && e.position.distanceTo(bot.entity.position) <= radius)
          .sort((a, b) => a.position.distanceTo(bot.entity.position) - b.position.distanceTo(bot.entity.position))
        if (!items.length) { send(res, { ok: false, reason: 'no_item' }); return }
        const it = items[0]
        recordAction('collect_item', 'running', { item: it.displayName || 'item' })
        disarmPathWatches()
        bot.pathfinder.setGoal(new GoalFollow(it, 0.8), true)
        const done = () => {
          const still = Object.values(bot.entities).includes(it)
          recordAction('collect_item', still ? 'failed' : 'done',
                       still ? { reason: 'item_not_picked' } : { picked: true })
        }
        armPathWatch('goal_reached', done)
        setTimeout(() => {
          if (lastAction.name === 'collect_item' && lastAction.status === 'running') {
            const still = Object.values(bot.entities).includes(it)
            recordAction('collect_item', still ? 'failed' : 'done',
                         still ? { reason: 'timeout' } : { picked: true })
          }
        }, 15000)
        send(res, { ok: true, item: it.displayName || 'item' })
        return
      }
      if (req.url === '/interact' && req.method === 'POST') {
        const d = JSON.parse(body || '{}')
        if (!bot) { send(res, { ok: false, reason: 'not_connected' }); return }
        let b = null
        try { b = bot.blockAt(new vec3(Math.floor(Number(d.x)), Math.floor(Number(d.y)), Math.floor(Number(d.z)))) } catch (e) {}
        if (!b || b.name === 'air') { send(res, { ok: false, reason: 'block_not_found' }); return }
        bot.activateBlock(b)
          .then(() => send(res, { ok: true, block: b.name }))
          .catch(e => send(res, { ok: false, reason: 'interact_failed' }))
        return
      }
      if (req.url === '/interact_entity' && req.method === 'POST') {
        const d = JSON.parse(body || '{}')
        if (!bot) { send(res, { ok: false, reason: 'not_connected' }); return }
        const ent = findEntityByName(d.entity)
        if (!ent) { send(res, { ok: false, reason: 'entity_not_visible' }); return }
        bot.activateEntity(ent)
          .then(() => send(res, { ok: true, entity: ent.name }))
          .catch(e => send(res, { ok: false, reason: 'interact_failed' }))
        return
      }
      if (req.url === '/equip_off' && req.method === 'POST') {
        const d = JSON.parse(body || '{}')
        if (!bot) { send(res, { ok: false, reason: 'not_connected' }); return }
        const item = findItemByName(d.item)
        if (!item) { send(res, { ok: false, reason: 'unknown_item' }); return }
        const found = bot.inventory.items().find(i => i.type === item.id)
        if (!found) { send(res, { ok: false, reason: 'item_not_in_inventory' }); return }
        bot.equip(found, 'off-hand')
          .then(() => send(res, { ok: true }))
          .catch(e => send(res, { ok: false, reason: 'equip_failed' }))
        return
      }
      if (req.url === '/unequip' && req.method === 'POST') {
        if (!bot) { send(res, { ok: false, reason: 'not_connected' }); return }
        try { bot.unequip('hand'); send(res, { ok: true }) }
        catch (e) { send(res, { ok: false, reason: 'unequip_failed' }) }
        return
      }
      if (req.url === '/drop' && req.method === 'POST') {
        const d = JSON.parse(body || '{}')
        if (!bot) { send(res, { ok: false, reason: 'not_connected' }); return }
        const item = findItemByName(d.item)
        if (!item) { send(res, { ok: false, reason: 'unknown_item' }); return }
        const count = Math.max(1, Number(d.count) || 1)
        const stack = bot.inventory.items().find(i => i.type === item.id)
        if (!stack) { send(res, { ok: false, reason: 'item_not_in_inventory' }); return }
        bot.tossStack(stack.clone ? stack : stack)
          .then(() => send(res, { ok: true }))
          .catch(() => bot.toss(item.id, count, null).then(() => send(res, { ok: true })).catch(e => send(res, { ok: false, reason: 'drop_failed' })))
        return
      }
      if (req.url === '/sort_inventory' && req.method === 'POST') {
        if (!bot) { send(res, { ok: false, reason: 'not_connected' }); return }
        try {
          // 简单整理：同类合并（把每个散堆移到同类首格）
          const items = bot.inventory.items()
          const seen = {}
          let moved = 0
          for (const it of items) {
            if (seen[it.name] === undefined) { seen[it.name] = it; continue }
            try { await bot.moveSlotItem(it.slot, seen[it.name].slot); moved++ } catch (e) {}
          }
          send(res, { ok: true, moved })
        } catch (e) { send(res, { ok: false, reason: 'sort_failed' }) }
        return
      }
      if (req.url === '/sleep_at' && req.method === 'POST') {
        const d = JSON.parse(body || '{}')
        if (!bot) { send(res, { ok: false, reason: 'not_connected' }); return }
        let b = null
        try { b = bot.blockAt(new vec3(Math.floor(Number(d.x)), Math.floor(Number(d.y)), Math.floor(Number(d.z)))) } catch (e) {}
        if (!b || !String(b.name || '').includes('bed')) { send(res, { ok: false, reason: 'bed_not_found' }); return }
        bot.sleep(b)
          .then(() => { recordAction('sleep', 'running', { bed: b.name }); send(res, { ok: true }) })
          .catch(e => send(res, { ok: false, reason: 'sleep_failed' }))
        return
      }
      if (req.url === '/goto_entity' && req.method === 'POST') {
        const d = JSON.parse(body || '{}')
        if (!bot || !bot.entity) { send(res, { ok: false, reason: 'not_connected' }); return }
        const ent = findEntityByName(d.entity) ||
          Object.values(bot.entities).find(e => e !== bot.entity && e.name === 'item' &&
            (!d.entity || String(e.displayName || '').toLowerCase().includes(String(d.entity).toLowerCase())))
        if (!ent) { send(res, { ok: false, reason: 'entity_not_visible' }); return }
        recordAction('goto_entity', 'running', { entity: String(d.entity || '') })
        disarmPathWatches()
        bot.pathfinder.setGoal(new GoalFollow(ent, Number(d.range) || 1.5), true)
        armPathWatch('goal_reached', () => { if (lastAction.name === 'goto_entity' && lastAction.status === 'running') recordAction('goto_entity', 'done', {}) })
        armPathWatch('path_update', (r) => { if (r && r.status === 'noPath' && lastAction.name === 'goto_entity' && lastAction.status === 'running') recordAction('goto_entity', 'failed', { reason: 'noPath' }) })
        send(res, { ok: true })
        return
      }
      if (req.url === '/action_result' && req.method === 'GET') {
        send(res, { ok: true, action: lastAction })
        return
      }
      if (req.url === '/stop_goto' && req.method === 'POST') {
        // 真正取消寻路：只清控制状态不会让 pathfinder 停下（它会继续走完当前 goal）
        try { bot.pathfinder.setGoal(null) } catch (e) { lastError = String(e).slice(0, 120) }
        try { bot.pathfinder.stop() } catch (e) { /* 老版本无此方法 */ }
        rawGaitStop()          // 原始步态接管期间也算"寻路"，一起取消
        clearCtl()
        if (lastAction.status === 'running') recordAction(lastAction.name, 'cancelled', {})
        send(res, { ok: true })
        return
      }
      if (req.url === '/stopfollow' && req.method === 'POST') {
        const dur = followStart ? (Date.now() - followStart) / 1000 : 0
        followStart = null
        stopFollow()
        recordAction('stop_follow', 'done', { followed_seconds: Math.round(dur) })
        send(res, { ok: true, followed_seconds: Math.round(dur) })
        return
      }
      if (req.url === '/stop' && req.method === 'POST') {
        stopCombat()
        if (bot) clearCtl()
        if (lastAction.status === 'running') recordAction(lastAction.name, 'cancelled', {})
        recordAction('stop', 'done', {})
        json(); return
      }
      send(res, 'not found', 404)
    } catch (e) {
      send(res, { error: String(e) }, 500)
    }
  })
})

// 端口独占 = 一个桥端口只能有一个 Haru。
// 为什么必须有这条：同名 bot 重复登录会被服务器互相踢下线（实测 kicked 断连 2584 次、
// 游戏里表现为"反复进入退出"）。之前的教训是人工重启时旧进程没杀干净 →
// 现在由端口占用自己兜住：抢不到端口就明确退出，而不是再起一个去互踢。
const BRIDGE_PORT = config.bridge_port || 5010
server.on('error', (e) => {
  if (e && e.code === 'EADDRINUSE') {
    console.error(`[Haru] 桥端口 ${BRIDGE_PORT} 已被占用——已经有一个 Haru 在运行，` +
                  '本实例退出（避免同名互踢造成反复进出）。')
    process.exit(1)
  }
  console.error('[Haru] 桥服务错误:', e && e.message ? e.message : e)
})

server.listen(BRIDGE_PORT, '127.0.0.1', () => console.log(`[Bridge] HTTP 桥 :${BRIDGE_PORT}`))

// ── 腿死看门狗（2026-09-24，2026-09-25 调 40s）：pathfinder 静默冻结一天
// 四连——/goto 回执 ok 但零位移，haru.log 无任何报错。有寻路目标却 40 秒
// 挪不动 0.3 格且不在水里 → 干净退出。必须抢在技能层 45s 停滞放弃之前——
// 技能放弃会 stop_goto 清掉目标，60s 版看门狗永远等不到触发（实战教训）。
// app 侧桥活性轮询发现失联后自动复活本进程并重进世界。
let __wdPos = null, __wdStale = 0, __wdHooked = false, __goalStatic = false
setInterval(() => {
  try {
    if (!bot) return
    // pathfinder 没有公开的 goal 属性（setGoal 存闭包变量，实测读不到）——
    // 包装 setGoal 自己记账：静态目标（坐标寻路）计入，动态跟随豁免；
    // goal_reached 清位（到达后站着不动不算卡死）。
    if (!__wdHooked && bot.pathfinder) {
      __wdHooked = true
      const __orig = bot.pathfinder.setGoal.bind(bot.pathfinder)
      // 按目标类型判别，不能按 dynamic 参数——/goto 传的也是 dynamic=true
      // （2026-09-25 实战：v3 恰好豁免了最常冻结的坐标寻路）。
      // GoalFollow（跟随/追实体）豁免：目标自己不动时原地待命是正常的；
      // GoalNear/GoalBlock（坐标寻路）计入：40 秒零位移 = 冻结。
      bot.pathfinder.setGoal = (goal, dynamic = false) => {
        try {
          __goalStatic = !!(goal && goal.constructor &&
                            goal.constructor.name !== 'GoalFollow')
        } catch (e) { __goalStatic = false }
        return __orig(goal, dynamic)
      }
      bot.on('goal_reached', () => { __goalStatic = false })
    }
    if (!bot.entity || !bot.pathfinder || !__goalStatic) {
      __wdStale = 0; __wdPos = null; return
    }
    const p = bot.entity.position
    if (__wdPos && p.distanceTo(__wdPos) < 0.3) __wdStale++
    else __wdStale = 0
    __wdPos = p.clone()
    const inWater = bot.water === true
    if (__wdStale >= 2 && !inWater) {
      // 原始步态正在接管、或刚刚"如实认输"（地形不允许直走）→ 不重启进程。
      // 26.1 的 pathfinder 已由兜底腿代偿，重启既治不好它，还会打断桥十几秒
      // （上层看到一串 ECONNREFUSED，背包读数也被清空）。真正该报的是位移为零
      // 这件事本身，交给技能层的停滞放弃去处理。
      if (rawGait || Date.now() - __gaitHonestFail < 90000) {
        if (__wdStale >= 4) {
          console.log('[Haru] 看门狗：静态目标零位移，但原始步态在代偿/刚如实认输 → 不重启（地形或目标不可达，交给上层换路）')
          __wdStale = 0
        }
        return
      }
      console.log('[Haru] 看门狗：静态寻路目标 40s 零位移，pathfinder 疑似冻结，重启进程自愈')
      process.exit(2)
    }
  } catch (e) { /* 观察者不添乱 */ }
}, 20000)

createBot()
