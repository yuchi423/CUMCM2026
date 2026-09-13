# 第三、第四问论文证据映射

本文只采用本地已冻结并完成审计的结果，修订稿为`paper/微网电力调控_第三四问本地结果修订稿.tex`。

| 论文部分 | 主模型 | 数值来源 | 代码入口 |
| --- | --- | --- | --- |
| 问题三 | `rolling_scenario` | `deliverables/q3/summary_tables.csv` | `final_run/q3/main.py` |
| 问题4-2 | `mean7` | `experiments/q4-saa-strict-full-20260913-01/results/summary_tables.csv` | `final_run/q4_saa_full/main.py` |
| 问题4-3 | `rolling_scenario_mean14` | `deliverables/q4-3/summary_tables.csv` | `final_run/q4_3_scenario/main.py` |

主要修正：

- 删除没有对应实现的岭回归、光伏融合、k-medoids、非前瞻场景树和风险压降实验；
- 第三问改为最近8条连续残差路径的候选计划生成与因果回放评分；
- 第三问每次更新当日全部尚未执行区间，下一发布时点前只执行随后6小时；
- 4-2使用过去7个完整日的同刻均价预测，4-3使用过去最多14个完整日的同刻均价预测；
- 当天未来和次日真实电价均不进入决策，附件4真实价格只用于到期结算；
- 4-2保留带符号净负荷，富余光伏可以进入充电过程；
- 风险结论限于已计算的日费用CVaR90，不再以紧急购电总量替代尾部风险；
- 4-2与4-3起点和预测结构不同，不将两者费用差解释为日内预报收益。

执行`python scripts/check_paper_q3_q4.py`可核对论文中的主要结果、旧数字清理和附录代码路径。
