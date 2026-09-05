"""Shared helpers for the task-0022 scenarios.

Pure polling task: the connector is driven entirely through one-shot
subcommands (``sync`` / ``dump``) via ``ctx.app.run([...])``. No long-lived
listener, so no serve lifecycle is needed here.
"""

from __future__ import annotations

import json
from typing import Any

from bench.verifier.io import read_json_output


def _stack(ctx):
    return ctx.app._stack


def reset_store(ctx) -> None:
    """Drop the canonical sqlite DB so each scenario starts empty.

    Scenarios share one DB file on the ``canonical-data`` volume for the whole
    grade; without this, tombstones/watermarks from an earlier scenario leak.
    """
    from bench.canonical_sqlite import reset_canonical_on_stack

    reset_canonical_on_stack(_stack(ctx))



def dump_store(ctx) -> list[dict[str, Any]] | None:
    """Snapshot the canonical store to output/candidates.json and read it back."""
    out = ctx.output_dir / "candidates.json"
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


def row_diff(store: list[dict[str, Any]] | None,
             want: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per-source_id, per-field comparison against the answer key.

    The targeted replacement for the deleted `store == fixture` blob compares
    (`backfill_matches_fixture`, `incremental_matches_fixture`). Their deletion
    left this task with NO field-level content check at all: the fixture was
    still loaded, but only for `len(fixture)`, so `backfill_row_count_6000` and
    `exactly_once` graded the row COUNT and the id multiset while every field of
    all 6,000 rows — including GlobalHire's per-record numeric UTC offsets — went
    unchecked. "Converge" is half this task's mechanic, and convergence on the
    right number of wrong rows is not convergence.

    Order-insensitive by source_id: emission order is not part of the contract.
    """
    if store is None:
        return [{"source_id": "<no output>", "field": "<store unreadable>"}]
    got_by_id = {r.get("source_id"): r for r in store}
    want_by_id = {r.get("source_id"): r for r in want}
    diffs: list[dict[str, Any]] = []
    for sid in sorted(set(want_by_id) | set(got_by_id), key=str):
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
                limit: int = 3) -> str:
    n = "none" if store is None else len(store)
    if not diffs:
        return f"{label}: {n} row(s), every field matches the answer key"
    shown = json.dumps(diffs[:limit], sort_keys=True, default=str)
    more = f" (+{len(diffs) - limit} more)" if len(diffs) > limit else ""
    return f"{label}: rows={n} expected={len(want)}; {len(diffs)} field diff(s): {shown}{more}"
