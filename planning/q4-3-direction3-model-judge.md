# 问题4-3方向3规划与 Model Judge 目标门槛

日期：2026-09-13
任务：T-012-q4-3-model-judge
状态：Stage A / Stage B 已完成；Cheap PoC **NOT EXECUTED**。

本文替代 `planning/task4-task5-decision.md` 中关于 Task 5 采用方向2的旧规划。问题3当前正式候选为方向3 `rolling_scenario`；问题4-2已选择 `saa_load`，其结果支持供需场景，但不支持价格—供需联合机制。

## 1. Task Contract Check

- **Goal**：在逐日波动且当天未来真实价格不可提前读取的条件下，完成问题3在问题4-3中的滚动购电、储能调度和费用结算。
- **Minimum Deliverable**：对2025-02-01至12-31共334天生成0:00初始计划、6:00/12:00/18:00调整计划、储能与紧急购电结果，填入官方 `result4-3.xlsx`；给出普通购电费、逐次调整费、5倍紧急购电费、期初/期末储电量及物理审计。
- **Scientific Interpretation Goal**：证明连续净负荷残差轨迹所保留的时间相关性，在逐日波动电价下能稳定降低真实结算的平均费用或高费用尾部风险；若加入日内价格更新，还需证明收益来自决策时已经观测到的价格信息。
- **Intended Target / Structure**：0:00、6:00、12:00、18:00的因果滚动优化；过去时段冻结；每次调整与上一版有效计划比较；真实储电量跨日连续。
- **Observable Evidence**：附件2实际负荷与光伏、附件3各发布时间的光伏预报、附件4逐日实际电价、决策日前历史价格、历史净负荷预测残差、当前SOC和上一版计划。
- **Inputs**：上述附件与问题3方向3、问题4-2已审计账本。
- **Unknowns / Decision Variables**：各版本尚未执行时段的普通购电量、充放电量、弃光量、SOC及必要的紧急购电补救量。
- **Required Outputs**：完整全年工作簿、四个题目指定日期的论文表、费用分解、风险指标、审计摘要和可复现入口。
- **Fallback Deliverable**：使用14日历史同刻均价作为当天价格预测，沿用方向2 `rolling_margin` 的0.8分位余量和同一因果执行器，仍完成全部题目输出，但不主张连续情景或日内价格更新具有额外价值。
- **Constraints**：当天尚未观察的真实价格不得进入计划；光伏先供负荷、再充电、再弃光；网电可充电；充放电单程效率均为90%；SOC为1200—10800kWh；充放电功率上限5000kW；计划终端SOC不低于1200kWh；实际SOC不按日重置。
- **Evaluation Criteria**：现金总费用、库存修正费用、日费用CVaR90、紧急购电量、已付未用网电、调整费、季度稳定性、物理/账本/信息边界审计和运行时间。
- **Dependencies**：T-011问题3方向3结果包与T-010问题4-2 SAA结果。

本方向使用线性规划或必要时的MILP生成候选计划，并用场景回放评分，**不使用动态规划DP**。

## 2. 方向3在问题4-3中的具体模型

设日期为 \(d\)，10分钟时段为 \(t\)，发布时间 \(k\in\{0,6,12,18\}\)。决策时可用的价格预测记为 \(\hat p^{(k)}_{d,t}\)，真实结算价格为 \(p_{d,t}\)。

### 2.1 信息和时间边界

1. 0:00只使用前14个已结束日的同刻价格均值形成当天价格曲线；当天未来真实价格只用于事后结算。
2. 6:00、12:00、18:00读取最新光伏预报、实际SOC和上一版计划，只修改当天尚未执行时段。
3. 主候选中价格预测全天保持0:00版本；日内价格更新单列为增强候选，不能混入主候选后再归因。
4. 为与已选定的问题3方向3结果一致，主实验的优化窗口截止当天24:00。跨午夜24小时前瞻仅可作为后续敏感性，不进入本轮模型选择。

### 2.2 连续残差场景

负载预测沿用问题3的过去35天同星期均值，样本不足时退回过去7天均值。光伏采用当前发布时间的最新预报。每次发布时间保留最近8条连续净负荷预测残差轨迹，保持同一路径内的时间顺序。

对点预测计划、0.8分位余量计划、各残差路径计划和“保持上一版计划”进行统一的因果执行回放。候选计划在每条情景中固定，不允许场景内电池预先看见未来后分别采取理想动作。

候选评分为

\[
J^{(k)}=\overline C^{(k)}+0.25\left(\operatorname{CVaR}_{0.9}^{(k)}-\overline C^{(k)}\right),
\]

其中情景费用按决策时可用的 \(\hat p^{(k)}_{d,t}\)计算普通购电、预计调整、紧急购电和统一的终端库存修正。选择评分最低的候选；“保持上一版计划”保证模型可以拒绝不值得的调整。

### 2.3 真实费用账本

最终真实费用为

\[
C_d=\sum_t p_{d,t}g^{\mathrm{final}}_{d,t}
+\sum_k\sum_{t\ge k}0.5p_{d,t}\left|g^{(k)}_{d,t}-g^{(k-1)}_{d,t}\right|
+\sum_t5p_{d,t}b_{d,t}.
\]

每次调整只和上一版尚未执行计划比较，例如 \(100\to80\to90\) 依次结算20和10的调整量，不能在12:00重新与0:00版本比较。计划选择时使用可用价格预测估计费用，实际交付后再使用附件4真实价格复算。

## 3. Stage A — Structural Screen

| 方案 | 结构与作用 | 关键证据/风险 | 裁决 |
| --- | --- | --- | --- |
| Delivery Baseline `rolling_margin_mean14` | 14日历史同刻均价 + 问题3方向2的0.8分位余量滚动调整；可独立生成完整 `result4-3.xlsx` | 简单、可复现；不能支持“连续误差结构有额外价值”的结论 | **KEEP** |
| Candidate A `rolling_scenario_mean14` | 14日历史同刻均价 + 问题3方向3连续残差情景和CVaR评分 | 问题3同结构已有334天审计，问题4-2供需场景全年有效；仍需验证在波动电价和调整费下的增益 | **KEEP** |
| Candidate B `rolling_scenario_price_update` | Candidate A基础上，6/12/18仅用当天已实现价格对剩余价格预测做低维偏差修正 | 可能利用实时信息，也可能只追随日内共同趋势；题面未明确日内价格信息发布机制 | **RISK** |
| Rejected `joint_price_netload_scenario` | 对价格误差与供需误差按历史日期配对，构造联合场景 | 问题4-2中匹配联合场景未优于错配负对照，且费用不优于只用供需场景 | **REJECT** |

Stage A保留一个Delivery Baseline和两个候选。联合场景只有出现新的可观察配对信息或新的负对照证据时才可重启。

## 4. Stage B — Dual-Layer Target Validity Gate

### 4.1 双层目标契约

- **Minimum Deliverable Target**：在严格信息边界和物理约束下完成334天问题4-3滚动策略、官方工作簿和完整费用账本。
- **Scientific Interpretation Target**：连续残差轨迹保留的时间相关性，在波动电价下比逐时分位余量更好地控制真实平均费用或CVaR90；日内价格增强若被采用，其增益来自当时已观测价格，而不是未来信息。
- **Observable Evidence**：时间有序的历史残差轨迹、决策日前价格、日内已实现价格前缀、最新光伏预报、实际SOC、真实执行与结算结果。
- **Scientific Target–Observable Link**：若时间相关性真实有用，保持连续路径的Candidate A应在保留期优于只保留逐时分位数的Baseline，并优于打乱路径时序的负对照；若已实现价格能修正未来价格，Candidate B应在不读取未来价格的前提下优于Candidate A。
- **Major Nuisance / Confounders**：未来真实电价泄漏；期末库存差异；多买弃用换取少量紧急购电；真实SOC反馈被误算为光伏或价格更新收益；重复使用全年结果调参；路径样本少导致偶然择优；日内价格共同趋势造成伪改进。
- **Confounder Diagnostic**：未来价格扰动不变性；同一实际轨迹和相同初始SOC下的 `no_update → state_only → rolling_margin → rolling_scenario → price_update` 消融；打乱每条残差路径时间顺序的负对照；现金与库存修正费用同时报告；开发/验证/评估分离及分季度复算。
- **Scientific Target Falsification**：Candidate A不能在验证期和评估期稳定优于Baseline，或不能优于时序打乱负对照；Candidate B的计划随尚未观测的未来价格变化，或其收益不能与只更新SOC/光伏区分；节省在库存修正后消失且风险电量恶化。
- **Fallback Trigger**：任一信息泄漏或物理审计失败；Candidate A在验证期或评估期未达到预声明门槛；Candidate B未相对A产生独立增益；关键价格信息边界无法从题面或团队假设中固定。
- **Fallback Deliverable**：退回 `rolling_margin_mean14`，按同一真实价格、执行器和账本生成全年结果。模型只表述为“历史均价下的稳健滚动调度”，不声称连续场景或价格更新机制已被识别。

### 4.2 候选信号检查

| 模型 | 使用信号 | 目标真实时的预期 | 干扰主导时的预期 | 诊断 | 交付能力 | 科学有效性 | 回退 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Baseline | 14日历史价格均值、逐时0.8分位残差、最新PV与SOC | 能形成保守可执行计划 | 费用未必最优，但仍完成题目输出 | PLANNED | READY | NOT REQUIRED | NOT REQUIRED |
| Candidate A | 最近8条连续净负荷残差路径及尾部费用 | 保留期均值/CVaR改善，且优于时序打乱对照 | 只因样本偶然性择优，留出期消失 | PLANNED | READY | **RISK** | PREDECLARED |
| Candidate B | Candidate A + 当天已实现价格前缀 | 剩余价格预测与费用进一步改善 | 仅追随日内趋势或发生未来泄漏 | PLANNED | READY | **RISK** | PREDECLARED |

所有诊断均为 **PLANNED / NOT EXECUTED**。现有问题3与问题4-2结果只能证明组成模块可运行，不能替代问题4-3的目标有效性检验。

### 4.3 预声明选择门槛

Cheap PoC获批后，沿用问题4既有的六个14日窗口，共84天：开发期28天、验证期28天、评估期28天。开发期只用于实现检查和固定候选，不根据验证/评估结果改参数。

- Candidate A相对Baseline，须在验证期和评估期分别满足：库存修正费用至少降低0.1%；或者日费用CVaR90至少降低1%，且现金支出不得恶化0.1%以上。
- Candidate B相对Candidate A使用同一门槛，并必须通过“只扰动未观测未来价格时计划不变”的检查。
- 任一候选必须通过费用复算、能量守恒、SOC、功率、互斥、跨日连续和调整版本链审计。
- 若复杂候选未过门槛，按复杂度从低到高回退，不事后改阈值；PoC不会生成正式 `result4-3.xlsx`。

## 5. HUMAN TARGET GATE

- **Task**：问题4-3，在未来真实电价不可提前读取的条件下，将问题3方向3推广到逐日波动电价。
- **Minimum deliverable target**：334天滚动计划、储能/紧急购电、真实费用账本、官方 `result4-3.xlsx` 和审计结果。
- **Scientific interpretation target**：连续残差路径能在波动电价下稳定改善平均费用或CVaR90；日内价格增强的收益必须来自已观测价格。
- **Observable evidence**：历史价格、连续净负荷残差、光伏滚动预报、当前SOC、上一版计划、实际执行和结算。
- **Major nuisance / confounders**：未来价格泄漏、库存差、过购弃用、状态反馈混杂、时序过拟合、日内共同趋势。
- **Planned confounder diagnostic**：未来信息扰动、分层消融、残差时序打乱负对照、库存修正、分期和季度稳定性。
- **Scientific target falsification**：复杂候选不优于简单基线/负对照，或任何计划依赖尚未观测的未来价格。
- **Fallback trigger**：信息/物理审计失败，或保留期未达到0.1%费用改善或等价的1%尾部改善门槛。
- **Fallback deliverable and selection rule**：退回 `rolling_margin_mean14` 完成所有输出；复杂度只在双保留期通过预声明门槛时升级。
- **AI target-validity verdict**：**RISK**。最小交付结构成立；方向3的跨题增益及日内价格更新价值尚未在问题4-3数据上验证。
- **Main unresolved ambiguity**：题面是否允许6:00/12:00/18:00使用当天已实现价格来修正剩余价格预测。当前主候选不依赖该假设，Candidate B依赖。

Human decision:

- [ ] `APPROVE DUAL TARGET AND FALLBACK`
- [ ] `MODIFY TARGET`
- [ ] `REJECT TARGET`

在用户明确批准前不执行Cheap PoC。
