# 问题4.3已完成成果总账

日期：2026-09-13
任务：T-012-q4-3-model-judge
分支：'codex/T-012-q4-3-model-judge'
当前主方案：'rolling_margin_mean14'
中文名：14日同刻均价预测的余量滚动方案

## 1. 当前口径

- 问题位置是问题4内部的4.3，不是独立任务5；
- 当天未来真实电价不可提前读取；
- 每天0:00只用前14个完整日期的同刻价格预测当天144个时段；
- 预测价用于计划，真实价只用于普通购电、逐次调整和紧急购电结算；
- 0:00、6:00、12:00、18:00按最新光伏预测、实际SOC和负荷余量滚动更新未执行计划；
- D-023是当前价格口径，取代D-022的已知价格假设。

## 2. 已完成并上传：模型选择与口径

| 成果 | 路径 | 状态 |
| --- | --- | --- |
| 方向3目标与候选审查 | 'planning/q4-3-direction3-model-judge.md' | 已上传 |
| 目标门槛决定 | 'memory/decisions/D-020-q4-3-direction3-target-gate.md' | 已上传 |
| 模型裁决 | 'memory/decisions/D-021-q4-3-model-gate.md' | 已上传 |
| 已知价格旧口径 | 'memory/decisions/D-022-q4-3-known-price-delivery.md' | 已上传，仅保留历史 |
| 价格预测现行口径 | 'memory/decisions/D-023-q4-3-price-forecast-recheck.md' | 已上传，取代D-022 |

裁决结论：

- 连续残差场景Candidate A未通过科学有效性门槛；
- 日内价格前缀更新Candidate B未达到预声明增益门槛；
- 正式操作性方案采用 'rolling_margin_mean14'。

## 3. 已完成并上传：84天模型筛选

### 3.1 代码与配置

- 'configs/q4_3_poc.json'
- 'scripts/q3_shared.py'
- 'scripts/run_q4_3_poc.py'
- 'scripts/audit_q4_3_poc.py'
- 'scripts/report_q4_3_poc.py'
- 'final_run/q4_3_poc/main.py'
- 'final_run/q4_3_poc/README.md'

### 3.2 证据与结论

- 'experiments/q4-3-poc-20260913-01/run.json'
- 'experiments/q4-3-poc-20260913-01/selection.json'
- 'experiments/q4-3-poc-20260913-01/audit_summary.json'
- 'experiments/q4-3-poc-20260913-01/model_report.md'
- 'experiments/q4-3-poc-20260913-01/results/'
- 'planning/q4-3-poc-results.md'

84天共完成六种策略、72576条10分钟执行记录和1764个计划版本。物理、费用、版本链、未来价格后缀扰动和时序打乱边际检查均通过。

## 4. 已完成并上传：334天正式回放

### 4.1 代码与入口

- 'configs/q4_3_full.json'
- 'scripts/run_q4_3_full.py'
- 'scripts/audit_q4_3_full.py'
- 'scripts/report_q4_3_full.py'
- 'final_run/q4_3_mean14/main.py'
- 'final_run/q4_3_mean14/README.md'

代码基线提交为 'd87f35f0860280ab1998008335ed4d8eaa538a21'。

### 4.2 可追溯实验

正式实验目录：'experiments/q4-3-full-20260913-02'

已上传：

- 'run.json'：记录代码提交、环境版本、配置和输入哈希；
- 'preflight_checks.json'：记录价格信息边界与调整费预检查；
- 'completion.json'：记录运行范围和完成状态；
- 'audit_summary.json'：记录独立审计；
- 'results/daily_summary.csv'：334天逐日汇总；
- 'results/monthly_summary.csv'：月度汇总；
- 'results/quarterly_summary.csv'：季度汇总；
- 'results/periods.csv'：开发、验证、评估期汇总；
- 'results/paper_dates_summary.csv'：四个指定日期汇总；
- 'results/price_metrics_daily.csv'：逐日电价预测误差；
- 'results/update_summary.csv'：1336次计划发布记录；
- 'results/summary_tables.csv'：全年核心指标。

为控制仓库体积，以下可由同一代码和输入重建，不上传Git：

- 'details/' 下的48096条逐时段明细和完整计划版本；
- 'input_prices.json' 价格副本；
- 可选图表文件。

### 4.3 全年核心结果

| 指标 | 数值 |
| --- | ---: |
| 天数 | 334 |
| 普通购电量 | 21078207.863158 kWh |
| 紧急购电量 | 44662.237362 kWh |
| 普通购电费 | 13763849.007598元 |
| 调整费 | 619471.945026元 |
| 紧急购电费 | 220141.344562元 |
| 总现金费用 | 14603462.297186元 |
| 库存修正费用 | 14603819.334775元 |
| 初始SOC | 2119.622103 kWh |
| 期末SOC | 1595.933627 kWh |
| 计划更新 | 1002次 |
| 实际改变计划 | 862次 |

独立审计结果：

- 48096条逐时段记录：PASS；
- 1336个计划版本：PASS；
- SOC、功率、能量守恒和跨日连续性：PASS；
- 普通购电、调整、紧急购电费用重算：PASS；
- 决策价格、结算价格和未来价格信息边界：PASS；
- 总费用重算最大误差：1.86×10^-9元。

## 5. 已完成并上传：复核与交接

- 完整求解过程：'planning/q4-3-full-solution-process.md'
- 任务状态：'memory/tasks/T-012-q4-3-model-judge.json'
- Model Judge交接：'memory/handoffs/2026-09-13-T-012-q4-3-model-judge.md'
- 全年方案交接：'memory/handoffs/2026-09-13-T-012-q4-3-full-local-review.md'
- 本成果总账：'planning/q4-3-completed-artifacts.md'

队友应先阅读本总账和完整求解过程，再读取D-023、正式实验的 'run.json' 与 'audit_summary.json'。

## 6. 尚未完成，不列入已完成成果

以下内容没有作为已完成成果上传：

1. 官方 'output/result4-3.xlsx'：尚未生成和验证；
2. 论文正式图表：按用户此前要求暂缓；
3. 本地 'scripts/package_q4_3_full.py' 与 'final_run/q4_3_mean14/workbook.mjs'：尚未运行和验收；
4. 'experiments/q4-3-full-20260913-01'：早期重复运行，代码提交标记不准确，已被可追溯的 '-02' 实验取代。

这些项目完成前，T-012继续保持 'in_progress'，不标记为done。

## 7. 队友恢复方式

    git fetch origin
    git switch codex/T-012-q4-3-model-judge
    git pull --ff-only
    python final_run/q4_3_mean14/main.py experiments/q4-3-full-<new-id>

默认入口运行334天推算与独立审计。只有明确需要图表时才增加 '--with-report'。
