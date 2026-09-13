# CUMCM2026

> 已归档第一问成果、第二问全年模型及最新18方案优化比较。最新第二问过程与结果见下方入口；原始输入按 `data/AVAILABILITY.md` 校验。

数模队伍的代码、实验、论文与 Agent 共享记忆仓库。所有正式成果归档于本仓库；不依赖某位队员的聊天历史或个人 Agent 记忆。

## 开始协作

```bash
git clone https://github.com/yuchi423/CUMCM2026.git
cd CUMCM2026
```

把 [system prompt](prompts/SYSTEM_PROMPT.md) 配置到所用 Agent 的系统/项目指令中；不支持该配置时，将其作为首次消息发送，并让 Agent 读取本地仓库。仅打开 GitHub 页面不等于拥有执行和写入权限。

接着发送：

> 请读取 AGENTS.md 并按其顺序恢复共享上下文。先核验当前分支、任务与成果文件，再继续当前任务；每个阶段更新状态并保存交接检查点。

Codex 支持通过 `AGENTS.md` 加载项目指令；其他工具是否自动加载请以其配置为准。[官方说明](https://learn.chatgpt.com/docs/agent-configuration/agents-md)

## 目录

| 路径 | 内容 |
| --- | --- |
| `AGENTS.md` | 唯一的协作规则来源，所有 Agent 共同遵守 |
| `prompts/` | 跨工具启动提示词 |
| `memory/PROJECT.md` | 长期约定、题目与目标、团队角色 |
| `memory/STATUS.md` | 精简的项目状态入口与任务索引 |
| `memory/tasks/` | 每个任务的负责人、分支、进度、下一步 |
| `memory/decisions/` | 关键决策、证据、放弃路线与重启条件 |
| `memory/handoffs/` | 每次交接的可执行检查点 |
| `problem/` | 原始题目、要求和问题拆解 |
| `planning/` | 候选模型、Plan A/B、比较指标与流程图 |
| `data/` | 数据清单、原始数据与处理后数据 |
| `src/`、`configs/` | 可复用代码与实验参数 |
| `experiments/` | 按运行 ID 保存配置、日志、指标和验证 |
| `paper/` | 论文正文、引用和由实验生成的图表 |
| `deliverables/` | 已验收的最终提交包 |
| `scripts/`、`tests/` | 运行入口、共享状态检查和有效性测试 |
| `templates/` | 任务、交接、决策和实验记录模板 |


**`git pull main` 不会拿到尚未合入 main 的任务进度。** 对方必须推送，接手方必须切到正确分支。Git 不传输运行中的进程、虚拟环境、未提交文件或被忽略的数据；这些都要提供恢复方式。

此方案保存可审计的目标、决策依据与实验状态，不保证重现上一模型内部思维，也不能在账号突然耗尽后补救未保存的工作。因此每个阶段都应落盘并推送，而非等 token 用尽。



THANKS FOR ALL
THANKS FOR OUR AMAZING TEAM !
<img width="628" height="458" alt="image" src="https://github.com/user-attachments/assets/f5cb7191-239c-49b3-82da-20dd35923896" />
