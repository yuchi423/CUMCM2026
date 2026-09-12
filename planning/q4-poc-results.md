# 1. Task Contract Check

## Minimum Deliverable

问题4-2需要在附件4逐日波动电价下重做问题2，形成2025年2月1日至12月31日的日前计划、实际执行、紧急购电、储能状态、总费用和 `result4-2.xlsx`。电价在0:00不可预知；当天计划只能使用此前已发生的价格，实际价格只用于对应交付时段结算。

## Scientific Interpretation Goal

在冻结问题2 `combined` 供需预测和储能口径后，验证正确对齐的历史价格信号能否形成可外推的经济收益，而不是由当天未来价格泄漏、价格错位、供需模型变化或少留期末库存造成表面节省。

## Fallback Deliverable

若历史价格方法不能稳定优于固定价控制组及错移负对照，使用前一日同刻价格 `lag1` 作为最简单因果交付基线，但不主张它一定节省费用；若 `lag1` 算法失败，再回退为附件1固定价格计划、附件4实际价格结算。该回退在PoC前已经批准。

# 2. Structural Screen

## Baseline

附件1固定日内价格曲线制定计划，在附件4真实价格上执行和结算。它不利用逐日价格历史，能够隔离“价格轨迹变化”和“主动使用价格预测”两种作用，结构成立，判定 `KEEP`。

## Candidate A

前一日同刻 `lag1`、过去7/14/28日同刻均值、过去35天同星期均值、14日指数加权均值均只读取过去价格，能够在0:00输出当天144点价格向量，判定 `KEEP`。其中 `lag1` 是批准的操作性回退。

## Candidate B

当天真实价格 `oracle` 只用于完美信息上界；它违反当前信息边界，不能成为可执行候选。前一日价格错移6小时只用于负对照，不能成为候选。

## Rejected Models

本轮不引入ARIMA、LSTM、Transformer或概率情景优化。附件4只有单年365条日曲线，简单方法尚未证明有稳定收益前，额外参数和调参空间没有证据支持。

# 3. Target Validity Gate

## Dual-Layer Target Contract

观测证据为附件4完整365天×144时段价格、附件2供需时序和问题2既有 `combined` 状态轨迹。主要干扰包括未来价格泄漏、价格时段错位、候选间供需口径不同、窗口初始SOC不同和期末库存差异。诊断采用未来价格整体扰动、6小时错位负对照、共同实际轨迹和库存调整费用。

## Candidate-level Signal Check

- 因果预测的信息边界：`PASS`。84天×9方法共756个未来扰动检查全部符合预期。
- 可执行模型与物理约束：进入PoC前为 `RISK`，运行后算法证据见第4节。
- “正确价格信号带来稳定经济收益”：进入PoC前为 `RISK`，预声明证伪条件是选中方法不能同时在validation和evaluation优于固定价及错位负对照。
- Fallback Status：`PREDECLARED`。

## HUMAN TARGET GATE

用户已于2026-09-12明确回复 `APPROVE DUAL TARGET AND FALLBACK`，批准记录见 [D-015](../memory/decisions/D-015-q4-target-approved.md)。

# 4. Cheap PoC

## PoC Plan

预声明协议见 [q4-poc-protocol.md](q4-poc-protocol.md)。比较9种方法，每种运行6个14天窗口：3月、4月用于开发选择，6月、8月用于验证，9月、12月用于评估。所有方法共用实际供需、实际结算价格、窗口初始SOC、0.8分位余量和1200kWh计划终端目标；跳过的日期不拼接SOC，因此本轮不是全年交付。

## Execution Results

`EXECUTED`。开发期原始最低库存调整费用来自14日同刻均值，为934,265.74元。根据预声明的0.5%近似并列与复杂度优先规则，7日同刻均值、14日均值、28日均值、同星期均值和指数均值进入近似并列集合，选出较简单的7日同刻均值 `mean7`。

| 方法 | 84天价格MAE | 日内Spearman | 库存调整费用/元 | 相对固定价/元 | 紧急购电/kWh | 已付未用/kWh |
|---|---:|---:|---:|---:|---:|---:|
| 附件1固定价 | 0.0959 | 0.925 | 3,654,397.04 | 0.00 | 8,447.8 | 343,240.4 |
| 前一日同刻 `lag1` | 0.0835 | 0.939 | 3,655,619.89 | +1,222.85 | 8,497.9 | 337,759.2 |
| 7日同刻均值 | 0.0808 | 0.952 | 3,643,634.08 | -10,762.96 | 8,441.1 | 334,243.0 |
| 14日同刻均值 | 0.0828 | 0.954 | 3,641,876.48 | -12,520.56 | 8,309.6 | 336,617.5 |
| 28日同刻均值 | 0.0870 | 0.950 | 3,645,132.95 | -9,264.09 | 8,438.5 | 342,111.0 |
| 同星期均值 | 0.0521 | 0.951 | 3,653,494.06 | -902.98 | 8,396.8 | 344,733.5 |
| 14日指数均值 | 0.0887 | 0.949 | 3,644,894.79 | -9,502.25 | 8,463.1 | 333,933.5 |
| 错移6小时负对照 | 0.4358 | -0.265 | 4,622,234.44 | +967,837.40 | 5,931.4 | 299,236.0 |
| 完美预知上界 | 0.0000 | 1.000 | 3,623,264.96 | -31,132.08 | 9,105.4 | 344,278.1 |

`mean7`在验证期比固定价少9,148.86元，在评估期比固定价多735.76元；它在两个阶段均明显优于错位负对照，但没有稳定优于固定价控制组。因此预声明的科学目标证伪条件已经触发。

逐时独立审计复算108,864条记录：能量平衡最大误差为3.41×10^-13，SOC递推最大误差为9.09×10^-13，计划审计最大误差为2.60×10^-10；同时充放电0次，求解失败0次。603个日计划直接通过LP审计，153个日计划由MILP复核/回退后通过。

- Algorithmic Verdict: **PASS**
- Scientific Validity Verdict: **FAIL**（未证明因果价格预测相对固定价有跨期稳定收益）
- Deliverable Verdict: **PARTIAL**（84天PoC完成；334天连续结果与正式Excel未生成）

## Fallback PoC

`EXECUTED`。`lag1`作为批准的最简单因果回退，在84天内算法与信息边界均通过，但库存调整费用比固定价控制组高1,222.85元，且评估期高4,766.18元。它只可作为操作性因果基线，不能用于宣称价格预测带来节省。

- Algorithmic Verdict: **PASS**
- Scientific Validity Verdict: **NOT REQUIRED**（回退只承担操作性交付）
- Deliverable Verdict: **PARTIAL**（尚未运行334天连续SOC并导出 `result4-2.xlsx`）

# 5. Evidence Comparison

| Dimension | 固定价控制组 | `lag1`回退 | `mean7`开发期选中 | 其他简单候选 |
|---|---|---|---|---|
| Task Fit | 可完成控制组计划 | 可执行因果计划 | 可执行因果计划 | 均可执行因果计划 |
| Minimum Deliverable Coverage | 84天，PARTIAL | 84天，PARTIAL | 84天，PARTIAL | 84天，PARTIAL |
| Confounder Diagnostic | 共同结算轨迹 | 未来扰动PASS | 未来扰动PASS、错位负对照 | 未来扰动PASS |
| Scientific falsification | N/A | 不主张收益 | 已触发 | 无一解除跨期不稳定问题 |
| Core PoC Metric | 3,654,397.04元 | 3,655,619.89元 | 3,643,634.08元 | 14日均值最低3,641,876.48元 |
| Sanity / Constraint | PASS | PASS | PASS | PASS |
| Robustness | 三阶段控制 | 验证期优、评估期劣 | 验证期优、评估期略劣 | 费用排序随阶段变化 |
| Interpretability | 高 | 最高 | 高 | 高 |
| Runtime risk | 低 | 低 | 低 | 低 |
| Competition utility | 可靠对照 | 最简单操作性回退 | 可讨论但不可定为最终 | 价格误差与成本不一致有分析价值 |

同星期均值拥有最低MAE，却没有最低调度费用，说明价格点预测精度不能替代决策费用评价。完美预知仅比固定价控制组降低31,132.08元，即0.85%，表明在当前 `combined` 供需余量与储能约束下，完善价格信息的理论空间本身较小。错移负对照增支96.78万元，证明日内价格位置确实影响调度，但不足以证明任一历史预测器具有稳定增益。

## Delivery Reconciliation

| Required Output | Actual artifact | Produced by | Claim level | Status |
|---|---|---|---|---|
| 价格方法比较 | 9方法×84天预测与费用表 | Primary + controls | Exploratory | COMPLETE |
| 因果信息检查 | 756个未来扰动检查 | Primary + fallback | Operational | COMPLETE |
| 物理与费用审计 | 108,864条逐时复算 | Primary + fallback | Operational | COMPLETE |
| 全年2—12月逐日结果 | 尚未运行334天连续SOC | N/A | Operational | MISSING |
| `result4-2.xlsx` | 尚未生成 | N/A | Operational | MISSING |

## Workflow Recheck

类型为 `OUTPUT_SCHEMA_FAILURE`：PoC按预声明停止条件有意停在84天证据比较，尚未覆盖题目要求的334天及Excel结构。Task Contract、数据支持和算法实现没有发现需要返回Stage A的问题。若用户选择 `RUN Detailed PoC`，应返回Stage C，固定比较 `lag1` 回退与附件1固定价控制组，运行334天连续SOC并生成正式输出；不得用后28天重新选择预测窗口。

# 6. Model Recommendation

- Recommended delivery mode: **OPERATIONAL FALLBACK（仅为当前建议，未完成正式交付）**
- Recommended model or rule: 前一日同刻价格 `lag1` 因果基线；附件1固定价为最终算法回退
- Why: `mean7`没有跨期稳定优于固定价，科学目标失败；`lag1`是预先批准、信息要求最低且最易解释的因果规则
- Deliverable verdict: **PARTIAL**
- Scientific validity verdict: **FAIL**（价格预测稳定节省的解释失败）
- Main target-validity evidence: 756个因果扰动检查通过；`mean7`评估期比固定价高735.76元
- Required output artifacts: [完整报告](../experiments/q4-price-poc-20260912-02/model_report.md)、[选择记录](../experiments/q4-price-poc-20260912-02/selection.json)、[独立审计](../experiments/q4-price-poc-20260912-02/audit_summary.json)、[汇总表](../experiments/q4-price-poc-20260912-02/results/summary_tables.csv)、[三张图](../experiments/q4-price-poc-20260912-02/figures/)
- Fallback used: 是，触发 `lag1` 操作性回退；尚未执行334天正式回退交付
- Unanswered required outputs: 334天连续计划、全年费用与 `result4-2.xlsx`
- Workflow recheck result: 返回Stage C详细运行/正式输出层，不修改目标合同
- Why not alternatives: 14日均值虽为84天数值最低，但与 `mean7`近似并列且同样未通过评估期；同星期均值预测误差最低但费用优势接近零；oracle不可执行；错位价格是负对照
- Main unresolved risk: 84天分段窗口不能代表全年连续SOC和季节变化
- Falsification condition: `lag1`在334天运行中出现信息泄漏、约束/费用审计失败，或无法生成正式输出时回退附件1固定价

# 7. HUMAN MODEL GATE

Recommended delivery mode: OPERATIONAL FALLBACK（当前交付仍为PARTIAL）

Human decision:

- [ ] MODIFY
- [ ] REJECT
- [ ] RUN Detailed PoC

由于完整334天结果和 `result4-2.xlsx` 尚未生成，本Gate不提供任何APPROVE选项。
