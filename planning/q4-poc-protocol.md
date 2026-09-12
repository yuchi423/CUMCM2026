# 问题4-2因果电价预测Cheap PoC协议

日期：2026-09-12。目标与回退口径已经用户以 `APPROVE DUAL TARGET AND FALLBACK` 批准，批准记录见 `memory/decisions/D-015-q4-target-approved.md`。

## PoC Question

冻结问题2 `combined` 的供需预测、14天残差窗口、0.8分位余量、预测终端1200kWh和因果执行器后，只用历史价格构造的简单预测，能否在附件4同一实际价格轨迹上形成可行调度，并比不利用逐日电价结构的控制组和错位价格负对照降低费用？

## Minimum Deliverable Question

PoC是否能够输出每个候选在预声明84天上的价格预测指标、计划与实际费用、紧急购电、未用计划电、窗口首末SOC、物理审计和信息边界检查？本阶段不要求生成完整334天 `result4-2.xlsx`。

## Scientific Validity Question

费用变化是否来自正确对齐且当时可获得的历史价格信号，而不是当天未来真实价格泄漏、供需模型同时改变、价格错位或多消耗期末库存？

## Minimum Implementation

- 价格控制组：附件1固定价格曲线。
- 因果候选：前一日同刻、过去7/14/28日同刻均值、过去35天同星期均值、14日指数加权均值。
- 负对照：前一日价格循环错移6小时。
- 信息上界：当天真实价格，仅标记为不可执行的oracle。
- 供需与储能全部沿用问题2 `combined`；候选之间只改变规划价格。
- 6个14天窗口：3月、4月为development，6月、8月为validation，9月、12月为evaluation。每个窗口从问题2 `combined` 已审计轨迹取得共同起始SOC，窗口内部连续运行；跳过的月份不拼接SOC，因此这里只是PoC。

## Performance Metrics

- 价格MAE、RMSE、日内Spearman排序相关、最高/最低四分位时段重合率；
- 实际普通购电费、5倍紧急购电费、总现金费用；
- 窗口库存调整费用、紧急购电量、未用计划电量；
- 物理与费用审计误差、求解器失败次数。

Development按库存调整费用选择因果候选。若与最低值相差不超过0.5%，按 `lag1 → mean7 → mean14 → mean28 → weekday35 → ewma14` 选择较简单方法。Validation和evaluation不参与改选。

## Confounder Diagnostic

1. 将当天及以后真实价格整体扰动，所有因果预测必须保持不变；oracle应变化。
2. 所有方法使用相同实际负荷、光伏、窗口初始SOC和附件4结算价格。
3. 负对照故意破坏日内价格位置；若选中方法不能优于负对照，不支持“正确价格时序有价值”的解释。
4. 报告现金费用和库存调整费用；若优势只存在于更低期末SOC，不支持费用改善解释。
5. oracle只表示完美信息参考，不进入候选选择。

## Algorithm Failure Condition

任一求解失败、未来价格扰动改变因果预测、费用复算误差或能量/SOC/功率违约超过1e-6，判定算法失败。

## Scientific Target Failure Condition

开发期选中方法在validation和evaluation均不能优于固定价格控制组，或不能优于错位价格负对照，或优势在库存调整后消失，则不能声称正确价格信号带来稳定经济价值。

## Fallback Trigger and Fallback PoC

触发科学目标失败时，保留 `lag1` 作为最简单的因果价格预测交付基线；若 `lag1` 算法失败，则回退为附件1固定价格计划、附件4实际结算，并明确不主张价格预测收益。

## Stop Condition

完成9种方法×84天运行、独立审计、三张图和报告后停止。PoC不自动扩展为334天，不调0.8分位和终端SOC，不生成正式Excel，等待Human Model Gate。
