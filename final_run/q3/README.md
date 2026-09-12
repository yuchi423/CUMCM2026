# 第三问一键运行

在仓库根目录运行：

```text
python final_run/q3/main.py experiments/<新的运行ID>
```

入口依次运行控制组和三条模型路线、独立审计、报告生成。依赖沿用项目Python环境中的NumPy、SciPy、openpyxl和Matplotlib。输出目录必须不存在；`details/`保存完整逐时审计材料但按项目约定不进入Git待提交区，必要汇总、报告和图表进入待提交区。

模型、费用口径、预报映射和实验切分见`planning/q3-experiment-protocol.md`。结果未通过`audit_summary.json`前不能用于论文。

已选主模型为方向2 `rolling_margin`，结果入口：[方向2结果包](../../deliverables/q3/README.md)。从完成的运行目录提取：

```text
python scripts/package_q3_main.py experiments/<运行ID> deliverables/q3
```

生成完整Excel前，运行目录必须含 `details/rolling_margin_dispatch.csv.gz` 和 `details/rolling_margin_plan_versions.jsonl.gz`：

```text
python scripts/build_q3_workbook_data.py . experiments/<运行ID>
node final_run/q3/workbook.mjs .
python final_run/q3/check_workbook.py .
```

Excel保存到 `output/result3.xlsx`，审核记录为 `output/result3_audit.json`。Node作者需要 `@oai/artifact-tool`；可按 `final_run/q2/README.md` 的临时依赖连接方法运行，不修改系统环境。
