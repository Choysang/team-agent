# Google Antigravity 适配
<!-- adapter: host=antigravity; verified=2026-10-27; host_version=unknown; evidence=tested -->

[返回能力与宿主分流](../harness-matrix.md) · [返回主流程](../../SKILL.md)

适用于Antigravity 2.0桌面应用、Antigravity IDE与 `agy` CLI。识别依据：系统提示为Antigravity，派发工具为 `invoke_subagent`。核对日期：2026-10-27（官方文档 + 本机实测 + 派发工具枚举 + 本机数据库）。

## 宿主事实

| 项目 | 行为 |
|---|---|
| 界面与会话模型 | 桌面端右下角模型切换面板提供当前会话模型选择：`Gemini 3.8 Flash High`、`Claude Sonnet 4.6 (Thinking)`、`Claude Opus 4.6 (Thinking)`、`GPT-OSS 120B (Medium)`、`Gemini 4 Argon`（置灰不可用） |
| 单次调用参数 | `invoke_subagent` 原生支持 `Model` 参数。类型为 string，**严格受枚举校验**：`["inherit", "flash_lite", "flash", "pro"]`。直接传界面具体模型名称（如 `Claude Sonnet 4.6 (Thinking)`）会被工具校验器硬拦截 |
| 接口与界面映射 | `inherit`（默认）是连通界面的桥梁：`'inherit' (default) uses your model`，实测子代理完整继承主会话界面选中的具体模型与思考模式（如 Flash High 或 Sonnet Thinking）。`flash`、`pro`、`flash_lite` 为平台独立子代理档位（实测走 `gemini-3.8-flash-tiered` 等），不占主模型配额 |
| 思考强度 | 子代理派发工具无独立 `effort` 参数。子代理设为 `inherit` 时继承主会话思考强度；设为 `flash`/`pro` 时使用平台固化默认强度 |
| 角色定义机制 | 宿主原生支持动态 `define_subagent` 工具（可配置 MCP、Subagent、Write 工具权限）；内置角色包括 `self`、`research`、`image-generator` |
| 规则文件 | 全局 `~/.gemini/config/AGENTS.md`；工作区 `AGENTS.md`/`GEMINI.md`/`.agents/rules/*.md`。单文件上限24KB，常驻规则共享20000 token预算 |
| 运行证据 | 宿主底层透明可读：`~/.gemini/antigravity/conversations/<id>.db` 的 `gen_metadata` 以 protobuf 记录底层真实调用模型（如 `gemini-3.8-flash-high`、`gemini-3.8-flash-tiered`），`parent_references` 记录父子会话链，`brain/<id>/.../transcript.jsonl` 记录每步会话轨迹 |

## 主方案

状态目录 `~/.team-agent/antigravity/profile.json` 维护职能模型映射 + 全局规则 `~/.gemini/config/AGENTS.md` 维护受管路由块。派发时由 Agent 直接在 `invoke_subagent` 传入对应的 `Model` 档位（`flash`、`pro` 或 `inherit`）。

升级阶梯支持单次调用动态升级：`flash_lite` → `flash` → `pro`，或切至 `inherit` 调用主会话高能力模型，最后主代理亲自完成。

## 执行步骤

状态目录：`~/.team-agent/antigravity/`。

1. **选择卡**：每个职能询问模型档位（`inherit`、`flash`、`pro`、`flash_lite`），说明强度由宿主继承或预设。需要使用 Claude 等高能力模型时选择 `inherit` 并在界面选中该模型。
2. **写配置**：写入 `~/.team-agent/antigravity/profile.json`。
3. **路由规则**：在 `~/.gemini/config/AGENTS.md` 写入或替换 `<!-- team-agent:antigravity routing:start -->` 到 `:end -->` 的受管路由块。块外用户规则保持完全不变。改前备份到 `~/.team-agent/backups/antigravity/`。
4. **运行验证**：派发最小子代理（Prompt: “只回复 pong，不用工具”），从宿主 SQLite 数据库 `conversations/<subagent-id>.db` 的 `gen_metadata` 核实真实底层模型与父子会话链。

## 验证

- 静态：重读 `profile.json` 与 `AGENTS.md` 受管路由块。
- 运行：各职能派发一次最小任务，读取 `conversations/<id>.db` 中 `gen_metadata` 的 `data` 字段中的模型名。

## 共存

- Gemini CLI 使用 `~/.gemini/agents/` 与 `~/.gemini/settings.json`；Antigravity 使用 `~/.gemini/config/` 与 `~/.gemini/antigravity/`。
- Antigravity CLI 的设置在 `~/.gemini/antigravity-cli/settings.json`，与桌面应用的 `~/.gemini/config/config.json` 不同。

来源：官方工具定义 `invoke_subagent`、本机实测 `conversations/*.db`、本机 `~/.gemini/antigravity/builtin/skills/agy-customizations/`。
