# 问题1可运行口径测试包

此目录是测试复现入口，不是最终批准的数模结果。使用Python 3.12建立项目虚拟环境，安装environment/q1-test-requirements.txt，在仓库根运行：

```bash
python final_run/q1-contract-tests/main.py --output experiments/q1-contract-tests-<new-id>
```

实际模型实现在scripts/run_q1_tests.py，原始输入在data/。输出包含results/、figures/、model_report.md、run.json。拒绝覆盖既有运行目录。
E0=E24=6000 kWh；充放电效率各90%；先光伏供负载，再在功率/容量允许范围内充电，剩余弃光。A1比较末值、起值周期补点和功率梯形积分；A5比较标签修正与保留标签加映射，仅前者生成完整候选result1.xlsx。

更正后光伏优先吸收，但仍允许利用剩余功率和容量以网电补充充电。独立费用核验：python scripts/independent_q1_check.py --run experiments/<本次新ID>。最新结果为-03，-01/-02使用了额外禁购电约束，不能作为当前结果。
