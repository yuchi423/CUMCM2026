# 问题4中的4.3：14日同刻均价预测余量滚动模型

该入口默认运行334天正式推算和独立审计。模型每天0:00只用前14个已结束日的同刻电价均值预测当天电价；附件4当天真实价格只用于结算。0:00、6:00、12:00、18:00的光伏预报、SOC反馈、余量和调整费规则沿用问题3。

在仓库根目录使用含NumPy、SciPy、openpyxl和Matplotlib的项目Python运行：

    python final_run/q4_3_mean14/main.py experiments/q4-3-full-<unique-id>

如需同时生成报告和图表，显式增加：

    python final_run/q4_3_mean14/main.py experiments/q4-3-full-<unique-id> --with-report

输出目录必须不存在。官方工作簿待团队确认初始SOC、价格信息边界和时段映射后再生成。
