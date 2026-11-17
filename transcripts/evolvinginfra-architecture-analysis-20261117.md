# Related-session excerpts: 分析 EvolvingInfra 架构设计

Selected visible assistant messages and excerpts from the code-quality report linked in the original analysis session. These excerpts concern the three bugs fixed in the pull requests. Unrelated architecture comparisons, operational handoff notes, infrastructure identifiers and workload contents are omitted. Dates below are UTC as recorded by the original session.

## Assistant — 2026-11-16T17:47:15.056Z

整洁度评估找到两类可局部复现的问题：跨语言协议对同一字段的校验不一致，以及配置继承可能留下孤立参数。报告会把这些具体缺陷与维护性风险分开，也会保留必要的 RDMA 生命周期约束。

## Assistant — 2026-11-16T17:52:13.338Z

**整洁度判断是：工程组织有明确优点，但核心路径的维护负担偏重。**主要问题集中在：

- Engine 通过 `Any/getattr`、内部字段探测缓存能力；admission 模块还会改写队列方法，时序契约分散。
- Frontend 的 `PDForward` 同时承载上传、候选选择、流恢复、迁移和取消，修改容易扩散。
- 协议与配置依赖松散字典和手工校验。局部复现了两个问题：嵌套 transfer ID 的 bool 校验不一致，以及配置继承留下旧参数值。

值得保留的是 Planner 的纯决策与 CAS 分层、Router 的资源凭据，以及 Engine 的 TP consensus 和传输安全约束。报告建议优先收敛缓存/admission 接口，再拆清每条 leg 的资源寿命，最后统一公共契约的表示与解析。

本次完成了源码分析和局部函数复现，未进行 GPU/RDMA E2E 或性能比较。

## Assistant — 2026-11-16T20:17:28.026Z

定位到一个具体错误：EI 的 MHA KV 页面地址步长被重复乘了 page size（64），导致 RDMA 读取范围越界。日志中，1024-token smoke 传了约 9.66 GB，而按 Qwen3-4B 的 KV 布局应约为 151 MB；完整 replay 随后全部报“内存区域未注册”。

我会保留原版失败记录，做一处最小兼容修复，并先用带边界检查的复制测试验证。

## Assistant — 2026-11-16T20:22:09.939Z

最小补丁已通过 24 组原生地址计算和边界复制测试，覆盖连续页、离散页及末尾页；同一测试在上游版本上能复现步长错误。下一轮会使用独立、固定版本的修复源码，并在出现 RDMA 隔离错误时立即结束运行，避免再等待 600 秒超时。

## Assistant — 2026-11-16T21:15:16.967Z

第五轮 EI 已完整完成 674/674 个请求并正常退出，用时 280.45 秒。 [comparison continuation omitted]

## Assistant — 2026-11-16T21:27:25.475Z

已在 <redacted> 跑通 **Qwen3-4B**，并在同一组 **2×H200、固定 1P1D** 上完成相同 workload 对照，两边均 **674/674 请求成功**。

- EI 使用了修复 MHA 页面步长重复乘法的最小补丁；Planner 关闭，动态 P/D 尚未测评。

[Performance comparison and private evidence links omitted.]

## Excerpts from the code-quality report referenced in that session

Source pinned to public upstream commit `738f586b9d0135cd51dba6b267d7962134478c7b`. Original local source links are converted to the corresponding public commit links.

## 5. M2：prepared 公共不变量已有可复现的校验漂移

**具体实例。** frontend 的 [PrefillLeg/DecodeLeg](https://github.com/mit-han-lab/EvolvingInfra/blob/738f586b9d0135cd51dba6b267d7962134478c7b/evolving_infra/router/frontend/protocol.py#L103) 将 KV marks 保留为 `dict`；[ForwardPD.matching_transfer](https://github.com/mit-han-lab/EvolvingInfra/blob/738f586b9d0135cd51dba6b267d7962134478c7b/evolving_infra/router/frontend/protocol.py#L160) 用普通相等比较检查 mark 的 `transfer_id` 与顶层 ID。Python 中 `True == 1`，因此顶层 `transfer_id=1`、两个嵌套 mark 的 `transfer_id=True` 能通过这段检查。client 的 [_leg_endpoint](https://github.com/mit-han-lab/EvolvingInfra/blob/738f586b9d0135cd51dba6b267d7962134478c7b/evolving_infra/router/client/backend/backend.py#L173) 对同一字段要求 `type(...) is int`，engine 的 [marks._require_int](https://github.com/mit-han-lab/EvolvingInfra/blob/738f586b9d0135cd51dba6b267d7962134478c7b/evolving_infra/engine/decoupled_engine/srt/disaggregation/symmetric/marks.py#L30) 也明确拒绝 bool。

此外，client 的 [_late_d_candidate_endpoints](https://github.com/mit-han-lab/EvolvingInfra/blob/738f586b9d0135cd51dba6b267d7962134478c7b/evolving_infra/router/client/backend/backend.py#L214) 手工知道 `v/mode/router_epoch/k/default_idx`、每个候选的共同 link 字段、arrival pick 与 export 列表；frontend 的匹配器只掌握其中一部分。后者把完整 mark 内容留给 engine 验证有合理性，但共同 envelope 不变量没有一个明确的共享定义。

**轻量验证结果。** 用 AST 从固定源码提取并执行原 `matching_transfer` 和 `_leg_endpoint` 函数体；只提供内存中的对象与 URL stub：

```text
ForwardPD.matching_transfer, nested bool transfer IDs: accepted
client._leg_endpoint, same nested bool transfer ID: rejected
```

这是局部校验差异的实证。因为环境未装 Pydantic/aiohttp，没有运行完整模型构造、HTTP 服务或 engine；不能把它表述为一次实际请求已消耗资源或造成错误执行，也没有证据表明合法 router 会生成这种 mark。

**建议。** 将 prepared envelope 与 late-D link 的共同不变量写成版本化 schema/轻量共享类型，并加入跨语言正反例 fixtures。各接收端继续独立校验；frontend 可以继续把 engine 私有字段当 opaque payload，但必须严格处理自己用来关联两条 leg 的 ID、候选身份与共同 link。不要以“消除重复”为理由移除不可信输入入口处的验证。

性质：已复现局部契约校验缺陷；系统后果未验证。


## 7. M2：launch 在原始 CLI token 上做语义合并，已有局部配置缺陷

**证据与符号。** [launch._flag/load](https://github.com/mit-han-lab/EvolvingInfra/blob/738f586b9d0135cd51dba6b267d7962134478c7b/evolving_infra/launch/__main__.py#L31) 把每个 argv token 都当作完整 flag，以 `split('=',1)[0]` 识别名字，再删除 base 中同名的 token。它无法把 `--page-size` 与后面的 `64` 视为一个参数。

用内存中的两份 YAML、源码原 `load` 函数复现：

```text
base.engine_args  = ['--page-size', '64', '--trust-remote-code']
child.engine_args = ['--page-size', '128']
merged            = ['64', '--trust-remote-code', '--page-size', '128']
```

继承覆盖之后旧值 `64` 留成孤立参数。仓库所读示例主要使用 `--flag=value`，因此这不是说示例部署必然失败；已确认的是 `load` 对分离 token 形式的合并错误，没有启动 engine 验证 argparse 最终报错。

**更大的结构负担。** [Stack.__init__](https://github.com/mit-han-lab/EvolvingInfra/blob/738f586b9d0135cd51dba6b267d7962134478c7b/evolving_infra/launch/serve.py#L47) 还要从字符串推断 EAGLE 模式；[start_services](https://github.com/mit-han-lab/EvolvingInfra/blob/738f586b9d0135cd51dba6b267d7962134478c7b/evolving_infra/launch/serve.py#L154) 再分别拼出 indexer、frontend、prepared contract 和 router 配置。frontend 的任意 mapping override 最后生效，但 prepared contract 仍由独立代码构造；这些跨进程应一致的事实没有先形成一个已解析部署契约。此处不额外声称当前默认配置冲突。

**建议。** 短期明确验证只接受 `--name=value`（布尔开关除外），给错误写法明确错误；或者使用能保留 flag/value 配对的规范化表示。跨进程共享的 model/page/hash/speculation/image 设置在启动前解析一次，从同一个部署对象生成各进程配置，并在这里拒绝冲突的 override。保持 `layout.Worker` 的端口计算独立，不将它并回启动脚本。

性质：已复现局部合并缺陷；跨进程契约漂移是静态维护风险。
