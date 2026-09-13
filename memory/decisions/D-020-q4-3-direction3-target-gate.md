# D-020：问题4-3方向3进入 HUMAN TARGET GATE

- 日期 / 任务 / 作者：2026-09-13 / T-012-q4-3-model-judge / Codex
- 状态：accepted
- 问题与约束：问题4-3要求在逐日波动电价下重做问题3；0:00不得读取当天未来真实价格，日内只调整尚未执行计划，每次调整与上一版有效计划比较。
- 选定方案及简明理由：结构筛选保留 `rolling_margin_mean14` 作为Delivery Baseline、`rolling_scenario_mean14` 作为方向3主候选、`rolling_scenario_price_update` 作为待证增强。问题3方向3已有审计结果，问题4-2供需场景全年有效，因此主候选有复用依据。
- 支持证据：`deliverables/q3/README.md`、`planning/q4-saa-full-results.md`、`planning/q4-saa-poc-results.md`、`planning/q4-joint-scenario-poc-results.md`。
- 备选与放弃原因：价格—供需联合配对场景在问题4-2未优于错配负对照，Stage A判定REJECT；日内价格更新依赖题面未明确的信息边界，判定RISK。
- 适用边界、不确定性与风险：问题4-3 Cheap PoC尚未执行；现有跨题证据不能证明方向3在波动电价下仍有增益。主候选使用当日截止窗口和0:00形成的14日历史均价曲线。
- 重新考虑的条件：用户批准双层目标和回退后执行预声明PoC；只有验证期与评估期均通过费用/尾部门槛和信息审计，才进入HUMAN MODEL GATE。
- 替代/被替代的决策：替代D-014中Task 5采用方向2的旧主线；不改变D-018的问题4-2结论。

- 人工门槛：2026-09-13用户明确回复 `APPROVE DUAL TARGET AND FALLBACK`。主候选不使用日内已实现价格修正；该能力只作为独立Candidate B消融，不能未经HUMAN MODEL GATE升级为最终模型。
