# T-012 问题4-3方向3全年结果交接

- 时间（含时区）：2026-09-13，Asia/Shanghai
- 交出方 / 预期接手方：Codex / 用户与团队论文组
- 任务文件 / 当前分支：`memory/tasks/T-012-q4-3-model-judge.json` / `codex/T-012-q4-3-model-judge`
- base_commit（本阶段开始时的真实 SHA）：`4a4aa62bb74b0abd29b92e3b942a00d25888851f`
- 目标和验收条件：以问题3方向3的连续残差情景滚动模型完成问题4-3全年因果回放、独立审计、官方工作簿和可复现交付。

## 当前事实

- 主模型为 `rolling_scenario_mean14`，取代旧 `rolling_margin_mean14` 作为问题4-3方向3主线；旧方案保留作对照。
- review确认原全年CVaR90汇总漏算边界样本概率质量。修正代码检查点为`536c11b46a4637f9b865efd327e124acf09f5921`，新运行目录为`experiments/q4-3-scenario-full-20260913-02`。
- 334天现金总费用为14,432,279.80元，库存修正费用为14,432,805.82元；相对旧余量基线分别节省171,182.49元和171,013.52元。
- 修正后的334天日费用CVaR90为73,458.35元；旧值73,323.89元撤回。修正前后逐日账本、计划版本、SOC和总费用一致，评分浮点差不超过7.28e-12。
- 独立审计通过：52,560条执行记录、1,460个计划版本；物理约束、费用、SOC连续性、价格因果边界、版本链、工作簿映射均PASS。
- 官方模板工作簿为 `output/result4-3.xlsx`，四张表顺序和官方表头保持不变；表格内容只填数据，计划与调整表使用模型slot 0—143的物理顺序。

## 已完成及证据

- `planning/q4-3-direction3-full-results.md`：方向、口径、全年结果和限制；
- `experiments/q4-3-scenario-full-20260913-02/model_report.md`：修正后模型报告、验证、失败边界和敏感性路线；
- `experiments/q4-3-scenario-full-20260913-02/results/*.csv`：修正后全年汇总、逐日、分期和论文日期数据；
- `experiments/q4-3-scenario-full-20260913-02/figures/`：三张PNG/PDF图；
- `memory/decisions/D-025-q4-3-review-corrections.md`：五点review的接受范围与论文表述边界；
- `output/result4-3.xlsx`：官方结果模板填数版本；
- `final_run/q4_3_scenario/main.py`：一键入口；`scripts/check_q4_3_workbook.py`：工作簿检查。

## 未提交的本地缓存

新运行的`details/`、`input_prices.json`、`workbook_data.json`、四张预览图和 `.inspect.ndjson` 是可由实验入口或工作簿审计重建的缓存，不放入提交；不影响结果包。若论文需要逐槽账本，先运行一键入口重建，不要手工修改官方工作簿。

## 恢复与下一步

环境可用版本：Python 3.13.14、NumPy 2.5.3、SciPy 1.18.1、openpyxl 3.1.5；工作簿生成需要仓库附带的Node运行时与 `artifact-tool`。在仓库根目录执行：

```text
python final_run/q4_3_scenario/main.py --help
python final_run/q4_3_scenario/main.py <new-run-dir>
```

下一步是人工审核任务分支中的模型假设与论文表述，然后由用户发起合并；当前不合并 `main`。

承载本记录的提交：`git log -1 --format=%H -- memory/handoffs/2026-09-13-T-012-q4-3-direction3-full-results.md`。
