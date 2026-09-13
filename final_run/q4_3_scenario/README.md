# 问题4-3方向3一键运行

正式模型为`rolling_scenario_mean14`：问题3连续残差情景滚动骨架、0/6/12/18计划更新、上一版有效计划逐次调整费；问题4增加因果电价预测和真实价格结算。

在仓库根目录运行：

```powershell
python final_run/q4_3_scenario/main.py experiments/<新的运行ID>
```

运行目录必须不存在。入口依次生成全年结果、独立审计、PNG/PDF图、模型报告、官方`output/result4-3.xlsx`及工作簿回读审计。主Python需要NumPy、SciPy与OpenPyXL；绘图需要Pillow、ReportLab与NumPy；Excel导出使用`@oai/artifact-tool`。Codex桌面环境会自动发现内置绘图和Node依赖，其他机器可显式传入`--report-python`、`--node`和`--node-modules`。

若暂时没有Node环境，可加`--compute-only`完成数值、审计、图和工作簿数据载荷，之后再执行`workbook.mjs`和`scripts/check_q4_3_workbook.py`。
