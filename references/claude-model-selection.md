# Claude Code 模型选择

[返回 Claude Code 适配](adapters/claude-code.md) · [通用职能](role-models.md)

本页帮助选择候选，不自动改模型、档位或备选。建议依据 2026-11-09 的 Anthropic 原文与 2026-11-13 的官方文档；新版本以当前宿主和账号为准。

## 按任务选择候选

| 任务特征 | 可评估的起点 | 验收重点 |
| --- | --- | --- |
| 短文本提取、文件定位、日志归纳 | 当前可用的 haiku 或 sonnet，默认档位 | 返回原文位置，主代理能快速复核 |
| 范围清楚的实现、反复执行的审查或起草 | sonnet / medium；复杂任务比较 opus | 测试、构建或明确的结果检查 |
| 多文件判断、复杂审查、视觉工作 | opus / medium，必要时 high | 覆盖关键边界与返工成本 |
| 大型迁移、开放式技术研究、复杂架构 | 可用时比较 fable / medium 或 high | 长任务完成率、证据完整性与总支出 |

职能名称不足以判断难度；Researcher 的资料查找和科学研究可能需要不同模型。已有 max 等明确选择继续保留。第一次配置时说明 medium 通常适合作为比较起点，low 适用于容易验收的短任务，xhigh/max 要用实际效果证明额外开销有价值。

## 用完成任务的成本比较

先提供可执行的结果检查，再比较两三个配置的成功率、延迟、用量和返工。高 effort 不保证优于更强模型的较低 effort；脚本的 `ladder` 只给确定的升级顺序，不宣称全局最省钱。不改用户已选的阶梯，也不把未配置的模型偷偷加作额度备选。

主模型保留决策，独立子代理处理会产生大量日志或材料的支线；返回摘要和文件/来源位置。工作能在一个上下文内顺序完成时，先衡量派发、重读和汇总开销。大窗口是能力上限，不是每次都要填满的目标。

切换模型时在任务边界新建调用，交接目标、已确认事实、证据位置、已做改动及待办。不要依赖旧模型的隐藏思考能被新模型继续读取；模型与速度模式的缓存也可能不同。记录宿主提供的真实用量，账号或网关费率未知时不推算价格。

## Claude Code 专有规则

- API 默认 effort 与 Claude Code 默认值分开：当前 Fable 5.2、Sonnet 5.5 在 API 为 high，在 Claude Code 为 medium。没有显式 effort 的跨模型子代理，不保证继承主会话档位。
- 内置 Explore 跟随会话模型，Fable 下的行为取决于连接方式；不能假设它一直是 Haiku。本技能保留内置提示词，由既有 mod/显式参数路由，不为省钱直接覆盖同名内置角色。
- Agent 调用当前使用别名；代理文件可使用完整模型 ID。别名是会变化的入口，也可能被 `ANTHROPIC_DEFAULT_*_MODEL` 固定；按工具、宿主版本、提供商和实际记录核对解析。`opusplan` 是主会话策略，不是新的团队职能模型。
- Fable 5.2 的 `fable` 别名需要 Claude Code 2.1.321+；旧版本可能解析为 5.1。宿主桌面内嵌版本与 PATH 版本分别核对。
- Fast 的当前官方入口是会话 `/fast`，不是 effort，也不是 Agent 的独立参数。它可能单独计费；本技能不修改主会话或为每个角色生成虚假开关。
- `autoCompactWindow` 是提前压缩阈值，不是扩容窗口；当前从 2.1.296 起可用于代理文件，本适配器不默认写入。
- 检测到旧别名 pin、模型白名单或网关时，报告其范围与影响，保留原值。限制来自宿主/账号时不把新增外部模型冒充可派发候选。

运行验证按 [Claude Code 适配](adapters/claude-code.md) 读取宿主记录；文档或隔离夹具通过不等于当前账号已调用成功。

来源：[用户提供的译文](https://aihot.news/items/ykrvgcox3whsx6tst5qesbf62)、[Anthropic 原文](https://claude.dev/blog/which-claude-model-should-you-build-with/)、[模型配置](https://code.claude.com/docs/en/model-config)、[子代理](https://code.claude.com/docs/en/sub-agents)、[Fast](https://code.claude.com/docs/en/fast-mode)。
