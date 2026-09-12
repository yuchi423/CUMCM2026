# T-010 问题4-2参考方法 Detailed PoC 交接

- 时间：2026-09-12，Asia/Shanghai；分支：`codex/T-010-q4-price-poc`。
- 用户已批准本轮Detailed PoC，记录为 `memory/decisions/D-016-q4-reference-detailed-poc-approved.md`。
- 有效实验：`experiments/q4-reference-poc-20260912-02`；结果入口：`planning/q4-reference-poc-results.md`。

## 已完成

在问题2 `combined` 口径和0点普通计划均冻结的条件下，完成8种方法×84天测试，拆分净负荷—价格水平预测与0、6、12、18点价格更新下的储能价值控制。开发期按预声明0.5%近似并列规则仍选出 `mean7_greedy`。

净负荷形状法将日前价格MAE从0.080820降到0.050655，下降37.3%，但只改价格在开发期省235.98元、验证期增93.16元、评估期增2,429.79元，未形成稳定费用收益。价值控制三阶段分别增费7,871.31元、14,174.05元和8,549.08元；即使使用oracle价格仍比oracle贪心多30,080.29元，因此当前库存价值/储备下界实现被淘汰。此结论不外推到参考文稿未提供的第5.2节完整随机优化框架。

独立审计复算96,768条记录和672个方法—日期组合，物理、SOC、价格预测、日内更新、费用和汇总均通过；1,008项未来价格扰动通过，同时充放电0次、求解失败0次。三张PNG/PDF图已人工检查。

## 环境与恢复

模型使用本机 `D:\Users\python.exe`（Python 3.11.9、NumPy 2.4.2、SciPy 1.17.1、Matplotlib 3.10.8）；附件4只读解析使用Codex运行时Python与OpenPyXL 3.1.5。复现命令：

```powershell
D:\Users\python.exe final_run/q4_reference_poc/main.py experiments/q4-reference-poc-NEW --io-python C:\Users\14592\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe
```

## 当前判断与下一步

Algorithmic Verdict为PASS，Scientific Validity Verdict为FAIL，Deliverable Verdict为PARTIAL。完整334天连续结果和 `result4-2.xlsx` 尚未生成。停在Human Model Gate：没有第5.2节完整模型等新识别信息时，不对失败的价值控制器继续调参，维持D-015预声明的 `lag1` 操作性回退和附件1最终回退。

