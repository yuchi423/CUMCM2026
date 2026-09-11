# 问题2全年一键运行

从仓库根目录运行，不覆盖旧结果目录：

```powershell
python final_run/q2/main.py --output experiments/q2-full-NEW
```

Python需要NumPy、SciPy、Matplotlib、OpenPyXL，实际版本见environment/q2-full-requirements.txt。Excel作者需要Node及`@oai/artifact-tool`；本机使用Codex提供的依赖路径，不改任何系统包。计算、输入审计与Excel可使用分开的运行时：

```powershell
& 'D:\Users\python.exe' final_run/q2/main.py --output experiments/q2-full-NEW --io-python 'C:\Users\14592\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' --node 'C:\Users\14592\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe' --node-modules 'C:\Users\14592\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\node_modules'
```

Node模块可通过本目录`node_modules`连接至既有artifact-tool依赖；F盘不支持junction时，使用`--node-modules`参数，入口会在系统临时目录建立任务专用连接并复制同一作者脚本执行。连接不进Git，也不修改包目录。若仅先运行数值、独立审计、图和报告，可加`--compute-only`，随后运行作者脚本及`python final_run/q2/check_workbook.py <运行目录>`完成Excel。

入口依赖仓库内已核验的`scripts/q2_core.py`、`scripts/prepare_q2_inputs.py`及PoC小实例测试；保留整个仓库即可复现。配置为`configs/q2_full.json`。报告与所有数值记录在指定运行目录，唯一Excel位于`outputs/T-006-<运行名>/result2.xlsx`，原始附件/模板不修改。

数值代码提交SHA、输入哈希与实际包版本在run.json。固定q全年结果只作参数比较；正式输出按2—4月0.8、5月起锁定选参q执行，切换时库存连续。审计与图表阶段可单独重跑，求解阶段拒绝覆盖现有运行目录。
