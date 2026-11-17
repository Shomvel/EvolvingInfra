# EvolvingInfra analysis and bug-fix session excerpts

The original analysis session comes first, followed by the implementation session. Both excerpts were scrubbed and explicitly authorized for publication.

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

---

# EvolvingInfra bug-fix session excerpts

These are excerpts of the user request, assistant updates, production-code edits and recorded validation for three independent fixes. Discovery, inherited instructions, hidden reasoning and test implementation calls are omitted; the tests are included in each pull request.

## User

[分析 EvolvingInfra 架构设计](codex://threads/<redacted>)

里面 Evolving Infra 有 bug 的地方给它们提pr

## Assistant

我先读取这段架构分析和相关代码，确认记录的 bug，再逐项修复、验证并提交 PR。

## Assistant

原会话里有三处已复现的 bug：MHA 的 KV 页步长重复乘法、配置继承遗留旧参数值，以及嵌套 transfer ID 接受布尔值。我会分别提交 PR，并核对上游是否已修复。仓库要求提 PR 前运行 `check-pr` 技能，我也会按它准备验证证据和脱敏会话记录。

## Assistant

上游 `main` 仍是分析时的版本，这三处 bug 都还在。MHA 修复已有同一补丁的真实双 H200 验证：674 个请求全部成功；我会补入仓库回归测试。另外两处会验证配置生成和 HTTP 拒绝行为，包括畸形请求是否会提前消耗输入。

## Implementation tool call (excerpt)

```diff
*** Update File: /Users/<user>/Downloads/evolvinginfra-bugfix-prs-20261117/launcher/evolving_infra/launch/__main__.py
@@
-def _flag(arg: str) -> str:
-    return arg.split("=", 1)[0]
+def _argument_groups(args: list[str]) -> list[list[str]]:
+    """Keep each option with its following values, including negative numbers."""
+    groups = []
+    for arg in args:
+        if not groups or arg.startswith("--") or (arg.startswith("-") and arg[1:2].isalpha()):
+            groups.append([arg])
+        else:
+            groups[-1].append(arg)
+    return groups
@@
     for key in ("engine_args", "router_args"):
         mine = cfg.pop(key, [])
-        replaced = {_flag(a) for a in mine}
-        out[key] = [a for a in out.get(key, []) if _flag(a) not in replaced] + mine
+        replaced = {group[0].split("=", 1)[0] for group in _argument_groups(mine)}
+        out[key] = [arg for group in _argument_groups(out.get(key, []))
+                    if group[0].split("=", 1)[0] not in replaced for arg in group] + mine
*** Update File: /Users/<user>/Downloads/evolvinginfra-bugfix-prs-20261117/launcher/docs/configuration.md
@@
-| `base` | another config file to start from; `engine_args` replace the base's flags of the same name | none |
+| `base` | another config file to start from; `engine_args` and `router_args` replace the base's options of the same name, including their values | none |
@@
 client processes; `router_args`, `engine_args` and `planner` added to the served config for

```

## Implementation tool call (excerpt)

```diff
*** Update File: /Users/<user>/Downloads/evolvinginfra-bugfix-prs-20261117/frontend/evolving_infra/router/frontend/protocol.py
@@
-        if any(mark.get("transfer_id") != self.transfer_id for mark in
+        if any(type(mark.get("transfer_id")) is not int or mark["transfer_id"] != self.transfer_id for mark in
@@
-        if self.prefill.late_d.get("k") != k:
+        if type(self.prefill.late_d.get("k")) is not int or self.prefill.late_d["k"] != k:
@@
-            if cand.kv_import.get("transfer_id") != self.transfer_id:
+            if type(cand.kv_import.get("transfer_id")) is not int or cand.kv_import["transfer_id"] != self.transfer_id:
                 raise ValueError("Candidate transfer IDs must match the plan")
-            if cand.late_d.get("cand_idx") != i or cand.late_d.get("k") != k:
+            if any(type(cand.late_d.get(key)) is not int or cand.late_d[key] != value
+                   for key, value in (("cand_idx", i), ("k", k))):

```

## Implementation tool call (excerpt)

```diff
*** Update File: /Users/<user>/Downloads/evolvinginfra-bugfix-prs-20261117/frontend/evolving_infra/router/frontend/README.md
@@
-u64 and must match both marks. The P and D endpoints must differ, be in `targets`
+u64 and must match both marks and every decode candidate's `kv_import`.
+Nested transfer IDs, late-D candidate counts (`k`) and candidate indices (`cand_idx`)
+must be JSON integers; booleans and floating-point numbers are rejected with HTTP 400
+before consuming the prepared input or submitting either leg.
+The P and D endpoints must differ, be in `targets`

```

## Assistant

静态排查发现，布尔值和整数相等的问题也出现在 late-D 的候选数量和序号检查中，我会一起修复并补回归。配置继承测试则覆盖了两种参数写法、多个值、负数和多层继承。

## Implementation tool call (excerpt)

```diff
*** Update File: /Users/<user>/Downloads/evolvinginfra-bugfix-prs-20261117/launcher/docs/configuration.md
@@
 | `base` | another config file to start from; `engine_args` and `router_args` replace the base's options of the same name, including their values | none |

+Both argument lists accept `--name=value` and separate tokens such as
+`[--page-size, '64']`. A child option replaces the base option together with all
+of its value tokens. Other options and their values keep their order.
+
*** Update File: /Users/<user>/Downloads/evolvinginfra-bugfix-prs-20261117/mha/evolving_infra/engine/README.md
@@
 - **Pull-based and peer KV transfer** (`srt/disaggregation/`): the decode side pulls KV,
   and a prefill worker can fetch a host-resident prefix from a peer instead of recomputing it.
@@
 ## Naming

```

## Implementation tool call (excerpt)

```diff
*** Update File: /Users/<user>/Downloads/evolvinginfra-bugfix-prs-20261117/launcher/evolving_infra/launch/__main__.py
@@
 def _argument_groups(args: list[str]) -> list[list[str]]:
@@
     return groups


+def merge_args(base: list[str], overrides: list[str]) -> list[str]:
+    """Replace options together with their values, keeping untouched groups."""
+    replaced = {group[0].split("=", 1)[0] for group in _argument_groups(overrides)}
+    return [arg for group in _argument_groups(base)
+            if group[0].split("=", 1)[0] not in replaced for arg in group] + overrides
+
+
 def load(path: Path) -> dict:
@@
-        replaced = {group[0].split("=", 1)[0] for group in _argument_groups(mine)}
-        out[key] = [arg for group in _argument_groups(out.get(key, []))
-                    if group[0].split("=", 1)[0] not in replaced for arg in group] + mine
+        out[key] = merge_args(out.get(key, []), mine)
*** Update File: /Users/<user>/Downloads/evolvinginfra-bugfix-prs-20261117/launcher/examples/slurm/submit.py
@@
-served config's flags of the same name (the name before `=`) and add the others; its `planner`
+served config's options and their values by name and add the others; its `planner`
@@
-from evolving_infra.launch.__main__ import _flag, load  # noqa: E402  (resolves `base:`)
+from evolving_infra.launch.__main__ import load, merge_args  # noqa: E402  (resolves `base:`)
@@
         if bench.get(key):
-            replaced = {_flag(a) for a in bench[key]}
-            cfg[key] = [a for a in cfg.get(key) or [] if _flag(a) not in replaced] + list(bench[key])
+            cfg[key] = merge_args(cfg.get(key) or [], list(bench[key]))

```

## Implementation tool call (excerpt)

```diff
*** Update File: /Users/<user>/Downloads/evolvinginfra-bugfix-prs-20261117/mha/evolving_infra/engine/decoupled_engine/srt/disaggregation/symmetric/export_table.py
@@
-    bytes (including draft layers); both ride in the "full" pool. Other
-    non-DSv4 models keep the default layout. DSv4:
+    bytes (including draft layers); both ride in the "full" pool. Native
+    MHA/MLA buffers provide explicit per-page byte strides. DSv4:
*** Update File: /Users/<user>/Downloads/evolvinginfra-bugfix-prs-20261117/mha/evolving_infra/engine/README.md
@@
-## Naming
+## Symmetric KV registration
+
+Native MHA and MLA buffer descriptors report bytes per allocator page.
+Symmetric P/D registers those values as explicit page strides, so each read
+addresses page `p` at `base + p * page_nbytes` and copies one page's bytes.
+This applies to MHA subclasses that retain the native descriptor layout.
+
+## Naming

```

## Assistant

三项修复的定向验证已通过：配置和 Slurm 共 36 项、prepared frontend/client 共 150 项、engine 共 38 项。扩展测试遇到两项网络测试失败，我正在核对原版表现和 Linux 环境；它们不涉及这次修改的校验路径。

## Validation artifacts

The following are tails of the actual command-output logs captured in this session.

### launcher-base.log

```text
FAILED tests/launch/test_launch.py::test_config_overrides_replace_whole_options[base_args7-child_args7-expected7-engine_args]
FAILED tests/launch/test_launch.py::test_config_overrides_replace_whole_options[base_args7-child_args7-expected7-router_args]
FAILED tests/launch/test_launch.py::test_config_overrides_survive_multiple_inheritance_levels
13 failed, 4 passed, 12 deselected in 0.36s
```

### launcher-base-slurm.log

```text
=========================== short test summary info ============================
FAILED tests/launch/test_submit.py::test_a_benchmark_override_replaces_the_option_and_its_values[engine_args]
FAILED tests/launch/test_submit.py::test_a_benchmark_override_replaces_the_option_and_its_values[router_args]
2 failed, 5 deselected in 0.17s
```

### launcher-fixed.log

```text
....................................                                     [100%]
36 passed in 0.70s
```

### frontend-base.log

```text
FAILED tests/frontend/test_distributed_tokenizer_late_d.py::test_late_d_rejects_noninteger_identifiers_before_consuming_input[cand_idx-False-1-0]
FAILED tests/frontend/test_distributed_tokenizer_late_d.py::test_late_d_rejects_noninteger_identifiers_before_consuming_input[cand_idx-True-2-1]
FAILED tests/frontend/test_distributed_tokenizer_late_d.py::test_late_d_rejects_noninteger_identifiers_before_consuming_input[cand_idx-0.0-1-0]
16 failed, 51 deselected in 1.62s
```

### frontend-focused.log

```text
........................................................................ [ 48%]
........................................................................ [ 96%]
......                                                                   [100%]
150 passed in 7.20s
```

### mha-base.log

```text
FAILED tests/engine/test_symmetric_registration.py::test_mha_registration_copies_pages_within_the_registered_span[source_pages2-target_pages2-True-4]
FAILED tests/engine/test_symmetric_registration.py::test_mha_registration_copies_pages_within_the_registered_span[source_pages2-target_pages2-True-64]
FAILED tests/engine/test_symmetric_registration.py::test_mha_registration_copies_pages_within_the_registered_span[source_pages2-target_pages2-True-128]
18 failed, 6 passed in 0.17s
```

### mha-fixed.log

```text
......................................                                   [100%]
38 passed in 0.11s
```

### frontend-fixed.log

```text
=========================== short test summary info ============================
FAILED tests/frontend/test_distributed_tokenizer.py::test_burst_has_no_admission_rejections_and_every_request_is_processed[512]
SUBFAILED(legacy_keepalive=True) tests/client/test_scheduler_frontend.py::BackendIdleConnectionTests::test_idle_peer_close_during_busy_loop_does_not_retry_post
2 failed, 284 passed, 6 skipped, 19 subtests passed in 34.64s
```

### upstream-macos-network.log

```text
../.venv/lib/python3.12/site-packages/aiohttp/streams.py:716: ClientOSError
=========================== short test summary info ============================
SUBFAILED(legacy_keepalive=True) tests/client/test_scheduler_frontend.py::BackendIdleConnectionTests::test_idle_peer_close_during_busy_loop_does_not_retry_post
1 failed, 2 passed, 1 subtests passed in 12.68s
```

## MHA implementation command and patch

The recorded command applied the previously validated minimal patch:

```text
git apply experiment/mha-page-stride.patch
```

```diff
--- a/evolving_infra/engine/decoupled_engine/srt/disaggregation/symmetric/export_table.py
+++ b/evolving_infra/engine/decoupled_engine/srt/disaggregation/symmetric/export_table.py
@@ -241,8 +241,11 @@
                 + list(kv_args.state_item_lens[i]),
             )
         }
-    if any(cls.__name__ == "MLATokenToKVPool" for cls in type(kvcache).__mro__):
-        # Native MLA buffer descriptors already use bytes per allocator page.
+    if any(
+        cls.__name__ in {"MHATokenToKVPool", "MLATokenToKVPool"}
+        for cls in type(kvcache).__mro__
+    ):
+        # Native MHA/MLA descriptors already use bytes per allocator page.
         # Supplying them as token strides would multiply page_size twice.
         return [
             PoolLayout(

```

## Linux integration validation

Command on a clean Linux source snapshot containing the three fixes:

```sh
uv run --locked --extra client --extra frontend --extra indexer --extra planner --extra test pytest -q tests
```

```text
.........................................................         [100%]
=============================== warnings summary ===============================
tests/indexer/test_query.py::PreparedQueryOverHttp::test_router_prepared_query_reaches_query_by_hash
  /tmp/evolvinginfra-bugfix-prs-20261117/source/evolving_infra/router/indexer/eagle_indexer_query.py:92: NotAppKeyWarning: It is recommended to use web.AppKey instances for keys.
  https://docs.aiohttp.org/en/stable/web_advanced.html#application-s-config
    app["client"] = ClientSession(timeout=ClientTimeout(total=1))

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
507 passed, 6 skipped, 1 warning, 55 subtests passed in 49.26s
```

The isolated macOS baseline reproduced the legacy-keepalive exception mismatch; the 512-request burst passed in that isolated baseline run. Linux completed both cases successfully.

## Owner publication instructions

### User

算了，还是公开对话记录

### User

然后链接到的那个session thread也摘录一下

## Final isolated-branch validation

Each final PR source snapshot was tested independently on Linux with the locked client/frontend/indexer/planner/test extras.

- launcher: 467 passed, 6 skipped, 1 warning, 55 subtests passed in 44.90s
- frontend: 464 passed, 6 skipped, 1 warning, 55 subtests passed in 45.25s
- mha: 472 passed, 6 skipped, 1 warning, 55 subtests passed in 44.76s

Replay client suite: 245 passed, 2 skipped. The Rust router is unchanged; its checks are left to the pull-request CI.


# Review and review-fix session excerpts (2026-11-17)

Visible user prompts and assistant messages from the session that reviewed pull requests #26–#29 and pushed the review fixes to all four branches. Tool calls and their output, hidden reasoning, system and local instructions, and transcript-export bookkeeping are omitted. The Linux test host is shown as `<build host>`. Dates are UTC as recorded by the session.

## User — 2026-11-17T05:11:05.241Z

#28 Reject noninteger identifiers in prepared P/D plans
#27 Replace inherited CLI options together with their values
#26 Fix MHA page strides in symmetric KV transfers https://github.com/mit-han-lab/EvolvingInfra/pulls review一下26~29 PR

## Assistant — 2026-11-17T05:19:50.383Z

Still fetching the repo (the clone is slow), so for now I'm pulling the PR diffs straight from the API.

## Assistant — 2026-11-17T05:23:00.405Z

Got the PR branches; now reading the MHA pool subclasses for #26.

## Assistant — 2026-11-17T05:28:38.419Z

PR #27 is read; on to #28 while the full checkout downloads.

## Assistant — 2026-11-17T05:29:27.600Z

The full fetch is crawling, so I'll pull just the files I need from the partial clone.

## Assistant — 2026-11-17T05:31:19.385Z

Checking whether the scheduler's abort path really waits for an in-flight lookup before reusing pages (#29's main safety claim).

## Assistant — 2026-11-17T05:33:13.018Z

#29 checks out so far. Back to #26: checking which pool types still fall through to the default (double-multiplying) layout.

## Assistant — 2026-11-17T05:36:51.043Z

Found a likely gap in #26 (wrapper pools still double-multiply). Checking whether those pool types are allowed in symmetric P/D at all.

## Assistant — 2026-11-17T05:38:12.798Z

#26 analysis done. Now finishing #27: checking for other users of the removed `_flag` helper and other places that merge arguments.

## Assistant — 2026-11-17T05:39:16.134Z

#27 looks correct; one doc-related gap noted. Now checking #28's `ForwardPD` model for missed fields.

## Assistant — 2026-11-17T05:41:37.609Z

## 结论

我只读了代码，没在本地跑测试：这台机器拉 GitHub 很慢，只取了相关文件。四个 PR 的核心改动都是对的，没有阻塞合并的错误。不过 #26 和 #28 都只修了同一类问题的一部分，建议在合并前补上。

| PR | 结论 | 问题 |
|---|---|---|
| #26 MHA page stride | 目标场景正确，修法不完整 | 1 个中等 |
| #27 CLI 参数覆盖 | 可合并 | 1 个低（文档） |
| #28 整数 ID 校验 | 可合并，但同类遗漏 | 1 个中等 |
| #29 reader 控制面响应 | 可合并 | 无阻塞问题，2 点备注 |

## #26 Fix MHA page strides

**问题（中等）：按类名匹配只修了 MHA 子类，包了一层 MHA 的池子仍会把 page_size 多乘一次。**
- `registration_pools_for` 按 `type(kvcache).__mro__` 里有没有 `MHATokenToKVPool` / `MLATokenToKVPool` 来判断。
- 下面几个池子本身不是 MHA 子类，但 `get_contiguous_buf_infos()` 直接转给内部的 MHA 池，拿到的也是每页字节数（`memory_pool.py:3918`、`:5273`，`swa_memory_pool.py:174`）。它们会落到默认分支，触发 `_pool_region_from_layout` 里的 `kv_item_lens[e] * page_size`，读到注册区之外，跟这次修的错误一模一样：
  - `HybridLinearKVPool`（Qwen3-Next 这类 GDN 混合模型）
  - `SWAKVPool`
  - `MiniMaxSparseKVPool`
- 生产代码里只有 `symmetric/planes.py` 调用 `Registration.from_kv_args`。PR 合并后，没有任何原生池子适合走那个默认分支。
- 建议二选一：
  - 让默认分支也按每页字节处理；
  - 或者遇到不认识的 kvcache 类型直接报错，不要静默按每 token 步长处理。
- 同时更新 `from_kv_args` 文档里"plain MHA/MLA"的说法。
- 这些模型目前在 symmetric 模式下是否本来就跑不通（比如 SWA 池对非 DSv4 模型从未注册），我没确认。但没有任何地方拦截它们，所以出错时不会报错，而是悄悄读错。

**备注：**
- 新测试用 `pytest.importorskip("msgspec")`。CI 里的 msgspec 是 `ai-dynamo` 间接带进来的，以后这个依赖一变，24 个回归用例会被静默跳过。#29 已经把 msgspec 显式加进了 test extra，建议合并顺序是 #29 先或者一起合。
- #29 的 GPU smoke 依赖的"MHA page-stride compatibility patch"就是本 PR。

## #27 Replace inherited CLI options

逻辑正确，测试覆盖了拆开写、`=` 写法、多值、负数、短选项和多层 `base`。

**问题（低）：** 新文档说两种参数列表都接受 `[--page-size, '64']` 这种拆开写法。但 `absolute()`（`launch/__main__.py`）只把 `--name=value` 形式里的相对文件路径转成绝对路径。拆开写的相对文件路径不会被转换，而进程是在工作目录里运行的，路径会失效。这是原有限制，但新文档让它看起来已经支持了。建议让 `absolute()` 也按 `_argument_groups` 分组处理，或者在文档里注明。

**小边界（不必改）：** 以 `--` 开头的值，或者形如 `-Inf` 的值，会被当成选项。这类配置里基本不会出现。

## #28 Reject noninteger identifiers

已加的检查是对的。

**问题（中等）：同一类字段还漏了 `prefill.late_d.default_idx` 和 `router_epoch`。**
- 引擎在 `late_d.py:461-507` 用 `_uint_below` 严格校验了 `v`、`mode`、`router_epoch`、`k`、`default_idx`、`cand_idx`。
- 前端只补了 `k` 和 `cand_idx`。
- 所以 `default_idx: true`、`default_idx: 1.0`、`default_idx >= k`，或者 `router_epoch: 1.0`，都能通过前端，消耗掉只能用一次的 prepared input（`Store.take`），然后才被引擎返回 400。
- 这正好违背了 PR 里"非法请求返回 400 前不消耗输入、修正后可以复用同一个 prepared ref"的承诺。
- 建议按引擎的规则在前端一起校验：
  - `default_idx` 必须在 `[0,k)` 内，且 `local_ok` 时允许 254；
  - `router_epoch` 必须在 `[0,2^32)` 内；
  - `v` 必须是整数。
- 再加对应的参数化用例。

## #29 Keep pull reader controls responsive

**看过没发现问题的部分：**
- `inflight` 在 `_on_chunk` 里提交任务前就加一，覆盖了排队、查页、READ 全过程。所以 `abort_import` 的 `finished.wait` 和 `_finish_pending` 都会等查页结束，之后调度器才释放页面。
- 查页前后各检查一次 `pending_terminal`。
- 失败路径的 `_fail` 和 `_read_finished` 原本就在传输线程上调用，`_send` 走队列，没有 ZMQ 跨线程问题。
- `_dst_pages_fn` 里的 `torch.cuda.device` 和 `current_stream` 都是线程本地的；`pool_pages_for_range` 只读分配器、行表和代际信息，多个查页并发执行是安全的。

**备注（非阻塞）：**
1. 等 CUDA 的查页现在占用的是传输线程（默认 4 个），不再是 socket 线程。4 个查页同时卡住时，其他请求的 READ 会排队。PR 没有声称吞吐提升，但值得在饱和负载下做一次 A/B 对比。
2. phase 从 `io.dst_pages` 改名为 `xfer.dst_pages`。如果有看板或分析脚本按旧名统计，需要同步修改。

---

这几个 PR 都是你的，我可以直接在各自分支上修 #26 和 #28，或者把以上意见作为 PR 评论贴上去。你选哪种？

## User — 2026-11-17T05:42:05.913Z

把这些都修了吧

## Assistant — 2026-11-17T05:42:52.785Z

I'll set up a working checkout. First checking whether <build host> already has an EvolvingInfra clone (better GitHub connectivity than this Mac).

## Assistant — 2026-11-17T05:43:34.145Z

<build host> has fast GitHub access and the original PR working dirs. Inspecting them.

## Assistant — 2026-11-17T05:44:54.634Z

Local worktrees for all four branches exist and are clean. Verifying they match the PR heads.

## Assistant — 2026-11-17T05:45:30.990Z

All four match. Reading the repo's contributor rules before editing.

## Assistant — 2026-11-17T05:49:13.682Z

Confirmed: the Mooncake push path addresses `ptr + page_index * item_len`, so `kv_item_lens` is bytes per page for every model. Now editing #26 to fix the default stride.

## Assistant — 2026-11-17T05:51:00.462Z

Now extending the #26 regression test to cover the wrapper pools and MLA.

## Assistant — 2026-11-17T05:52:26.620Z

Fixed version passes (86). Now showing the new test fails on the current PR head and on the base for the expected reason.

## Assistant — 2026-11-17T05:52:59.326Z

Readable test IDs would help; adding them, then re-running the comparison with summaries.

## Assistant — 2026-11-17T05:53:40.898Z

#26 committed. Now #27: reading the launch guide, then making `absolute()` handle the split form.

## Assistant — 2026-11-17T05:55:06.583Z

Implementing the split-form path resolution in `absolute()`.

## Assistant — 2026-11-17T05:55:47.062Z

Reflowing that docstring and updating the launch guide's `base:` bullet.

## Assistant — 2026-11-17T05:57:30.021Z

The head-comparison printed nothing, which suggests the venv may import `evolving_infra` from a different checkout. Checking.

## Assistant — 2026-11-17T05:58:51.620Z

On the PR head, the split-form value stays relative (`b.json`) as expected; with the fix all 38 launch tests pass. Committing #27.

## Assistant — 2026-11-17T05:59:23.587Z

#27 committed. Now #28: checking which `late_d` fields the router actually sends on each leg.

## Assistant — 2026-11-17T06:01:07.814Z

Now writing the frontend checks.

## Assistant — 2026-11-17T06:01:59.975Z

Updating the validator docstring, then adding the regression test.

## Assistant — 2026-11-17T06:04:42.545Z

#28 committed. For #29, the actionable note is the `io.dst_pages` → `xfer.dst_pages` rename. Searching for stale references to the old name.

## Assistant — 2026-11-17T06:05:49.593Z

`io.dst_pages` was the only `io.*` phase, so after #29 the phase-timer docs describe a phase family that no longer exists. Fixing that line.

## Assistant — 2026-11-17T06:06:43.353Z

All four branches are committed locally. Now running the CI-equivalent checks on Linux (<build host>), as the original PRs did. Checking the <build host> toolchain first.

## Assistant — 2026-11-17T06:07:18.196Z

<build host> has `uv`. I'll ship each branch as a `git archive` into a fresh directory and run the CI commands from a standalone script.

## Assistant — 2026-11-17T06:10:24.207Z

Running the four suites one after another on <build host> (some tests bind fixed local ports, so not in parallel).

## Assistant — 2026-11-17T06:11:37.208Z

While <build host> runs, I'll pull the current PR descriptions to update them.

## Assistant — 2026-11-17T06:11:52.610Z

Drafting the updated #26 description (the <build host> numbers get filled in when the run finishes).

## Assistant — 2026-11-17T06:12:22.832Z

Now #27's description.

## Assistant — 2026-11-17T06:14:34.934Z

The <build host> CI runs finished; checking results.

## Assistant — 2026-11-17T06:14:49.161Z

All four pass on Linux. Confirming the single warning is the existing aiohttp one, then filling in the descriptions.

## Assistant — 2026-11-17T06:15:14.326Z

Trailing `**` left over after the substitution; fixing.

## Assistant — 2026-11-17T06:15:28.969Z

Updating the #28 Details/Transcript lines and the #29 description.

## Assistant — 2026-11-17T06:16:16.747Z

Descriptions are ready. Pushing the four branches to the fork (the Mac's GitHub link is slow, but these pushes are small).

## Assistant — 2026-11-17T06:16:26.996Z

zsh doesn't word-split like bash; using explicit arguments.

## Assistant — 2026-11-17T06:16:46.151Z

Pushed. Updating the PR descriptions; #26's title becomes "Fix KV page strides in symmetric registration" because the fix now covers more than MHA.

## Assistant — 2026-11-17T06:17:59.491Z

Everything is updated. Saving a short memory note about this project's PR workflow for future sessions.

## Assistant — 2026-11-17T06:18:39.423Z

我修了评审里提到的全部问题。四个 PR 都推送了新提交并更新了 PR 描述；完整 CI 测试在 <build host>（Linux, Python 3.12）上四个都通过了。修复都没有上 GPU 跑过。

| PR | 新 head | 改动 | Linux 全量测试 |
|---|---|---|---|
| [#26](https://github.com/mit-han-lab/EvolvingInfra/pull/26) | `0f26309` | 从根上修 stride，标题改为 "Fix KV page strides in symmetric registration" | 520 passed, 6 skipped |
| [#27](https://github.com/mit-han-lab/EvolvingInfra/pull/27) | `08c244d` | `absolute()` 支持拆开写的参数 | 469 passed, 6 skipped |
| [#28](https://github.com/mit-han-lab/EvolvingInfra/pull/28) | `0fbd90e` | 补齐 late-D 字段校验，标题同步修改 | 476 passed, 6 skipped |
| [#29](https://github.com/mit-han-lab/EvolvingInfra/pull/29) | `7b00c97` | 修正过时的 phase 文档 | 452 passed, 6 skipped |

四个分支的 `check_agent_files` 和 `uv lock --check` 也都通过。唯一的 warning 是测试里原有的 aiohttp AppKey 提示。

**#26：** 我确认过，代码里每个 KV 池报告的 `kv_item_lens` 都是"每页字节数"，Mooncake 推送路径也按 `ptr + page_index * item_len` 取地址。所以真正出错的地方是注册时的默认步长又乘了一次 `page_size`。
- 改成直接用 `kv_item_lens` 作为步长，并删掉了按类名判断 MHA/MLA 的分支。这样 `HybridLinearKVPool`、`SWAKVPool`、`MiniMaxSparseKVPool` 这些包装池也一起修好了。
- 回归测试扩到 6 种池 × 4 种页大小，共 72 个用例。在 base 上挂 45 个；在 PR 原来的提交上挂 27 个，全是包装池，例如页大小 64 时读 16384 字节，应为 256；修复后全部通过。

**#27：** `absolute()` 现在按选项分组处理，`--name value` 写法里的相对文件路径也会转成绝对路径。新测试在原提交上失败，修复后通过。同时更新了 `docs/configuration.md`、launch 的 `AGENTS.md` 和 `load()` 的说明。

**#28：** 前端现在和引擎、SDK 用同一套规则，下面这些情况在消耗只能用一次的 prepared input 之前就返回 400：
- `default_idx` 必须在 `[0,k)` 内；
- `router_epoch` 必须在 `[0,2^32)` 内；
- `v` 必须是整数，`mode` 必须是字符串；
- prepared plan 里 `local_ok` 必须不传或为 false；
- 每个候选 D 的 `v`/`mode`/`router_epoch` 必须和 P 的值、类型都一致。

新增的 12 个用例在原提交上全部失败，修复后通过。

**#29：** 代码本身没问题。只修了 phase timer 文档：`io.dst_pages` 原来是唯一的 `io.*` phase，改名后那段说明已经过时。

**还没做的：**
- 新的修复都没上 GPU 跑。#26 PR 描述里记的 GPU 测试是更早的 MHA 版本；hybrid、SWA、MiniMax 模型在 symmetric 模式下也没测过。
- #29 中，多个查页同时卡在等 CUDA 时，传输线程会被占满；这对 READ 并发的影响需要 GPU A/B 才能测，PR 描述里已注明没测。
- 这次修复所在会话的 transcript 还没发布到 fork 的 `agent-logs` 分支。仓库的 check-pr 规则要求先由你确认再发布，三个 PR 描述里都注明了这一点。需要的话我来打码整理并发布。

<build host> 上的测试日志在 `<build-host evidence dir>`。
