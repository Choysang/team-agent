# Codex 专用适配
<!-- adapter: host=codex; verified=2026-10-27; host_version=desktop-bundled-0.164.0-alpha.11.1; evidence=tested -->
[返回能力与宿主分流](../harness-matrix.md) · [返回主流程](../../SKILL.md)

仅在当前执行宿主为Codex时使用。CLI、IDE和桌面环境共享部分配置协议，实际派发参数与加载行为仍逐一核验。本适配器不是其他Agent宿主的配置工具。

## 主方案

角色TOML的 `model`/`model_reasoning_effort`（含原生default/worker/explorer的受管绑定），派发原生角色时显式传入解析后的模型与强度。派发参数含模型与档位，升级阶梯可完整执行。

先核对当前工具中角色的固定模型约束。通用入口已固定为其他职能的模型时，显式参数不能绕过该约束；在已获用户确认且对应类型已加载时，使用该职能的 `team-*` 后备入口，保留用户的模型与强度。已验证的例子是 Designer：`default` 固定模型与 Designer 不同时，执行 `resolve --role team-designer` 并派发 `team-designer`，不改写共享的 default。`resolve --role Designer` 目前仍返回 default，调用者必须完成这项冲突核对。

## 由Agent自行执行

下面的命令由你用自己的Shell在技能目录执行，不交给用户。环境没有Python 3.11+时手工完成同样的结果：

1. `~/.codex/agents/team-<职能>.toml`：`name`、`description`、`developer_instructions`、`model`、`model_reasoning_effort`（值取自 `models_cache.json` 中 `visibility=list` 的模型及其 `supported_reasoning_levels`）。
2. 已有专业角色与内置 `default`/`worker`/`explorer` 的TOML顶部写入受管块：`# team-agent model binding:start` / `model = "…"` / `model_reasoning_effort = "…"` / `# team-agent model binding:end`。已有非受管的model字段时先与用户核对。
3. `~/.codex/AGENTS.md` 中维护派发路由受管块；派发原生角色时显式传入模型与强度。
4. 状态保存在 `~/.team-agent/profile.json`（schema_version=2）。

## 目录与每次调用

需要Python 3.11+标准库。下文python指已确认版本的解释器；Windows也可用py -3，macOS/Linux常用python3。命令相对Skill目录执行。状态默认 ~/.team-agent；宿主默认 ~/.codex，支持CODEX_HOME和 --codex-home；状态路径用 --state-dir覆盖。

```text
python scripts/team_adapter.py models
python scripts/team_adapter.py verify
python scripts/team_adapter.py onboarding --dispatch-models <当前派发工具实际列出的逗号分隔模型ID>
```

首次onboarding返回模型、思考档位、Fast和上下文，以及现有角色配置。原生可选范围为模型目录与当前派发工具接受枚举的交集；没有工具枚举只展示目录，不标全部可调用。档位取所选模型真实supported_reasoning_levels，不补造medium/high/Thinking。

每次适配器调用检查目录变化；catalog_notice有待处理项时按 [更新卡片](../model-updates.md) 展示全配置。每次同时检查返回的context.recommendations，存在建议时读取 [模型特例](../model-policies.md) 并按匹配项提示，无建议不打扰。

当前工具暴露request_user_input_async时使用原生卡片；request_user_input仅在模式允许时使用。没有卡片工具才使用通用流程的编号选项，不借用其他IDE的档位标签。明确处理目录变化后仅确认该指纹：

```text
python scripts/team_adapter.py catalog-ack --fingerprint <指纹> --decision selected --roles <逗号分隔职能>
python scripts/team_adapter.py catalog-ack --fingerprint <指纹> --decision defer
```

## 原生角色、文件与默认关联

Codex内置default、worker、explorer；本机专业模板不等于官方内置角色。按 [已有角色优先](../host-role-priority.md) 选择实际类型；已审阅映射在该参考链接的role-families.json中，优先于名字猜测。

个人角色位于 ~/.codex/agents，项目角色可位于 .codex/agents。当前脚本扫描和托管绑定的是选定 --codex-home/agents 下的个人角色，不将项目角色扫描宣称为已实现；当前工具暴露的实际角色仍是加载证据。

每份独立TOML包含name、description、developer_instructions；model和model_reasoning_effort使用真实值。不要往角色TOML加入fork_turns、readonly、model_provider等无效覆盖字段。fork_turns属于本次工具调用约束，不是角色文件字段。

```text
python scripts/team_adapter.py sync --profile <完整配置JSON>
python scripts/team_adapter.py preference --role Explorer --skip-fallback-prompts true
python scripts/team_adapter.py preference --role all --skip-fallback-prompts false
python scripts/bind_roles.py
python scripts/team_adapter.py resolve --role <实际宿主角色或团队职能>
```

profile使用schema_version=2；roles每项包含model、effort、fallbacks、active_index，可选skip_fallback_prompts。fallbacks为有序model/effort对象数组；根偏好作默认，角色显式偏好优先。sync接收完整profile，未选角色保留原值。

bind_roles保留已有专用指令，按职能绑定本机专用角色模型。首次绑定后，sync和备选切换同步更新托管绑定；未托管的显式模型冲突须先核对。全局AGENTS提供派发前路由，team-*仅作明确请求或必要后备。

历史桌面实测中，内置worker/explorer直接调用曾未应用同名TOML模型覆盖；不能将该结果推定为所有客户端的固定行为。当前接口支持时，对原生worker/explorer/default显式传resolve的model与reasoning_effort；专业角色保留指令并核对固定参数的优先级。模型覆盖要求冷上下文的当前接口使用fork_turns="none"。

工具实际列出角色时，resolve/verify可传 --loaded-agent-types <逗号分隔实际类型>。磁盘存在不能证明已加载；TOML的name才是派发身份。派发任务边界沿用 [通用派发协议](../dispatch-protocol.md)。

## Fast、上下文与服务商

当前原生子代理service tier跟随根会话；本次spawn接口无独立service_tier参数，角色TOML也不覆盖。Fast是速度/服务档位，不是模型或思考强度。只展示“跟随主会话；实际档位待核对”，不提供虚假的角色独立开关。

用户明确要求切换父会话Fast时才使用宿主原生UI或CLI的 /fast，并说明同父会话子代理共同继承。不能改模型目录默认服务档位冒充独立子代理支持；目录声明能力不证明账号可用、实际开启或计费倍率。

当前独立子代理窗口不支持；按模型的宿主覆盖与调试见 [Codex上下文](../context-settings.md)。仅在处理该功能时读取；Luna800k建议另按 [模型特例](../model-policies.md) 匹配，不将其作为所有模型默认值。

子代理继承父会话权限、provider和MCP。外部API/CC Switch的发现和切换边界见 [服务商发现](provider-discovery.md)。不能在子代理TOML中为各角色独立切provider。

## 备选与运行验证

```text
python scripts/team_adapter.py failover --role Executor --reason quota_exceeded --event-id <唯一失败ID>
```

同一失败幂等、不回绕，只有已配置的有序链可切换。reason使用适配器已识别的真实错误类别；账户/工作区额度耗尽、网络和权限错误不作模型级切换，细则沿用主流程。

verify只验证静态文件。应用/切换后取得新调用的真实线程ID；完整团队验证先冻结本次配置、时间及真实agent_type，每个职能用独立新线程：

```text
python scripts/runtime_probe.py --codex-home <目录> --thread-id <线程ID> --model <预期模型> --effort <预期档位>
python scripts/verify_runtime.py --prepare <本次测试清单.json>
# 以各角色新建调用，将真实线程ID写入清单threads字段后：
python scripts/verify_runtime.py --threads <本次测试清单.json>
```

验证读取本机state_5.sqlite与对应会话JSONL，核对线程、具名turn_context、model/effort与真实输出；具体格式由scripts/runtime_probe.py处理。缺失、不可读或无新输出保持未验证；旧线程和自报型号不是证据。

verify_runtime写入 ~/.team-agent/runtime-verification.json；仅所有已配置职能匹配才整体pass。采用已批准的后备入口时，在派发前将其真实类型写入本次清单的 expected_agent_types；不要在验证后修改预期来掩盖不一致。不一致时核对固定参数优先级、重新sync并更新托管绑定，重建受影响调用再测。dashboard只记录真实用量，不模拟费用。

桌面内置运行时与 PATH 中独立 CLI 可能版本不同；优先记录实际桌面进程对应可执行文件的版本。核验时还要读取会话最终输出，确认最小任务只返回 pong 且没有工具调用。完整匹配证明当前宿主的角色、模型与强度选择，不证明账号计费、真实任务质量或未测试宿主的加载行为。

来源：[官方子代理配置](https://learn.chatgpt.com/docs/agent-configuration/subagents)、[配置参考](https://learn.chatgpt.com/docs/config-file/config-reference)、[速度设置](https://learn.chatgpt.com/docs/agent-configuration/speed)。接口变化后重新核验；本机历史证据不替代当前接口检查。
