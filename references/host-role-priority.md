# Codex 专用：优先复用已有角色

> 仅用于Codex宿主；其他宿主不需要读本文件。

[返回Codex适配](adapters/codex.md) · [通用角色分层](role-models.md)。下表是当前Codex适配映射，不是其他宿主的角色清单。

模型配置和派发角色分开处理。优先使用宿主当前提供的原生/专业角色，保留其专业指令；不要只为选模型而替换成 team-*。

| 用户配置职能 | 优先派发角色 | 缺少专业角色时 |
|---|---|---|
| Explorer | explorer | 原生探索入口 |
| Executor | worker | 原生执行入口 |
| Reviewer | code-reviewer | default，附带审查任务说明 |
| ExpertAdvisor | architect，其次 planner/code-architect | default，附带规划任务说明 |
| Researcher | docs-lookup | explorer，附带资料查询任务说明 |
| Designer | default，附带设计任务说明；固定模型冲突时按 Codex 卡核对 | 用户批准且已加载时使用 team-designer，不改共享 default |
| GeneralWorker | default | 通用回退 |

若已明确选择 python-reviewer、cpp-build-resolver 等现有专业角色，保留原类型，不再归并成通用角色；只复用相应职能的模型和档位。传入当前工具实际暴露的 --loaded-agent-types 可确认专用候选可用，其证据优先于本地文件是否存在。TOML 的 name 是派发身份，文件名不代替 name；无加载证据时保持未验证，不以磁盘存在代替已加载。已加载角色缺少可核对的模型默认值时，应在接口支持的情况下显式传入配置参数，并保留实际调用验证。

对内置 worker/explorer/default 显式传入解析后的 model 和 reasoning_effort。原生入口仍由 Codex 分配，Skill 只提供角色匹配与模型参数。team-* 保留兼容，但只有明确请求或确需后备时使用，不是默认优先目标。

实际验证先 --prepare，冻结本次真实 agent_type，再新建调用并填线程ID。相同 default 类型可承担不同职能，但每个职能的验证必须使用各自的新线程，不能复制一个成功ID证明全部配置。

## 已审阅的本机职责表

[role-families.json](role-families.json) 按已审阅的47份专业模板指令分为8个工作簇，逐名指定共享哪一个用户模型配置。这是本机模板的维护分类，不是官方定义的8种代理，也不代表所有Codex安装都只有47份模板。

这些模板的指令正文均不同，当时没有独立工具或权限开关。未来模板新增features或独立工具限制时应重新核对。测试执行、文档发布等可共同使用Executor，语言与领域审查可共同使用Reviewer，但继续保留各自专业指令。Researcher可与Explorer同模型但独立配置；本批模板没有以界面视觉设计为主要任务的模板，type-design-analyzer属于类型系统审查，不能按design一词映射Designer。

已审阅明确映射优先于名称关键词。verify的coverage区分reviewed_instructions、builtin、name_hint、fallback；新角色尚未审阅时标明来源或回退，不把名字猜测报告为已核验。sync/failover重新绑定仍保留专业模板正文。

来源：[官方子代理配置](https://learn.chatgpt.com/docs/agent-configuration/subagents)。本机模板分类属于本适配器维护数据，不能由官方来源推定。
