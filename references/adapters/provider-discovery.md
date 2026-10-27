# 外部服务商与模型发现

[返回能力与宿主分流](../harness-matrix.md) · [Codex适配](codex.md)

本功能是元数据发现，不是新的Agent宿主适配器，也不是跨服务商派发器。CC Switch可保存不同应用的服务商信息；当前执行宿主、父会话服务商、模型ID须分别核对。

## 当前实现

scripts/external_catalog.py由Codex适配器调用，读取选定 --codex-home 下的cc-switch-model-catalog.json，以及其相邻 .cc-switch/cc-switch.db。它不是单独CLI，不为其他宿主提供配置写入。

只输出模型ID、服务商标识、宿主标识、明确声明的思考档位与来源。不会输出凭据，不调用外部服务，也不保存或迁移密钥；未知能力保持未知。发现结果中的host字段是元数据来源，不是已通过适配验证的宿主列表。

原生可选模型与外部发现分列。发现状态discovered_not_verified_callable只说明目录中存在，billing_group默认unknown，不根据名字猜测模型额度是否独立。

## 何时能成为候选

当前父会话连接的服务商须能调用该模型，当前宿主接口也须接受它；两者都有实际证据后才进入选择卡。仅有CC Switch元数据或API网页说明不够。

Codex子代理继承父会话provider，角色TOML不提供每角色provider覆盖。CC Switch切换父会话后，刷新宿主目录与派发接口，重新核对可调用范围、真实档位和已有配置，再按需新建调用验证。不能把候选链当成跨服务商切换机制。

其他宿主是否允许每代理服务商配置、如何继承、怎样验证，由它自己的适配器实现；当前未实现，不借用Codex字段或默认行为。
