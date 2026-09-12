# 问题4-2匹配价格—供需残差情景 Cheap PoC

该入口使用此前28个已结束日的匹配价格、负载和光伏预测残差，对六个满足问题2 `combined` 约束的普通购电计划进行情景回放选择。

```powershell
D:\Users\python.exe final_run/q4_joint_scenario_poc/main.py experiments/q4-joint-scenario-poc-NEW --io-python C:\Users\14592\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe
```

输出目录必须不存在。程序依次读取附件4快照、运行模型、独立重放情景评分并生成报告和PNG/PDF图。
