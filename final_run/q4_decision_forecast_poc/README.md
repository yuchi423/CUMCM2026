# 问题4-2决策导向电价候选 Cheap PoC

该入口比较固定—近期价格收缩组合、过去28日费用选择器、MAE选择器和逐日权重上界。所有可执行候选只读取已经结束日期的信息。

```powershell
D:\Users\python.exe final_run/q4_decision_forecast_poc/main.py experiments/q4-decision-forecast-poc-NEW --io-python C:\Users\14592\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe
```

输出目录必须不存在。程序依次读取附件4快照、运行PoC、独立审计并生成报告和PNG/PDF图。
