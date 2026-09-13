# Task 4 strict-PV-priority SAA full run

名义层、全部历史场景和实际执行层统一执行“光伏先供负荷、余光尽量充电、达到功率或容量上限后才弃光”，并显式保证同一时段充放电互斥。

```powershell
D:\Users\python.exe final_run/q4_saa_full/main.py experiments/q4-saa-strict-full-20260913-01 --io-python C:\Users\14592\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe --model-python D:\Users\python.exe
```

The command runs the frozen 334-day model, independent audit, and report generator. The output directory must not exist before the run.
# 严格修复后的交付说明

本入口在模型、独立审计、报告之后，按selection.json选定方法填充原result4-2.xlsx模板，输出至outputs/<实验目录名>/result4-2.xlsx。IO Python需已有openpyxl（只读验证）；不会安装依赖或修改模板表头、原格式。20260913正式结果科学选择门槛FAIL，最终交付mean7，最新结果见planning/q4-saa-strict-full-results.md；旧SAA PASS结论已撤回。计划表原时段标签错位10分钟，保留表头并输出外部template_time_mapping.csv。
