# 输入可用性

本检查点包含10份原始输入：problem/C题.pdf、data/raw/下4份数据、data/templates/下5份空白模板；并包含data/structure-audit.json（9个工作簿、21个工作表的结构记录）。模板不是求解结果。
所有输入随任务分支 `codex/T-001-yuchi-model-planning` 提交。队友须fetch并切到该分支、pull --ff-only，再在仓库根执行 `python scripts/check_inputs.py`，预期PASS（10份），以及 `python scripts/check_state.py`。
文件来源、相对路径和SHA256见data/MANIFEST.csv；不依赖交出方桌面。原件只读，清洗或填表另存。尚未完成数据质量审计、模型求解与正式数值结果。

2026-09-10用户明确说明仓库为private并授权上传。GitHub仓库API同时核实private=true，原始输入上传授权阻塞已解除，记录见memory/decisions/D-003-original-input-upload.md。
上一检查点204f7de仅同步文档；本检查点补入原始输入。最终远端同步以推送成功和本地/远端SHA一致为准。
