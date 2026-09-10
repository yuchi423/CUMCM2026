# 运行环境

共享状态检查：Python 3.10+，仅标准库，命令 `python scripts/check_state.py`。

建模环境尚未确定。采用 Python 后保存经过实际运行的依赖版本/锁文件；采用 MATLAB 时记录 release、toolbox 和入口命令。新成员按此处恢复环境，不提交 .venv，不依赖个人绝对路径。记录安装、最小运行与结果验证命令，未验证平台明确标注。

## 本次输入检查

只读xlsx结构脚本在当前机器Python运行时、openpyxl 3.1.5下执行。队友如需重新生成结构报告，在自己的环境中安装 `python -m pip install -r environment/intake-requirements.txt`，然后在仓库根执行 `python scripts/inspect_inputs.py`。
输入哈希检查：`python scripts/check_inputs.py`，仅标准库。该检查验证文件传输一致性，不验证模型或数据质量。
本轮仅调用已有运行时，没有安装求解器或改动系统环境；不把此输入检查依赖当成建模环境锁文件。
