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
