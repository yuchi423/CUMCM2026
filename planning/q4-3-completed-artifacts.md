# 问题4-3已完成成果总账

日期：2026-09-13

任务：`T-012-q4-3-model-judge`

分支：`codex/T-012-q4-3-model-judge`
当前主模型：`rolling_scenario_mean14`

## 当前结论

问题4-3延续问题3方向3的连续残差情景滚动模型。每天0:00、6:00、12:00、18:00更新计划；电价使用最多14个已结束日的同刻均价预测，附件4真实价格只用于结算。调整费逐次比较上一版有效计划的未执行后缀。

修正后正式实验为`experiments/q4-3-scenario-full-20260913-02`。统计期2025-02-01至12-31共334天，1月从6000 kWh开始连续热身。

| 指标 | 数值 |
| --- | ---: |
| 现金总费用 | 14,432,279.80元 |
| 库存修正费用 | 14,432,805.82元 |
| 日费用CVaR90 | 73,458.35元 |
| 普通购电量 | 20,946,711.61 kWh |
| 紧急购电量 | 19,840.03 kWh |
| 正式期初/期末SOC | 2,119.62 / 1,348.09 kWh |
| 实际改变计划 | 747 / 1002次 |

相对旧余量对照，现金费用减少171,182.49元，库存修正费用减少171,013.52元。CVaR旧值73,323.89元已因边界样本权重错误撤回；修正不改变计划、SOC、逐日费用和总费用。

## 正式交付

- 方向与结果：`planning/q4-3-direction3-full-results.md`
- 五点review决定：`memory/decisions/D-025-q4-3-review-corrections.md`
- 正式实验：`experiments/q4-3-scenario-full-20260913-02/`
- 交付包：`deliverables/q4-3/`
- 官方模板结果：`output/result4-3.xlsx`
- 一键入口：`final_run/q4_3_scenario/main.py`
- 交接：`memory/handoffs/2026-09-13-T-012-q4-3-direction3-full-results.md`

正式实验包含汇总表、逐日结果、月度/季度/阶段结果、四个论文日期、三张PNG/PDF图、模型报告、独立审计、求解器审计和工作簿审计。官方工作簿保持四张表顺序和官方表头，111999个填数单元逐格复核，最大差异为0。

## 历史证据

- `experiments/q4-3-poc-20260913-01`：84天模型筛选。精确CVaR修正后，方向3在验证期和评估期仍未通过预设门槛，Scientific FAIL不变。
- `experiments/q4-3-full-20260913-02`：旧余量方案334天对照，现金费用14,603,462.30元。
- `experiments/q4-3-scenario-full-20260913-01`：CVaR修正前的首轮方向3全年运行，计划与正式实验相同，风险汇总由`cvar_correction.json`说明。

## 论文边界

- 4-2与4-3相差118,415.66元，但初始SOC、价格预测、供需模型和调整权限不同，不能把差额归因为日内预报收益。
- 4-2选择mean7是为了满足团队预设的季度稳定性门槛；修复后SAA全年费用更低，门槛不是题设要求。
- 84天FAIL与334天同起点全年节省可以同时成立；全年结果不能单独证明时间相关性是收益来源。
- 当前只验证逐次计费，只优化到当天24:00；初始—最终一次计费与跨午夜24小时窗口仍是待运行敏感性。

## 恢复方式

```text
git fetch origin
git switch codex/T-012-q4-3-model-judge
git pull --ff-only
python final_run/q4_3_scenario/main.py experiments/q4-3-scenario-full-<new-id>
```

完整逐槽明细、输入价格副本和工作簿中间载荷由一键入口重建，不重复上传Git。
