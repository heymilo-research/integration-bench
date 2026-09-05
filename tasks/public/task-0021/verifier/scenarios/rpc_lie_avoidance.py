"""rpc_lie_avoidance -- placements must come from v1, never from v2's lie.

Does NOT re-run the connector -- re-reads the SAME placements.json that
`version_discovery.py` produced this grade.

The vendor boots (per docker-compose.yaml) with GH_V2_RPC_COLLECTION=placements:
every `/v2/placements` request -- list or by-id, any params -- returns HTTP
200 wrapping a JSON-RPC-style error envelope (`{"jsonrpc": "2.0", "error":
{...}}`), never real data. There is no breadcrumb pointing away from v1 for
this collection (v1 is fully healthy here), so a connector that decides
per-collection from what it actually observes has no reason to prefer v2 for
placements at all. A connector that instead blanket-switches everything to
v2 (reasoning "v2 fixed candidates, so v2 must be the better version overall")
gets this collection wrong: it either crashes on the envelope's shape (no
`data` key) or -- worse -- ingests the error object's own fields as if they
were placement records.

Replaced check: the whole-document compare `placements_backfill_exact` is now
the per-row, per-field `placements_rows_exact` diff.

L1 : placements.json matches the 400-row answer key exactly; three named rows
     whose wire timestamps carry different numeric UTC offsets match the answer
     key exactly (row-level legibility for the same claim); no ingested row
     carries the RPC-error envelope's shape (a `jsonrpc` or `error` key).
L3 : only_v1_used_for_placements (traffic-conditional) -- at least one
     `/v1/placements` call actually served the collection, and the output is
     byte-correct (the only way byte-correct placement data can exist at all,
     since `/v2/placements` never returns real records here).
L2 : none -- the builtin conduct gates run ONCE per vendor lifetime, in the
     last scenario (see version_discovery.py's module docstring).
"""

from __future__ import annotations

import json

from bench.verifier.io import read_json_output

# Three placements whose wire `modified_at` carries a different numeric UTC
# offset (+05:30, +03:30, -08:00) -- two half-hour zones and one negative
# whole-hour zone, so a row-exact check on them is sensitive to the offset
# actually being honored rather than dropped.
_PLACEMENT_ROW_IDS = ("plc_00003", "plc_00013", "plc_00005")


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

    placements = read_json_output(ctx.output_dir / "placements.json", timeout_s=15.0)
    if placements is None:
        ctx.check(
            "placements_output_readable",
            False,
            "missing/unreadable placements.json",
            pass_value=0,
            fail_value=-1,
            mandatory=False,
        )
        return

    # The RPC-lie trap: /v2/placements returns HTTP 200 wrapping a JSON-RPC
    # error envelope for every request, so byte-correct placement data can only
    # come from v1. A connector that blanket-switched to v2 to fix candidates
    # either crashes here or ingests the error object's own fields as records.
    fixture = json.loads((ctx.fixtures / "placements_backfill.json").read_text(encoding="utf-8"))
    placement_diffs = _row_diff(placements, fixture)
    matches_fixture = not placement_diffs
    ctx.check("placements_rows_exact",
        matches_fixture,
        _diff_detail("placements", placements, fixture, placement_diffs),
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )

    fixture_by_id = {r["source_id"]: r for r in fixture}
    output_by_id = {r.get("source_id"): r for r in placements}
    for plc_id in _PLACEMENT_ROW_IDS:
        want = fixture_by_id.get(plc_id)
        got = output_by_id.get(plc_id)
        ctx.check(f"placement_row_exact:{plc_id}",
            want is not None and got == want,
            f"got={got!r}" if got != want else "row matches answer key",
            pass_value=1,
            fail_value=0,
            mandatory=False,
        )

    # Belt-and-suspenders: no row's `data` carries the RPC-error envelope's
    # own keys (a connector that treated the envelope as a page of "data"
    # would either produce zero rows or leak `jsonrpc`/`error` into a row).
    tainted = [
        r.get("source_id")
        for r in placements
        if "jsonrpc" in (r.get("data") or {}) or "error" in (r.get("data") or {})
    ]
    ctx.check("no_placement_row_carries_rpc_envelope",
        not tainted,
        f"tainted rows={tainted[:5]}",
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )

    # ------------------------------------------------------------------- L3
    log = vendor.request_log()
    v1_placement_calls = [
        e
        for e in log
        if e.get("method") == "GET"
        and e.get("path") == "/v1/placements"
        and int(e.get("status", 200)) == 200
    ]
    v2_placement_calls = [
        e for e in log if e.get("method") == "GET" and e.get("path") == "/v2/placements"
    ]
    placement_traffic = v1_placement_calls + v2_placement_calls
    if placement_traffic:
        ctx.check("only_v1_used_for_placements",
            len(v1_placement_calls) > 0 and matches_fixture,
            f"v1_placement_calls={len(v1_placement_calls)} v2_placement_calls={len(v2_placement_calls)} "
            f"matches_fixture={matches_fixture}",
            pass_value=2,
            fail_value=0,
            mandatory=False,
        )
