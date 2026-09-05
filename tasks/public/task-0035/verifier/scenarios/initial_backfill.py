"""initial_backfill — Vettly full backfill at CHECKPOINT=0.

Drives the connector's first pass (``python -m vettly_sync``) against the
pristine sandbox (300 subjects / 400 checks / 250 reports = 950 records,
page size 50 -> many pages per collection) and asserts:

  * output matches the answer-key fixtures for all three collections;
  * every report's `finished_at` (wire) is correctly mapped to canonical
    `completed_at`, and every timestamp is parsed as an epoch-seconds int
    (never mis-parsed as ISO, never treated as millis);
  * the token log shows correct single-use rotation: no refresh_token value
    is ever presented twice across the whole backfill;
  * the backfill mints more than one access token overall (the dataset is
    large enough that a single ~75s effective token lifetime cannot cover
    the whole crawl), proving the connector actually exercises re-auth
    rather than getting lucky with one long-lived token.

Then the whole conduct rulebook (builtin_l2).
"""

import json

from bench.verifier.builtin_l2 import builtin_l2
from bench.verifier.io import read_json_output


def _row_diff(rows, want):
    """Per-source_id, per-field comparison against the answer key.

    Replaces the `output == fixture` compares behind
    `initial_backfill_checks.json_matches_fixture`,
    `initial_backfill_reports.json_matches_fixture`,
    `initial_backfill_subjects.json_matches_fixture`,
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
                # The visible contract says `data` contains record fields; it
                # does not define gold's private business-fields-only
                # projection. Require every canonical business value while
                # permitting additional raw source/envelope fields.
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


def _assert_no_replayed_refresh(ctx) -> None:
    token_log = ctx.vendor("vettly").token_log()
    # Every refresh_token VALUE presented on the request side must be unique
    # across the run; the vendor's own token log records mints, not the
    # connector's outgoing refresh_token values, so cross-check via the
    # request log's /oauth/token bodies instead.
    request_log = ctx.vendor("vettly").request_log()
    refresh_calls = [
        e for e in request_log
        if e.get("path") == "/oauth/token"
        and isinstance(e.get("body"), dict)
        and e["body"].get("grant_type") == "refresh_token"
    ]
    presented = [e["body"].get("refresh_token") for e in refresh_calls if e["body"].get("refresh_token")]
    # Evidence slice: at least one refresh_token actually presented. Zero
    # refresh calls (e.g. a submission that crashes before ever
    # authenticating) makes "0 duplicates" and "0 failures" vacuously true
    # for both checks below.
    if presented:
        ctx.check("no_refresh_token_replayed",
            len(presented) == len(set(presented)),
            f"{len(presented)} refresh calls, {len(set(presented))} distinct refresh_token values presented",
            pass_value=2,
            fail_value=0,
            mandatory=False,
        )
        # Every refresh call in a correct rotation must succeed (200) — a
        # connector that replays a spent token gets 400 invalid_grant instead.
        failed = [e for e in refresh_calls if int(e.get("status", 0)) >= 400]
        ctx.check("all_refresh_calls_succeeded",
            len(failed) == 0,
            f"{len(failed)} of {len(refresh_calls)} refresh calls failed (single-use replay or dead grant)",
            pass_value=2,
            fail_value=0,
            mandatory=False,
        )
    # THE trap, and recorded unconditionally: the docs claim a refresh token
    # lives until its absolute expiry, so a docs-faithful connector mints once
    # and rides it. A 950-record, page-size-50 backfill outlives the access
    # token, so exactly one mint means the connector never rotated at all.
    ctx.check("multiple_token_mints_over_long_backfill",
        len(token_log) > 1,
        f"only {len(token_log)} token(s) minted for a 950-record, page-size-50 backfill",
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )


def _assert_no_query_credential(ctx) -> None:
    request_log = ctx.vendor("vettly").request_log()
    # Evidence slice: some real API call happened. The compose/rig
    # healthcheck polls `GET /` unconditionally even for a silent
    # connector, so "request_log non-empty" alone is not evidence — scope
    # to actual vendor surface paths (auth + data plane).
    real_calls = [e for e in request_log if e.get("path") not in ("/", "")]
    if not real_calls:
        return
    leaks = [
        e
        for e in request_log
        if "client_secret" in (e.get("query") or {})
        or "access_token" in (e.get("query") or {})
        or "refresh_token" in (e.get("query") or {})
    ]
    # NOT `no_credentials_in_query_string`: builtin_l2 registers a check under
    # that exact name on the same signal, and check_probe_bar rejects one name
    # recorded with two different scorings (measured on task-0028). This clone
    # exists because it also looks for `refresh_token` in the query string --
    # this task's own credential -- which builtin_l2's generic version does not.
    # 0/-1: a leak must cost, but a connector that simply does not leak has
    # earned nothing here.
    ctx.check("no_oauth_credential_in_query_string",
        len(leaks) == 0,
        f"{len(leaks)} request(s) carried a credential as a query param",
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )


async def _compare(ctx, filename: str, fixture_name: str, exit_code: int) -> None:
    output_path = ctx.output_dir / filename
    output = read_json_output(output_path, timeout_s=20.0 if exit_code == 0 else 0.5)
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
    # Conjoined with this run's exit code for the same reason as incremental.py:
    # a fixture match on a file this run did not write is not evidence. This
    # scenario runs first so nothing precedes it today, but the guard costs
    # nothing and survives any future reordering.
    diffs = _row_diff(output, fixture)
    detail = f"exit={exit_code}; " + _diff_detail(filename, output, fixture, diffs)
    ok = exit_code == 0 and not diffs
    # reports.json is mandatory: it is the collection carrying the field-mapping
    # lie (`finished_at` on the wire -> canonical `completed_at`), so a complete
    # solution must get it exactly right. Recorded unconditionally now -- a
    # missing file is a diff, not an absent check.
    if filename == "reports.json":
        ctx.check(f"initial_backfill_{filename}_rows_exact", ok, detail,
            pass_value=2, fail_value=0, mandatory=True,
        )
    else:
        ctx.check(f"initial_backfill_{filename}_rows_exact", ok, detail,
            pass_value=2, fail_value=0, mandatory=False,
        )


def _assert_completed_at_mapped(ctx) -> None:
    output_path = ctx.output_dir / "reports.json"
    output = read_json_output(output_path, timeout_s=5.0)
    if not output:
        return
    missing = [
        row["source_id"] for row in output
        if not row.get("is_deleted") and row.get("data", {}).get("completed_at") is None
    ]
    ctx.check("completed_at_mapped_from_finished_at",
        len(missing) == 0,
        f"{len(missing)} report(s) missing canonical completed_at: {missing[:5]}",
        pass_value=2,
        fail_value=0,
        mandatory=False,
    )


async def run(ctx) -> None:
    # CHECKPOINT=0 is the pristine world (the compose default), but
    # `multiple_token_mints_over_long_backfill` (below) previously relied on
    # the backfill's REAL wall-clock duration outrunning the token's ~75s
    # effective TTL — a race a fast connector against a local sandbox can
    # simply win, giving a false gold failure (measured 2026-08-01: gold's
    # backfill completes in ~1s in the Docker-free rig, nowhere near 75s).
    # Canon: a fault must never be expressed as a wall-clock duration the
    # connector might beat. Vettly ships a deterministic knob for exactly
    # this — FAULT_TOKEN_EXPIRY_MIDRUN=1 backdates the FIRST access token
    # from the FIRST grant so it is born already-expired, forcing a re-auth
    # on the very first data-plane call regardless of how fast the crawl
    # runs. Needs its own boot to take effect (env is read once at exec
    # time), hence the explicit recreate at the same checkpoint=0.
    ctx.vendor("vettly")._stack.vendor_env["FAULT_TOKEN_EXPIRY_MIDRUN"] = "1"
    ctx.vendor("vettly").recreate(checkpoint=0)

    marker_ts = max((e.get("ts", 0) for e in ctx.vendor("vettly").request_log()), default=-1.0)
    exit_code, stdout, stderr = ctx.app.run()
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

    await _compare(ctx, "subjects.json", "subjects_checkpoint_0.json", exit_code)
    await _compare(ctx, "checks.json", "checks_checkpoint_0.json", exit_code)
    await _compare(ctx, "reports.json", "reports_checkpoint_0.json", exit_code)

    await builtin_l2(ctx)
    _assert_no_replayed_refresh(ctx)
    _assert_no_query_credential(ctx)
    _assert_completed_at_mapped(ctx)
