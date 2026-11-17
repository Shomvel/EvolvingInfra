# Reader control progress validation

Candidate: `f2cbd744d4aeb917b3698475854aeda179acad60`, based on upstream `738f586b9d0135cd51dba6b267d7962134478c7b`.

## CPU

Ubuntu/Linux, Python 3.12.3, uv 0.13.8, 8 vCPU / 16 GiB. Commands used the same isolated sandbox, serially.

| Command | Result |
|---|---|
| Base + new regression tests: `uv run --locked --extra client --extra frontend --extra indexer --extra planner --extra test pytest -q tests/engine/test_pull_reader.py` | Expected exit 1: 2 failed, 2 passed. The second transfer could not send DONE while the first lookup was blocked. |
| Candidate: `uv run --locked --extra client --extra frontend --extra indexer --extra planner --extra test pytest -q tests/engine` | 18 passed |
| Candidate: `uv run --locked --extra client --extra frontend --extra indexer --extra planner --extra test pytest -q tests` | 452 passed, 6 skipped, 55 subtests passed, 1 warning |
| `uv lock --check` | exit 0 |
| `uv run --locked python tools/check_agent_files.py` | exit 0 |

The base run used the initial regression tests; a later cleanup removed a thread-name assertion after the failing DONE assertion. The final full suite used the committed tests (SHA256 `c611eb5495576736bfdca3b26b78f229d7e4fc6ea82be5bc4ce37a30db065c75`). The warning is aiohttp's AppKey warning in an unchanged indexer test. The sandbox was released after logs and hashes were downloaded.

[Base log](base-red.log), [final CPU log](final-cpu.log), [CPU commands and fingerprints](cpu-summary.json).

## Native GPU serving

One node, 2x H200, Qwen3-4B BF16, TP1, fixed 1P1D, page 64, context 32768, chunk 4096, KV budget 131072 tokens per worker, maximum 16 requests per worker. Actual software: torch 2.13.0+cu129, CUDA 12.9, FlashInfer 0.6.17, Mooncake 0.3.13. This is the qualified CUDA 12.9 source environment, not the project's CUDA 13 image.

The unchanged Rust router and prepared SDK drove P -> native Mooncake RDMA -> D. Eight requests completed with exact 32-token lengths, exact prompt echoes, contiguous usage and terminal DONE; a separate 4096-token stream was closed after the first output. The next request completed. Coverage included a 4096-token reused prefix and four concurrent requests. Both workers found HCAs without TCP fallback. Before shutdown, both reader import counts and export-table entry counts were zero. The job and its one replica reached Succeeded; both GPUs reported zero memory after shutdown.

Run with the supplied [smoke client](native_smoke.py):

```bash
python native_smoke.py --stack <work-dir>/stack.json --out native-smoke.json
```

The GPU environment includes a separate [MHA page-stride compatibility patch](mha-page-stride.patch), which is not in this PR. Source and patch fingerprints are retained in [gpu-summary.json](gpu-summary.json). The three overlaid reader-progress files match the candidate commit exactly.

The first request recorded CHUNK reception at 1794883876.9185169, FINAL reception at 1794883876.9317908, and first READ start at 1794883877.2830203. FINAL was processed about 351 ms before the READ began; the control thread progressed during the page-lookup/dispatch interval. This is a behavior observation, not a latency or throughput speedup measurement.

Cancellation on GPU occurred after decode started, when import was complete. Cancellation during blocked page lookup is covered by the CPU channel regression. The outer serve process exited 0 without a harness forced kill, but engine children used their SIGQUIT cleanup path after SIGTERM; this does not establish natural exit of every child.

No multi-node, TP>1, DSA, saturation, repeated A/B or full replay test was run. No throughput gain is claimed. Router Rust and Trie tests were not rerun because those components are unchanged.

## check-pr

1. One purpose: keep reader control processing independent of destination page translation; owner is @Shomvel; no linked issue.
2. Design: reuses the existing transfer pool, in-flight reservation and cancellation drain; no new threads, flags, defaults or environment-specific code.
3. Validation: base failure, candidate full CPU suite and native GPU serving above completed.
4. Docs: callback threading and `xfer.dst_pages` phase are documented in the symmetric README and interface.
5. Contents: PR template retained, AI assistance declared; scrubbed transcript publication and the private engine pseudonym were requested by the owner. Human review remains with the owner.
