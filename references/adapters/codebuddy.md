# CodeBuddy 适配（CodeBuddy Code CLI、CodeBuddy IDE、WorkBuddy）
<!-- adapter: host=codebuddy; verified=2026-10-27; host_version=unknown; evidence=local -->

[返回能力与宿主分流](../harness-matrix.md) · [返回主流程](../../SKILL.md)

识别依据：系统提示为CodeBuddy或WorkBuddy；内置子代理General-Purpose、Plan、Explore。核对日期：2026-10-27，依据官方文档与本机 `~/.codebuddy` 目录。

## 宿主事实（CodeBuddy Code）

| 项目 | 行为 |
|---|---|
| 子代理文件 | 项目 `.codebuddy/agents/`（优先），用户 `~/.codebuddy/agents/` |
| `model` | 模型ID、名称或别名；场景变体 `lite`/`reasoning`；`inherit`/`default` 表示不强制 |
| `effort` | `minimal`/`low`/`medium`/`high`/`xhigh`/`max`；省略则跟随会话 |
| 模型优先级 | `CODEBUDDY_CODE_SUBAGENT_MODEL` 环境变量 > 本次调用参数（仅default/lite/reasoning）> 项目 `subagents.agents.<名称>.model` > 全局同名设置 > 内置声明（Explore=lite）> 主会话模型 |
| 设置文件 | `~/.codebuddy/settings.json`（项目 `.codebuddy/settings.json`、`settings.local.json`） |
| IDE | 设置页Agent标签或 `~/.codebuddy/agents/`，支持Model字段 |

## 主方案

`settings.json` 的 `subagents.agents.<名称>.model` 按名称设模型（含内置角色），`team-*` 文件给其余职能设模型与强度。单次调用只能选default/lite/reasoning，升级阶梯改为：改派配置更强模型的职能代理 → 主代理亲自完成。

## 执行步骤

状态目录：`~/.team-agent/codebuddy/`。

1. 覆盖检查：设置了 `CODEBUDDY_CODE_SUBAGENT_MODEL` 时，所有按职能的模型都会失效，列为blocking并请用户决定是否移除。
2. 选择卡：模型选项为当前可选模型或lite/reasoning；强度六档。
3. 按名称设模型（覆盖内置角色的首选方式）：读入 `~/.codebuddy/settings.json`，只写 `subagents.agents.<名称>.model`（Explore、Plan、General-Purpose及team-*），保留 `enabledPlugins`、`hooks` 等其余键。
4. 生成 `~/.codebuddy/agents/team-<职能>.md`（Reviewer、Researcher、Designer、GeneralWorker），frontmatter写 `model`、`effort`，Reviewer/Researcher加只读限制，附生成标记。
5. 内置角色的强度：文档没有按名称设强度的settings键，显示“跟随会话”。
6. 路由说明：写到CodeBuddy的用户记忆文件（以当前版本为准），使用 `<!-- team-agent:codebuddy routing:start/end -->` 受管块。

## 验证

`/agents` 面板会显示每个子代理的模型设置与“被环境变量覆盖”提示，可请用户查看。本机 `~/.codebuddy/sessions/`、`traces/` 中若能找到子代理模型字段，按Claude Code卡的方法核对；否则结论保持“已写入，运行未验证”。

来源：[CodeBuddy Code Sub-agents](https://www.workbuddy.ai/docs/cli/sub-agents)、[CodeBuddy IDE Subagents](https://www.codebuddy.cn/docs/ide/Features/Subagents)。
