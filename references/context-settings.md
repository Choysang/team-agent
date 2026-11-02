# Codex 专用：上下文诊断与旧目录退役

> 仅用于Codex宿主；其他宿主不需要读本文件。

[返回Codex适配](adapters/codex.md) · [返回能力与宿主分流](harness-matrix.md)

## 默认与能力边界

模型列表与上下文元数据由宿主管理，Skill只读当前缓存。不再通过 model_catalog_json 设置按模型窗口：该参数是启动时选用的完整本地目录，优先于远端发现，会导致后续新模型不出现。旧的“每次使用时合并缓存”无法解除这个启动时的接管。

当前派发接口仍无独立子代理上下文窗口参数，也没有经本适配器验证、同时保留原生模型发现的按模型窗口覆盖方案。context-set在子代理或host-model范围均返回unsupported，且不写宿主设置。不要用全局model_context_window、独立CLI、定时任务或常驻目录服务冒充按模型支持。

只有宿主将来提供并经核对的原生细粒度覆盖能力，才能重新实现这个功能。上下文窗口、自动压缩阈值与实际可用窗口分别展示；不把窗口大小当作压缩点。

## 旧状态与恢复

旧context-overrides.json保留用户期望值，报告为requested_window。没有生效的目录指针时状态为inactive，configured_window为空；不能把旧偏好说成已启用。模型、角色和思考档位继续从现有profile读取。

models、onboarding、resolve和context均不再刷新或重新启用context-models.json。注释或移除旧指针后，遗留偏好不阻塞新模型检查。目录读取失败仍保留原有模型选择，不当作下架。

旧受管目录仍启用时，报告legacy_static_catalog与blocks_remote_model_discovery，明确需要退役。用户自己设置的外部目录只报告，不改写。清理宿主配置先说明将移除受管指针以恢复原生目录，沿用已有修复授权；没有授权才询问。

```text
python scripts/team_adapter.py context
# 获授权后：仅删除本Skill的受管目录头，备份配置，保留所有窗口偏好：
python scripts/team_adapter.py context-retire
# 撤销指定旧窗口偏好，同时退役共享旧目录，其他偏好留作未生效记录：
python scripts/team_adapter.py context-reset --model <模型>
```

retire只操作本Skill位于文件开头的标记块；非受管model_catalog_json冲突时拒绝改写。BOM、行尾和块外内容保持，失败回滚。旧json不再作为启动目录使用；不自动删除用户数据。重新启动宿主后核对模型列表，已运行会话的工具枚举不热更新。

## 验证

context返回宿主目录默认值、声明上限、旧请求值与元数据估算。估算不作为遥测。存在完整目录接管时不以原生缓存推定实际窗口。

```text
python scripts/team_adapter.py models --dispatch-models <当前工具真实模型枚举>
python scripts/team_adapter.py context --model <模型> --thread-id <新线程ID>
```

上下文观测核对线程身份、具名当前turn和token_count的model_context_window；记录缺失保持未验证。容量不证明服务端能接收同等长度输入或长文本任务效果。新的模型目录只能证明已发现；可派发还需当前接口支持。

来源：[官方模型目录说明](https://learn.chatgpt.com/docs/model-catalogs)、[配置参考](https://learn.chatgpt.com/docs/config-file/config-reference)。2026-11-02核对：本地目录会阻止远端拉取。
