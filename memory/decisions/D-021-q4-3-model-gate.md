# D-021：问题4-3方向3Cheap PoC失败并进入HUMAN MODEL GATE

- 日期 / 任务 / 作者：2026-09-13 / T-012-q4-3-model-judge / Codex
- 状态：proposed
- 问题与约束：在已批准的双层目标、0.1%费用/1%CVaR门槛和时序打乱负对照下，验证问题3方向3能否推广到问题4-3。
- 选定方案及简明理由：AI推荐操作性回退 `rolling_margin_mean14`。方向3在验证期和评估期库存修正费用分别比Baseline高0.456%和0.692%；2026-09-13按边界概率质量修正后，CVaR90分别高0.592%和1.875%。日内价格修正增益远低于门槛，原Scientific FAIL裁决不变。
- 支持证据：`experiments/q4-3-poc-20260913-01/selection.json`、`audit_summary.json`、`model_report.md`和`planning/q4-3-poc-results.md`。
- 备选与放弃原因：方向3连续场景和日内价格修正均Scientific FAIL；联合价格—供需场景此前结构性REJECT；时序打乱对照不稳定且仅用于证伪，不能事后升级为主模型。
- 适用边界、不确定性与风险：PoC共84天，算法审计PASS，但尚未运行334天或生成官方 `result4-3.xlsx`，Deliverable为PARTIAL。
- 重新考虑的条件：用户选择MODIFY并批准以Fallback完成全年最低交付；或提供能改变时间相关性识别的新数据/新机制后重新进入Target Gate。
- 替代/被替代的决策：不改变D-020已批准的目标契约；本决策对问题4-3否决方向3科学主张，不改变问题3方向3的既有结果。
