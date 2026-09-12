# Task 4 strict-PV-priority SAA full run

名义层、全部历史场景和实际执行层统一执行“光伏先供负荷、余光尽量充电、达到功率或容量上限后才弃光”，并显式保证同一时段充放电互斥。

```powershell
D:\Users\python.exe final_run/q4_saa_full/main.py experiments/q4-saa-strict-full-20260913-01 --io-python C:\Users\14592\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe --model-python D:\Users\python.exe
```

The command runs the frozen 334-day model, independent audit, and report generator. The output directory must not exist before the run.
