# 第一问独立论文源稿

入口：[question1.tex](question1.tex)。只需此文件即可上传Overleaf，编译器选择XeLaTeX并编译两次；本次未安装编译器、未生成PDF，分页、表格宽度和字体呈现尚未做渲染验证。

内容：问题分析、时间映射、符号与假设、能量和库存约束、严格光伏优先与互斥、LP下界和MILP核对、表1/2、两幅内嵌数据曲线、时间解释检验与评价。未采用参考论文的DP方法，不包含问题二至四。

- 基准：experiments/q1-contract-tests-20260911-03，费用35126.948589元。
- 口径：planning/confirmed-contract.md；A1/A5作为明确计算假设写入正文，未用论文写作代替团队最终验收。
- 样式：沿用仓库“数模论文模板.tex”的A4、中文字号、页边距、标题与页码，删除模板指导语，改用TeX Live自带Fandol字体以减少平台依赖。正文为第一问独立稿，并非完整四问论文。
- 文件独立：表格和145个时刻的曲线数据均在tex内部，无外置图片、CSV或bib依赖；需要常规ctex/pgfplots等LaTeX宏包。
- 结果来源：sources.json保留实验、模板、口径文件与tex的SHA256；static_check.json记录静态验证，compiled=false与page_layout_verified=false。

仓库根目录复核：

    python paper/q1/check_source.py
    python scripts/check_state.py
    python scripts/check_inputs.py

check_source.py核验TeX括号/环境/交叉引用、内嵌曲线和结果表、主要数值与能量账本、来源哈希。它不替代LaTeX编译，不验证最终版面；后续修改tex后应审核并更新来源记录。

文本来源哈希按UTF-8和LF换行规范化后计算，避免Windows/Linux拉取时换行差异导致误报；原始二进制输入仍按data/MANIFEST.csv的原始字节哈希核验。
