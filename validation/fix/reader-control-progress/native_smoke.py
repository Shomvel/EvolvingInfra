# SPDX-FileCopyrightText: Copyright (c) 2026 The evolving-infra Authors
# SPDX-License-Identifier: MIT
"""Exercise the prepared SDK, real P/D imports, cancellation and prefix reuse."""
import argparse
import asyncio
import json
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from evolving_infra.router.client.agent.identity import RequestIdentity
from evolving_infra.router.client.backend.backend import Backend
from evolving_infra.router.client.scheduler.scheduler import Scheduler


async def values(raw):
    buffer = b""
    async for part in raw:
        buffer += part
        buffer = buffer.replace(b"\r\n", b"\n")
        while b"\n\n" in buffer:
            frame, buffer = buffer.split(b"\n\n", 1)
            body = b"\n".join(line[5:].lstrip() for line in frame.splitlines()
                               if line.startswith(b"data:"))
            if body == b"[DONE]":
                yield None
            elif body:
                yield json.loads(body)
    if buffer.strip():
        raise ValueError("Incomplete SSE frame")


async def run(stack, output):
    backend = Backend(timeout_s=600)
    scheduler = Scheduler(stack["router_url"], backend=backend)
    trajectory = "reader-progress-" + uuid.uuid4().hex
    rows = []
    report = {"passed": False, "requests": rows, "trajectory": trajectory}

    async def request(branch, turn, prompt, budget=32, cancel_after_first=False):
        identity = RequestIdentity(trajectory, branch, turn)
        body = {"protocol_version": 1, "request_id": identity.engine_rid,
                "config_id": stack["config_id"],
                "input": {"model": stack["model_path"], "input_ids": prompt},
                "generation": {"sampling_params": {
                    "temperature": 0.0, "max_new_tokens": budget,
                    "min_new_tokens": budget, "ignore_eos": True,
                    "skip_special_tokens": False},
                    "stream": True, "return_prompt_token_ids": True}}
        started = time.monotonic()
        async with backend._client().post(stack["tokenizer_url"] + "/prepare", json=body,
                                          allow_redirects=False) as response:
            response.raise_for_status()
            prepared = await response.json()
        raw = scheduler.stream_prepared(stack["tokenizer_url"], prepared, identity=identity)
        tokens, events, last_meta = [], [], {}
        done = terminal = echoed = cancelled = False
        try:
            async for value in values(raw):
                events.append({"elapsed_s": time.monotonic()-started, "value": value})
                if value is None:
                    done = True
                    continue
                if done or "error" in value:
                    raise ValueError("Native stream error or output after DONE")
                delta = value["output_ids"]
                assert all(type(x) is int for x in delta)
                tokens.extend(delta)
                last_meta = value["meta_info"]
                assert last_meta["completion_tokens"] == len(tokens)
                if value.get("prompt_token_ids") is not None:
                    assert value["prompt_token_ids"] == prompt
                    echoed = True
                if last_meta.get("finish_reason") is not None:
                    assert last_meta["finish_reason"]["type"] in {"length", "stop"}
                    terminal = True
                if cancel_after_first and tokens:
                    assert len(tokens) < budget
                    cancelled = True
                    break
        finally:
            await raw.aclose()
        row = {"branch": branch, "turn": turn, "rid": identity.engine_rid,
               "prompt_tokens": len(prompt), "max_new_tokens": budget,
               "output_tokens": len(tokens), "elapsed_s": time.monotonic()-started,
               "cancelled_after_first_output": cancelled, "terminal": terminal,
               "done": done, "prompt_echo_matches": echoed,
               "cached_tokens": last_meta.get("cached_tokens", 0), "events": events}
        rows.append(row)
        output.write_text(json.dumps(report, indent=2) + "\n")
        if not cancel_after_first:
            assert done and terminal and echoed and len(tokens) == budget
        else:
            assert cancelled and not done
        return row

    prefix = [100+(i*17)%1000 for i in range(4096)]
    try:
        await request(0, 1, prefix)
        second = await request(0, 2, prefix+[1001+i for i in range(128)])
        assert second["cached_tokens"] >= 64
        await request(1, 1, prefix)
        await asyncio.gather(*(request(branch, 1, prefix+[1200+branch]*128)
                               for branch in range(2, 6)))
        await request(6, 1, prefix+[1500]*128, budget=4096, cancel_after_first=True)
        ended = await scheduler.end_trajectory(trajectory, 6)
        assert ended.get("accepted") is True
        report["cancel_end_accepted"] = ended
        # A new request must progress after the client closes the previous D stream.
        await request(7, 1, prefix+[1600]*128)
        endings = []
        for branch in [0, 1, 2, 3, 4, 5, 7]:
            answer = await scheduler.end_trajectory(trajectory, branch)
            assert answer.get("accepted") is True
            endings.append({"branch": branch, "answer": answer})
        report["branch_ends"] = endings
        report["backend_loads_after_requests"] = await scheduler.get_backend_loads()
        host = urlsplit(stack["router_url"]).hostname
        states = []
        for port in (30000, 31000):
            async with backend._client().get(f"http://{host}:{port}/server_info") as response:
                response.raise_for_status()
                info = await response.json()
            ranks = [item["symmetric_pd"] for item in info["internal_states"]]
            assert ranks and all(item["reader"]["import_count"] == 0 for item in ranks)
            assert all(item["export_table"]["entries"] == 0 for item in ranks)
            states.append({"port": port, "ranks": ranks})
        report["engine_states_after_requests"] = states
        report["passed"] = True
    finally:
        await backend.close()
        output.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--stack", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(run(json.loads(args.stack.read_text()), args.out))
