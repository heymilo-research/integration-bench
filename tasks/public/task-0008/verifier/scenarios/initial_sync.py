"""initial_sync — StaffLine full back-fill at CHECKPOINT=0.

Drives the connector's first full-sync pass (``staffline_fullsync sync``)
against the pristine sandbox and grades the two canonical output files.

Grading note (2026-08-07). This scenario previously voted once per file with
``initial_sync_{candidates,applications}.json_matches_fixture``. Those were
deleted in the per-test-scoring migration — correctly, they were whole-document
compares — but nothing replaced them, which left the whole scenario recording
only ``app_exit_ok`` plus an ``_exists`` check that fires *only when the file is
missing*. A successful run therefore graded NOTHING here, and the vendor's
``include_stage`` requirement (LIES.md #1: ``GET /svc/applications`` answers
``400 MISSING PARAM include_stage`` until the param is sent, so a connector that
omits it backfills zero applications) went ungraded. Replaced with per-entity row
counts, per-field equality that names the offending row and field, and the
request-log evidence for the include_stage requirement.

Then the conduct rulebook (builtin_l2) plus the wrong_auth_route gate.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from bench.verifier.builtin_l2 import builtin_l2
from bench.verifier.io import read_json_output

from _scenario_util import (  # noqa: E402
    assert_no_query_token,
    grade_fields,
    row_count_detail,
)

_NO_APPLICATIONS_NOTE = (
    "0 here means the connector never sent include_stage — "
    "GET /svc/applications answers 400 without it"
)


async def run(ctx) -> None:
    # CHECKPOINT=0 is the pristine world (the compose default); no recreate needed.
    handle = ctx.vendor("staffline")
    marker_ts = max((e.get("ts", 0) for e in handle.request_log()), default=-1.0)
    exit_code, stdout, stderr = ctx.app.run(["sync"])
    # AND-ed with this run's own data-plane traffic (task-0043 pattern,
    # 2026-08-02): exit 0 alone is vacuously bankable by a do-nothing run, and
    # the three scenarios here share one output dir so a leftover
    # candidates.json is no evidence that THIS run did anything. The compose
    # healthcheck's bare "/" pings don't count.
    ran_data_calls = [
        e for e in handle.request_log()
        if e.get("ts", 0) > marker_ts and e.get("path") not in ("/", "")
    ]
    ctx.check(
        "app_exit_ok",
        exit_code == 0 and len(ran_data_calls) > 0,
        f"exit={exit_code} data_plane_calls={len(ran_data_calls)} stderr={stderr[:500]}",
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )

    for entity in ("candidates", "applications"):
        output = read_json_output(
            ctx.output_dir / f"{entity}.json",
            timeout_s=15.0 if exit_code == 0 else 0.5,
        )
        if output is None:
            ctx.check(
                f"initial_{entity}.json_exists",
                False,
                f"missing or unreadable {entity}.json",
                pass_value=0,
                fail_value=-1,
                mandatory=False,
            )
        fixture = read_json_output(
            ctx.fixtures / f"{entity}_initial.json", timeout_s=5.0
        ) or []
        # Row counts are scored per entity with literal values at this call site,
        # not inside the helper — see _scenario_util.grade_fields for why.
        if entity == "applications":
            # +2 and mandatory: applications is where the include_stage
            # requirement bites, and a backfill that lands zero applications is
            # not a solution to the ticket however clean the rest of the run looks.
            ok, detail = row_count_detail(
                entity, output, fixture, _NO_APPLICATIONS_NOTE
            )
            ctx.check(
                "initial_row_count:applications",
                ok,
                detail,
                pass_value=2,
                fail_value=0,
                mandatory=True,
            )
        else:
            ok, detail = row_count_detail(entity, output, fixture)
            ctx.check(
                "initial_row_count:candidates",
                ok,
                detail,
                pass_value=1,
                fail_value=0,
                mandatory=False,
            )
        grade_fields(ctx, "initial_", entity, output, fixture)

    # Wire-level evidence for the requirement the applications row count depends
    # on: EVERY applications list call must carry include_stage, not merely the
    # first. `query` is read as a DICT, not a raw query string — a substring match
    # against str() would appear to work and silently never fire.
    calls = [
        e
        for e in handle.request_log()
        if e.get("method") == "GET" and e.get("path") == "/svc/applications"
    ]
    with_param = [e for e in calls if (e.get("query") or {}).get("include_stage")]
    ctx.check(
        "include_stage_sent_on_every_applications_list",
        len(calls) > 0 and len(with_param) == len(calls),
        f"{len(with_param)}/{len(calls)} GET /svc/applications calls sent include_stage",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )

    await builtin_l2(ctx)
    assert_no_query_token(ctx)
