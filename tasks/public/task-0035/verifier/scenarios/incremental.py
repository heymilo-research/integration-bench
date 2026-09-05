"""incremental — Vettly catch-up pass after the scripted mutation timeline.

Recreates the vendor at CHECKPOINT=5 (every mutation in
``vendors/vettly/mutations.yaml`` applied: chk_0042/chk_0099 status updates,
rpt_0011/rpt_0033 result updates, sub_0007 soft-deleted), then runs
``python -m vettly_sync --incremental``. Because initial_backfill ran first
and left the back-filled store (and the persisted per-collection
`modified_since` watermark) in the shared output volume, this pass must
converge that store to upstream reality using ONLY the delta.

Checks: output matches the post-mutation answer key for all three
collections; the incremental pass sent `modified_since=<epoch seconds>` on
every collection request; the watermark round-trips in epoch seconds (no ISO
conversion — a connector that built an ISO watermark would see the vendor
ignore the filter and return the whole table, which the bounded-request-count
check below would catch); then the conduct rulebook.
"""

import json

from bench.verifier.builtin_l2 import builtin_l2
from bench.verifier.io import read_json_output

# All 5 timeline entries are applied by CHECKPOINT >= 5 (index i applied when
# CHECKPOINT >= i+1; the last entry, rpt_0033's update, is index 4).
MUTATED_CHECKPOINT = 5


def _row_diff(rows, want):
    """Per-source_id, per-field comparison against the answer key.

    Replaces the `output == fixture` compares behind
    `incremental_checks.json_matches_fixture`,
    `incremental_reports.json_matches_fixture` and
    `incremental_subjects.json_matches_fixture`.
    """
    if rows is None:
        return [{"source_id": "<no output>", "field": "<missing or unreadable>"}]
    got_by_id = {r.get("source_id"): r for r in rows}
    want_by_id = {r.get("source_id"): r for r in want}
    diffs = []
    for sid in sorted(set(want_by_id) | set(got_by_id), key=str):
        w, g = want_by_id.get(sid), got_by_id.get(sid)
        if g is None:
            diffs.append({"source_id": sid, "field": "<missing row>"})
            continue
        if w is None:
            diffs.append({"source_id": sid, "field": "<unexpected row>"})
            continue
        for key in sorted(set(w) | set(g)):
            if key == "data" and isinstance(w.get(key), dict) and isinstance(g.get(key), dict):
                for field, value in sorted(w[key].items()):
                    if g[key].get(field) != value:
                        diffs.append({"source_id": sid, "field": f"data.{field}",
                                      "want": value, "got": g[key].get(field)})
            elif w.get(key) != g.get(key):
                diffs.append({"source_id": sid, "field": key,
                              "want": w.get(key), "got": g.get(key)})
    return diffs


def _diff_detail(label, rows, want, diffs, limit=3):
    n = "none" if rows is None else len(rows)
    if not diffs:
        return f"{label}: {n} row(s), every field matches the answer key"
    shown = json.dumps(diffs[:limit], sort_keys=True, default=str)
    more = f" (+{len(diffs) - limit} more)" if len(diffs) > limit else ""
    return f"{label}: rows={n} expected={len(want)}; {len(diffs)} field diff(s): {shown}{more}"


def _assert_modified_since_epoch_seconds(ctx) -> None:
    request_log = ctx.vendor("vettly").request_log()
    collection_requests = [
        e
        for e in request_log
        if e.get("path") in ("/v1/subjects", "/v1/checks", "/v1/reports")
    ]
    by_path: dict[str, list[dict]] = {}
    for e in sorted(collection_requests, key=lambda e: e.get("ts", 0)):
        by_path.setdefault(e["path"], []).append(e)

    # Evidence slice: at least one collection was actually listed this pass.
    # A submission that crashes before making any /v1/* call makes "0
    # collections missing modified_since" and "0 non-epoch values"
    # vacuously true for both checks below.
    if not by_path:
        return

    missing = []
    non_epoch = []
    for path, entries in by_path.items():
        first = entries[0]
        since = (first.get("query") or {}).get("modified_since")
        if since is None:
            missing.append(path)
            continue
        try:
            int(since)
        except (TypeError, ValueError):
            non_epoch.append((path, since))

    ctx.check("incremental_pass_uses_modified_since",
        len(missing) == 0,
        f"collections never sent `modified_since`: {missing}",
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )
    ctx.check("modified_since_is_epoch_seconds",
        len(non_epoch) == 0,
        f"non-epoch-seconds modified_since values: {non_epoch}",
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )


def _assert_bounded_request_count(ctx) -> None:
    # The vendor's request log is reset on every recreate (fresh boot), so by
    # the time this scenario reads it, it holds ONLY this incremental pass's
    # requests. The filtered delta is 1-2 rows per collection (fits on a
    # single page), so a `modified_since`-scoped crawl needs roughly one
    # request per collection plus token calls — many more implies the
    # connector ignored the watermark and re-crawled the full table (or, if
    # it treated the watermark as ISO, the vendor would have returned
    # everything unfiltered, both of which this bound catches).
    request_log = ctx.vendor("vettly").request_log()
    v1_requests = [e for e in request_log if e.get("path", "").startswith("/v1/")]
    # Evidence slice: the pass actually listed something. Zero /v1/* calls
    # (e.g. a crash before any request) makes "<=6 requests" vacuously true
    # without the connector ever exercising the watermark at all.
    if v1_requests:
        ctx.check("no_unnecessary_full_resync:incremental_pass",
            len(v1_requests) <= 6,
            f"{len(v1_requests)} /v1/* requests on the incremental pass; expected a small bounded delta",
            pass_value=1,
            fail_value=0,
            mandatory=False,
        )


async def _compare(ctx, filename: str, fixture_name: str, exit_code: int) -> None:
    output_path = ctx.output_dir / filename
    output = read_json_output(output_path, timeout_s=15.0 if exit_code == 0 else 0.5)
    if output is None:
        ctx.check(
            f"{filename}_exists",
            False,
            f"missing or unreadable {filename}",
            pass_value=0,
            fail_value=-1,
            mandatory=False,
        )
    fixture = json.loads((ctx.fixtures / fixture_name).read_text(encoding="utf-8"))
    # Conjoin THIS run's exit code: `initial_backfill` already wrote these files
    # into the shared workspace, so a crashed incremental run never rewrites them
    # and the leftovers can still match — passing on INERTIA rather than on this
    # run's work (measured on task-0044, 2026-08-01). The short read timeout does
    # not help: a stale file on disk reads instantly.
    diffs = _row_diff(output, fixture)
    detail = f"exit={exit_code}; " + _diff_detail(filename, output, fixture, diffs)
    ok = exit_code == 0 and not diffs
    if filename == "reports.json":
        ctx.check(f"incremental_{filename}_rows_exact", ok, detail,
            pass_value=2, fail_value=0, mandatory=True,
        )
    else:
        ctx.check(f"incremental_{filename}_rows_exact", ok, detail,
            pass_value=2, fail_value=0, mandatory=False,
        )


async def run(ctx) -> None:
    ctx.vendor("vettly").recreate(checkpoint=MUTATED_CHECKPOINT)

    marker_ts = max((e.get("ts", 0) for e in ctx.vendor("vettly").request_log()), default=-1.0)
    exit_code, stdout, stderr = ctx.app.run(["--incremental"])
    # AND-ed with this run's own data-plane traffic (task-0043 pattern,
    # 2026-08-02): exit 0 alone is vacuously bankable by a do-nothing run, and
    # every scenario in this task writes the SAME subjects/checks/reports
    # filenames into a shared output dir, so a readable file is not evidence
    # THIS run produced it.
    ran_data_calls = [
        e for e in ctx.vendor("vettly").request_log()
        if e.get("ts", 0) > marker_ts and str(e.get("path", "")).startswith("/v1/")
    ]
    # Pure plumbing.
    ctx.check("app_exit_ok",
        exit_code == 0 and len(ran_data_calls) > 0,
        f"exit={exit_code} data_plane_calls={len(ran_data_calls)} stderr={stderr[:500]}",
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )

    await _compare(ctx, "subjects.json", "subjects_checkpoint_1.json", exit_code)
    await _compare(ctx, "checks.json", "checks_checkpoint_1.json", exit_code)
    await _compare(ctx, "reports.json", "reports_checkpoint_1.json", exit_code)

    await builtin_l2(ctx)
    _assert_modified_since_epoch_seconds(ctx)
    _assert_bounded_request_count(ctx)
