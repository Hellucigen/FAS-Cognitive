# 实验期间代码修改记录（2026-09-28 论文实验套件）

每个修改记录：change_id / timestamp / file / reason / before / after /
bug_or_design_issue / design_preserving / affected_experiments

## C01
- timestamp: 2026-09-28 10:2x
- file: experiment_mode.py（规范化统一实验模式入口，§0/§2）
- reason: 用户要求统一实验模式入口 FAS_EXPERIMENT_MODE（NORMAL/LEARNING_CLOSED_LOOP/SANDBOX）
- before: 仅支持 EXPERIMENT_MODE=learning_closed_loop 二值（off/learning_closed_loop）
- after: 支持 FAS_EXPERIMENT_MODE（EXPERIMENT_MODE 兼容）；mode() 规范化枚举
  (normal/learning_closed_loop/sandbox)；seed 播种（random/numpy/torch 已加载者）；
  banner 扩展为 FAS RESEARCH EXPERIMENT 横幅（experiment_id/mode/timestamp/
  git commit/seed/配置快照/MC 端点/LLM 摘要/shield 清单）；新增
  git_hash()/experiment_id()/config_snapshot() 助手
- bug_or_design_issue: 无（纯附加，原有 API 语义全部保持）
- design_preserving: true
- affected_experiments: 全部（实验模式入口）

## C02
- timestamp: 2026-09-28 10:2x
- file: config.py（"experiment" 段新增 experiment_id/seed 键）
- reason: 归一化入口需要 seed/experiment_id 配置承载
- before: 无此两键
- after: "experiment_id": ""（空=自动生成），"seed": None（None=不播种）
- bug_or_design_issue: 无
- design_preserving: true
- affected_experiments: 全部

## C03
- timestamp: 2026-09-28 10:3x
- file: app.py（实验目标注入条件收紧）
- reason: §2.5 实验模式不得注入任务目标；sandbox 模式目标必须由部署侧给出
- before: enabled() 时注入 goal_obtain（含 sandbox）
- after: 仅 learning_closed_loop 注入目标；banner 两种模式都打
- bug_or_design_issue: 设计对齐（sandbox 不注入任务答案）
- design_preserving: true
- affected_experiments: 全部（sandbox 语义修正）

## C04
- timestamp: 2026-09-28 10:3x
- file: experiment_recorder.py（新建，§4 统一 Experiment Recorder）
- reason: 论文实验需要可审计、可重复的 run 数据包（metadata/events/snapshots/
  metrics/summary），扩展 fas_log 而非另起炉灶
- after: RunRecorder 类：run 目录包生成 + 事件流（events.jsonl 同时落 fas_log）
  + graph/internal_state 快照 + 计数器/metrics + manifest 登记
- bug_or_design_issue: 无
- design_preserving: true（纯附加，零行为改动）
- affected_experiments: 全部（记录层）

## C05
- timestamp: 2026-09-28 10:2x
- file: .gitignore（实验数据目录退出跟踪）
- reason: 图谱快照含真实经历/偏好数据（隐私先例），实验数据不必入库
- before: 无 FAS_Research_Experiments 条目
- after: /FAS_Research_Experiments/ 忽略
- bug_or_design_issue: 无
- design_preserving: true（不影响行为）
- affected_experiments: 无

## C06
- timestamp: 2026-09-28 10:2x
- file: minecraft/bot/config.json（环境配置修正）
- reason: 用户指定 Minecraft 端口 50656；旧配置 64446 与运行中的客户端
  （监听 50656）不符，桥连不上
- before: port=64446
- after: port=50656（原始备份 /tmp/bot_config_backup_20260928.json）
- bug_or_design_issue: 过期配置（非代码 bug）
- design_preserving: true
- affected_experiments: Minecraft 系列（M1-M7）

（后续修改继续追加）
