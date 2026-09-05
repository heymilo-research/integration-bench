"""Shared helpers for the task-0020 scenarios.

These wrap the harness's ComposeStack/AppHandle/VendorHandle so each scenario
reads as a short sequence of intent-level steps. Nothing here mutates the
harness — it only uses the stack objects the harness hands the scenario.

This is a pure polling task: the connector is driven entirely through one-shot
subcommands (``sync`` / ``dump``) via ``ctx.app.run([...])``. There is no
long-lived listener, so no serve lifecycle is needed here.
"""

from __future__ import annotations

import json
from typing import Any

from bench.verifier.io import read_json_output


def _stack(ctx):
    # AppHandle stores the ComposeStack the harness created for this grade.
    return ctx.app._stack


# ---------------------------------------------------------------------------
# Store isolation
# ---------------------------------------------------------------------------

def reset_store(ctx) -> None:
    """Drop the canonical sqlite DB so each scenario starts empty.

    Scenarios share one DB file on the ``canonical-data`` volume for the whole
    grade; without this, tombstones/watermarks from an earlier scenario leak.
    """
    from bench.canonical_sqlite import reset_canonical_on_stack

    reset_canonical_on_stack(_stack(ctx))


# ---------------------------------------------------------------------------
# Store inspection
# ---------------------------------------------------------------------------

def dump_store(ctx) -> list[dict[str, Any]] | None:
    """Snapshot the canonical store to output/candidates.json and read it back."""
    out = ctx.output_dir / "candidates.json"
    # Remove any stale snapshot so we never read a previous scenario's file.
    try:
        out.unlink()
    except OSError:
        pass
    code, _stdout, _stderr = ctx.app.run(["dump"])
    if code != 0:
        return None
    return read_json_output(out, timeout_s=15.0)


def load_fixture(ctx, name: str) -> list[dict[str, Any]]:
    return json.loads((ctx.fixtures / name).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Split-brain (v2) regression guardrail — task-0020 spec §3a.
# ---------------------------------------------------------------------------

def gh_v2_env_value(ctx) -> str:
    """Read GH_V2_ENABLED directly off the running vendor container.

    The verifier process's own environment is a DIFFERENT process from the
    vendor container docker-compose.yaml boots — checking os.environ here
    would prove nothing about what the vendor was actually launched with.
    """
    stack = _stack(ctx)
    result = stack.exec(
        "vendor", "sh", "-c", "printf '%s' \"${GH_V2_ENABLED:-0}\"", check=False,
    )
    return (result.stdout or "0").strip()


def assert_gh_v2_disabled(ctx, request_log: list[dict[str, Any]], label: str) -> None:
    """Regression guardrail, not new machinery (task-0020 spec §3a).

    GlobalHire also hosts the split-brain dual-version mechanic
    (GH_V2_ENABLED / GH_V1_TRUNCATE / GH_V2_RPC_COLLECTION) — a
    globalhire-native telling of that split-brain story is out of THIS
    task's scope (bullpen's task-0044 is a different vendor's version of it;
    a globalhire-native v1/v2 coexistence task is its own suite entry).
    task-0020 stays pinned v1-only. These two checks catch env drift before
    any split-brain-shaped behavior could silently leak into a v1-only task
    if envs were ever miscopied:

      1. the vendor container's own GH_V2_ENABLED is unset/"0", and
      2. no request in this scenario's log ever targeted /v2/*.

    The Deprecation/Link breadcrumb header itself never fires while
    GH_V2_ENABLED is unset — main.py's `_is_v1_truncated_collection_path`
    short-circuits False before the header is ever attached — so there is
    nothing else live to probe for; absence of /v2 traffic (plus the env
    read above) is the complete, sandbox-observable guardrail.
    """
    suffix = f":{label}" if label else ""
    v2_env = gh_v2_env_value(ctx)
    # 0/-1 (MEASURED): both guardrails pass on the empty probe — they assert
    # facts about the ENVIRONMENT and about traffic the starter never sends, not
    # about anything a submission has to earn. They exist to catch env drift, so
    # they must cost when they break and pay nothing when they hold.
    ctx.check(f"gh_v2_stays_disabled{suffix}",
        v2_env != "1",
        f"vendor container GH_V2_ENABLED={v2_env!r} (must stay unset/0 — this task is v1-only)",
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )
    v2_requests = [e for e in request_log if str(e.get("path", "")).startswith("/v2")]
    ctx.check(f"no_v2_traffic{suffix}",
        not v2_requests,
        f"{len(v2_requests)} request(s) targeted /v2/* (task is v1-only): "
        f"{[e.get('path') for e in v2_requests[:3]]}",
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )


# ---------------------------------------------------------------------------
# Answer-key comparison
# ---------------------------------------------------------------------------

def row_diff(store: list[dict[str, Any]] | None,
             want: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per-source_id, per-field comparison against an answer key.

    The targeted replacement for this task's `store == fixture` blob compares
    (`initial_sync_matches_fixture`, `incremental_matches_fixture`,
    `incr_regression_probe_store_unchanged`). Over a 6000-row store a blob
    compare reports only "rows=N fixture rows=M", which says nothing about WHICH
    of the three stale-doc lies a submission tripped; this names the row and the
    field. Order-insensitive: dump order is not part of the contract.
    """
    if store is None:
        return [{"source_id": "<no output>", "field": "<store unreadable>"}]
    got_by_id = {r.get("source_id"): r for r in store}
    want_by_id = {r.get("source_id"): r for r in want}
    diffs: list[dict[str, Any]] = []
    for sid in sorted(set(want_by_id) | set(got_by_id)):
        w, g = want_by_id.get(sid), got_by_id.get(sid)
        if g is None:
            diffs.append({"source_id": sid, "field": "<missing row>"})
            continue
        if w is None:
            diffs.append({"source_id": sid, "field": "<unexpected row>"})
            continue
        for key in sorted(set(w) | set(g)):
            if w.get(key) != g.get(key):
                diffs.append({"source_id": sid, "field": key,
                              "want": w.get(key), "got": g.get(key)})
    return diffs


def diff_detail(label: str, store: list[dict[str, Any]] | None,
                want: list[dict[str, Any]], diffs: list[dict[str, Any]],
                limit: int = 4) -> str:
    n = "none" if store is None else len(store)
    if not diffs:
        return f"{label}: {n} row(s), every field matches the answer key"
    shown = json.dumps(diffs[:limit], sort_keys=True, default=str)
    more = f" (+{len(diffs) - limit} more)" if len(diffs) > limit else ""
    return f"{label}: rows={n} expected={len(want)}; {len(diffs)} field diff(s): {shown}{more}"
