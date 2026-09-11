# 第三问一键运行

在仓库根目录运行：

```text
python final_run/q3/main.py experiments/<新的运行ID>
```

入口依次运行控制组和三条模型路线、独立审计、报告生成。依赖沿用项目Python环境中的NumPy、SciPy、openpyxl和Matplotlib。输出目录必须不存在；`details/`保存完整逐时审计材料但按项目约定不进入Git待提交区，必要汇总、报告和图表进入待提交区。

模型、费用口径、预报映射和实验切分见`planning/q3-experiment-protocol.md`。结果未通过`audit_summary.json`前不能用于论文。
