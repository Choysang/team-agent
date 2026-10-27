# Codex 专用：按模型设置与调试上下文

> 仅用于Codex宿主；其他宿主不需要读本文件。

[返回Codex适配](adapters/codex.md) · [返回能力与宿主分流](harness-matrix.md)。本文件的命令、目录字段和限制仅适用于Codex；其他宿主不能照搬。

## 入口和卡片

默认不修改。用户明确提到调整上下文时展示当前设置，并用原生卡片区分“上下文窗口”与“自动压缩阈值”。两者不能互换。模型与数值已明确时直接复用，不重复询问。Luna 800k的有条件建议移至 [模型特例](model-policies.md)，仅在精确匹配时读取和提示。

先确定范围。Skill中的角色或子代理请求默认只针对子代理；当前Codex不支持独立窗口时，报告不支持并保持宿主原值。不得自动改成宿主范围，也不靠更换Agent类型或独立CLI进程绕过。未回答不应用，不能将预选项视为用户提交。

## 可用能力

Codex当前子代理派发接口没有独立上下文窗口字段。宿主支持启动参数 model_catalog_json；本适配器复制当前完整模型目录，仅改用户指定slug的 context_window，不修改其余属性、其他模型、角色模型选择或思考强度。不写全局 model_context_window / model_auto_compact_token_limit，不改原始 models_cache.json。

这是宿主内按模型生效：使用同一模型的主会话和子代理都受影响，不是子代理专属。context-set默认scope=subagent，当前返回unsupported且不写宿主。只有用户明确要求宿主模型范围时才执行 context-set --scope host-model --model <模型> --window <tokens>；不能从子代理要求推定此范围。

宿主范围的覆盖保存在context-overrides.json，目录为context-models.json。适配器将覆盖叠加到最新缓存，其他模型保留原值；重载宿主后生效，已运行会话不热切换。context-reset只撤销已存在的指定模型宿主覆盖。

如果已有手动 model_catalog_json 或全局 model_context_window，先核对冲突并完成具体迁移，不覆盖、不删除用户设置。源目录缺少目标模型或窗口超过其声明 max_context_window 时报告错误；最大值未知则只允许当前声明窗口内的设置。API官网最大窗口不能直接替代当前服务商和宿主目录的限制。

模型切换和备选切换按实际模型查各自设置：只有相同slug复用覆盖；不同slug保持默认，除非用户另行配置。不能把一个长上下文数字复制给所有模型。

```text
python scripts/team_adapter.py context --model <模型>
# 默认子代理范围；当前返回unsupported，不改宿主：
python scripts/team_adapter.py context-set --model <模型> --window <tokens>
# 仅在用户明确要求宿主模型范围时：
python scripts/team_adapter.py context-set --scope host-model --model <模型> --window <tokens>
python scripts/team_adapter.py context --model <模型> --thread-id <新线程ID>
python scripts/team_adapter.py context-reset --model <模型>
```

## 验证

context 报告包含目录原值、声明最大值、显式覆盖和按 effective_context_window_percent 算出的估算值。保留比例是宿主元数据；原始窗口与实际可用窗口可能不同，估算不是遥测。

用宿主 debug models 确认目录被原生解析，再新建实际模型调用。context --thread-id 核对线程身份、当前模型与 token_count 中的 model_context_window；无具名当前turn或记录缺失保持未验证。报告的容量不证明服务端接受同等规模的长输入，也不证明长文本检索或推理质量；需要这些结论时另做带精确输入计数的长输入测试。

自动压缩阈值是另一项设置。当前适配器不实现按模型独立压缩阈值，不能承诺 window 数值就是压缩触发点，也不能偷偷用全局阈值替代。

来源：[Codex配置参考](https://learn.chatgpt.com/docs/config-file/config-reference)、[子代理配置](https://learn.chatgpt.com/docs/agent-configuration/subagents)。当前宿主接口变化后重新核验能力，不硬编码未来参数。
