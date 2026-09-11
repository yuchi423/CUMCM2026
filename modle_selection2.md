基于 [model_selection1.md](D:/Desktop/CUMCM2026/planning/model_selection1.md) 的结构性结论是：

> **Baseline 确定性 LP 最适合做主模型；Candidate B 只适合作为出现退化解时的二级择优；Candidate A 应作为互斥条件无法由 LP 保证时的后备。**

这不是最终模型批准：本轮没有运行优化器或复核原始附件，文档中“已执行输入审计”等内容仅视为文件作者报告的证据。`PoC NOT EXECUTED`。

# 1. Task Contract Check

## Minimum Deliverable

问题1至少需要实际生成：

- 144个10分钟区间的购电量；
- 表1、表2要求的汇总数值；
- 全天购电量与购电费用；
- 逐时购电、充电、放电、盈余和储电量明细；
- 填写后的 `result1.xlsx`；
- 费用重算、能量平衡、储能边界和时间映射验证结果。

当前上述数值成果尚未生成，交付状态为 `MISSING`。

## Scientific Interpretation Goal

`NOT REQUIRED`。

这是给定预测条件下的确定性调度问题，不需要证明因果机制或真实天气下的运行性能。

## Fallback Deliverable

预声明的算法路线：

1. 先解 Baseline LP；
2. 若只出现可证明消除的同时充放电，优先代数消环；
3. 若同费用多解影响输出解释，再启用 Candidate B；
4. 若 A4 失败或额外硬物理约束要求离散模式，转 Candidate A；
5. 无储能方案只能作为费用和可行性对照，不能冒充最优调度。

# 2. Structural Screen

## Baseline：确定性 LP — `KEEP，主模型首选`

| 阵营 | 论点 |
|---|---|
| 蓝方辩护 | 直接输出购电与储能计划；模型线性，可获得全局最优解；速度快、稳定、容易复核和写入论文；不需要估计隐藏参数。 |
| 蓝方核心证据 | 在允许免费弃电、效率不超过1且没有额外模式限制时，文档给出的代数变换说明：任意同时充放电解都存在一个费用相同、储电轨迹相同的互斥解。 |
| 红方攻击 | LP只证明“存在”互斥最优解，求解器原始返回值仍可能同时充放电；若直接填表，物理解释会很难看。 |
| 红方致命条件 | 一旦增加弃电上限、弃电成本、特定来源弃电限制、非线性效率或模式约束，现有等价证明可能失效。 |
| 其他风险 | LP不能解决时间错位、kW/kWh换算、效率口径、初始库存和模板标签错误；这些错误甚至可能产生“费用很低”的假结果。 |

**裁决：** 在文件提出的 Q1-A1～Q1-A5 全部成立时，它是最简单、证据链最清楚的方案。

---

## Candidate A：显式互斥 MILP — `KEEP，硬约束后备`

| 阵营 | 论点 |
|---|---|
| 蓝方辩护 | 二元变量直接保证每个时段只能充电或放电；不依赖求解器选择哪个退化解；在 LP 等价证明失效后仍能保持模式互斥。 |
| 蓝方适用场景 | 弃电受限或有成本、充放电存在离散模式、引入启停规则，或者LP结果无法被可靠修复。 |
| 红方攻击 | 增加144个二元变量，需要报告最优性间隙、上下界和求解状态，计算及复现成本更高。 |
| 红方关键反驳 | 在当前假设下，MILP理论上不能比LP得到更低费用；它解决的是“解的形式”，不是经济目标上的缺陷。 |
| 红方盲点 | 即便使用MILP，时间、单位、效率、库存和盈余建模错误仍然存在；整数变量不会自动让账本正确。 |
| 额外限制 | 它只禁止同一时段同时充放电，并未限制频繁切换，也没有真正建立电池寿命模型。 |

**裁决：** 物理约束最强，但在当前线性条件下直接设为主模型属于复杂度证据不足。

---

## Candidate B：费用最优后的吞吐量择优 — `RISK，条件保留`

| 阵营 | 论点 |
|---|---|
| 蓝方辩护 | 在一级最低费用解集合中最小化总充放电量，可以清除冗余循环，使调度表更简洁、容易解释。 |
| 蓝方相对优势 | 通常只需连续优化，不增加144个二元变量；比MILP轻量，又比直接接受LP原始退化解更干净。 |
| 蓝方数学依据 | 在当前免费弃电条件下，如果存在同时充放电，可以保持费用与储电轨迹不变并降低吞吐量，因此二级目标应排除这种循环。 |
| 红方攻击 | 它不是独立主模型，而是依赖一级LP或MILP得到正确的 \(C^*\)；一级模型错了，二级优化只会把错误包装得更整洁。 |
| 红方关键风险 | 若使用 \(C\le C^*+\varepsilon_C\)，就不再严格保持最低费用，必须预先固定容差并报告实际费用增量。 |
| 红方解释边界 | 最小吞吐量只能称为“同费用解的择优规则”，不能声称已经优化电池寿命；它也不控制开关次数。 |
| 红方适用限制 | 如果一级解没有循环或解释歧义，Candidate B不会产生实质价值，第二次求解就是多余复杂度。 |

**裁决：** 它比 Candidate A 更适合处理“退化解不美观”，但不能取代 Baseline，也不能处理 LP 等价条件失效的问题。

## 红蓝对抗总表

| 比较维度 | Baseline LP | Candidate A MILP | Candidate B 二级择优 |
|---|---|---|---|
| 模型角色 | 主优化模型 | 硬约束后备 | 二级择优规则 |
| 主要目标 | 费用最小 | 费用最小并强制互斥 | 保持费用最优后减少吞吐 |
| 互斥保证 | 存在性保证，原始解未必互斥 | 直接保证 | 当前假设下可通过二级目标实现 |
| 复杂度 | 最低 | 最高 | 中等，需要两次求解 |
| 对口径变化稳健性 | 较低 | 最高 | 与LP证明条件相同 |
| 当前新增价值 | 完成核心任务 | 暂无证据证明必须使用 | 仅在退化或多解时存在 |
| 结构裁决 | `KEEP` | `KEEP` | `RISK` |

## 谁更合适

按当前文件中的假设，排序不是简单的 A/B/LP 三选一，而是：

1. **Baseline LP：首选主模型。**
2. **Candidate B：出现冗余循环或同费用多解时，作为LP的条件性增强。**
3. **Candidate A：LP互斥等价条件失效时的硬约束后备。**

具体来说：

- 若 Q1-A4“允许免费弃电”成立，并且LP解经检查没有异常：直接使用 **Baseline**。
- 若费用已最优，但原始解出现同时充放电、吞吐过大或多解影响论文表达：使用 **Baseline + Candidate B**。
- 若弃电存在限制或成本、模式互斥必须作为硬约束，或者消环前提不成立：使用 **Candidate A**。

因此，**当前不支持直接用 Candidate A 替换 Baseline，也不支持把 Candidate B 包装成独立模型**。

## Rejected Models

这三个方案暂不直接淘汰。Candidate B 为条件保留，而不是无条件进入实施。

# 3. Target Validity Gate

## Dual-Layer Target Contract

| 字段 | 当前建议 |
|---|---|
| Minimum Deliverable Target | 在统一口径下生成完整144时段调度、表1/2、费用、明细和结果文件 |
| Scientific Interpretation Target | `NOT REQUIRED` |
| Observable Evidence | 144时段电价、负载、光伏预测和题给储能参数 |
| Scientific Target–Observable Link | `NOT REQUIRED`；给定预测不能自动代表真实运行 |
| Major Nuisance / Confounders | 时间错位、功率与电量换算、效率方向、免费初始库存、盈余处理、循环充放电 |
| Planned Diagnostic | 独立重算费用和能量；检查储能边界与初末状态；小实例LP/MILP对照；检查消环前后不变性；核验Excel时间映射 |
| Scientific Target Falsification | `NOT REQUIRED` |
| Algorithmic Falsification | 任一无法解释的能量违例、费用不一致、时间错位，或小实例无法与精确解一致 |
| Fallback Trigger | LP互斥修复不成立，或 Q1-A4 等条件发生改变 |
| Fallback Deliverable | 保持同一题目输出和经济目标，转入互斥MILP；不得降低物理验收标准 |

## Candidate-level Signal Check

| 项目 | Baseline | Candidate A | Candidate B |
|---|---|---|---|
| 使用信号 | 价格、净负荷、储能状态 | 同左，加模式变量 | 一级最优集合中的冗余吞吐 |
| 正常预期 | 可行且费用不高于无储能对照 | 费用不低于LP下界且严格互斥 | 费用不变或在预设容差内，吞吐量下降 |
| 干扰主导表现 | 低费用依赖单位、库存或盈余漏洞 | 互斥正确但经济账本仍可能错误 | 将错误的一级解整理成“漂亮结果” |
| Diagnostic status | `PLANNED` | `PLANNED` | `PLANNED` |
| Deliverable capability | `PARTIAL` | `PARTIAL` | `PARTIAL` |
| Scientific validity | `NOT REQUIRED` | `NOT REQUIRED` | `NOT REQUIRED` |
| Operational verdict | `RISK` | `RISK` | `RISK` |
| Fallback status | `PREDECLARED` | 后备方案 | 保留一级结果 |

# HUMAN TARGET GATE

- **Task：** 问题1单日储能购电优化。
- **Minimum deliverable target：** 144区间调度、表1/2、费用、明细和结果副本。
- **Scientific interpretation target：** `NOT REQUIRED`。
- **Observable evidence：** 文件报告的144条预测输入与储能参数。
- **Major nuisance/confounders：** 单位、效率、时间标签、初末库存、盈余和循环充放电。
- **Planned diagnostic：** 独立账本重算、小实例精确对照、LP/MILP比较、消环不变性及模板映射检查。
- **Scientific target falsification：** `NOT REQUIRED`。
- **Fallback trigger：** LP物理互斥不能成立或其证明前提被修改。
- **Fallback selection rule：** Baseline → 必要时Candidate B → 条件失效时Candidate A。
- **AI target-validity verdict：** 科学目标 `NOT REQUIRED`；操作目标 `RISK`。
- **Main unresolved ambiguity：** Q1-A1～Q1-A5尚未由你确认，尤其是效率口径、免费弃电和时间标签。
- **Current state：** `PoC NOT EXECUTED`，数值交付 `MISSING`。

Human decision：

- [ ] **APPROVE DUAL TARGET AND FALLBACK**：批准上述口径与回退顺序，允许进入Cheap PoC。
- [ ] **MODIFY TARGET**：指出需要修改的Q1-A条目或回退顺序。
- [ ] **REJECT TARGET**。

<oai-mem-citation>
<citation_entries>
MEMORY.md:20-26|note=[used only to avoid inferring unverified team role responsibilities]
</citation_entries>
<rollout_ids>
</rollout_ids>
</oai-mem-citation>
