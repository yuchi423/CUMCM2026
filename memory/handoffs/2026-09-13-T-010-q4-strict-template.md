# T-010 严格光伏优先修复及模板结果交接

- 时间：2026-09-13 +08:00
- 交出方：current user / Codex；分支：codex/T-010-q4-price-poc
- base_commit：bc3111f
- 目标：修复名义/历史场景PV优先，完成334天并按冻结规则交付原模板。

完成实验`experiments/q4-saa-strict-full-20260913-01`，运行3627.09秒，模型冻结提交3fb4bb9。5方法240480条执行账本、334名义计划、4676历史场景、1336信息扰动检查独立审计PASS。最大误差1.8233e-08，实际控制器/PV优先误差0，无失败/跳日。SAA费用门槛因第三季度FAIL，最终mean7；D-019及planning/q4-saa-strict-full-results.md为最新结果，撤回旧SAA PASS主张。

原模板填充`outputs/T-010-q4-strict/result4-2.xlsx`、workbook_audit.json、template_time_mapping.csv。仅3原表、原表头原格式保留，48096计划数值、2004四小时储能行及994紧急/无事件行逐格核对误差0。模板时段错位保持原文，用外部位置映射。未覆盖4-3新结果。新三张研究图已查看；图3的7日/14日标签靠近，不影响数值读取，正式排版可另调整。

环境：模型D:\Users\python.exe；IO/验证为bundled Python3.12.14、openpyxl3.1.5；Node/artifact-tool为bundled runtime。无需安装、更改系统环境。原始输入哈希见实验input_prices.json与audit_summary.json。

运行命令：`D:\Users\python.exe scripts/run_q4_saa_full.py experiments/q4-saa-strict-full-NEW --price-input experiments/q4-saa-full-20260912-01/input_prices.json`，随后运行独立audit及report脚本。填表命令见planning结果文档，不需重跑334天。

失败尝试：精确贪心补救MILP在60秒单日限制下无可行解，未采用；正式SAA仍为较理想化场景补救，仅保证物理/PV约束，不宣称完全因果树。Artifact Tool导出会规范化字体/边框，因此使用原ZIP包值替换保留格式；openpyxl仅只读审计。没有在运行后修改14日窗口、0.8分位或选择门槛。

无运行中模型进程。下一步阅读结果并将mean7与模板映射用于4-2论文；4-3若要继续，单独安排。shared-state检查及同步状态以最终提交消息为准。承载提交通过`git log -1 --format=%H -- memory/handoffs/2026-09-13-T-010-q4-strict-template.md`获取。
