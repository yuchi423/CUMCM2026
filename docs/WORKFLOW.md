# Git 协作与恢复

## 认领和日常工作

团队先在已有沟通渠道确定同一任务只有一位当前负责人。Agent 不擅自向队友发送消息。不同任务可并行，统一采用 `codex/<任务ID>-<成员简称>` 分支；每人独立克隆或工作区。

工作区干净时：

```bash
git switch main
git pull --ff-only
git switch -c codex/T-001-yourname
```

先更新对应任务 JSON 的 owner、branch、status=in_progress、updated_at，提交认领记录并 `git push -u origin HEAD`。新任务从模板创建，ID 包含成员简称以避免重号，明确依赖和验收条件。并行认领不同分支不会自动互斥：如同一任务已被认领，应协调后再动手。

每个小阶段都保存检查点。执行合适验证后：

```bash
python scripts/check_state.py
git diff
git add <本阶段的明确文件路径>
git diff --cached
git commit -m "T-001: describe checkpoint"
git push
git rev-parse HEAD
git ls-remote origin refs/heads/<当前分支>
```

最后两条的 SHA 必须一致，才能说队友可拉取。不要盲目 `git add .`，防止混入无关文件和私密数据。记忆文件不必事后补写当前提交 SHA；它与成果同一提交即形成版本一致的检查点。

## 交接与恢复

1. 交出方保存成果、填写交接、更新任务后一起提交推送，报告任务 ID、分支和最终 SHA。
2. 接手方在干净工作区 fetch，切换该分支，pull --ff-only，核验 SHA；读取 AGENTS、PROJECT、任务与 latest_handoff。
3. 检查数据哈希、环境、现有结果与最近验证，执行最小复现。未获得数据或失败时标明阻塞，不重新假设结果正确。
4. 接手方更新 owner 和状态，提交推送后继续原分支。交出方停止向该分支提交。改用新分支时必须同步更新任务 branch 并告知队友。

接手提示词：

> 请按 AGENTS.md 接手任务 <ID>，交接分支为 <branch>，预期提交为 <SHA>。从任务 latest_handoff 恢复，先核验实际文件和环境，再完成 next_action。保持阶段检查点和共享记忆。

突发断线时从最后远端检查点恢复；若最后仍是 in_progress，不代表旧进程仍在运行。先确认原负责人是否停止，核对实验日志，避免重复启动昂贵运行。

## 主线整合与冲突

工作完成后任务设 review，提交合并请求供团队检查。验收者检查复现证据、论文数值来源和交接完整性；通过后将任务设 done、更新 STATUS，再按团队授权合入 main。未获得合并授权的 Agent 保持 review，不自行合并。

出现分叉时不要强推或 reset。fetch 后检查 `git log --oneline --left-right HEAD...origin/<分支>` 和 diff，保留双方成果。合并前核对重复实验、参数与验证范围；同一任务的矛盾状态由负责人依据证据解决，不能用更新时间简单裁决。

## 状态与完成定义

`todo → in_progress → review → done`；暂停可为 blocked，交接可为 handoff，放弃为 cancelled。blocked/handoff 恢复后回到 in_progress。review/done 必须有成果路径和验证记录；done 还需要团队认可验收条件已满足。任务失败也应记录命令、错误和已排除原因。

总览只保留入口和关键里程碑；历史任务/交接无需每次全部读取。外部输入位置和环境恢复命令也是交接的一部分。
