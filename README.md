# Team Agent

让子代理按职能使用你指定的模型、思考强度和备选，而不是默认沿用主会话的模型。

几乎所有带子代理的 Agent 都有同一个问题：主代理派发子代理时，会把自己的模型和思考强度原样交给子代理。查文件用最贵的模型，审查却用最便宜的，额度用完也不会自动换。Team Agent 让你按职能（探索、执行、审查、规划等）指定“用哪个模型、什么强度、额度不够时依次换谁”，并由当前 Agent 自己把选择落到它所在宿主真正会执行的位置，再实测确认生效。

## 你会得到什么

- 每个职能固定的模型与思考强度，写入当前宿主的原生配置（角色文件、设置项或插件），下次新会话起自动生效。
- 有序备选：额度、限流、过载时按你排好的顺序切换，冷却结束后回到首选。
- 升级阶梯：子代理结果不合格时，同一模型从 medium 逐级调高强度，到顶再换更强的模型，同样从 medium 起。
- 实测证据：从宿主自己的运行记录核对子代理实际用了哪个模型和强度，不以模型自报为准。
- 宿主自适配：换到另一个 Agent 时，它先用自己的工具和文档核对本宿主的适配说明，有变化就更新并记下日期与证据等级，一处沉淀、处处生效。

是否派发子代理仍由 Agent 自己判断，本技能不干预、不鼓励多派。需要多个子代理分工时，你可以明确要求“按 team-agent 方式协作”。

## 安装

**让 Agent 安装**：把下面这句话发给你的 Agent。

> 请帮我安装这个 Skill：https://github.com/Choysang/team-agent

**使用命令安装**：

```text
npx skills add Choysang/team-agent
```

同时使用多个 Agent 时，建议把技能放在一个目录，再让各个 Agent 的技能目录用链接指向它。

## 配置

无需密钥，也无需手动运行命令。第一次使用时，Agent 会用选择卡问你每个职能的模型和强度，选项只列当前宿主实际可用的模型。需要安装插件、修改全局设置或环境变量时，它会先说明原因并征得你同意。你的选择保存在 `~/.team-agent/<宿主>/`，不写进技能目录。

## 使用

用自然语言即可，例如：

- “用 team-agent 把审查子代理设成 opus、强度 high，探索用便宜的模型。”
- “给 Reviewer 加两个备选模型，额度不够时依次切换。”
- “检查一下我给子代理配置的模型到底有没有生效。”
- “出了新模型，看看各个职能的配置要不要更新。”
- “用 team-agent 带队，把这三个模块的重构分给几个子代理并行做。”

## 支持的宿主

| 宿主 | 生效方式 | 验证程度 |
|---|---|---|
| Claude Code | 随技能附带的插件按角色类型补全模型与强度，加角色文件和 CLAUDE.md 路由 | 已实测（Linux 云端、Windows 桌面） |
| Codex | 角色 TOML 的模型与推理强度，派发时显式传入 | 已实测（Windows 桌面） |
| Antigravity | 路由规则 + 每次派发传模型档位（inherit / flash_lite / flash / pro），无独立强度 | 已实测（Windows 桌面） |
| Qoder、CodeBuddy | 设置项按名称设模型 + 角色文件 | 已核对本机配置，未实测 |
| Gemini CLI、Cursor、GitHub Copilot、OpenCode | 各自的角色文件或设置项 | 仅依据官方文档 |
| Kimi Code | 不支持给子代理单独设模型，会如实告知 | 文档明确 |
| 其他宿主 | 按通用方法现场判定能力 | 现场核对 |

在未实测的宿主上首次使用时，Agent 会做一次最小实测，通过后才把该宿主标记为已实测。

## 依赖与数据

- 核心流程只需要 Agent 自身的文件、Shell 和提问能力。
- 可选脚本需要 Python 3.10+（Codex 与 Claude Code 适配器需要 3.11+），只用标准库；没有 Python 时 Agent 按说明手工写入。
- Claude Code 插件需要 Claude Code 2.1.287+（桌面应用 2.1.286+），组织策略禁止插件时自动改用降级方案。
- 不调用任何外部服务，不上传数据。实测只读取宿主保存在本机的运行记录。
- 已在 Windows 与 Linux 上运行；macOS 未验证。

## 适用边界

适合：配置或修改子代理的模型与强度、在新宿主上首次落地、额度切换、结果不合格时升级、新模型出现时更新、明确要求的多子代理协作。

不适合：切换主会话模型、普通编码或问答、Agent 自行决定的日常派发。把任务交给 Codex 等外部 CLI 执行，请使用对应的专用技能。

## 测试与排查

```text
python -m unittest discover -s tests
python scripts/test_runtime_probe.py
claude plugin test .
```

最后一条测试 Claude Code 插件，需要已安装 Claude Code。各宿主的写入位置、生效方式与排查方法见 [能力与宿主分流](references/harness-matrix.md) 和 references/adapters 下的各宿主说明。

## 致谢

设计吸收了这些项目的经验：[oil-oil/codex](https://github.com/oil-oil/codex)（委托与复核方法）、[obra/superpowers](https://github.com/obra/superpowers)（子代理状态与审查）、[Anthropic advisor strategy](https://claude.com/blog/the-advisor-strategy)（按需动用强模型）、[youngbinkim0/opencode-fallback](https://github.com/youngbinkim0/opencode-fallback)（错误分类与冷却）、[code-yeongyu/oh-my-openagent](https://github.com/code-yeongyu/oh-my-openagent)（按代理的备选链）、[musistudio/claude-code-router](https://github.com/musistudio/claude-code-router) 与 [tzachbon/claude-model-router-hook](https://github.com/tzachbon/claude-model-router-hook)（模型路由方式对比）。技能结构按 [oil-oil/oil-skill-creator](https://github.com/oil-oil/oil-skill-creator) 的规范整理。

## 维护记录

- 2026-11-02（3.5.1）：修复 Codex 新模型被旧上下文目录屏蔽的问题。退役静态模型目录覆盖及自动刷新；注释配置或旧偏好损坏不再阻塞模型发现与角色解析。新增 `context-retire`，备份清理受管目录指针，保留个人模型选择与未生效的窗口偏好。

## 许可证

MIT
