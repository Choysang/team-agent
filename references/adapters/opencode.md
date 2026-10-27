# OpenCode 适配
<!-- adapter: host=opencode; verified=2026-10-27; host_version=unknown; evidence=docs -->

[返回能力与宿主分流](../harness-matrix.md) · [返回主流程](../../SKILL.md)

识别依据：系统提示为opencode；`task` 工具按子代理名派发。核对日期：2026-10-27，依据官方文档，尚未在本机实测。

## 主方案

OpenCode本身就支持按代理指定模型，不需要额外机制：你现场识别当前已连接的服务商与可用模型（`provider/model-id`），把用户的选择写进 `opencode.json` 的 `agent.<名称>.model`，由宿主自己调用。不安装插件，不写回退插件，不写死模型清单。

| 项目 | 行为 |
|---|---|
| 配置位置 | `~/.config/opencode/opencode.json`（或 `.jsonc`）的 `agent` 键；Markdown代理：`~/.config/opencode/agents/*.md`、项目 `.opencode/agents/*.md` |
| 字段 | `mode`: primary/subagent/all；`model`: `provider/model-id`；其他键原样透传给服务商（如OpenAI推理模型的 `reasoningEffort`） |
| 默认 | 子代理未设model时用调用它的主代理的模型 |
| 内置子代理 | `general`、`explore`，在 `agent.<名称>.model` 中覆盖 |

## 执行步骤

状态目录：`~/.team-agent/opencode/`。

1. 识别可用模型：以你当前可调用的 `provider/model-id` 为准（宿主的模型列表或你自己的配置），选择卡只列这些。强度只在该服务商的透传键已确认时提供，否则跳过并说明。
2. 写入 `opencode.json`：Explorer→`agent.explore.model`，Executor与GeneralWorker→`agent.general.model`（共用时取Executor的配置并说明），其他职能各写一个 `agent.team-<职能>`（`mode: subagent`、`model`、`description`、职责 `prompt`）。只改这些键，保留其余内容；JSONC注释无法安全保留时先请用户确认。
3. 升级阶梯：没有单次调用参数，改派配置了更强模型的职能代理，阶梯走完由主代理亲自完成。

## 验证

OpenCode在本地数据目录保存会话消息（含服务商与模型ID），格式随版本变化。能读懂当前版本的记录时，按新子代理会话核对modelID；否则结论保持“已写入，运行未验证”。

来源：[OpenCode Agents](https://opencode.ai/docs/agents)。
