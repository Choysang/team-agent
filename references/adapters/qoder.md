# Qoder 适配（CLI与IDE）
<!-- adapter: host=qoder; verified=2026-10-27; host_version=unknown; evidence=local -->

[返回能力与宿主分流](../harness-matrix.md) · [返回主流程](../../SKILL.md)

识别依据：系统提示为Qoder；派发工具为 `Agent`，默认类型general-purpose。核对日期：2026-10-27，依据官方CLI文档与本机 `~/.qoder` 目录；IDE以界面为准。

## 宿主事实

| 项目 | 行为 |
|---|---|
| 子代理文件 | 用户级 `~/.qoder/agents/*.md`；项目级 `.qoder/agents/*.md`（受文件夹信任状态影响）。名称取frontmatter的 `name` |
| `model` | 默认 `inherit`；可写 `auto`、`lite`、`efficient`、`performance` 或具体模型名/别名 |
| `effort` | `low`/`medium`/`high`/`xhigh`/`max` 或正整数（推理预算） |
| 其他字段 | `tools`、`disallowedTools`、`permissionMode`、`maxTurns`、`timeoutMins`、`mcpServers`、`hooks`、`skills`、`isolation` |
| 内置子代理 | general-purpose、Explore、Plan（另有qoder-guide、statusline-setup），不能编辑；同名自定义会替换 |
| 改内置模型 | `settings.json` 的 `agents.overrides.<名称>.modelConfig.model` |
| 调用参数 | 文档未写明 `Agent` 工具是否接受model/effort；以你自己的工具定义为准 |

## 职能到类型

与Claude Code相同：Explorer→Explore、ExpertAdvisor→Plan、Executor→general-purpose；Reviewer、Researcher、Designer、GeneralWorker→生成的 `team-<职能>`；已有专业代理保留原名。

## 主方案

`settings.json` 的 `agents.overrides` 给内置三类设模型，`team-*` 文件给其余职能设模型与强度。升级阶梯：你的 `Agent` 工具定义含model/effort参数时，按阶梯显式传；不含时改派配置更强模型的职能代理。

## 执行步骤

状态目录：`~/.team-agent/qoder/`。

1. 选择卡：模型选项为Qoder的档位与当前可选模型名，强度用五档或整数预算。
2. 内置三类：读入 `~/.qoder/settings.json`，只写 `agents.overrides.{Explore,Plan,general-purpose}.modelConfig.model`，保留 `enabledPlugins` 等其余键。文档未说明overrides能否设强度，内置角色的强度显示“跟随会话”。
3. 生成 `~/.qoder/agents/team-<职能>.md`，格式与Claude Code卡中的手工模板相同（`model`、`effort`、Reviewer/Researcher加 `disallowedTools: Write, Edit`，附生成标记）。
4. 路由说明：写到Qoder的用户记忆/规则文件（以当前版本为准，通常为 `~/.qoder/` 下的AGENTS类文件），使用 `<!-- team-agent:qoder routing:start/end -->` 受管块；找不到确定位置时只依靠子代理 `description`。

## 验证

本机 `~/.qoder/projects/` 保存会话记录；若其中能找到子代理的模型字段，按Claude Code卡的方法核对；否则结论保持“已写入，运行未验证”。

来源：[Qoder CLI子智能体](https://docs.qoder.com/zh/cli/subagent)。
