# 问题4-3方向3 Model Judge Cheap PoC裁决

日期：2026-09-13
实验：`experiments/q4-3-poc-20260913-01`
代码提交：`0e8f135`
状态：Cheap PoC与独立审计已完成；正式334天交付未开始。

## 1. Stage C — Cheap PoC

本轮严格使用批准前冻结的六个14日窗口，共84天。六个方案共享实际轨迹和窗口初始SOC；真实费用使用附件4价格结算。计划只读取目标日前14日价格，价格增强在6/12/18只读取当天已实现价格前缀。

| 方案 | 84天现金费用/元 | 库存修正费用/元 | 日费用CVaR90/元 | 紧急购电/kWh | 调整费/元 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 0时后不调整 | 4,358,536.21 | 4,360,508.43 | 101,068.63 | 180,215.22 | 0.00 |
| 仅状态反馈 | 3,844,051.87 | 3,845,929.10 | 83,498.78 | 77,012.56 | 77,822.78 |
| 方向2余量基线 | **3,611,075.08** | **3,610,178.75** | **74,128.22** | 934.17 | 148,223.26 |
| 方向3连续场景 | 3,630,834.22 | 3,630,803.72 | 75,327.24 | 577.64 | 170,810.98 |
| 时序打乱负对照 | 3,589,706.01 | 3,590,048.92 | 75,204.81 | 3,380.52 | 147,359.29 |
| 方向3加日内价格修正 | 3,630,963.58 | 3,630,717.28 | 75,325.85 | 577.64 | 170,953.52 |

### 保留期结果

| 比较 | 验证期 | 评估期 | 裁决 |
| --- | ---: | ---: | --- |
| 方向3相对方向2库存修正费用 | 高0.456% | 高0.692% | 两期均失败 |
| 方向3相对方向2日费用CVaR90 | 高0.592% | 高1.875% | 两期均失败 |
| 方向3相对时序打乱对照库存修正费用 | 高2.034% | 高0.227% | 连续时序未提供正证据 |
| 日内价格修正相对方向3库存修正费用 | 低0.0051% | 低0.0003% | 远低于0.1%门槛 |

Algorithmic Verdict为 **PASS**：72,576条10分钟执行记录、1,764个计划版本全部独立复算；未来价格后缀扰动不改变任何可用价格预测；时序打乱保持每个时段8个残差样本的边际分布，最大误差为0。

Scientific Validity Verdict：Candidate A方向3为 **FAIL**，Candidate B日内价格增强为 **FAIL**。方向3虽然少用了356.54kWh紧急购电，但现金、库存修正费用和CVaR90均高于余量基线，额外复杂度没有获得证据支持。

2026-09-13复核发现原CVaR直接平均完整尾部样本，没有给边界样本按概率质量加权。上表与保留期比例已按等权经验CVaR修正；现金费用、库存修正费用、计划轨迹和Scientific FAIL裁决均不变。

Fallback PoC Verdict：`rolling_margin_mean14`为 **PASS**。它完成84天因果回放和全部审计，是当前操作性回退，但尚未形成题目要求的334天工作簿。

## 2. Stage D — Evidence Comparison

| Dimension | Baseline `rolling_margin_mean14` | Candidate A `rolling_scenario_mean14` | Candidate B `rolling_scenario_price_update` |
| --- | --- | --- | --- |
| Task Fit | 直接生成滚动计划 | 直接生成滚动计划 | 直接生成滚动计划 |
| Minimum Deliverable Coverage | PARTIAL：84天，无工作簿 | PARTIAL：84天，无工作簿 | PARTIAL：84天，无工作簿 |
| Scientific Target–Observable Link | NOT REQUIRED | 连续路径应优于分位余量与时序打乱对照 | 已实现价格前缀应改善剩余调度 |
| Confounder Diagnostic | 状态/PV消融已执行 | 时序打乱负对照已执行 | 未来价格扰动与无价格更新消融已执行 |
| Falsification Status | 未触发操作性失败 | **TRIGGERED** | **TRIGGERED** |
| Scientific Validity | NOT REQUIRED | **FAIL** | **FAIL** |
| Fallback Status | PoC PASS | 回退到Baseline | 回退到Baseline |
| Data Support | 足够 | 8条路径样本偏少但可运行 | 题面信息边界仍是建模假设 |
| Assumption Burden | 低—中 | 中—高 | 高 |
| Core PoC Metric | 库存修正3,610,178.75元 | 3,630,803.72元 | 3,630,717.28元 |
| Constraint Check | PASS | PASS | PASS |
| Robustness | 两个保留期均优于A | 两期均不优于Baseline | 两期均未达到增益门槛 |
| Interpretability | 高 | 中 | 中 |
| Runtime / Implementation Risk | 中 | 高 | 高 |
| Writing Cost | 低 | 高且结论受限 | 高且信息假设敏感 |
| Downstream Utility | 可直接扩展为全年交付 | 不宜支持方向3科学主张 | 不宜支持实时价格更新主张 |

复杂度裁决为 `USE BASELINE`。时序打乱负对照的84天总费用虽最低，但它在评估期高于Baseline，且其职责是证伪“连续时间相关性”，不能在看到结果后改名为新主模型。

## 3. Delivery Reconciliation

| Required Output | Actual artifact | Produced by | Claim level | Status |
| --- | --- | --- | --- | --- |
| 84天候选费用、风险和规则 | `results/*.csv`、`selection.json` | Baseline与候选 | Operational / diagnostic | COMPLETE |
| 84天物理、费用、信息审计 | `audit_summary.json` | 独立审计 | Operational | COMPLETE |
| 334天滚动策略 | 未运行 | Fallback拟生成 | Operational | MISSING |
| 官方 `result4-3.xlsx` | 未生成 | Fallback拟生成 | Operational | MISSING |
| 四个指定日期完整论文表 | 仅覆盖窗口，未导出官方表 | Fallback拟生成 | Operational | PARTIAL |

Deliverable Verdict为 **PARTIAL**。最早未完成点是Stage C仅批准84天PoC，而不是合同误读、数据缺口或实现失败。Scientific Validity已经FAIL，扩大同一PoC不会增加新的识别信息，因此不建议Detailed PoC。下一步若继续，应修改执行范围为“用预声明Fallback完成334天最低交付”。

## 4. HUMAN MODEL GATE

- **Recommended delivery mode**：OPERATIONAL FALLBACK
- **推荐模型或规则**：`rolling_margin_mean14`
- **原因**：方向3在验证期和评估期的平均费用与CVaR90均劣于Baseline，并未优于时序打乱负对照；价格增强增益接近零。
- **Baseline**：PoC PASS，进入全年前仍是候选回退。
- **Candidate A**：Algorithmic PASS / Scientific FAIL。
- **Candidate B**：Algorithmic PASS / Scientific FAIL。
- **Main evidence**：72,576条执行记录、1,764个版本、两个保留期和时序打乱负对照。
- **Deliverable verdict**：PARTIAL。
- **Scientific validity verdict**：FAIL。
- **Required output artifacts**：334天策略、官方工作簿、指定日期表仍缺失。
- **Fallback used**：`rolling_margin_mean14`。
- **Workflow recheck result**：Stage C运行范围不足；不存在需要修复的合同、数据或算法错误。
- **Main unresolved risk**：84天回退结果能否在连续334天运行后保持费用与季度稳定性。
- **Why not alternatives**：方向3与价格增强均触发预声明证伪；联合场景已在问题4-2被负对照淘汰。
- **What would falsify this recommendation**：Fallback在全年运行中违反物理/信息边界，或不能生成官方输出。

由于Minimum Deliverable仍为PARTIAL，本门槛不提供PRIMARY或FALLBACK批准选项。允许的人工决定为：

- [ ] `MODIFY`：接受方向3科学目标失败，将下一阶段改为 `rolling_margin_mean14` 的334天操作性交付与官方工作簿。
- [ ] `REJECT`：停止问题4-3当前路线并重新定义候选。

在用户作出上述决定前不进入334天正式运行。
