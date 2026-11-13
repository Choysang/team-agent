# 能力与宿主分流

[返回主流程](../SKILL.md)。先确定“由哪个宿主执行”，再看“连接哪个服务商”和“选择哪个模型”，三者是不同维度。切换服务商不等于换了宿主。

## 识别当前宿主

按顺序取证，前一项明确时不必看后面：

1. 系统提示中的产品身份（例如“You are Claude Code”“Codex”“Antigravity”“opencode”）。
2. 当前子代理派发工具的名称与参数，见下表“派发工具特征”。
3. 宿主环境变量（如Claude Code的 `CLAUDECODE`、`CLAUDE_CONFIG_DIR`，Codex的 `CODEX_HOME`）。
4. 配置目录只作佐证：一台机器常同时装有多个Agent，且有些目录被多个宿主共用（见“共用目录”）。

仍无法确定时，按 [通用适配](adapters/generic.md) 只整理配置草案，不写任何宿主文件。

| 宿主 | 派发工具特征 | 适配卡 |
|---|---|---|
| Claude Code（CLI、IDE、桌面、Claude应用中的Claude Code会话） | `Agent`（旧名`Task`），参数含 `subagent_type`、`model`、`effort` | [claude-code](adapters/claude-code.md) |
| Codex（CLI、IDE、桌面） | 派发参数含 `agent_type`、`fork_turns`，可传模型与 `reasoning_effort` | [codex](adapters/codex.md) |
| Google Antigravity | `invoke_subagent` | [antigravity](adapters/antigravity.md) |
| Gemini CLI | 子代理以同名工具出现（`codebase_investigator`、`generalist`、`cli_help`） | [gemini-cli](adapters/gemini-cli.md) |
| Cursor | 系统提示为Cursor；可在启动子代理时指定模型 | [cursor](adapters/cursor.md) |
| GitHub Copilot（VS Code、Copilot CLI） | VS Code本地为 `runSubagent`；Copilot CLI为原生 `task` | [copilot](adapters/copilot.md) |
| OpenCode | 系统提示为opencode；`task` 工具按 `subagent_type` 派发 | [opencode](adapters/opencode.md) |
| Qoder（CLI/IDE） | 系统提示为Qoder；内置general-purpose/Explore/Plan | [qoder](adapters/qoder.md) |
| CodeBuddy Code / CodeBuddy IDE / WorkBuddy | 系统提示为CodeBuddy；内置General-Purpose/Plan/Explore | [codebuddy](adapters/codebuddy.md) |
| Kimi Code、Kiro、Trae及其他 | 现场核对 | [generic](adapters/generic.md) |

## 每个宿主的主方案

每张卡第二行有核对元数据（日期、宿主版本、证据等级）。使用前按 [宿主自适配](self-adapt.md) 判断是否需要由当前Agent重新核对并更新本宿主那一行与卡片。

每个宿主只用这一套方案；主方案前提不满足时才用降级方案。“升级阶梯”一列说明能否在单次调用里换模型和强度（见 [派发协议](dispatch-protocol.md)）。

| 宿主 | 主方案 | 降级方案 | 升级阶梯 | 运行证据 | 验证程度 |
|---|---|---|---|---|---|
| Claude Code | 技能自带mod `team-agent-router` 按角色类型补全model/effort，配合生成的team-*角色文件 | 插件被禁用时：角色文件frontmatter + CLAUDE.md路由块 | 完整：单次调用传model与effort | 子代理meta与transcript、mod派发日志 | 历史主方案已实测；当前版本规则按文档核对 |
| Codex | 角色TOML的 `model`/`model_reasoning_effort`（含原生角色的受管绑定），派发时显式传入；固定通用入口冲突按 Codex 卡使用已批准职能后备 | 无Python时手工写TOML | 派发参数含模型与档位；角色固定值仍优先 | state_5.sqlite与会话JSONL | 已实测 |
| Antigravity | `profile.json` + `config/AGENTS.md` 路由块，派发调用传 `Model`（inherit/flash/pro） | 无 | 单次调用可换档位（flash_lite→flash→pro，或inherit用主会话模型）；无独立强度 | 本地 conversations/<id>.db 与 transcript | 已实测 |
| Gemini CLI | settings.json `agents.overrides.<内置>.modelConfig.model` + `~/.gemini/agents/team-*.md` | 无 | 只能换代理 | 无本地记录（/stats） | 仅文档 |
| Cursor | `~/.cursor/agents/team-*.md` 的 `model: <ID>[effort=…]` | 无 | 主代理启动时可指定模型 | 无本地记录 | 仅文档 |
| Copilot | `team-*.agent.md` 的 `model` 列表（即备选链）与 `reasoning-effort` | 无 | runSubagent可传model | 无本地记录 | 仅文档 |
| OpenCode | 现场识别可用的 `provider/model-id`，写入opencode.json的 `agent.<名称>.model` | 无 | 只能换代理 | 本地会话存储 | 仅文档 |
| Qoder | settings `agents.overrides.<内置>.modelConfig.model` + `~/.qoder/agents/team-*.md`（model、effort） | 无 | 以工具定义为准 | 待核 | 本机目录已核对 |
| CodeBuddy | settings `subagents.agents.<名称>.model` + `~/.codebuddy/agents/team-*.md`（model、effort） | 无 | 调用参数仅default/lite/reasoning | `/agents` 面板 | 本机目录已核对 |
| Kimi Code CLI | 不支持子代理单独设模型 | — | 不支持 | — | 文档明确 |

“仅文档”的宿主写入后结论为“已写入，运行未验证”，直到出现可核对的运行证据。现场发现文档未写的接口时，按通用适配补充并记下核对日期，不临时叠加第二种机制。

## 共用目录与冲突

- `~/.claude/agents/`：Claude Code读取；Cursor与VS Code也会读取（Claude格式）。为Claude Code写的 `model: opus` 在Cursor中不是有效ID，需在 `~/.cursor/agents/` 放同名文件遮蔽。
- `~/.codex/agents/`：Codex读取，Cursor也会读取。
- `~/.gemini/`：Gemini CLI用 `~/.gemini/agents/` 与 `~/.gemini/settings.json`；Antigravity用 `~/.gemini/config/`（agents、AGENTS.md、config.json）和 `~/.gemini/antigravity/`。`~/.gemini/GEMINI.md` 可能被两者共用，不在其中写宿主专属路由。
- 同一技能可能装在多个宿主的技能目录（如 `~/.agents/skills/` 与 `~/.gemini/antigravity/skills/`）；更新技能时各处一并更新，状态统一在 `~/.team-agent/<宿主>/`。
- `~/.agents/`：Kimi Code读取 `~/.agents/agents/`；本技能常装在 `~/.agents/skills/`，二者不冲突。

## 四类功能的边界

| 类别 | 内容 | 边界 |
|---|---|---|
| 通用流程 | 职能、选择卡、备选、更新确认、证据分级 | 所有宿主适用 |
| 宿主适配 | 写入位置、字段、生效层、验证方法 | 只按对应适配卡执行，参数名不跨宿主套用 |
| 服务商发现 | CC Switch等的模型与声明档位 | [服务商发现](adapters/provider-discovery.md)；发现不等于当前宿主可调用 |
| 模型特例 | 精确模型的有条件建议 | 仅Codex：[模型特例](model-policies.md)；宿主、模型、能力须同时匹配，其他宿主不读 |

不提供子代理能力的宿主：由主代理自己完成可独立的任务，不模拟子代理，不承诺派发或生效测试。
