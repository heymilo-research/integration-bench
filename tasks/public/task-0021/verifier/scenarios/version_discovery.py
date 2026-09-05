"""version_discovery -- the connector's one full run, and the discovery gate.

This is the only scenario in this task that actually drives the connector
(`ctx.app.run()`); a full v2 cursor crawl of 6,000 candidates is the
expensive part of this grade, so it happens exactly once here. The remaining
three scenarios re-inspect this SAME run's persisted output/*.json and the
vendor's accumulated request log -- the app/vendor containers stay up across
scenarios within one grade, and output/ is bind-mounted to the host, so later
scenarios can read what this one wrote without re-running anything.

The vendor boots (per docker-compose.yaml) with GH_V2_ENABLED=1 and
GH_V1_TRUNCATE=candidates:100 -- `GET /v1/candidates` silently caps at 100 of
the seeded 6,000 rows, but every /v1 response for that collection also
carries a `Deprecation`/`Link` breadcrumb pointing at `/v2/candidates`. A
connector that never notices the breadcrumb (or notices it but doesn't follow
it) lands ~100 rows and fails `candidates_rows_exact` outright -- this is
the one true discovery gate on this task's ladder.

Agencies has no such trap (v1 and v2 are byte-identical for it), so it is
reachable by any connector that maps records correctly, including one that
never discovers v2 exists at all -- it is checked here both as a whole-list
answer-key comparison and, per-id, on two named agencies whose wire timestamps
carry different UTC offsets (row-level legibility: a whole-list mismatch says
"something differs", a per-id row says WHICH record and WHICH field).

Replaced checks: the whole-document compares `candidates_backfill_exact` and
`agencies_backfill_exact` are now the per-row, per-field `candidates_rows_exact`
/ `agencies_rows_exact` diffs -- same claim, order-insensitive, and the failure
detail names the row and field instead of one opaque bool.

L1 : app exits 0; candidates.json matches the full 6,000-row answer key; the
     candidate id multiset is exactly the answer key's (no dupes, nothing
     missing); agencies.json matches its answer key both as a whole and on two
     named rows.
L2 : none -- the builtin conduct gates run ONCE per vendor lifetime, in the
     last scenario (this task never recreates the vendor, so its request log
     accumulates from here to the end of the grade and is complete there).
"""

from __future__ import annotations

import json

from bench.verifier.io import read_json_output

# Two agencies whose wire `modified_at` carries a different numeric UTC offset
# (+05:30 and -08:00), so a row-exact check on them is sensitive to the
# offset actually being honored rather than dropped.
_AGENCY_ROW_IDS = ("agy_00011", "agy_00005")


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
    marker_ts = max((e.get("ts", 0) for e in vendor.request_log()), default=-1.0)
    exit_code, _stdout, stderr = ctx.app.run()
    # Published for split_brain_composition.py, which gates this task's single
    # builtin_l2 invocation on the run having actually succeeded.
    ctx.app_run_exit_code = exit_code
    # AND-ed with this run's own data-plane traffic (task-0043 pattern,
    # 2026-08-02): exit 0 alone is vacuously bankable by a do-nothing run.
    # Traffic rather than output readability, because the outputs are only
    # read behind the exit gate below and output/ persists across scenarios.
    ran_data_calls = [
        e for e in vendor.request_log()
        if e.get("ts", 0) > marker_ts and str(e.get("path", "")).startswith(("/v1/", "/v2/"))
    ]
    ctx.check("app_exit_ok",
        exit_code == 0 and len(ran_data_calls) > 0,
        f"exit={exit_code} data_plane_calls={len(ran_data_calls)} stderr={stderr[:500]}",
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )
    if exit_code != 0:
        return

    candidates = read_json_output(ctx.output_dir / "candidates.json", timeout_s=30.0)
    agencies = read_json_output(ctx.output_dir / "agencies.json", timeout_s=15.0)

    if candidates is None:
        ctx.check(
            "candidates_output_exists",
            False,
            "missing/unreadable candidates.json",
            pass_value=0,
            fail_value=-1,
            mandatory=False,
        )
        return
    if agencies is None:
        ctx.check(
            "agencies_output_exists",
            False,
            "missing/unreadable agencies.json",
            pass_value=0,
            fail_value=-1,
            mandatory=False,
        )
        return

    candidates_fx = _load_fixture(ctx, "candidates_backfill.json")
    agencies_fx = _load_fixture(ctx, "agencies_backfill.json")

    # THE discovery gate: v1 silently caps this collection at 100 of 6,000 rows
    # and only a Deprecation/Link breadcrumb points at /v2. Full row-for-row
    # correctness is unreachable without noticing and following it.
    candidates_diffs = _row_diff(candidates, candidates_fx)
    ctx.check("candidates_rows_exact",
        not candidates_diffs,
        _diff_detail("candidates", candidates, candidates_fx, candidates_diffs),
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )
    # A connector that stayed on v1 alone (missing the breadcrumb) lands ~100
    # rows instead of 6000 -- this makes that failure legible on its own,
    # independent of the full-equality check above.
    # The truncation made legible on its own: a connector that never escaped v1
    # lands ~100 rows. Losing 98% of the tenant must not be Solved.
    ctx.check("candidates_row_count_6000",
        len(candidates) == 6000,
        f"rows={len(candidates)} (expected 6000; v1 alone silently caps at 100)",
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )
    # Exactly-once coverage of the id space, independent of field mapping: the
    # emitted source_id multiset must equal the answer key's -- nothing missing,
    # nothing duplicated, nothing invented. (A bare "no duplicates" check is
    # passed for free by any short output, which is why this is stated as
    # multiset equality against the key rather than as distinctness alone.)
    # str() so a row missing source_id (or carrying a non-string one) sorts
    # instead of raising and taking the whole grade down with it.
    out_ids = sorted(str(r.get("source_id")) for r in candidates)
    key_ids = sorted(str(r["source_id"]) for r in candidates_fx)
    missing = sorted(set(key_ids) - set(out_ids))
    extra = sorted(set(out_ids) - set(key_ids))
    ctx.check("candidates_ids_exactly_once",
        out_ids == key_ids,
        f"rows={len(out_ids)} distinct={len(set(out_ids))} key={len(key_ids)} "
        f"missing={missing[:3]}(+{max(0, len(missing) - 3)}) extra={extra[:3]}",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )

    # Agencies carries no version trap (v1 and v2 are byte-identical for it), so
    # this is supporting rather than central -- but it is NOT free: the starter
    # fails it (measured), because GlobalHire's per-record numeric UTC offsets
    # still have to be honored.
    agencies_diffs = _row_diff(agencies, agencies_fx)
    ctx.check("agencies_rows_exact",
        not agencies_diffs,
        _diff_detail("agencies", agencies, agencies_fx, agencies_diffs),
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )
    agencies_by_id = {r.get("source_id"): r for r in agencies}
    agencies_key_by_id = {r["source_id"]: r for r in agencies_fx}
    for agency_id in _AGENCY_ROW_IDS:
        want = agencies_key_by_id.get(agency_id)
        got = agencies_by_id.get(agency_id)
        ctx.check(f"agency_row_exact:{agency_id}",
            want is not None and got == want,
            f"got={got!r}" if got != want else "row matches answer key",
            pass_value=1,
            fail_value=0,
            mandatory=False,
        )
