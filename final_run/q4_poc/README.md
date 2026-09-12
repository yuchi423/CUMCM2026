# 问题4-2因果电价预测 Cheap PoC

本入口冻结问题2 `combined` 的供需预测、0.8分位余量、储能参数和实际执行规则，只比较日前可获得的历史价格预测方法。真实当天价格只用于结算，oracle只作信息上界。

在仓库根目录运行：

```powershell
C:\Users\14592\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe final_run/q4_poc/main.py experiments/q4-price-poc-20260912-01
```

输出目录必须尚不存在。程序依次完成建模、独立复核和三张图的生成；任一步失败都会终止并返回非零状态。
