"""split_brain_composition -- the top rung: all three collections correct
from the SAME run, not three separately-lucky ones; and the one place the
builtin L2 conduct gates run for this task.

Does NOT re-run the connector -- re-reads the SAME three output files
`version_discovery.py` produced this grade, and re-derives both routing
signals from the full accumulated request log. This is deliberately the
literal "fixing A breaks B" trap named in the ticket's business story: a
connector that blanket-switches every collection to v2 to fix candidates
simultaneously wrecks placements (v2's RPC lie); a connector that stays
all-v1 to keep placements safe simultaneously keeps candidates truncated.
Both mistakes already fail their own collection's L1 rung individually
(`candidates_rows_exact` / `placements_rows_exact`), but this
scenario is the single gate that requires every one of those individual
proofs to hold AT ONCE, from one run, plus both routing signals -- the
composed claim the earlier scenarios only checked piecewise.

Why builtin_l2 runs HERE and nowhere else: those gates are prohibitions read
off the vendor's request log, and a running connector passes them for free.
Invoking them once per scenario multiplies that free credit by the scenario
count and inflates the do-nothing floor (measured suite-wide, WORKLOG
2026-08-01). The correct cadence is once per vendor LIFETIME -- a vendor
unlinks its logs at every boot, so each recreate() epoch needs its own
invocation or its traffic is lost. This task never calls recreate(): one boot,
one accumulating log, so exactly one invocation, placed last where the log is
complete. It is reached only when the connector's run exited 0 AND all three
outputs are readable -- a crashed or output-less run must never bank vacuous
conduct credit on a short/empty log.

L3 : split_brain_composition -- candidates.json,
     placements.json, and agencies.json all match their answer keys, AND
     candidate traffic was predominantly v2, AND placement traffic used v1,
     all simultaneously. Recorded unconditionally (both branches record it),
     so it is mandatory: this is the composed claim the task exists to make.
L2 : the builtin conduct gates/soft checks, once for the whole grade.
"""

from __future__ import annotations

import json

from bench.verifier.builtin_l2 import builtin_l2
from bench.verifier.io import read_json_output

_ENTITIES = ("candidates", "placements", "agencies")


def _load_fixture(ctx, name: str):
    return json.loads((ctx.fixtures / name).read_text(encoding="utf-8"))


def _row_diff(actual: list | None, expected: list) -> list[dict]:
    """Per-source_id, per-field comparison against an answer key.

    Replaces this task's `output == fixture` blob compares
    (`candidates_backfill_exact`, `agencies_backfill_exact`,
    `placements_backfill_exact`, and the `matches` dict in
    split_brain_composition.py). Two differences that matter: it is
    order-insensitive (emission order is not part of the contract, and a
    6,000-row v2 cursor crawl need not land in the fixture's order), and it
    names the row and field that disagree instead of returning one opaque bool.
    """
    if actual is None:
        return [{"source_id": "<no output>", "field": "<unreadable>"}]
    got_by_id = {r.get("source_id"): r for r in actual}
    want_by_id = {r.get("source_id"): r for r in expected}
    diffs: list[dict] = []
    for sid in sorted(set(want_by_id) | set(got_by_id), key=str):
        want, got = want_by_id.get(sid), got_by_id.get(sid)
        if got is None:
            diffs.append({"source_id": sid, "field": "<missing row>"})
            continue
        if want is None:
            diffs.append({"source_id": sid, "field": "<unexpected row>"})
            continue
        for key in sorted(set(want) | set(got)):
            if want.get(key) != got.get(key):
                diffs.append({"source_id": sid, "field": key,
                              "want": want.get(key), "got": got.get(key)})
    return diffs


def _diff_detail(label: str, actual: list | None, expected: list,
                 diffs: list[dict], limit: int = 3) -> str:
    n = "none" if actual is None else len(actual)
    if not diffs:
        return f"{label}: {n} row(s), every field matches the answer key"
    shown = json.dumps(diffs[:limit], sort_keys=True, default=str)
    more = f" (+{len(diffs) - limit} more)" if len(diffs) > limit else ""
    return f"{label}: rows={n} expected={len(expected)}; {len(diffs)} diff(s): {shown}{more}"


async def run(ctx) -> None:
    vendor = ctx.vendor("globalhire")

    outputs = {}
    all_readable = True
    for name in _ENTITIES:
        out = read_json_output(ctx.output_dir / f"{name}.json", timeout_s=15.0)
        outputs[name] = out
        if out is None:
            all_readable = False

    if not all_readable:
        ctx.check("split_brain_composition",
            False,
            f"one or more outputs unreadable: "
            f"{ {name: outputs[name] is not None for name in _ENTITIES} }",
            pass_value=2,
            fail_value=0,
            mandatory=True,
        )
        return

    # Per-row, per-field rather than `== fixture` (see _row_diff's docstring).
    matches = {
        name: not _row_diff(outputs[name], _load_fixture(ctx, f"{name}_backfill.json"))
        for name in _ENTITIES
    }

    log = vendor.request_log()

    def _calls(method: str, path: str) -> list[dict]:
        return [e for e in log if e.get("method") == method and e.get("path") == path]

    v1_candidates = _calls("GET", "/v1/candidates")
    v2_candidates = _calls("GET", "/v2/candidates")
    candidate_routing_ok = (
        len(v2_candidates) > 0 and len(v2_candidates) > len(v1_candidates) and len(v1_candidates) <= 5
    )

    v1_placements = [e for e in _calls("GET", "/v1/placements") if int(e.get("status", 200)) == 200]
    placement_routing_ok = len(v1_placements) > 0 and matches["placements"]

    composed = (
        matches["candidates"]
        and matches["placements"]
        and matches["agencies"]
        and candidate_routing_ok
        and placement_routing_ok
    )
    ctx.check("split_brain_composition",
        composed,
        f"matches={matches} candidate_routing_ok={candidate_routing_ok} "
        f"placement_routing_ok={placement_routing_ok} "
        f"v1_candidates={len(v1_candidates)} v2_candidates={len(v2_candidates)} "
        f"v1_placements={len(v1_placements)}",
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )

    # ------------------------------------------------------------------- L2
    # Once per vendor lifetime, and only on a run that actually produced
    # output (see the module docstring). `app_run_exit_code` is stashed on ctx
    # by version_discovery.py -- the only scenario that drives the connector.
    if getattr(ctx, "app_run_exit_code", None) != 0:
        return

    await builtin_l2(ctx)
